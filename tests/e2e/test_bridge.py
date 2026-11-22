"""Real-browser test: pad hits and pattern play/stop made in a tab reach an open DAW bridge (CHOP-11), with a FAKE bridge.

No DAW, MIDI port or audio device is started: the server runs with CHOPPAD_DAW_FAKE=<log>, which swaps Hydrogen and Ardour for
bridges that append every call (with a wall-clock timestamp) to that log. The page timestamps every keydown, so the delay
between the key and the bridge receiving the note can be measured. Run on a Python with Playwright:
    /usr/bin/python3 -W ignore -m unittest tests/e2e/test_bridge.py
Skips when Playwright or a library song is missing. The song is opened from a temporary copy; the real media/ is never touched.
"""
import json
import os
import shutil
import socket
import statistics
import subprocess
import tempfile
import time
import unittest
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MEDIA = os.path.join(REPO, "media")
HEAVY = {"audio.wav", "video.mp4"}  # linked, not copied
S = "window.choppad.song.session"
KEYS = ["KeyZ", "KeyX", "KeyC", "KeyV"]

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def library_song():
    for sid in sorted(os.listdir(MEDIA)) if os.path.isdir(MEDIA) else []:
        d = os.path.join(MEDIA, sid)
        if all(os.path.isfile(os.path.join(d, f)) for f in ("audio.wav", "video.mp4", "meta.json", "analysis.json", "chops.json")):
            return sid
    return None


def chrome_path():
    import glob
    for c in [os.environ.get("CHOPPAD_CHROME"), *sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")), reverse=True),
              "/usr/bin/chromium", "/usr/bin/google-chrome-stable"]:
        if c and os.path.isfile(c):
            return c
    return None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipIf(sync_playwright is None, "playwright is not installed for this Python")
