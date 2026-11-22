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

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
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
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = subprocess.Popen(
            [os.path.join(REPO, ".venv", "bin", "python"), os.path.join(REPO, "components", "integrate", "choppad.py"),
             "--serve", "--port", str(cls.port)],
            env={**os.environ, "CHOPPAD_REPO": cls.tmp}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        self.assertEqual(errors + errors2, [])


if __name__ == "__main__":
    unittest.main()
