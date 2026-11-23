"""Real-browser tests for the W3b sampler core (second slice): project tempo, pad filter and envelope, mute groups,
16 levels, resample, song mode, stems.

Run with a Python that has Playwright installed (like test_core.py):
    /usr/bin/python3 -m unittest tests/e2e/test_core2.py
Every test drives `window.choppad.song.session` (store actions and session methods); none depends on a selector of the
song screen. Songs are opened from a temporary copy of their media folders (audio/video linked, json copied), so the
real library is never written to. Skips when Playwright or a library song is missing.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from megastate import forget_projects, saved_project  # noqa: E402
MEDIA = os.path.join(REPO, "media")
HEAVY = {"audio.wav", "video.mp4"}  # linked, not copied

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def library_songs(n=2):
    out = []
    for sid in sorted(os.listdir(MEDIA)) if os.path.isdir(MEDIA) else []:
        d = os.path.join(MEDIA, sid)
        if all(os.path.isfile(os.path.join(d, f)) for f in ("audio.wav", "video.mp4", "meta.json", "analysis.json", "chops.json")):
            out.append(sid)
    # Funkorama (101 BPM) first when it is there: it is the song the acceptance numbers are written for
    out.sort(key=lambda s: (s != "dmIO-dT5BFw", s))
    return out[:n]


def shortest_song():
    """The shortest library song (stems for it are quick to make in a test)."""
    best = None
    for sid in library_songs(99):
        try:
            d = json.load(open(os.path.join(MEDIA, sid, "meta.json"))).get("duration_s") or 1e9
        except (OSError, ValueError):
            continue
        if best is None or d < best[0]:
            best = (d, sid)
    return best[1] if best else None


class FakeStemsService:
    """A stems service for the test: answers a POST of audio.wav with a zip of four quieter copies of it (gains 1/2, 1/4, 1/8, 1/16)."""
    def __init__(self):
        import io
        import threading
        import wave
        import zipfile
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import numpy as np
        self.requests = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests += 1
                with wave.open(io.BytesIO(body)) as w:
                    ch, sw, sr, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
                x = np.frombuffer(frames, dtype="<i2")
                out = io.BytesIO()
                with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
                    for name, gain in (("drums", 0.5), ("bass", 0.25), ("vocals", 0.125), ("other", 0.0625)):
                        b = io.BytesIO()
                        with wave.open(b, "wb") as o:
                            o.setnchannels(ch); o.setsampwidth(sw); o.setframerate(sr)
                            o.writeframes((x * gain).astype("<i2").tobytes())
                        z.writestr(f"{name}.wav", b.getvalue())
                data = out.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/separate"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def chrome_path():
    for c in [os.environ.get("CHOPPAD_CHROME"), *sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")), reverse=True),
              "/usr/bin/chromium", "/usr/bin/google-chrome-stable"]:
        if c and os.path.isfile(c):
            return c
    return None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def api(base, path, body=None, method=None, raw=None, headers=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    h = {"Content-Type": "application/json"} if raw is None else {}
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


# Analysis helpers that run inside the page: decode a WAV, find where a known sound starts in a render, measure how long it is audible.
PAGE_HELPERS = """
window.__t = {
  async decode(bytes) { return window.choppad.audio.ctx.decodeAudioData(new Uint8Array(bytes).buffer.slice(0)); },
  // lag (in samples) at which `ref` (first `n` samples) best matches `sig` around `expect`, searched +-`range` samples
  lagOf(sig, ref, expect, range, n) {
    let best = -2, bestLag = 0;
    let er = 0; for (let i = 0; i < n; i++) er += ref[i] * ref[i];
    for (let lag = -range; lag <= range; lag++) {
      let dot = 0, es = 0;
      const o = expect + lag;
      if (o < 0 || o + n > sig.length) continue;
      for (let i = 0; i < n; i++) { dot += ref[i] * sig[o + i]; es += sig[o + i] * sig[o + i]; }
      const c = dot / Math.sqrt(er * es + 1e-30);
      if (c > best) { best = c; bestLag = lag; }
    }
    return { lag: bestLag, corr: best };
  },
  // seconds from `from` until the last sample above `frac` x the peak of the stretch [from, from+span*1.5]
  audibleEnd(sig, sr, from, span, frac) {
    const a = Math.round(from * sr), b = Math.min(sig.length, a + Math.round(span * 1.5 * sr));
    let peak = 0; for (let i = a; i < b; i++) peak = Math.max(peak, Math.abs(sig[i]));
    let last = a; for (let i = a; i < b; i++) if (Math.abs(sig[i]) > frac * peak) last = i;
    return (last - a) / sr;
  },
};
"""


@unittest.skipIf(sync_playwright is None, "playwright is not installed for this Python")
@unittest.skipIf(len(library_songs(1)) < 1, "need a library song in media/")
class Core2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sids = library_songs()
        cls.sid = cls.sids[0]
        cls.short = shortest_song()
        if cls.short not in cls.sids:
            cls.sids = cls.sids + [cls.short]
        cls.tmp = tempfile.mkdtemp(prefix="choppad-core2-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.pristine = {}
        for sid in cls.sids:
            dst = os.path.join(cls.tmp, "media", sid)
            os.makedirs(dst)
            src = os.path.join(MEDIA, sid)
            for f in os.listdir(src):
                if f in HEAVY:
                    os.symlink(os.path.join(src, f), os.path.join(dst, f))
                elif f.endswith(".json") and f not in ("project.json", "onsets.json"):
                    shutil.copy(os.path.join(src, f), dst)
            cls.pristine[sid] = open(os.path.join(dst, "chops.json")).read()
        cls.stems_service = FakeStemsService()
        cls.addClassCleanup(cls.stems_service.close)
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = subprocess.Popen(
            [os.path.join(REPO, ".venv", "bin", "python"), os.path.join(REPO, "components", "integrate", "choppad.py"),
             "--serve", "--port", str(cls.port)],
            env={**os.environ, "CHOPPAD_REPO": cls.tmp, "CHOPPAD_STEMS_BACKEND": "remote", "CHOPPAD_STEMS_URL": cls.stems_service.url},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.server.wait, 10)
        cls.addClassCleanup(cls.server.terminate)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + "/api/songs", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("server did not start")
        cls.pw = sync_playwright().start()
        cls.addClassCleanup(cls.pw.stop)
        args = ["--autoplay-policy=no-user-gesture-required"]
        try:
            cls.browser = cls.pw.chromium.launch(args=args)
        except Exception:
            cls.browser = cls.pw.chromium.launch(args=args, executable_path=chrome_path(), headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):  # every test starts from an empty project and the original chop table
        forget_projects(os.path.join(self.tmp, "media"))
        for sid in self.sids:
            d = os.path.join(self.tmp, "media", sid)
            for f in ("project.json", "onsets.json"):
                if os.path.exists(os.path.join(d, f)):
                    os.remove(os.path.join(d, f))
            open(os.path.join(d, "chops.json"), "w").write(self.pristine[sid])
            for sub in ("stretch", "resample", "stems"):
                shutil.rmtree(os.path.join(d, sub), ignore_errors=True)

    S = "window.choppad.song.session"

    def open_song(self, sid=None):
        page = self.browser.new_page(viewport={"width": 1200, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
        page.goto(f"{self.base}/#/song/{sid or self.sid}")
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.evaluate(PAGE_HELPERS)
        self.addCleanup(page.close)
        return page, errors

    def project_on_disk(self, sid=None):
        return api(self.base, f"/api/songs/{sid or self.sid}")[1]["project"]

    # ------------------------------------------------------------------ 1. project tempo
    def test_project_tempo_fit_pattern_and_render(self):
        page, errors = self.open_song()
        S = self.S
        song_bpm = page.evaluate(f"{S}.store.get().chops.bpm")
        tempo = round(song_bpm * 0.8, 3)                                  # 20% slower
        pads = [0, 1, 2, 3]
        page.evaluate(f"""() => {{
            const s = {S};
            s.store.dispatch({{ type: 'pattern.load', pattern: {{ bars: 2, events: {json.dumps([{"beat": 2 * i, "pad": p, "bank": 0, "vel": 1, "pass": -1} for i, p in enumerate(pads)])} }} }});
            s.store.dispatch({{ type: 'tempo.set', bpm: {tempo} }});
        }}""")
        self.assertEqual(page.evaluate(f"{S}.seq.state.bpm"), tempo, "the sequencer runs at the project tempo")
        self.assertEqual(page.evaluate(f"{S}.tempoBpm"), tempo)

        # before fitting: the rendered pad is only 80% of its step span (the chop is one beat at the song's tempo)
        span = 60 / tempo
        res = page.evaluate(f"""async () => {{
            const s = {S}; const sr = s.buffer.sampleRate;
            const bytes = await s.renderPatternWav();
            const buf = await window.__t.decode(Array.from(bytes));
            return {{ sr: buf.sampleRate, plain: window.__t.audibleEnd(buf.getChannelData(0), buf.sampleRate, 0, {span}, 0.01) }};
        }}""")
        self.assertLess(res["plain"], 0.88 * span, res)

        fit = page.evaluate(f"{S}.fitToTempo({{ scope: 'pattern' }})")
        self.assertTrue(fit["ok"], fit)
        self.assertEqual(sorted(f["pad"] for f in fit["fitted"]), pads)
        ov = page.evaluate(f"{S}.store.get().project.overrides")
        for p in pads:
            st = ov[f"0:{p}"]["stretch"]
            self.assertEqual(st["fit"], tempo)
            self.assertAlmostEqual(st["ratio"], song_bpm / tempo, places=4)

        # each fitted pad's sound is one step span long at the new tempo, and hits land on the new grid
        out = page.evaluate(f"""async () => {{
            const s = {S}; const T = window.__t;
            const bytes = await s.renderPatternWav();
            const buf = await T.decode(Array.from(bytes)); const sig = buf.getChannelData(0); const sr = buf.sampleRate;
            const hits = [];
            const evs = s.store.get().project.pattern.events;
            for (const e of evs) {{
                const ref = s.engine.getPadBuffer(e.bank, e.pad);
                const expect = Math.round(e.beat * 60 / {tempo} * sr);
                const n = Math.round(0.3 * sr);
                const r = T.lagOf(sig, ref.getChannelData(0), expect, Math.round(0.03 * sr), n);
                hits.push({{ pad: e.pad, errMs: r.lag / sr * 1000, corr: r.corr, bufDur: ref.duration }});
            }}
            return hits;
        }}""")
        for h in out:
            self.assertLess(abs(h["errMs"]), 15, h)          # includes the master limiter's constant 6 ms look-ahead
            self.assertGreater(h["corr"], 0.5, h)
            self.assertAlmostEqual(h["bufDur"], span, delta=0.02 * span, msg=h)
        errs = [h["errMs"] for h in out]
        self.assertLess(max(errs) - min(errs), 2.0, f"hits are evenly on the grid: {errs}")
        # audible length of one fitted pad played alone fills its step
        alone = page.evaluate(f"""async () => {{
            const s = {S}; const T = window.__t;
            const keep = s.store.get().project.pattern;
            s.store.dispatch({{ type: 'pattern.load', pattern: {{ bars: 1, events: [{{ beat: 0, pad: 1, bank: 0, vel: 1, pass: -1 }}] }} }});
            const buf = await T.decode(Array.from(await s.renderPatternWav()));
            const end = T.audibleEnd(buf.getChannelData(0), buf.sampleRate, 0, {span}, 0.01);
            s.store.dispatch({{ type: 'pattern.load', pattern: keep }});
            return end;
        }}""")
        self.assertGreater(alone, 0.93 * span, alone)
        self.assertLess(alone, span + 0.015, alone)

        # MIDI export carries the project tempo
        us = page.evaluate(f"""() => {{ const m = {S}.patternMidi(); for (let i = 0; i < m.length - 5; i++) if (m[i] === 0xff && m[i+1] === 0x51) return (m[i+3] << 16) | (m[i+4] << 8) | m[i+5]; return 0; }}""")
        self.assertAlmostEqual(us, 60e6 / tempo, delta=2)

        # a pad stretched by hand is never touched by a re-fit; fitted pads follow a new tempo
        page.evaluate(f"{S}.stretchPad(0, 5, {{ ratio: 1.5 }})")
        hand = page.evaluate(f"{S}.store.get().project.overrides['0:5'].stretch")
        self.assertNotIn("fit", hand)
        new_tempo = 96
        page.evaluate(f"{S}.setTempo({new_tempo})")
        ov = page.evaluate(f"{S}.store.get().project.overrides")
        for p in pads:
            self.assertEqual(ov[f"0:{p}"]["stretch"]["fit"], new_tempo)
            self.assertAlmostEqual(ov[f"0:{p}"]["stretch"]["ratio"], song_bpm / new_tempo, places=4)
        self.assertEqual(ov["0:5"]["stretch"], hand)
        # back to the song's own tempo: fitted pads are plain slices again, the hand-stretched one stays
        page.evaluate(f"{S}.setTempo(null)")
        ov = page.evaluate(f"{S}.store.get().project.overrides")
        self.assertTrue(all("stretch" not in ov.get(f"0:{p}", {}) for p in pads))
        self.assertEqual(ov["0:5"]["stretch"], hand)
        self.assertEqual(page.evaluate(f"{S}.seq.state.bpm"), song_bpm)

        # saved on the server, shown in a second tab
        page.evaluate(f"{S}.setTempo(88)")
        page.evaluate(f"{S}.remote.flush()")
        self.assertEqual(self.project_on_disk()["tempo"], {"bpm": 88, "follow": True})
        page2, errors2 = self.open_song()
        self.assertEqual(page2.evaluate(f"{S}.store.get().project.tempo.bpm"), 88)
        page.evaluate(f"{S}.store.dispatch({{ type: 'tempo.set', bpm: 70 }})")
        page2.wait_for_function(f"{S}.store.get().project.tempo.bpm === 70", timeout=10000)
        self.assertEqual(errors + errors2, [], str(errors + errors2))

    # ------------------------------------------------------------------ 2. per-pad filter and ADSR
    RENDER_PAD = """async ([pad, params]) => {
        const s = window.choppad.song.session, T = window.__t;
        s.store.dispatch({ type: 'pad.reset', pad });
        if (params) s.store.dispatch({ type: 'pad.set', pad, params });
        s.store.dispatch({ type: 'pattern.load', pattern: { bars: 1, events: [{ beat: 0, pad, bank: 0, vel: 1, pass: -1 }] } });
        const buf = await T.decode(Array.from(await s.renderPatternWav()));
        const x = buf.getChannelData(0), sr = buf.sampleRate;
        const n = Math.round(0.5 * sr);
        let e = 0, d = 0, tail = 0, head = 0;
        for (let i = 1; i < n; i++) { e += x[i] * x[i]; d += (x[i] - x[i - 1]) ** 2; }
        for (let i = Math.round(0.25 * sr); i < Math.round(0.4 * sr); i++) tail += x[i] * x[i];
        for (let i = Math.round(0.02 * sr); i < Math.round(0.1 * sr); i++) head += x[i] * x[i];
        return { energy: e, hf: d / (e + 1e-12), tail, head };
    }"""

    def test_pad_filter_and_envelope_in_the_offline_render_and_over_http(self):
        page, errors = self.open_song()
        S = self.S
        plain = page.evaluate(self.RENDER_PAD, [3, None])
        lp = page.evaluate(self.RENDER_PAD, [3, {"filter": {"type": "lp", "cutoff": 400, "q": 0.7}}])
        hp = page.evaluate(self.RENDER_PAD, [3, {"filter": {"type": "hp", "cutoff": 6000, "q": 0.7}}])
        self.assertLess(lp["hf"], 0.25 * plain["hf"], (plain, lp))      # a low-pass takes the top off
        self.assertGreater(hp["hf"], 2 * plain["hf"], (plain, hp))      # a high-pass leaves the top
        self.assertLess(hp["energy"], 0.5 * plain["energy"])
        # the envelope: sustain 0 with a short decay silences the tail, the default holds it
        shaped = page.evaluate(self.RENDER_PAD, [3, {"decay": 0.04, "sustain": 0}])
        self.assertLess(shaped["tail"], 0.02 * plain["tail"], (plain, shaped))
        self.assertGreater(shaped["head"], 0.01 * plain["head"], "the attack and the start of the decay are still there")
        # an agent (the HTTP action endpoint) sets a filter; the open page shows it and it is saved
        st, out = api(self.base, f"/api/songs/{self.sid}/actions", {"actions": [
            {"type": "pad.set", "pad": 5, "params": {"filter": {"type": "bp", "cutoff": 1500, "q": 4}, "decay": 0.2, "sustain": 0.3}}]})
        self.assertEqual(st, 200)
        page.wait_for_function(f"{S}.store.get().project.pads[5].filter.type === 'bp'", timeout=10000)
        pad5 = page.evaluate(f"{S}.store.get().project.pads[5]")
        self.assertEqual((pad5["filter"], pad5["decay"], pad5["sustain"]), ({"type": "bp", "cutoff": 1500, "q": 4}, 0.2, 0.3))
        self.assertEqual(out["project"]["pads"][5]["filter"]["cutoff"], 1500)
        # the engine voice for that pad has a filter in the graph (and a pad without one has none)
        n = page.evaluate(f"""() => {{
            const ctx = window.choppad.audio.ctx; let made = 0; const orig = ctx.createBiquadFilter.bind(ctx);
            ctx.createBiquadFilter = () => {{ made++; return orig(); }};
            const s = {S}; s.engine.trigger(6, 1); const none = made; s.engine.trigger(5, 1); const one = made;
            s.engine.stopAll(); ctx.createBiquadFilter = orig; return [none, one];
        }}""")
        self.assertEqual(n, [0, 1])
        # export: the saved project's pad settings reach the SFZ
        st, out = api(self.base, f"/api/songs/{self.sid}/export", {"kind": "sfz", "bank": 0})
        self.assertEqual(st, 200, out)
        sfz = open(out["path"]).read()
        self.assertIn("fil_type=bpf_2p", sfz)
        self.assertIn("ampeg_sustain=30.0", sfz)
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ 3. mute groups
    def test_mute_groups_in_the_real_engine_and_over_http(self):
        page, errors = self.open_song()
        S = self.S
        # an agent sets the groups over HTTP; the page gets them
        st, out = api(self.base, f"/api/songs/{self.sid}/actions", {"actions": [
            {"type": "pad.set", "pad": 0, "params": {"muteGroup": 1}}, {"type": "pad.set", "pad": 1, "params": {"muteGroup": 1}},
            {"type": "pad.set", "pad": 2, "params": {"muteGroup": 2}}, {"type": "pad.set", "pad": 9, "params": {"muteGroup": 12}}]})
        self.assertEqual((st, out["applied"]), (200, 3), "a group of 12 is not a group")
        page.wait_for_function(f"{S}.store.get().project.pads[2].muteGroup === 2", timeout=10000)
        self.assertEqual(page.evaluate(f"{S}.store.get().project.pads.map((p) => p.muteGroup).slice(0, 4)"), [1, 1, 2, 0])
        self.assertEqual(self.project_on_disk()["pads"][1]["muteGroup"], 1)
        # which voices a hit cuts: spy on real source nodes (a one-shot voice is only ever stopped by a cut)
        js = """(choke) => {
            const ctx = window.choppad.audio.ctx, s = window.choppad.song.session;
            const made = [], cut = new Set();
            const mk = ctx.createBufferSource.bind(ctx); ctx.createBufferSource = () => { const n = mk(); made.push(n); return n; };
            const stop = AudioBufferSourceNode.prototype.stop;
            AudioBufferSourceNode.prototype.stop = function (...a) { cut.add(this); return stop.apply(this, a); };
            s.store.dispatch({ type: 'settings.set', params: { choke } });
            for (const p of [0, 2, 3]) s.engine.trigger(p, 1);
            const before = made.map((n) => cut.has(n));
            s.engine.trigger(1, 1);
            const after = made.map((n) => cut.has(n));
            s.engine.stopAll(); ctx.createBufferSource = mk; AudioBufferSourceNode.prototype.stop = stop;
            s.store.dispatch({ type: 'settings.set', params: { choke: true } });
            return { before, after };
        }"""
        off = page.evaluate(js, False)
        self.assertEqual(off["before"], [False, False, False])
        self.assertEqual(off["after"][:3], [True, False, False], "pad 1 cuts pad 0 (same group), not pad 2 (other group) or pad 3 (none)")
        on = page.evaluate(js, True)
        self.assertEqual(on["after"][:3], [True, True, True], "global choke: everything is cut, as before")
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ 4. 16 levels
    def test_sixteen_levels_record_and_play_back(self):
        page, errors = self.open_song()
        S = self.S
        spy = f"""() => {{ const s = {S}; window.__calls = []; const t = s.engine.trigger;
            s.engine.trigger = (...a) => {{ window.__calls.push({{ pad: a[0], vel: a[1], extra: a[4] ?? null }}); return t(...a); }}; }}"""
        page.evaluate(spy)
        # pitch: the 16 pads are 16 semitone steps of pad 2
        info = page.evaluate(f"{S}.setSixteenLevels({{ pad: 2, mode: 'pitch' }})")
        self.assertTrue(info["ok"])
        self.assertEqual([l["pitch"] for l in info["levels"]], list(range(-7, 9)))
        self.assertEqual(page.evaluate(f"{S}.store.get().settings.sixteen"), {"pad": 2, "mode": "pitch"})
        page.evaluate(f"() => {{ {S}.hit(0); {S}.hit(7); {S}.hit(15); }}")
        calls = page.evaluate("window.__calls")
        self.assertEqual([c["pad"] for c in calls], [2, 2, 2])
        self.assertEqual([c["extra"]["pitch"] for c in calls], [-7, 0, 8])
        self.assertEqual(page.evaluate(f"{S}.store.get().selected"), 2, "the inspector shows the source pad")
        # recording writes the source pad with a per-event pitch; undo and step toggles still work
        page.evaluate(f"{S}.panic()")
        page.evaluate(f"{S}.toggleRecord()")
        page.wait_for_timeout(300)
        page.evaluate(f"() => {{ {S}.hit(3); {S}.hit(12); }}")
        page.evaluate(f"{S}.toggleRecord()")
        page.evaluate(f"{S}.panic()")
        evs = page.evaluate(f"{S}.store.get().project.pattern.events")
        self.assertEqual(sorted(e["pitch"] for e in evs), [-4, 5])
        self.assertTrue(all(e["pad"] == 2 and "cutoff" not in e for e in evs))
        page.evaluate(f"{S}.store.dispatch({{ type: 'pattern.toggleStep', pad: 6, step: 3 }})")
        self.assertEqual(len(page.evaluate(f"{S}.store.get().project.pattern.events")), 3)
        page.evaluate(f"{S}.store.dispatch({{ type: 'pattern.undo' }})")
        left = page.evaluate(f"{S}.store.get().project.pattern.events")
        self.assertEqual([e["pad"] for e in left], [6], "undo took the recorded take, the drawn step stays")
        # saved with the events
        page.evaluate(f"{S}.store.dispatch({{ type: 'pattern.addEvent', event: {{ beat: 1, pad: 2, bank: 0, vel: 1, pass: 9, pitch: 4, cutoff: 800 }} }})")
        page.evaluate(f"{S}.remote.flush()")
        saved = self.project_on_disk()["pattern"]["events"]
        self.assertIn({"beat": 1, "pad": 2, "bank": 0, "vel": 1, "pass": 9, "pitch": 4, "cutoff": 800}, saved)

        # velocity: Full level goes off so the velocities count, and comes back when the mode is left
        self.assertTrue(page.evaluate(f"{S}.store.get().settings.fullLevel"))
        page.evaluate(f"{S}.setSixteenLevels({{ pad: 1, mode: 'velocity' }})")
        self.assertFalse(page.evaluate(f"{S}.store.get().settings.fullLevel"))
        page.evaluate("window.__calls = []")
        page.evaluate(f"() => {{ {S}.hit(0); {S}.hit(15); }}")
        self.assertEqual([round(c["vel"], 4) for c in page.evaluate("window.__calls")], [0.0625, 1.0])
        page.evaluate(f"{S}.setSixteenLevels({{ mode: 'off' }})")
        self.assertTrue(page.evaluate(f"{S}.store.get().settings.fullLevel"))
        self.assertIsNone(page.evaluate(f"{S}.store.get().settings.sixteen"))
        page.evaluate("window.__calls = []")
        page.evaluate(f"{S}.hit(4)")
        self.assertEqual([c["pad"] for c in page.evaluate("window.__calls")], [4], "off: pads are pads again")

        # the pitch and the cutoff of an event are in the offline render
        res = page.evaluate(f"""async () => {{
            const s = {S}, T = window.__t;
            const render = async (ev) => {{
                s.store.dispatch({{ type: 'pattern.load', pattern: {{ bars: 1, events: [{{ beat: 0, pad: 2, bank: 0, vel: 1, pass: -1, ...ev }}] }} }});
                const b = await T.decode(Array.from(await s.renderPatternWav()));
                const x = b.getChannelData(0), sr = b.sampleRate, n = Math.round(0.6 * sr);
                let e = 0, d = 0; for (let i = 1; i < n; i++) {{ e += x[i] * x[i]; d += (x[i] - x[i - 1]) ** 2; }}
                return {{ hf: d / (e + 1e-12), end: T.audibleEnd(x, sr, 0, 0.6, 0.01) }};
            }};
            return {{ plain: await render({{}}), low: await render({{ pitch: -7 }}), high: await render({{ pitch: 8 }}),
                     dark: await render({{ cutoff: 150 }}), bright: await render({{ cutoff: 16000 }}) }};
        }}""")
        self.assertLess(res["dark"]["hf"], 0.2 * res["plain"]["hf"], res)       # a low cutoff takes the top off
        self.assertAlmostEqual(res["bright"]["hf"], res["plain"]["hf"], delta=0.35 * res["plain"]["hf"], msg=res)
        self.assertLess(res["high"]["end"], 0.6 * res["low"]["end"], res)         # +8 semitones is shorter than -7
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ 5. resample
    def test_resample_pattern_offline_and_master_capture(self):
        page, errors = self.open_song()
        S = self.S
        evs = [{"beat": b, "pad": p, "bank": 0, "vel": 1, "pass": -1} for b, p in ((0, 0), (1, 1), (2, 2), (3, 3))]
        page.evaluate("([evs]) => { const s = window.choppad.song.session; s.store.dispatch({ type: 'pattern.load', pattern: { bars: 1, events: evs } }); s.store.dispatch({ type: 'tempo.set', bpm: 120 }); }", [evs])
        # 2 bars of a 1-bar pattern at 120 BPM = 4 s: it loops; offline, so no waiting
        r = page.evaluate(f"{S}.resample({{ bars: 2, source: 'pattern', pad: 9 }})")
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["duration"], 4.0, delta=0.001)
        self.assertEqual((r["bank"], r["pad"]), (0, 9))
        ov = page.evaluate(f"{S}.store.get().project.overrides['0:9']")
        self.assertEqual((ov["src"], ov["start"]), (r["url"], 0))
        self.assertAlmostEqual(ov["end"], 4.0, delta=0.001)
        wav = urllib.request.urlopen(self.base + r["url"]).read()
        self.assertEqual(wav[:4], b"RIFF")
        # the same pattern gives the same file (deterministic), the pad plays that file
        again = page.evaluate(f"{S}.resample({{ bars: 2, source: 'pattern', pad: 10 }})")
        self.assertEqual(again["url"], r["url"])
        res = page.evaluate(f"""async () => {{
            const s = {S}, T = window.__t, buf = s.engine.getPadBuffer(0, 9), sr = buf.sampleRate, x = buf.getChannelData(0);
            const half = Math.round(2 * sr);
            const ref = x.slice(0, Math.round(0.4 * sr));
            return {{ dur: buf.duration, second: T.lagOf(x, ref, half, Math.round(0.002 * sr), ref.length), peak: x.reduce((m, v) => Math.max(m, Math.abs(v)), 0) }};
        }}""")
        self.assertAlmostEqual(res["dur"], 4.0, delta=0.001)
        self.assertGreater(res["second"]["corr"], 0.97, "bar 2 is bar 1 again")
        self.assertEqual(res["second"]["lag"], 0)
        self.assertGreater(res["peak"], 0.05)
        # a resample plays at its own level (no re-normalisation) and the pad sounds when hit
        page.evaluate(f"{S}.hit(9)")
        # the resample is saved, shown by a second tab, and an agent can see it over HTTP
        page.evaluate(f"{S}.remote.flush()")
        saved = self.project_on_disk()["overrides"]["0:9"]
        self.assertEqual(saved["src"], r["url"])
        page2, errors2 = self.open_song()
        self.assertEqual(page2.evaluate(f"{S}.store.get().project.overrides['0:9'].src"), r["url"])
        self.assertEqual(page2.evaluate(f"{S}.engine.getPadBuffer(0, 9)?.duration > 3.9"), True)

        # exports: the SFZ points at the resample, the zip carries it whole
        st, out = api(self.base, f"/api/songs/{self.sid}/export", {"kind": "sfz", "bank": 0})
        self.assertEqual(st, 200, out)
        line = [l for l in open(out["path"]).read().splitlines() if "key=45 " in l][0]
        self.assertIn(f"sample=resample/{r['url'].rsplit('/', 1)[1]}", line)
        st, out = api(self.base, f"/api/songs/{self.sid}/export", {"kind": "zip", "bank": 0})
        self.assertEqual(st, 200, out)
        # a kit made from the bank keeps it; loading the kit on another bank puts the resample there
        kit = page.evaluate(f"{S}.saveBankAsKit('resample kit')")
        self.assertTrue(kit["ok"], kit)
        self.assertEqual(kit["kit"]["pads"][9]["src"], r["url"])
        page.evaluate(f"{S}.setBank(1)")
        page.evaluate(f"{S}.loadKit({json.dumps(kit['kit']['id'])})")
        self.assertEqual(page.evaluate(f"{S}.store.get().project.overrides['1:9'].src"), r["url"])
        self.assertEqual(page.evaluate(f"{S}.engine.getPadBuffer(1, 9)?.duration > 3.9"), True)
        # fitting to a tempo leaves a resample alone
        page.evaluate(f"{S}.store.dispatch({{ type: 'tempo.set', bpm: 90 }})")
        fit = page.evaluate(f"{S}.fitToTempo({{ scope: 'bank' }})")
        self.assertTrue(any(s["pad"] == 9 and "resample" in s["reason"] for s in fit["skipped"]), fit)
        self.assertEqual(page.evaluate(f"{S}.store.get().project.overrides['1:9'].stretch ?? null"), None)
        # an empty pattern, bad arguments
        bad = page.evaluate(f"{S}.resample({{ bars: 0, source: 'pattern', pad: 1 }})")
        self.assertFalse(bad["ok"])
        page.evaluate(f"{S}.store.dispatch({{ type: 'pattern.clear' }})")
        self.assertIn("empty", page.evaluate(f"{S}.resample({{ bars: 1, source: 'pattern', pad: 1 }})")["error"])
        self.assertEqual(errors + errors2, [], str(errors + errors2))

    def test_resample_master_bus_with_the_audio_worklet(self):
        page, errors = self.open_song()
        S = self.S
        evs = [{"beat": b, "pad": p, "bank": 0, "vel": 1, "pass": -1} for b, p in ((0, 0), (1, 1), (2, 2), (3, 3))]
        page.evaluate("([evs]) => { const s = window.choppad.song.session; s.store.dispatch({ type: 'pattern.load', pattern: { bars: 1, events: evs } }); s.store.dispatch({ type: 'tempo.set', bpm: 150 }); }", [evs])
        offline = page.evaluate(f"""async () => {{ const s = {S}; return Array.from(await s.renderPatternWav()); }}""")
        r = page.evaluate(f"{S}.resample({{ bars: 1, source: 'master', pad: 12 }})")   # 1.6 s, in real time
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["duration"], 1.6, delta=0.002)
        cmp = page.evaluate("""async ([off]) => {
            const s = window.choppad.song.session, T = window.__t;
            const o = await T.decode(off), live = s.engine.getPadBuffer(0, 12);
            const a = o.getChannelData(0), b = live.getChannelData(0), sr = live.sampleRate;
            const n = Math.round(1.0 * sr);
            // where the offline render appears in the live capture
            const q = T.lagOf(b, a.slice(0, n), 0, Math.round(0.06 * sr), n);
            let peak = 0; for (let i = 0; i < b.length; i++) peak = Math.max(peak, Math.abs(b[i]));
            return { lagMs: q.lag / sr * 1000, corr: q.corr, peak, dur: live.duration };
        }""", [offline])
        self.assertGreater(cmp["peak"], 0.05, "something was recorded")
        self.assertGreater(cmp["corr"], 0.8, cmp)
        self.assertLess(abs(cmp["lagMs"]), 40, cmp)
        self.assertFalse(page.evaluate(f"{S}.seq.state.playing"), "the pattern the capture started is stopped again")
        self.assertTrue(page.evaluate(f"{S}.seq.state.metronome"))
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ 6. song mode
    def test_song_mode_chain_plays_renders_and_syncs(self):
        page, errors = self.open_song()
        S = self.S
        # a project that is one pattern (an old project) becomes pattern 1; add pattern 2; chain p1 x2, p2 x1; 240 BPM = 1 s a bar
        page.evaluate("""() => {
            const d = (a) => window.choppad.song.session.store.dispatch(a), ev = (beat, pad) => ({ beat, pad, bank: 0, vel: 1, pass: -1 });
            d({ type: 'pattern.load', pattern: { bars: 1, events: [ev(0, 0), ev(2, 1)] } });
            d({ type: 'tempo.set', bpm: 240 });
            d({ type: 'song.addPattern', name: 'Chorus', bars: 1 });
            d({ type: 'pattern.addEvent', event: ev(1, 2) }); d({ type: 'pattern.addEvent', event: ev(3, 3) });
            d({ type: 'song.setChain', chain: [{ pattern: 'p1', repeats: 2 }, { pattern: 'p2', repeats: 1 }] });
            d({ type: 'settings.set', params: { metronome: false } });
        }""")
        song = page.evaluate(f"{S}.store.get().project.song")
        self.assertEqual([(p["id"], p["name"], len(p["events"])) for p in song["patterns"]], [("p1", "Pattern 1", 2), ("p2", "Chorus", 2)])
        self.assertEqual((song["current"], song["mode"]), ("p2", "pattern"))

        # whole-chain renders: WAV hits at the right place in the arrangement, MIDI with all six hits
        out = page.evaluate(f"""async () => {{
            const s = {S}, T = window.__t, sr = s.buffer.sampleRate;
            const buf = await T.decode(Array.from(await s.renderSongWav())); const sig = buf.getChannelData(0);
            const ref = (pad) => {{ const c = s.engine.getSlice(0, pad), a = Math.round(c.start * sr); return s.buffer.getChannelData(0).slice(a, a + Math.round(0.15 * sr)); }};
            const want = [[0, 0.0], [1, 0.5], [0, 1.0], [1, 1.5], [2, 2.25], [3, 2.75]];
            const hits = want.map(([pad, t]) => {{ const r = T.lagOf(sig, ref(pad), Math.round(t * buf.sampleRate), Math.round(0.03 * buf.sampleRate), Math.round(0.15 * buf.sampleRate));
                                                  return {{ pad, t, errMs: r.lag / buf.sampleRate * 1000, corr: r.corr }}; }});
            const m = s.songMidi(); let on = 0; for (let i = 0; i < m.length - 2; i++) if (m[i] === 0x99) on++;
            const single = s.patternMidi(); let on1 = 0; for (let i = 0; i < single.length - 2; i++) if (single[i] === 0x99) on1++;
            return {{ hits, on, on1, seconds: buf.duration }};
        }}""")
        for h in out["hits"]:
            self.assertLess(abs(h["errMs"]), 15, h)
            self.assertGreater(h["corr"], 0.5, h)
        self.assertEqual((out["on"], out["on1"]), (6, 2))
        self.assertGreater(out["seconds"], 3.0)

        # live: pattern mode loops the edited pattern, song mode plays the chain and loops it, switching exactly on the bar
        spy = f"""() => {{ const s = {S}; window.__w = []; const t = s.engine.trigger;
            s.engine.trigger = (p, v, when, ...r) => {{ window.__w.push([p, when]); return t(p, v, when, ...r); }}; }}"""
        page.evaluate(spy)
        page.evaluate(f"{S}.store.dispatch({{ type: 'song.setMode', mode: 'song' }})")
        self.assertTrue(page.evaluate(f"{S}.seq.state.song !== null"))
        page.evaluate(f"{S}.togglePattern()")
        page.wait_for_timeout(1250)
        where = page.evaluate(f"{S}.songPosition()")
        self.assertEqual(where["id"], "p1", where)
        page.wait_for_timeout(1500)
        self.assertEqual(page.evaluate(f"{S}.songPosition().id"), "p2")
        page.wait_for_timeout(1100)
        page.evaluate(f"{S}.panic()")
        w = page.evaluate("window.__w")
        t0 = w[0][1]
        rel = [(p, round(t - t0, 4)) for p, t in w]
        self.assertEqual([p for p, _ in rel[:8]], [0, 1, 0, 1, 2, 3, 0, 1], "pattern 1 twice, pattern 2, then the chain starts over")
        for (_, got), want in zip(rel[:8], [0, 0.5, 1.0, 1.5, 2.25, 2.75, 3.0, 3.5]):
            self.assertAlmostEqual(got, want, delta=0.002, msg=str(rel))
        # back to pattern mode: only the edited pattern (pattern 2) loops
        page.evaluate(f"{S}.store.dispatch({{ type: 'song.setMode', mode: 'pattern' }})")
        page.evaluate("window.__w = []")
        page.evaluate(f"{S}.togglePattern()")
        page.wait_for_timeout(1300)
        page.evaluate(f"{S}.panic()")
        self.assertEqual(sorted({p for p, _ in page.evaluate("window.__w")}), [2, 3])

        # saved, shown by a second tab, changed by an agent over HTTP
        page.evaluate(f"{S}.remote.flush()")
        saved = self.project_on_disk()["song"]
        self.assertEqual((saved["mode"], saved["current"], [c["repeats"] for c in saved["chain"]]), ("pattern", "p2", [2, 1]))
        page2, errors2 = self.open_song()
        self.assertEqual(page2.evaluate(f"{S}.store.get().project.song.chain.length"), 2)
        st, res = api(self.base, f"/api/songs/{self.sid}/actions", {"actions": [
            {"type": "song.setChain", "chain": [{"pattern": "p2", "repeats": 3}]}, {"type": "song.setMode", "mode": "song"}]})
        self.assertEqual((st, res["applied"]), (200, 2))
        page.wait_for_function(f"{S}.store.get().project.song.mode === 'song'", timeout=10000)
        self.assertEqual(page.evaluate(f"{S}.seq.state.song.total"), 12, "the sequencer follows: 3 bars of 4 beats")
        page2.wait_for_function(f"{S}.store.get().project.song.mode === 'song'", timeout=10000)
        self.assertEqual(page2.evaluate(f"{S}.store.get().project.song.chain"), [{"pattern": "p2", "repeats": 3}])
        # and a pattern edit in one tab shows in the other, inside the pattern the song holds
        page.evaluate(f"{S}.store.dispatch({{ type: 'pattern.addEvent', event: {{ beat: 0.5, pad: 9, bank: 0, vel: 1, pass: 5 }} }})")
        page2.wait_for_function(f"{S}.store.get().project.song.patterns[1].events.some((e) => e.pad === 9)", timeout=10000)
        self.assertEqual(errors + errors2, [], str(errors + errors2))

    # ------------------------------------------------------------------ 7. stems
    def test_stems_through_a_remote_backend_onto_pads(self):
        sid = self.short
        page, errors = self.open_song(sid)
        S = self.S
        self.assertFalse(page.evaluate(f"!!{S}.meta.stems"), "no stems yet")
        res = page.evaluate(f"{S}.separateStems()")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["stems"], ["drums", "bass", "vocals", "other"])
        self.assertEqual(page.evaluate(f"{S}.meta.stems"), ["drums", "bass", "vocals", "other"])
        for n in ("drums", "bass"):
            self.assertEqual(urllib.request.urlopen(f"{self.base}/media/{sid}/stems/{n}.wav").status, 200)
        again = page.evaluate(f"{S}.separateStems()")     # cached: the service is not asked a second time
        self.assertTrue(again["ok"])
        self.assertEqual(self.stems_service.requests, 1)
        self.assertEqual(api(self.base, f"/api/songs/{sid}")[1]["meta"]["stems"], ["drums", "bass", "vocals", "other"])

        # pad 3 plays its slice from the drums stem (half as loud as the mix), pad 4 is the same slice of the mix
        page.evaluate(f"""() => {{ const d = (a) => {S}.store.dispatch(a);
            d({{ type: 'slice.set', bank: 0, pad: 3, start: 10, end: 10.5 }}); d({{ type: 'slice.set', bank: 0, pad: 4, start: 10, end: 10.5 }}); }}""")
        ok = page.evaluate(f"{S}.setStem(0, 3, 'drums')")
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(page.evaluate(f"{S}.store.get().project.overrides['0:3']"), {"start": 10, "end": 10.5, "stem": "drums"})
        peaks = page.evaluate(f"""() => {{
            const s = {S}, sr = s.buffer.sampleRate, a = Math.round(10 * sr), b = Math.round(10.5 * sr);
            const peak = (buf) => {{ const x = buf.getChannelData(0); let m = 0; for (let i = a; i < b; i++) m = Math.max(m, Math.abs(x[i])); return m; }};
            return {{ mix: peak(s.buffer), stem: peak(s.engine.getPadBuffer(0, 3)), whole: s.engine.getPadBuffer(0, 3).duration > s.buffer.duration - 0.1,
                     none: s.engine.getPadBuffer(0, 4) }};
        }}""")
        self.assertAlmostEqual(peaks["stem"] / peaks["mix"], 0.5, delta=0.02, msg=str(peaks))
        self.assertTrue(peaks["whole"], "the stem buffer is the whole stem, played from the slice start")
        self.assertIsNone(peaks["none"])
        lvl = page.evaluate(f"""async () => {{
            const s = {S}, T = window.__t;
            const peak = async (pad) => {{
                s.store.dispatch({{ type: 'pattern.load', pattern: {{ bars: 1, events: [{{ beat: 0, pad, bank: 0, vel: 1, pass: -1 }}] }} }});
                const x = (await T.decode(Array.from(await s.renderPatternWav()))).getChannelData(0); let m = 0;
                for (let i = 0; i < x.length; i++) m = Math.max(m, Math.abs(x[i])); return m; }};
            return {{ stem: await peak(3), mix: await peak(4) }};
        }}""")
        self.assertLess(lvl["stem"], 0.65 * lvl["mix"], lvl)
        self.assertGreater(lvl["stem"], 0.3 * lvl["mix"], lvl)

        # fitting to a tempo stretches the stem's slice, not the mix's, and keeps the stem
        page.evaluate(f"{S}.store.dispatch({{ type: 'tempo.set', bpm: {page.evaluate(f'{S}.store.get().chops.bpm') * 0.8} }})")
        fit = page.evaluate(f"{S}.fitToTempo({{ scope: 'bank' }})")
        self.assertTrue(fit["ok"], fit)
        ov3 = page.evaluate(f"{S}.store.get().project.overrides['0:3']")
        ov4 = page.evaluate(f"{S}.store.get().project.overrides['0:4']")
        self.assertEqual(ov3["stem"], "drums")
        self.assertIn("fit", ov3["stretch"])
        self.assertNotEqual(ov3["stretch"]["url"], ov4["stretch"]["url"])
        amp = page.evaluate(f"""() => {{ const s = {S}, p = (b) => {{ const x = s.engine.getPadBuffer(0, b).getChannelData(0); let m = 0; for (const v of x) m = Math.max(m, Math.abs(v)); return m; }};
            return [p(3), p(4)]; }}""")
        self.assertAlmostEqual(amp[0] / amp[1], 0.5, delta=0.05, msg=str(amp))
        # taking the stem off puts the pad back on the mix and drops the stretch that was made from the stem
        page.evaluate(f"{S}.setStem(0, 3, null)")
        self.assertEqual(page.evaluate(f"{S}.store.get().project.overrides['0:3']"), {"start": 10, "end": 10.5})
        page.evaluate(f"{S}.setStem(0, 3, 'bass')")

        # saved, exported, shown by a second tab
        page.evaluate(f"{S}.remote.flush()")
        self.assertEqual(self.project_on_disk(sid)["overrides"]["0:3"]["stem"], "bass")
        st, out = api(self.base, f"/api/songs/{sid}/export", {"kind": "sfz", "bank": 0})
        self.assertEqual(st, 200, out)
        line = [l for l in open(out["path"]).read().splitlines() if "key=39 " in l][0]
        self.assertIn("sample=stems/bass.wav", line)
        page2, errors2 = self.open_song(sid)
        self.assertEqual(page2.evaluate(f"{S}.store.get().project.overrides['0:3'].stem"), "bass")
        self.assertEqual(page2.evaluate(f"{S}.engine.getPadBuffer(0, 3) !== null"), True)
        self.assertEqual(errors + errors2, [], str(errors + errors2))


if __name__ == "__main__":
    unittest.main()
