"""Real-browser tests for the W3 sampler core: transient chop, time-stretch, kits across songs.

Run with a Python that has Playwright installed (like test_smoke.py):
    python3 -m unittest tests/e2e/test_core.py
Skips when Playwright or library songs are missing. Songs are opened from a temporary copy of their media
folders (audio/video linked, json copied), so the real library is never written to.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_smoke as ts  # noqa: E402  (helpers only; its TestCase is not re-exported here)

REPO, MEDIA = ts.REPO, ts.MEDIA
sync_playwright = ts.sync_playwright


def library_songs(n=2):
    out = []
    for sid in sorted(os.listdir(MEDIA)) if os.path.isdir(MEDIA) else []:
        d = os.path.join(MEDIA, sid)
        if all(os.path.isfile(os.path.join(d, f)) for f in ("audio.wav", "video.mp4", "meta.json", "analysis.json", "chops.json")):
            out.append(sid)
    return out[:n]


def api(base, path, body=None, method=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method=method or ("POST" if body is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@unittest.skipIf(sync_playwright is None, "playwright is not installed for this Python")
@unittest.skipIf(len(library_songs()) < 2, "need two library songs in media/")
class CoreScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sids = library_songs()
        cls.sid, cls.sid2 = cls.sids
        cls.tmp = tempfile.mkdtemp(prefix="choppad-core-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.pristine = {}
        for sid in cls.sids:
            dst = os.path.join(cls.tmp, "media", sid)
            os.makedirs(dst)
            src = os.path.join(MEDIA, sid)
            for f in os.listdir(src):
                if f in ts.HEAVY:
                    os.symlink(os.path.join(src, f), os.path.join(dst, f))
                elif f.endswith(".json") and f not in ("project.json", "onsets.json"):
                    shutil.copy(os.path.join(src, f), dst)
            cls.pristine[sid] = open(os.path.join(dst, "chops.json")).read()
        cls.port = ts.free_port()
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
            cls.browser = cls.pw.chromium.launch(args=args, executable_path=ts.chrome_path(), headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):  # every test starts from an empty project and the original chop table
        for sid in self.sids:
            d = os.path.join(self.tmp, "media", sid)
            for f in ("project.json", "onsets.json"):
                if os.path.exists(os.path.join(d, f)):
                    os.remove(os.path.join(d, f))
            open(os.path.join(d, "chops.json"), "w").write(self.pristine[sid])
        shutil.rmtree(os.path.join(self.tmp, "media", "_kits"), ignore_errors=True)
        for sid in self.sids:
            shutil.rmtree(os.path.join(self.tmp, "media", sid, "stretch"), ignore_errors=True)

    def open_song(self, sid=None):
        page = self.browser.new_page(viewport={"width": 1500, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
        page.on("response", lambda r: errors.append(f"404 {r.url}") if r.status == 404 and not r.url.endswith("/favicon.ico") else None)
        page.goto(f"{self.base}/#/song/{sid or self.sid}")
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        return page, errors

    S = "window.choppad.song.session"

    # ------------------------------------------------------------------ 1. transient auto-chop
    def test_transient_chop(self):
        page, errors = self.open_song()
        S = self.S
        before = page.evaluate(f"{S}.store.get().chops")
        self.assertNotEqual(before.get("mode"), "transient")

        page.locator(".mode-transient").click()
        page.wait_for_function(f"{S}.store.get().chops.mode === 'transient'", timeout=60000)
        ch = page.evaluate(f"{S}.store.get().chops")
        self.assertNotEqual(len(ch["banks"]), len(before["banks"]), "transient mode should change the bank count")
        self.assertTrue(all(len(b) == 16 for b in ch["banks"][:-1]))
        self.assertEqual(ch["chops"][0]["end"], ch["chops"][1]["start"])
        self.assertIn("on", page.locator(".mode-transient").get_attribute("class"))

        # pad starts line up with audible onsets: more energy right after a slice start than just before it, far more
        # often than at the middle of the same slices (a control with no reason to be an onset)
        res = page.evaluate(f"""() => {{
            const buf = {S}.buffer, sr = buf.sampleRate, L = buf.getChannelData(0);
            const rms = (a, b) => {{ a = Math.max(0, Math.round(a * sr)); b = Math.round(b * sr); let s = 0; for (let i = a; i < b; i++) s += L[i] * L[i]; return Math.sqrt(s / Math.max(1, b - a)) + 1e-9; }};
            const cs = {S}.store.get().chops.chops.slice(0, 96);
            const frac = (ts) => ts.filter((t) => rms(t, t + 0.03) > 1.15 * rms(t - 0.03, t - 0.002)).length / ts.length;
            return {{ starts: frac(cs.map((c) => c.start)), mids: frac(cs.map((c) => (c.start + c.end) / 2)) }};
        }}""")
        self.assertGreaterEqual(res["starts"], 0.3, res)
        self.assertGreaterEqual(res["starts"], 1.5 * res["mids"], res)

        # a pad plays its slice and the master bus shows signal
        page.keyboard.press("KeyZ")
        peak = page.evaluate("""async () => {
            const a = window.choppad.audio.analyser, buf = new Float32Array(a.fftSize);
            let peak = 0;
            for (let i = 0; i < 20; i++) { a.getFloatTimeDomainData(buf); for (const v of buf) peak = Math.max(peak, Math.abs(v)); await new Promise((r) => setTimeout(r, 15)); }
            return peak;
        }""")
        self.assertGreater(peak, 0.001)

        # the sensitivity slider moves the slice count the right way
        def count_at(v):
            page.evaluate("""(v) => { const el = document.querySelector('.sens'); el.value = v; el.dispatchEvent(new Event('change')); }""", v)
            page.wait_for_function(f"Math.abs({S}.store.get().chops.sensitivity - {v}) < 1e-6", timeout=60000)
            return page.evaluate(f"{S}.store.get().chops.chops.length")
        low, high = count_at(0.2), count_at(0.9)
        self.assertGreater(high, low)

        # saved on the server, survives a reload
        page.close()
        page, errors2 = self.open_song()
        self.assertEqual(page.evaluate(f"{S}.store.get().chops.mode"), "transient")
        self.assertAlmostEqual(page.evaluate(f"{S}.store.get().chops.sensitivity"), 0.9, places=3)

        # back to the grid
        page.locator(".mode-grid").click()
        page.wait_for_function(f"{S}.store.get().chops.mode === 'grid'", timeout=30000)
        self.assertEqual(page.evaluate(f"{S}.store.get().chops.grid"), before["grid"])
        self.assertEqual(errors + errors2, [])
        page.close()

    # ------------------------------------------------------------------ 2. time-stretch
    RENDER_JS = """async () => {
        const s = window.choppad.song.session;
        const bytes = new Uint8Array(await s.renderPatternWav());
        const dv = new DataView(bytes.buffer);
        const ch = dv.getUint16(22, true), sr = dv.getUint32(24, true), n = (bytes.length - 44) / 2 / ch;
        const x = new Float32Array(n);
        for (let i = 0; i < n; i++) x[i] = dv.getInt16(44 + i * 2 * ch, true) / 32768;
        let peak = 0; for (const v of x) peak = Math.max(peak, Math.abs(v));
        let last = 0; for (let i = n - 1; i >= 0; i--) if (Math.abs(x[i]) > peak * 0.002) { last = i; break; }
        const head = Array.from(x.slice(0, Math.floor(0.25 * sr)));
        return { sr, peak, length: last / sr, head };
    }"""

    def render_pad0(self, page):
        return page.evaluate(self.RENDER_JS)

    def test_stretch_pad(self):
        page, errors = self.open_song()
        S = self.S
        page.evaluate(f"{S}.store.dispatch({{ type: 'pattern.toggleStep', pad: 0, step: 0, bank: 0 }})")
        sl = page.evaluate(f"{S}.engine.getSlice(0, 0)")
        dur = sl["end"] - sl["start"]
        base = self.render_pad0(page)
        self.assertGreater(base["peak"], 0.01)
        self.assertAlmostEqual(base["length"], dur, delta=0.012)

        # fit the pad to a faster tempo from the Time-stretch panel
        bpm = page.evaluate(f"{S}.store.get().chops.bpm")
        target = bpm * 1.25
        ratio = bpm / target
        page.locator(".st-bpm").fill(str(target))
        page.locator(".st-pad").click()
        page.wait_for_function(f"{S}.store.get().project.overrides['0:0']?.stretch && {S}.engine.getPadBuffer(0, 0)", timeout=60000)
        ov = page.evaluate(f"{S}.store.get().project.overrides['0:0']")
        self.assertAlmostEqual(ov["stretch"]["ratio"], ratio, places=3)
        self.assertEqual((ov["start"], ov["end"]), (sl["start"], sl["end"]))
        self.assertIn("stretched", page.locator('.pad[data-pad="0"]').get_attribute("class"))
        self.assertAlmostEqual(page.evaluate(f"{S}.engine.getPadBuffer(0, 0).duration"), dur * ratio, delta=0.004)

        # the offline WAV render uses it: its length follows the ratio and the audio is the stretched audio
        r = self.render_pad0(page)
        self.assertAlmostEqual(r["length"], dur * ratio, delta=0.012)
        self.assertLess(abs(r["length"] - dur * ratio), abs(r["length"] - dur))
        # (the master limiter delays the render by a few ms, so look for the best alignment)
        corr = """([head, alt]) => {
            const n = 8000, from = 600;
            let bestC = -1;
            for (let lag = 0; lag < 700; lag++) {
                let dot = 0, na = 0, nb = 0;
                for (let i = from; i < from + n; i++) { const x = head[i + lag] ?? 0, y = alt[i]; dot += x * y; na += x * x; nb += y * y; }
                bestC = Math.max(bestC, dot / Math.sqrt(na * nb + 1e-12));
            }
            return bestC;
        }"""
        alt = page.evaluate(f"Array.from({S}.engine.getPadBuffer(0, 0).getChannelData(0).slice(0, 20000))")
        best = page.evaluate(corr, [r["head"], alt])
        self.assertGreater(best, 0.95, "render does not play the stretched buffer")

        # pitch works on top: +12 semitones halves the rendered length again
        page.evaluate(f"{S}.store.dispatch({{ type: 'pad.set', pad: 0, params: {{ pitch: 12 }} }})")
        self.assertAlmostEqual(self.render_pad0(page)["length"], dur * ratio / 2, delta=0.012)
        page.evaluate(f"{S}.store.dispatch({{ type: 'pad.set', pad: 0, params: {{ pitch: 0 }} }})")

        # a hit on the pad plays the stretched sound live
        page.keyboard.press("KeyZ")
        peak = page.evaluate("""async () => {
            const a = window.choppad.audio.analyser, buf = new Float32Array(a.fftSize);
            let peak = 0;
            for (let i = 0; i < 20; i++) { a.getFloatTimeDomainData(buf); for (const v of buf) peak = Math.max(peak, Math.abs(v)); await new Promise((r) => setTimeout(r, 15)); }
            return peak;
        }""")
        self.assertGreater(peak, 0.001)

        # exported with the stretched sound (SFZ points at the rendering)
        code, out = api(self.base, f"/api/songs/{self.sid}/export", {"kind": "sfz", "bank": 0, "overrides": ov and {"0:0": ov}})
        self.assertEqual(code, 200, out)
        sfz = open(out["path"]).read()
        self.assertIn("sample=stretch/" + ov["stretch"]["url"].rsplit("/", 1)[1], sfz)

        # saved, and still there (buffer loaded again) after a reload
        page.wait_for_timeout(700)
        saved = json.load(open(os.path.join(self.tmp, "media", self.sid, "project.json")))
        self.assertEqual(saved["overrides"]["0:0"]["stretch"]["url"], ov["stretch"]["url"])
        page.close()
        page, errors2 = self.open_song()
        self.assertAlmostEqual(page.evaluate(f"{S}.engine.getPadBuffer(0, 0).duration"), dur * ratio, delta=0.004)
        self.assertAlmostEqual(self.render_pad0(page)["length"], dur * ratio, delta=0.012)

        # reset pad goes back to the song region
        page.locator(".st-clear").click()
        page.wait_for_function(f"!{S}.store.get().project.overrides['0:0']?.stretch && !{S}.engine.getPadBuffer(0, 0)")
        back = self.render_pad0(page)
        self.assertAlmostEqual(back["length"], dur, delta=0.012)
        self.assertLess(page.evaluate(corr, [back["head"], alt]), 0.8, "control: the plain slice must not match the stretched audio")
        self.assertEqual(errors + errors2, [])
        page.close()

    # ------------------------------------------------------------------ 3. kits across songs
    def test_kit_saved_on_song_a_plays_on_song_b(self):
        S = self.S
        # song A: give pad 1 a sound of its own, save the bank as a kit from the Kit panel
        page, errors = self.open_song(self.sid)
        a_slice = page.evaluate(f"{S}.engine.getSlice(0, 0)")
        page.evaluate(f"{S}.store.dispatch({{ type: 'pad.set', pad: 0, params: {{ pitch: 0, gainDb: -2 }} }})")
        page.locator(".kit-name").fill("e2e kit")
        page.locator(".kit-save").click()
        page.wait_for_function("document.querySelector('.kit-out').textContent.includes('saved kit')", timeout=20000)
        kit = json.load(open(os.path.join(self.tmp, "media", "_kits", "e2e-kit", "kit.json")))
        self.assertEqual(kit["schema"], "choppad.kit/1")
        self.assertEqual(kit["pads"][0]["song_id"], self.sid)
        self.assertEqual(kit["pads"][0]["params"]["gainDb"], -2)
        self.assertEqual(len([p for p in kit["pads"] if p]), len(page.evaluate(f"{S}.store.get().chops.banks[0]")))
        page.wait_for_selector('.kits li.kit[data-id="e2e-kit"]')
        a_audio_dur = page.evaluate(f"{S}.buffer.duration")
        page.close()

        # song B: pick the kit in the library sidebar; its pads now come from song A
        page, errors2 = self.open_song(self.sid2)
        page.wait_for_selector('.kits li.kit[data-id="e2e-kit"]')
        b_slice = page.evaluate(f"{S}.engine.getSlice(0, 0)")
        page.locator('.kits li.kit[data-id="e2e-kit"] .title').click()
        page.wait_for_function("document.querySelector('.kits li.kit .meta').textContent.includes('loaded on bank')", timeout=60000)
        ov = page.evaluate(f"{S}.store.get().project.overrides['0:0']")
        self.assertEqual((ov["song"], ov["start"], ov["end"]), (self.sid, a_slice["start"], a_slice["end"]))
        self.assertNotEqual((a_slice["start"], a_slice["end"]), (b_slice["start"], b_slice["end"]))
        self.assertAlmostEqual(page.evaluate(f"{S}.engine.getPadBuffer(0, 0).duration"), a_audio_dur, delta=0.01)
        self.assertEqual(page.evaluate(f"{S}.store.get().project.pads[0].gainDb"), -2)
        self.assertIn("foreign", page.locator('.pad[data-pad="0"]').get_attribute("class"))

        # the pad produces signal ...
        page.keyboard.press("KeyZ")
        peak = page.evaluate("""async () => {
            const a = window.choppad.audio.analyser, buf = new Float32Array(a.fftSize);
            let peak = 0;
            for (let i = 0; i < 20; i++) { a.getFloatTimeDomainData(buf); for (const v of buf) peak = Math.max(peak, Math.abs(v)); await new Promise((r) => setTimeout(r, 15)); }
            return peak;
        }""")
        self.assertGreater(peak, 0.001, "the kit pad made no sound")

        # ... and it is song A's audio, not song B's: compare the offline render with both sources
        page.evaluate(f"{S}.store.dispatch({{ type: 'pattern.toggleStep', pad: 0, step: 0, bank: 0 }})")
        r = self.render_pad0(page)
        self.assertGreater(r["peak"], 0.01)
        both = page.evaluate("""async ([head, sidA, sidB, a, b]) => {
            const ctx = window.choppad.audio.ctx;
            const take = async (sid, start) => {
                const buf = await fetch(`/media/${sid}/audio.wav`).then((r) => r.arrayBuffer()).then((x) => ctx.decodeAudioData(x));
                return Array.from(buf.getChannelData(0).slice(Math.round(start * buf.sampleRate), Math.round(start * buf.sampleRate) + 20000));
            };
            const corr = (alt) => {
                const n = 8000, from = 600; let best = -1;
                for (let lag = 0; lag < 700; lag++) {
                    let dot = 0, na = 0, nb = 0;
                    for (let i = from; i < from + n; i++) { const x = head[i + lag] ?? 0, y = alt[i]; dot += x * y; na += x * x; nb += y * y; }
                    best = Math.max(best, dot / Math.sqrt(na * nb + 1e-12));
                }
                return best;
            };
            return { fromA: corr(await take(sidA, a)), fromB: corr(await take(sidB, b)) };
        }""", [r["head"], self.sid, self.sid2, a_slice["start"], b_slice["start"]])
        self.assertGreater(both["fromA"], 0.95, both)
        self.assertLess(both["fromB"], 0.8, both)

        # exports work for the mixed bank (SFZ here; the Hydrogen writer is covered by the API tests)
        code, out = api(self.base, f"/api/songs/{self.sid2}/export", {"kind": "sfz", "bank": 0, "overrides": {"0:0": ov}})
        self.assertEqual(code, 200, out)
        sfz = open(out["path"]).read()
        self.assertIn(f"sample=../{self.sid}/audio.wav", sfz)
        code, out = api(self.base, "/api/kits/e2e-kit/export", {"kind": "sfz"})
        self.assertEqual(code, 200, out)

        # saved with the song, rebound after a reload (the other song's audio is fetched again, once)
        page.wait_for_timeout(700)
        saved = json.load(open(os.path.join(self.tmp, "media", self.sid2, "project.json")))
        self.assertEqual(saved["overrides"]["0:0"]["song"], self.sid)
        page.close()
        page, errors3 = self.open_song(self.sid2)
        self.assertAlmostEqual(page.evaluate(f"{S}.engine.getPadBuffer(0, 0).duration"), a_audio_dur, delta=0.01)
        n_audio_fetches = page.evaluate(f"performance.getEntriesByType('resource').filter((e) => e.name.endsWith('/media/{self.sid}/audio.wav')).length")
        self.assertEqual(n_audio_fetches, 1)
        self.assertEqual(errors + errors2 + errors3, [])
        page.close()

    # ------------------------------------------------------------------ 4. key detection and zip export
    def test_key_in_lcd_and_library_and_zip_export(self):
        import io
        import zipfile
        apath = os.path.join(self.tmp, "media", self.sid, "analysis.json")
        doc = json.load(open(apath))
        doc.pop("key", None)                       # a song analysed before key detection existed
        json.dump(doc, open(apath, "w"))
        page, errors = self.open_song()
        page.wait_for_function("document.querySelector('.lcd .key')?.textContent.length > 2", timeout=30000)
        key = json.load(open(apath))["key"]       # detected on the server, cached in analysis.json
        want = f"{key['tonic']} {'min' if key['mode'] == 'minor' else 'maj'}"
        self.assertEqual(page.locator(".lcd .key").inner_text(), want)
        self.assertIn("confidence", page.locator(".lcd .key").get_attribute("title"))
        page.wait_for_function(f"document.querySelector('.songs li[data-id=\"{self.sid}\"] .key')?.textContent === '{want}'", timeout=10000)

        # the chops zip: 16 WAVs of the current bank, downloadable from the link
        page.locator(".exp-zip").click()
        page.wait_for_selector(".exp-out a.exp-download", timeout=60000)
        href = page.locator(".exp-out a.exp-download").get_attribute("href")
        resp = page.request.get(self.base + href)
        self.assertEqual(resp.status, 200)
        z = zipfile.ZipFile(io.BytesIO(resp.body()))
        self.assertEqual(z.namelist(), [f"pad{i:02d}.wav" for i in range(1, 17)])
        self.assertTrue(all(i.file_size > 1000 for i in z.infolist()))
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
