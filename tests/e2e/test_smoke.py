"""Real-browser smoke test: the song screen on a real library song, in headless Chromium.

Run with a Python that has Playwright installed (the repo venv does not carry it):
    python3 -m unittest tests/e2e/test_smoke.py
Skips when Playwright or a library song is missing. The song is opened from a temporary copy of
its media folder, so the real project.json and chops.json are never touched.
"""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MEDIA = os.path.join(REPO, "media")
HEAVY = {"audio.wav", "video.mp4"}  # linked, not copied

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def library_song():
    """First library song that has everything the song screen needs."""
    for sid in sorted(os.listdir(MEDIA)) if os.path.isdir(MEDIA) else []:
        d = os.path.join(MEDIA, sid)
        if all(os.path.isfile(os.path.join(d, f)) for f in ("audio.wav", "video.mp4", "meta.json", "analysis.json", "chops.json")):
            return sid
    return None


def chrome_path():
    """A Chrome to drive when Playwright's own download does not match this Playwright version."""
    import glob
    for c in [os.environ.get("CHOPPAD_CHROME"), *sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")), reverse=True),
              "/usr/bin/chromium", "/usr/bin/google-chrome-stable"]:
        if c and os.path.isfile(c):
            return c
    return None


def lcd_page(page, name, sub=1):
    """Press the page key (again, to cycle sub-pages) until the LCD shows page `name`, sub-page `sub` (1-based)."""
    key = page.locator(f'.mode[data-mode="{name}"]')
    for _ in range(8):
        on = key.get_attribute("aria-pressed") == "true"
        if on and page.locator(".lcd-sub").inner_text().startswith(f"{sub}/"):
            return
        key.click()
    raise AssertionError(f"LCD page {name} {sub} not reachable")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipIf(sync_playwright is None, "playwright is not installed for this Python")
