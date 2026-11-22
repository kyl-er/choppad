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


if __name__ == "__main__":
    unittest.main()