@unittest.skipIf(library_song() is None, "no library song in media/")
class BrowserToBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.addClassCleanup(cls.pw.stop)
        args = ["--autoplay-policy=no-user-gesture-required"]
        try:
            cls.browser = cls.pw.chromium.launch(args=args)
        except Exception:
            cls.browser = cls.pw.chromium.launch(args=args, executable_path=chrome_path(), headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        """A fresh server (and so a fresh, closed fake bridge) for every test, on a temporary copy of the song."""
        self.sid = library_song()
        self.tmp = tempfile.mkdtemp(prefix="choppad-bridge-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        dst = os.path.join(self.tmp, "media", self.sid)
        os.makedirs(dst)
        src = os.path.join(MEDIA, self.sid)
        for f in os.listdir(src):
            if f in HEAVY:
                os.symlink(os.path.join(src, f), os.path.join(dst, f))
            elif f.endswith(".json") and f != "project.json":
                shutil.copy(os.path.join(src, f), dst)
        self.log = os.path.join(self.tmp, "fake-daw.jsonl")
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = {**os.environ, "CHOPPAD_REPO": self.tmp, "CHOPPAD_DAW_FAKE": self.log, "CHOPPAD_DAW_DIR": os.path.join(self.tmp, "daw-state")}
        self.server = subprocess.Popen(
            [os.path.join(REPO, ".venv", "bin", "python"), os.path.join(REPO, "components", "integrate", "choppad.py"),
             "--serve", "--port", str(self.port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.server.wait, 10)
        self.addCleanup(self.server.terminate)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/api/songs", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("server did not start")

    def post(self, what, body):
        req = urllib.request.Request(f"{self.base}/api/songs/{self.sid}/{what}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())

    def open_page(self):
        page = self.browser.new_page(viewport={"width": 1500, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/#/song/{self.sid}")
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        page.wait_for_function(f"{S}.remote.connected", timeout=10000)
        self.addCleanup(page.close)
        return page, errors

    def entries(self, op=None):
        try:
            with open(self.log) as f:
                rows = [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            return []
        return [r for r in rows if op is None or r["op"] == op]

    def wait_entries(self, op, n, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            rows = self.entries(op)
            if len(rows) >= n:
                return rows
            time.sleep(0.01)
        return self.entries(op)

    def test_keyboard_hits_reach_the_bridge_within_50ms_and_other_tabs_stay_quiet(self):
        page, errors = self.open_page()
        other, _ = self.open_page()
        # nothing is reported while no bridge is open
        page.keyboard.press("KeyZ")
        time.sleep(0.3)
        self.assertEqual(self.entries("note"), [])
        # open the (fake) bridge from "outside" (as an agent would); both tabs learn it from the stream
        r = self.post("daw", {"daw": "hydrogen", "command": "open"})
        self.assertTrue(r["ok"], r)
        for p in (page, other):
            p.wait_for_function(f"{S}.remote.daw.open === true", timeout=10000)
        self.assertEqual(page.evaluate(f"{S}.remote.daw"), {"open": True, "bank": 0})
        page.evaluate("""() => {
            window.__t0 = []; window.__pads = []; window.__other = 0;
            window.addEventListener('keydown', (e) => { if (!e.repeat) window.__t0.push(performance.timeOrigin + performance.now()); }, true);
            window.choppad.song.session.on('hit', (e) => window.__pads.push(e.detail.pad));
        }""")
        other.evaluate("""() => { window.__hits = 0; window.choppad.song.session.on('hit', () => { window.__hits++; }); }""")
        n = 24
        for i in range(n):
            page.keyboard.press(KEYS[i % len(KEYS)])
            page.wait_for_timeout(110)
        rows = self.wait_entries("note", n)
        starts = page.evaluate("window.__t0")
        pads = page.evaluate("window.__pads")
        self.assertEqual(len(rows), n, f"{len(rows)} of {n} hits arrived; page errors: {errors}")
        self.assertEqual([r["pad"] for r in rows], pads)
        delays = [r["t"] * 1000 - t0 for r, t0 in zip(rows, starts)]
        self.assertTrue(all(d > -5 for d in delays), delays)
        print(f"\nkey -> bridge delay over {n} hits: median {statistics.median(delays):.1f} ms, max {max(delays):.1f} ms")
        self.assertLess(statistics.median(delays), 50, f"median {statistics.median(delays):.1f} ms; all: {[round(d, 1) for d in delays]}")
        self.assertEqual(other.evaluate("window.__hits"), 0, "a local hit must not be played by the other tab")
        self.assertEqual(errors, [])

    def test_pattern_play_and_stop_pressed_in_the_browser_reach_the_bridge(self):
        page, _ = self.open_page()
        self.post("daw", {"daw": "hydrogen", "command": "open"})
        page.wait_for_function(f"{S}.remote.daw.open === true", timeout=10000)
        page.locator(".seq-play").click()
        page.wait_for_function(f"{S}.seq.state.playing === true", timeout=5000)
        self.assertEqual([r["command"] for r in self.wait_entries("transport", 1)][:1], ["play"])
        page.locator(".seq-play").click()
        page.wait_for_function(f"{S}.seq.state.playing === false", timeout=5000)
        self.assertEqual([r["command"] for r in self.wait_entries("transport", 2)], ["play", "stop"])
        # an agent's play command executed in this tab is mirrored once by the server, not reported again by the tab
        before = len(self.entries("transport"))
        self.post("transport", {"command": "play-pattern"})
        page.wait_for_function(f"{S}.seq.state.playing === true", timeout=5000)
        time.sleep(0.5)
        self.assertEqual(len(self.entries("transport")), before + 1)
        self.post("transport", {"command": "stop"})

    def test_daw_state_is_on_the_song_document_and_follows_the_bridge(self):
        get = lambda: json.loads(urllib.request.urlopen(f"{self.base}/api/songs/{self.sid}").read())["daw"]  # noqa: E731
        self.assertEqual(get(), {"open": False, "daw": None, "bank": None})
        page, _ = self.open_page()
        self.assertEqual(page.evaluate(f"{S}.remote.daw"), {"open": False, "bank": None})
        self.post("daw", {"daw": "hydrogen", "command": "open"})
        self.assertEqual(get(), {"open": True, "daw": "hydrogen", "bank": 0})
        page.wait_for_function(f"{S}.remote.daw.open === true", timeout=10000)
        # a tab opened after the bridge learns it from the first message of its stream
        late, _ = self.open_page()
        late.wait_for_function(f"{S}.remote.daw.open === true", timeout=10000)


if __name__ == "__main__":
    unittest.main()