@unittest.skipIf(library_song() is None, "no library song in media/")
class SongScreenSmoke(unittest.TestCase):
    OPTIONAL_404 = ("/favicon.ico",)  # files the app does not ship

    @classmethod
    def setUpClass(cls):
        cls.sid = library_song()
        cls.tmp = tempfile.mkdtemp(prefix="choppad-smoke-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        dst = os.path.join(cls.tmp, "media", cls.sid)
        os.makedirs(dst)
        src = os.path.join(MEDIA, cls.sid)
        for f in os.listdir(src):
            if f in HEAVY:
                os.symlink(os.path.join(src, f), os.path.join(dst, f))
            elif f.endswith(".json") and f != "project.json":  # start from a clean project
                shutil.copy(os.path.join(src, f), dst)
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

    def setUp(self):  # every test starts from an empty project
        path = os.path.join(self.tmp, "media", self.sid, "project.json")
        if os.path.exists(path):
            os.remove(path)

    def open_song(self):
        page = self.browser.new_page(viewport={"width": 1500, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url} {(r.request.post_data or \'\')[:400]}") if r.status >= 400 and not r.url.endswith(self.OPTIONAL_404) else None)  # every failed request, with its body, so a flake names itself
        page.goto(f"{self.base}/#/song/{self.sid}")
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        return page, errors

    @staticmethod
    def state(page):
        return page.evaluate("""() => {
            const { store } = window.choppad.song.session;
            const s = store.get();
            return { bank: s.bank, selected: s.selected, grid: s.chops.grid, banks: s.chops.banks.length,
                     pad0: s.project.pads[0], events: s.project.pattern.events, fx: s.project.fx,
                     overrides: s.project.overrides, view: window.choppad.song.state };
        }""")

    def test_play_edit_reload(self):
        page, errors = self.open_song()
        self.assertEqual(page.locator(".pad").count(), 16)
        self.assertEqual(page.evaluate("window.choppad.audio.ctx.state"), "running")

        # a pad hit from the keyboard sounds, selects the pad and lights it
        page.keyboard.press("KeyX")
        page.wait_for_function("window.choppad.song.state.lastChop !== null")
        st = self.state(page)
        self.assertEqual(st["selected"], 1)
        self.assertIn("selected", page.locator('.pad[data-pad="1"]').get_attribute("class"))
        self.assertGreater(page.evaluate("""async () => {
            const a = window.choppad.audio.analyser, buf = new Float32Array(a.fftSize);
            let peak = 0;
            for (let i = 0; i < 20; i++) {
                a.getFloatTimeDomainData(buf);
                for (const v of buf) peak = Math.max(peak, Math.abs(v));
                await new Promise((r) => setTimeout(r, 15));
            }
            return peak;
        }"""), 0.001, "the pad hit produced no signal on the master bus")

        # bank navigation
        label = page.locator(".bank-label").inner_text()
        page.keyboard.press("BracketRight")
        page.wait_for_function("window.choppad.song.session.store.get().bank === 1")
        self.assertNotEqual(page.locator(".bank-label").inner_text(), label)
        page.keyboard.press("BracketLeft")
        page.wait_for_function("window.choppad.song.session.store.get().bank === 0")

        # pad sound, step grid and fx edits go through the store
        page.keyboard.press("KeyZ")
        page.wait_for_function("window.choppad.song.session.store.get().selected === 0")
        lcd_page(page, "PAD")          # W4: the pad inspector is the PAD page of the LCD
        page.locator('.inspector input[name="pitch"]').fill("5")
        page.locator('.stepgrid i[data-pad="0"][data-step="4"]').click()
        lcd_page(page, "FX")           # W4: presets are on the FX page
        page.locator('.fx-presets button[data-preset="warm"]').click()
        st = self.state(page)
        self.assertEqual(st["pad0"]["pitch"], 5)
        self.assertEqual([(e["pad"], e["beat"], e["pass"]) for e in st["events"]], [(0, 1, -1)])
        self.assertAlmostEqual(st["fx"]["reverb"], 0.18)
        self.assertIn("on", page.locator('.stepgrid i[data-pad="0"][data-step="4"]').get_attribute("class"))
        lcd_page(page, "PAD")
        self.assertIn("+5 st", page.locator('.inspector [data-out="pitch"]').inner_text())

        # regrid replaces the chop table
        banks_before = st["banks"]
        lcd_page(page, "GRID")         # W4: grid / meter are on the GRID page
        page.locator(".grid-btns button.grid-1-8").click()
        page.wait_for_function("window.choppad.song.session.store.get().chops.grid === '1/8'")
        self.assertGreater(self.state(page)["banks"], banks_before)
        page.locator(".grid-btns button.grid-1-4").click()
        page.wait_for_function("window.choppad.song.session.store.get().chops.grid === '1/4'")

        # the edits were saved and come back after a reload
        page.wait_for_timeout(700)
        saved = json.load(open(os.path.join(self.tmp, "media", self.sid, "project.json")))
        self.assertEqual(saved["pads"][0]["pitch"], 5)
        page.close()
        page, errors2 = self.open_song()
        st = self.state(page)
        self.assertEqual(st["pad0"]["pitch"], 5)
        self.assertEqual(len(st["events"]), 1)
        self.assertAlmostEqual(st["fx"]["reverb"], 0.18)
        lcd_page(page, "PAD")
        self.assertEqual(page.locator('.inspector input[name="pitch"]').input_value(), "5")
        self.assertEqual(errors + errors2, [])
        page.close()


    def test_transport_record_and_render(self):
        page, errors = self.open_song()
        sess = "window.choppad.song.session"
        page.evaluate(f"() => {{ window.__flashes = 0; {sess}.on('flash', () => window.__flashes++); }}")

        # a drawn step plays back in the loop and lights its pad
        page.locator('.stepgrid i[data-pad="2"][data-step="0"]').click()
        page.keyboard.press("KeyP")
        page.wait_for_function(f"{sess}.seq.state.playing === true")
        page.wait_for_function("window.__flashes >= 1", timeout=8000)
        self.assertIn("■", page.locator(".seq-play").inner_text())

        # recording a live hit adds an event; undo removes that take and keeps the drawn step
        page.keyboard.press("KeyB")
        page.wait_for_function(f"{sess}.seq.state.recording === true")
        page.keyboard.press("KeyX")
        page.wait_for_function(f"{sess}.store.get().project.pattern.events.length === 2")
        page.keyboard.press("Space")  # panic stops everything
        page.wait_for_function(f"{sess}.seq.state.playing === false")
        self.assertIn("▶", page.locator(".seq-play").inner_text())
        page.locator(".seq-undo").click()
        events = page.evaluate(f"{sess}.store.get().project.pattern.events")
        self.assertEqual([(e["pad"], e["pass"]) for e in events], [(2, -1)])

        # the pattern renders offline to a WAV and to a MIDI file
        wav = page.evaluate(f"async () => {{ const b = await {sess}.renderPatternWav(); return [b.byteLength, String.fromCharCode(...new Uint8Array(b.buffer ?? b).slice(0, 4))]; }}")
        self.assertGreater(wav[0], 44100)
        self.assertEqual(wav[1], "RIFF")
        self.assertEqual(page.evaluate(f"String.fromCharCode(...{sess}.patternMidi().slice(0, 4))"), "MThd")

        # song transport: Enter plays the original from the bank start, the clock moves, Enter pauses
        page.keyboard.press("Enter")
        page.wait_for_function(f"{sess}.songPlaying === true")
        t0 = page.evaluate(f"{sess}.position()")
        page.wait_for_function(f"{sess}.position() > {t0} + 0.3")
        self.assertIn("Pause", page.locator(".play").inner_text())
        page.keyboard.press("Enter")
        page.wait_for_function(f"{sess}.songPlaying === false")

        # sections arrive from the server and are drawn
        page.wait_for_function(f"{sess}.store.get().sections.length > 0", timeout=20000)
        self.assertGreater(page.locator(".section-seg").count(), 0)
        if os.environ.get("CHOPPAD_SHOT"):  # optional screenshot for a human to look at
            page.screenshot(path=os.environ["CHOPPAD_SHOT"])
        self.assertEqual(errors, [])
        page.close()


    def api(self, path, body, origin="agent"):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-ChopPad-Origin": origin})
        return json.loads(urllib.request.urlopen(req, timeout=5).read())

    def test_agent_control_shows_live_without_reload(self):
        page, errors = self.open_song()
        sess = "window.choppad.song.session"
        page.wait_for_function(f"{sess}.remote.connected === true", timeout=5000)
        page.evaluate("window.__noReload = 'same page'")
        other, errors2 = self.open_song()  # a second tab on the same song
        other.wait_for_function(f"{sess}.remote.connected === true", timeout=5000)
        page.evaluate(f"""() => {{
            window.__peak = 0; window.__hits = 0;
            {sess}.on('hit', () => window.__hits++);
            const a = window.choppad.audio.analyser, buf = new Float32Array(a.fftSize);
            setInterval(() => {{ a.getFloatTimeDomainData(buf); for (const v of buf) window.__peak = Math.max(window.__peak, Math.abs(v)); }}, 4);
            window.__t = null;
            {sess}.store.subscribe((s, p, action, meta) => {{ if (meta.remote && window.__t === null) window.__t = Date.now(); }});
        }}""")

        # an agent's batch of actions, sent from Python: the open page shows them, nothing reloads
        t0 = time.time() * 1000
        out = self.api(f"/api/songs/{self.sid}/actions", {"actions": [
            {"type": "pad.set", "pad": 0, "params": {"pitch": 7, "gainDb": -4}},
            {"type": "pattern.toggleStep", "pad": 0, "step": 4, "bank": 0},
            {"type": "fx.set", "params": {"reverb": 0.33}},
        ]})
        self.assertEqual(out["applied"], 3)
        self.assertGreaterEqual(out["listeners"], 2)
        page.wait_for_function(f"{sess}.store.get().project.fx.reverb === 0.33", timeout=2000)
        page.wait_for_function("document.querySelector('.stepgrid i[data-pad=\"0\"][data-step=\"4\"]')?.classList.contains('on')", timeout=2000)
        lcd_page(page, "PAD")          # W4: the inspector is the PAD page of the LCD
        page.wait_for_function("document.querySelector('.inspector input[name=\"pitch\"]')?.value === '7'", timeout=2000)
        arrived = page.evaluate("window.__t")
        delay_ms = arrived - t0
        print(f"\n[agent-control] POST -> first remote action in the page: {delay_ms:.0f} ms", flush=True)
        self.assertLess(delay_ms, 2000)
        st = self.state(page)
        self.assertEqual((st["pad0"]["pitch"], st["pad0"]["gainDb"], round(st["fx"]["reverb"], 2)), (7, -4, 0.33))
        self.assertEqual([(e["pad"], e["beat"], e["pass"]) for e in st["events"]], [(0, 1, -1)])
        self.assertEqual(page.evaluate("window.__noReload"), "same page")  # no reload happened
        lcd_page(page, "FX")           # W4: the fx sliders are the FX page
        self.assertEqual(page.locator(".fx input[name=reverb]").input_value(), "0.33")
        other.wait_for_function(f"{sess}.store.get().project.pattern.events.length === 1", timeout=2000)  # the second tab too
        saved = json.load(open(os.path.join(self.tmp, "media", self.sid, "project.json")))
        self.assertEqual(saved["pads"][0]["pitch"], 7)  # the server wrote it, not the page

        # and the other way: a hand edit in one tab reaches the other tab and the disk, with no echo
        page.locator('.stepgrid i[data-pad="3"][data-step="8"]').click()
        other.wait_for_function(f"{sess}.store.get().project.pattern.events.length === 2", timeout=2000)
        page.wait_for_timeout(400)
        self.assertEqual(page.evaluate(f"{sess}.store.get().project.pattern.events.length"), 2)  # not doubled by its own echo
        saved = json.load(open(os.path.join(self.tmp, "media", self.sid, "project.json")))
        self.assertEqual(sorted(e["pad"] for e in saved["pattern"]["events"]), [0, 3])

        # transport: an agent hit sounds in the page (signal on the master analyser)
        page.wait_for_timeout(1200)  # the click above sounded; let it die away, then listen for the agent's hit only
        page.evaluate("window.__peak = 0; window.__hits = 0")
        out = self.api(f"/api/songs/{self.sid}/transport", {"command": "hit", "pad": 1, "velocity": 1})
        self.assertGreaterEqual(out["listeners"], 2)
        page.wait_for_function("window.__hits >= 1", timeout=2000)
        page.wait_for_function("window.__peak > 0.001", timeout=2000)
        self.assertEqual(errors + errors2, [])
        other.close()
        page.close()


if __name__ == "__main__":
    unittest.main()
