"""CHOP-12 browser tests: the second sampler-core slice has a home in the station UI. Every test operates the feature through
the screen (LCD soft keys, keypad, data wheel, buttons, the pads) and checks the effect in the store or in the audio; none of the
features is driven through the session. Covers project tempo and fit to tempo, pad filter, envelope, mute groups, 16 levels,
resample, song mode (patterns, chain, play, export), stems, import progress, the DAW bridge state, and the two small fixes
(the PLAY key is not a second STOP; Enter on a focused control activates it).

Run with a Python that has Playwright installed (like test_station.py):
    /usr/bin/python3 -W ignore -m unittest tests/e2e/test_station2.py
Songs are served from temporary copies of their media folders (real media/ is never written); no DAW is started (the bridge is
the fake of CHOPPAD_DAW_FAKE) and no network is used. CHOPPAD_SHOTS=1 also writes docs/design/shots/built2-*.png.
"""
import json
import math
import os
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from megastate import forget_projects, saved_project  # noqa: E402
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_core2 as tc  # noqa: E402  (helpers only: the fake stems service, the page-side analysis helpers)
import test_smoke as ts  # noqa: E402  (helpers only)

REPO, MEDIA = ts.REPO, ts.MEDIA
sync_playwright = ts.sync_playwright
S = "window.choppad.song.session"
SHOTS = os.path.join(REPO, "docs", "design", "shots")

# the new LCD sub-pages: (page key, sub-page number, name used in screenshot files)
NEW_PAGES = [("PAD", 5, "env"), ("PAD", 6, "filter"), ("PAD", 7, "mute"), ("PAD", 8, "levels"), ("GRID", 4, "tempo"), ("SEQ", 3, "song"),
             ("SEQ", 4, "chain"), ("EXPORT", 3, "sample"), ("EXPORT", 4, "daw"), ("AI", 2, "stems")]

# energy of the first difference of channel 0 of a rendered WAV: a measure of the high frequencies in it
HF_JS = """async () => {
    const buf = await window.__t.decode(Array.from(await window.choppad.song.session.renderPatternWav()));
    const x = buf.getChannelData(0); let hf = 0, all = 0;
    for (let i = 1; i < x.length; i++) { const d = x[i] - x[i - 1]; hf += d * d; all += x[i] * x[i]; }
    return { hf, all };
}"""


def free_port():
    return ts.free_port()


def make_repo(sids, prefix="choppad-station2-"):
    """A temporary CHOPPAD_REPO holding copies of the songs' json (audio and video linked)."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    for sid in sids:
        dst = os.path.join(tmp, "media", sid)
        os.makedirs(dst)
        src = os.path.join(MEDIA, sid)
        for f in os.listdir(src):
            if f in ts.HEAVY:
                os.symlink(os.path.join(src, f), os.path.join(dst, f))
            elif f.endswith(".json") and f not in ("project.json", "onsets.json"):
                shutil.copy(os.path.join(src, f), dst)
    return tmp


def start_server(tmp, env=None):
    port = free_port()
    proc = subprocess.Popen(
        [os.path.join(REPO, ".venv", "bin", "python"), os.path.join(REPO, "components", "integrate", "choppad.py"), "--serve", "--port", str(port)],
        env={**os.environ, "CHOPPAD_REPO": tmp, "CHOPPAD_STEMS_BACKEND": "none", **(env or {})}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/api/songs", timeout=1)
            return proc, base
        except OSError:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("server did not start")


def lcd_page(page, name, sub=1):
    """Press the page key (again, to cycle sub-pages) until the LCD shows page `name`, sub-page `sub` (1-based). Unlike the
    helper of test_smoke it can reach sub-page 8 (PAD has eight now)."""
    key = page.locator(f'.mode[data-mode="{name}"]')
    for _ in range(14):
        if key.get_attribute("aria-pressed") == "true" and page.locator(".lcd-sub").inner_text().startswith(f"{sub}/"):
            return
        key.click()
    raise AssertionError(f"LCD page {name} {sub} not reachable")


def click_wav(path, seconds=8, bpm=120, sr=22050):
    """A tiny WAV of clicks on the beat (enough for the import pipeline to find a tempo)."""
    n = int(seconds * sr)
    frames = bytearray()
    beat = int(sr * 60 / bpm)
    for i in range(n):
        k = i % beat
        v = int(20000 * math.exp(-k / 400.0) * math.sin(2 * math.pi * (880 if (i // beat) % 4 else 440) * k / sr)) if k < 3000 else 0
        frames += struct.pack("<h", max(-32768, min(32767, v)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


@unittest.skipIf(sync_playwright is None, "playwright is not installed for this Python")
@unittest.skipIf(ts.library_song() is None, "no library song in media/")
class Station2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sid = ts.library_song()
        cls.short = tc.shortest_song()
        sids = [cls.sid] + ([cls.short] if cls.short != cls.sid else [])
        cls.tmp = make_repo(sids)
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.pristine = {s: open(os.path.join(cls.tmp, "media", s, "chops.json")).read() for s in sids}
        cls.sids = sids
        proc, cls.base = start_server(cls.tmp)
        cls.addClassCleanup(proc.wait, 10)
        cls.addClassCleanup(proc.terminate)
        cls.pw = sync_playwright().start()
        cls.addClassCleanup(cls.pw.stop)
        args = ["--autoplay-policy=no-user-gesture-required"]
        try:
            cls.browser = cls.pw.chromium.launch(args=args)
        except Exception:
            cls.browser = cls.pw.chromium.launch(args=args, executable_path=ts.chrome_path(), headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):  # every test starts from an empty project and the original chop table
        forget_projects(os.path.join(self.tmp, "media"))
        for sid in self.sids:
            d = os.path.join(self.tmp, "media", sid)
            if os.path.exists(os.path.join(d, "project.json")):
                os.remove(os.path.join(d, "project.json"))
            open(os.path.join(d, "chops.json"), "w").write(self.pristine[sid])
            for sub in ("stretch", "resample", "stems"):
                shutil.rmtree(os.path.join(d, sub), ignore_errors=True)

    # ------------------------------------------------------------------ helpers
    def open_song(self, base=None, sid=None, viewport=(1500, 1000), init=None, **ctx_args):
        ctx = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, **ctx_args)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 and not r.url.endswith("/favicon.ico") and not (r.status == 501 and r.url.endswith("/stems")) and "/media/x/" not in r.url else None)
        if init:
            page.add_init_script(init)
        page.goto(f"{base or self.base}/#/song/{sid or self.sid}")
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        page.evaluate(tc.PAGE_HELPERS)
        return page, errors

    def store(self, page, expr):
        return page.evaluate(f"{S}.store.get().{expr}")

    @staticmethod
    def soft(page, i):
        page.locator(f'.soft[data-soft="{i}"]').click()

    @staticmethod
    def keys(page, text):
        for k in text.split() if " " in text else list(text):
            page.locator(f'.keypad .key:text-is("{k}")').click()

    def type_in(self, page, soft_index, text):
        """Select an LCD field with its soft key and type a value on the keypad (digits and '.'), then ENT."""
        self.soft(page, soft_index)
        self.keys(page, text)
        page.locator('.keypad .key:text-is("ENT")').click()

    @staticmethod
    def draw(page, pad, step=0):
        page.locator(f'.stepgrid i[data-pad="{pad}"][data-step="{step}"]').click()

    # ------------------------------------------------------------------ project tempo, fit to tempo
    def test_project_tempo_and_fit_to_tempo(self):
        page, errors = self.open_song()
        song_bpm = self.store(page, "chops.bpm")
        lcd_page(page, "GRID", 4)
        # F1 selects TEMPO, the keypad types 92, ENT sets the project tempo
        self.type_in(page, 0, "92")
        page.wait_for_function(f"{S}.store.get().project.tempo?.bpm === 92", timeout=5000)
        # the header and the MAIN line of the LCD say so, with the song's own tempo beside it
        self.assertIn(f"92.0 BPM (song {song_bpm:.1f})", page.locator(".s-meta").inner_text())
        lcd_page(page, "MAIN")
        self.assertIn(f"92.0 BPM (SONG {song_bpm:.1f})", page.locator(".statusline").inner_text())
        self.assertEqual(page.locator(".lcd-main .bpm").inner_text(), "92.0")
        # one pad stretched by hand (PAD page 4), then the pattern pads are fitted from the TEMPO page
        self.draw(page, 0)
        self.draw(page, 1, 4)
        lcd_page(page, "PAD", 4)
        page.locator(".pad[data-pad='1']").click()   # selects pad 2
        self.type_in(page, 0, "110")
        page.locator(".st-pad").click()
        page.wait_for_function(f"{S}.store.get().project.overrides['0:1']?.stretch", timeout=60000)
        lcd_page(page, "GRID", 4)
        page.locator(".pt-fit-pattern").click()
        page.wait_for_function(f"{S}.store.get().project.overrides['0:0']?.stretch?.fit === 92", timeout=60000)
        page.wait_for_function("document.querySelector('.fit-out')?.textContent.includes('FITTED')", timeout=20000)
        out = page.locator(".fit-out").inner_text()
        self.assertIn("1 PAD FITTED", out)
        self.assertIn("PAD 2 — stretched by hand", out)
        self.assertIsNone(self.store(page, "project.overrides['0:1'].stretch.fit"), "a hand stretch is never fitted")
        # fitted pads are marked differently from hand-stretched ones
        p0, p1 = page.locator(".pad[data-pad='0']"), page.locator(".pad[data-pad='1']")
        self.assertIn("fitted", p0.get_attribute("class"))
        self.assertNotIn("handstretched", p0.get_attribute("class"))
        self.assertEqual(p0.locator(".pad-tag").inner_text(), "FIT")
        self.assertIn("handstretched", p1.get_attribute("class"))
        self.assertNotIn("fitted", p1.get_attribute("class"))
        self.assertTrue(p1.locator(".pad-tag").is_hidden())
        # the tempo follows: a new tempo re-fits the fitted pad; "song tempo" takes the fit away
        self.type_in(page, 0, "100")
        page.wait_for_function(f"{S}.store.get().project.overrides['0:0']?.stretch?.fit === 100", timeout=60000)
        page.locator(".pt-song").click()
        page.wait_for_function(f"{S}.store.get().project.tempo.bpm === null && !{S}.store.get().project.overrides['0:0']?.stretch", timeout=60000)
        self.assertNotIn("(song", page.locator(".s-meta").inner_text())
        # fit bank needs a tempo: disabled without one
        self.assertTrue(page.locator(".pt-fit-bank").is_disabled())
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ pad filter
    def test_pad_filter_on_the_lcd_changes_the_render(self):
        page, errors = self.open_song()
        self.draw(page, 0)
        page.evaluate(f"{S}.store.dispatch({{ type: 'pattern.setBars', bars: 1 }})")
        before = page.evaluate(HF_JS)
        page.keyboard.press("KeyZ")  # selects pad 1
        lcd_page(page, "PAD", 6)
        page.locator(".f-type .opt", has_text="LP").click()
        self.type_in(page, 1, "500")     # CUTOFF: 500 Hz typed in real units on a log-scale range
        self.assertEqual(self.store(page, "project.pads[0].filter.type"), "lp")
        self.assertEqual(self.store(page, "project.pads[0].filter.cutoff"), 500)
        self.assertIn("500 Hz", page.locator('.lcd-body [data-out="cutoff"]').inner_text())
        after = page.evaluate(HF_JS)
        self.assertLess(after["hf"], 0.2 * before["hf"], (before, after))
        self.assertGreater(after["all"], 0, "the filtered pad still sounds")
        # Q by keypad, and the data wheel steps the cutoff on the log scale (up)
        self.type_in(page, 2, "6")
        self.assertEqual(self.store(page, "project.pads[0].filter.q"), 6)
        self.soft(page, 1)
        page.locator(".wk.inc").click()
        self.assertGreater(self.store(page, "project.pads[0].filter.cutoff"), 500)
        # keyboard: arrow keys on the focused range
        c0 = self.store(page, "project.pads[0].filter.cutoff")
        page.locator('.lcd-body input[name="cutoff"]').focus()
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("ArrowLeft")
        self.assertLess(self.store(page, "project.pads[0].filter.cutoff"), c0)
        # OFF puts it back
        page.locator(".f-type .opt", has_text="off").click()
        self.assertEqual(self.store(page, "project.pads[0].filter.type"), "off")
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ pad envelope
    def test_envelope_page_all_four_values_and_the_drawn_curve(self):
        page, errors = self.open_song()
        lcd_page(page, "PAD", 5)
        pts0 = page.locator(".adsr-line").get_attribute("points")
        self.type_in(page, 0, "20")      # attack ms
        self.type_in(page, 1, "0.4")     # decay s
        self.type_in(page, 2, "0.5")     # sustain
        self.type_in(page, 3, "300")     # release ms
        pad = self.store(page, "project.pads[0]")
        self.assertAlmostEqual(pad["attack"], 0.02)
        self.assertAlmostEqual(pad["decay"], 0.4)
        self.assertAlmostEqual(pad["sustain"], 0.5)
        self.assertAlmostEqual(pad["release"], 0.3)
        self.assertNotEqual(page.locator(".adsr-line").get_attribute("points"), pts0, "the curve follows the values")
        self.assertIn("decay 400 ms, sustain 50 percent, release 300 ms", page.locator(".adsr").get_attribute("aria-label"))
        # the curve really changes with one value: a higher sustain lifts the plateau
        y = lambda: float(page.locator(".adsr-line").get_attribute("points").split(" ")[2].split(",")[1])  # noqa: E731
        y0 = y()
        self.type_in(page, 2, "0.9")
        self.assertLess(y(), y0)
        # keypad 0 turns the decay stage off
        self.type_in(page, 1, "0")
        self.assertEqual(self.store(page, "project.pads[0].decay"), 0)
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ mute groups
    def test_mute_group_on_the_pad_and_the_choke_toggle(self):
        page, errors = self.open_song()
        lcd_page(page, "PAD", 7)
        self.assertTrue(self.store(page, "settings.choke"))
        self.assertIn("GLOBAL CHOKE IS ON", page.locator(".mg-info").inner_text().upper())
        self.type_in(page, 0, "3")
        self.assertEqual(self.store(page, "project.pads[0].muteGroup"), 3)
        self.assertEqual(page.locator(".pad[data-pad='0'] .pad-mg").inner_text(), "M3")
        self.assertTrue(page.locator(".pad[data-pad='1'] .pad-mg").is_hidden())
        # the one-line explanation, and the choke toggle next to it
        self.soft(page, 1)   # F2 = CHOKE
        self.assertFalse(self.store(page, "settings.choke"))
        self.assertIn("GLOBAL CHOKE IS OFF", page.locator(".mg-info").inner_text().upper())
        self.assertIn("SAME GROUP", page.locator(".mg-info").inner_text().upper())
        self.type_in(page, 0, "0")
        self.assertEqual(self.store(page, "project.pads[0].muteGroup"), 0)
        self.assertTrue(page.locator(".pad[data-pad='0'] .pad-mg").is_hidden())
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ 16 levels
    def test_sixteen_levels_key_labels_and_recorded_levels(self):
        page, errors = self.open_song()
        before = [page.locator(f".pad[data-pad='{p}'] .pad-label").inner_text() for p in range(16)]
        page.locator(".lvl-key").click()
        self.assertEqual(self.store(page, "settings.sixteen"), {"pad": 0, "mode": "velocity"})
        self.assertEqual(page.locator(".lvl-key").get_attribute("aria-pressed"), "true")
        # the 16 pads show their level instead of the bar.beat label
        labels = [page.locator(f".pad[data-pad='{p}'] .pad-label").inner_text() for p in range(16)]
        self.assertEqual(labels, [str(math.floor(100 * (p + 1) / 16 + 0.5)) for p in range(16)])
        self.assertNotEqual(labels, before)
        # the source pad is named on the LCD (MAIN line and the 16 LEVELS page)
        lcd_page(page, "MAIN")
        self.assertIn("16 LEVELS: PAD 1 · VELOCITY", page.locator(".statusline").inner_text())
        lcd_page(page, "PAD", 8)
        self.assertIn("SOURCE PAD 1", page.locator(".lv-info").inner_text())
        # recording a hit on pad 8 = pad 1 at level 8/16
        page.locator(".seq-play").click()
        page.wait_for_function(f"{S}.seq.state.playing === true")
        page.locator(".rec").click()
        page.wait_for_function(f"{S}.seq.state.recording === true")
        box = page.locator(".pad[data-pad='7']").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.up()
        page.wait_for_function(f"{S}.store.get().project.pattern.events.length >= 1")
        ev = self.store(page, "project.pattern.events[0]")
        self.assertEqual(ev["pad"], 0)
        self.assertAlmostEqual(ev["vel"], 0.5)
        page.locator(".seq-play").click()
        # the mode on the LCD: pitch shows semitones on the pads, filter shows Hz
        page.locator(".lv-mode .opt", has_text="pitch").click()
        self.assertEqual(self.store(page, "settings.sixteen"), {"pad": 0, "mode": "pitch"})
        self.assertEqual([page.locator(f".pad[data-pad='{p}'] .pad-label").inner_text() for p in (0, 7, 15)], ["-7", "0", "+8"])
        page.locator(".lv-mode .opt", has_text="filter").click()
        self.assertEqual(page.locator(".pad[data-pad='15'] .pad-label").inner_text(), "16.0k")
        # the key turns it off again, labels come back
        page.locator(".lvl-key").click()
        self.assertIsNone(self.store(page, "settings.sixteen"))
        self.assertEqual([page.locator(f".pad[data-pad='{p}'] .pad-label").inner_text() for p in range(16)], before)
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ resample
    def test_resample_pattern_and_master_onto_pads(self):
        page, errors = self.open_song()
        self.draw(page, 0)
        self.draw(page, 2, 4)
        lcd_page(page, "EXPORT", 3)
        self.type_in(page, 2, "1")       # LENGTH: 1 bar
        self.type_in(page, 3, "6")       # TARGET PAD: 6
        page.locator(".rs-go").click()
        page.wait_for_function(f"{S}.store.get().project.overrides['0:5']?.src", timeout=30000)
        src = self.store(page, "project.overrides['0:5'].src")
        self.assertRegex(src, r"^/media/_mega/[\w-]+/samples/[0-9a-f]{16}\.wav$")   # step D: a resample is stored under the mega bank
        page.wait_for_function("document.querySelector('.rs-out')?.textContent.includes('NOW PLAYS')", timeout=20000)
        self.assertIn("PAD 6 (BANK 1) NOW PLAYS", page.locator(".rs-out").inner_text())
        pad = page.locator(".pad[data-pad='5']")
        self.assertIn("resampled", pad.get_attribute("class"))
        self.assertEqual(pad.locator(".pad-tag").inner_text(), "RS")
        # the master bus is recorded in real time: "recording…" shows while it runs
        page.locator(".rs-source .opt", has_text="master").click()
        page.locator(".rs-unit .opt", has_text="sec").click()
        self.type_in(page, 2, "1.2")
        self.type_in(page, 3, "7")
        t0 = time.time()
        page.locator(".rs-go").click()
        page.wait_for_function("document.querySelector('.rs-out')?.textContent.includes('RECORDING')", timeout=3000)
        self.assertTrue(page.locator(".rs-go").is_disabled(), "GO waits while it records")
        page.wait_for_function(f"{S}.store.get().project.overrides['0:6']?.src", timeout=30000)
        self.assertGreater(time.time() - t0, 1.0, "the master capture takes its real time")
        page.wait_for_function("document.querySelector('.rs-out')?.textContent.includes('NOW PLAYS')", timeout=20000)
        self.assertRegex(page.locator(".rs-out").inner_text(), r"PAD 7 \(BANK 1\) NOW PLAYS A 1\.\d\d S RESAMPLE")
        self.assertAlmostEqual(self.store(page, "project.overrides['0:6'].end"), 1.2, delta=0.05)
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ song mode
    def test_song_mode_patterns_chain_and_playing_markers(self):
        page, errors = self.open_song()
        # 240 BPM: a bar lasts 1 s, so the chain passes quickly; 1 bar patterns
        lcd_page(page, "GRID", 4)
        self.type_in(page, 0, "240")
        page.wait_for_function(f"{S}.store.get().project.tempo?.bpm === 240", timeout=5000)
        while self.store(page, "project.pattern.bars") != 1:
            page.locator(".bars-key").click()
        self.draw(page, 0)
        lcd_page(page, "SEQ", 3)
        page.locator(".song-new").click()                      # pattern 2 (the first one becomes "Pattern 1")
        self.assertEqual([p["id"] for p in self.store(page, "project.song.patterns")], ["p1", "p2"])
        self.assertEqual(self.store(page, "project.song.current"), "p2")
        self.draw(page, 3, 4)
        page.locator(".pat-name").fill("Chorus")
        self.assertEqual(self.store(page, "project.song.patterns[1].name"), "Chorus")
        page.locator(".song-copy").click()                     # pattern 3 = a copy of Chorus
        self.assertEqual(len(self.store(page, "project.song.patterns")), 3)
        self.assertEqual(len(self.store(page, "project.song.patterns[2].events")), 1)
        page.locator(".song-remove").click()
        self.assertEqual([p["id"] for p in self.store(page, "project.song.patterns")], ["p1", "p2"])
        # select = the pattern being edited in the step grid
        page.locator(".pat-list .pat", has_text="Pattern 1").click()
        self.assertEqual(self.store(page, "project.song.current"), "p1")
        self.assertTrue(page.locator('.stepgrid i[data-pad="0"][data-step="0"]').get_attribute("class").find("on") >= 0)
        # chain: add two entries, point the second at Chorus, repeats 2 on the first, reorder, remove
        lcd_page(page, "SEQ", 4)
        page.locator(".chain-add").click()
        page.locator(".chain-add").click()
        self.assertEqual(self.store(page, "project.song.chain"), [{"pattern": "p1", "repeats": 1}, {"pattern": "p1", "repeats": 1}])
        page.locator(".chain-row[data-i='1'] .cr-next").click()
        page.locator(".chain-row[data-i='0'] .cr-rep").fill("2")
        page.locator(".chain-row[data-i='0'] .cr-rep").press("Tab")
        self.assertEqual(self.store(page, "project.song.chain"), [{"pattern": "p1", "repeats": 2}, {"pattern": "p2", "repeats": 1}])
        page.locator(".chain-row[data-i='0'] .cr-down").click()
        self.assertEqual([c["pattern"] for c in self.store(page, "project.song.chain")], ["p2", "p1"])
        page.locator(".chain-row[data-i='0'] .cr-down").click()
        self.assertEqual([c["pattern"] for c in self.store(page, "project.song.chain")], ["p1", "p2"])
        page.locator(".chain-add").click()
        page.locator(".chain-row[data-i='2'] .cr-del").click()
        self.assertEqual(len(self.store(page, "project.song.chain")), 2)
        # mode switch: song mode makes PLAY play the chain, and the key says so
        page.locator(".song-mode .opt", has_text="song").click()
        self.assertEqual(self.store(page, "project.song.mode"), "song")
        self.assertIn("song-mode", page.locator(".seq-play").get_attribute("class"))
        self.assertIn("Chain", page.locator(".seq-play").inner_text())
        # play: songPosition() goes through the chain, the LCD and the chain list mark the entry and pattern that sound
        page.evaluate("""() => { window.__seen = { ids: new Set(), rows: new Set(), info: new Set(), main: new Set() };
            setInterval(() => { const s = window.choppad.song.session; if (!s.seq.state.playing) return; const w = s.songPosition(); window.__seen.ids.add(w.id + ':' + w.index);
              const r = document.querySelector('.chain-row.playing'); if (r) window.__seen.rows.add(r.dataset.i);
              const i = document.querySelector('.chain-info'); if (i && i.textContent.includes('▶')) window.__seen.info.add(i.textContent.replace(/BAR.*/, '').trim()); }, 40); }""")
        page.locator(".seq-play").click()
        page.wait_for_function(f"{S}.seq.state.playing === true")
        page.wait_for_function("window.__seen.rows.size >= 2", timeout=8000)
        seen = page.evaluate("({ ids: [...window.__seen.ids], rows: [...window.__seen.rows], info: [...window.__seen.info] })")
        self.assertEqual({i.split(":")[0] for i in seen["ids"]}, {"p1", "p2"})
        self.assertEqual(sorted(seen["rows"]), ["0", "1"])
        self.assertTrue(any("ENTRY 1/2 · PATTERN 1" in i for i in seen["info"]), seen["info"])
        self.assertTrue(any("ENTRY 2/2 · CHORUS" in i for i in seen["info"]), seen["info"])
        self.assertIn("Chain on", page.locator(".seq-play").inner_text())
        lcd_page(page, "MAIN")
        page.wait_for_function("document.querySelector('.statusline')?.textContent.includes('SONG ▶')", timeout=5000)
        lcd_page(page, "SEQ", 3)
        page.wait_for_function("document.querySelector('.pat.playing')", timeout=5000)
        page.locator(".stop").click()
        page.wait_for_function(f"{S}.seq.state.playing === false")
        # export the whole song: .wav and .mid are downloads
        lcd_page(page, "SEQ", 4)
        with page.expect_download(timeout=60000) as dl:
            page.locator(".song-wav").click()
        self.assertTrue(dl.value.suggested_filename.endswith("-song.wav"))
        wav = open(dl.value.path(), "rb").read()
        self.assertEqual(wav[:4], b"RIFF")
        self.assertGreater(len(wav), 44100)
        with page.expect_download(timeout=10000) as dl:
            page.locator(".song-mid").click()
        self.assertTrue(dl.value.suggested_filename.endswith("-song.mid"))
        self.assertEqual(open(dl.value.path(), "rb").read()[:4], b"MThd")
        # saved with the project
        page.evaluate(f"{S}.remote.flush()")
        saved = saved_project(os.path.join(self.tmp, "media"), self.sid)["song"]
        self.assertEqual((saved["mode"], [c["repeats"] for c in saved["chain"]]), ("song", [2, 1]))
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ stems
    def test_stems_without_a_backend_explain_what_is_missing(self):
        page, errors = self.open_song()
        lcd_page(page, "AI", 2)
        page.locator(".stems-run").click()
        page.wait_for_function("document.querySelector('.stem-out')?.textContent.includes('NO STEMS BACKEND')", timeout=20000)
        text = page.locator(".stem-out").text_content()
        self.assertIn("CHOPPAD_STEMS_BACKEND=remote", text)
        self.assertIn("CHOPPAD_STEMS_URL", text)
        # no stems: picking one for a pad explains instead of failing silently
        page.locator(".pad-stem .opt", has_text="drums").click()
        self.assertIn("Separate the song first", page.locator(".stem-out").text_content())
        self.assertIsNone(self.store(page, "project.overrides['0:0']"))
        self.assertEqual(errors, [], str(errors))

    def test_stems_with_a_backend_and_the_per_pad_selector(self):
        service = tc.FakeStemsService()
        self.addCleanup(service.close)
        tmp = make_repo([self.short], "choppad-station2-stems-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        proc, base = start_server(tmp, {"CHOPPAD_STEMS_BACKEND": "remote", "CHOPPAD_STEMS_URL": service.url})
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        page, errors = self.open_song(base, self.short)
        lcd_page(page, "AI", 2)
        page.locator(".stems-run").click()
        page.wait_for_function("document.querySelector('.stem-out')?.textContent.includes('STEMS READY')", timeout=120000)
        self.assertEqual(page.evaluate(f"{S}.meta.stems"), ["drums", "bass", "vocals", "other"])
        page.locator(".pad-stem .opt[data-stem='drums']").click()
        page.wait_for_function(f"{S}.store.get().project.overrides['0:0']?.stem === 'drums'", timeout=30000)
        pad = page.locator(".pad[data-pad='0']")
        self.assertEqual(pad.locator(".pad-tag").inner_text(), "DRM")
        page.locator(".pad-stem .opt[data-stem='bass']").click()
        page.wait_for_function(f"{S}.store.get().project.overrides['0:0']?.stem === 'bass'", timeout=30000)
        page.locator(".pad-stem .opt", has_text="mix").click()
        page.wait_for_function(f"!{S}.store.get().project.overrides['0:0']?.stem", timeout=30000)
        self.assertTrue(pad.locator(".pad-tag").is_hidden())
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ import progress
    def test_import_progress_from_the_library_stream_opens_the_song(self):
        page, errors = self.open_song(init="window.__es = []; const E = window.EventSource; window.EventSource = class extends E { constructor(u, o) { super(u, o); window.__es.push(String(u)); } };")
        wav = os.path.join(self.tmp, "tiny-clicks.wav")
        click_wav(wav)
        requests = []
        page.on("request", lambda r: requests.append(r.url))
        page.evaluate("""() => { window.__jobs = []; new MutationObserver(() => {
            for (const r of document.querySelectorAll('.job[data-job]')) window.__jobs.push([r.querySelector('.job-stage')?.textContent, r.querySelector('.bar i')?.style.width, r.querySelector('.job-url')?.textContent]); })
            .observe(document.querySelector('.jobs'), { subtree: true, childList: true, characterData: true, attributes: true }); }""")
        page.locator(".file-pick input").set_input_files(wav)
        page.wait_for_selector(".job[data-job]", timeout=10000)
        page.wait_for_function("window.location.hash.startsWith('#/song/') && !window.location.hash.endsWith('%s')" % self.sid, timeout=180000)
        seen = page.evaluate("window.__jobs")
        self.assertTrue(any(s and "·" in s and w and w != "0%" and w != "" for s, w, _ in seen), seen[:10])
        self.assertTrue(any(u == "tiny-clicks.wav" for _, _, u in seen))
        steps = {s.split(" · ")[0] for s, _, _ in seen if s and " · " in s}
        self.assertGreaterEqual(len(steps), 2, f"each step of the import is shown: {steps}")
        self.assertIn("/api/events", page.evaluate("window.__es"), "the library follows imports on the event stream")
        self.assertFalse([r for r in requests if "/api/jobs/" in r], "the stream is used; polling is only the fallback")
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ DAW bridge state
    def test_daw_bridge_status_buttons_and_display_only_tab(self):
        tmp = make_repo([self.sid], "choppad-station2-daw-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        log = os.path.join(tmp, "fake-daw.jsonl")
        proc, base = start_server(tmp, {"CHOPPAD_DAW_FAKE": log, "CHOPPAD_DAW_DIR": os.path.join(tmp, "daw-state")})
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        entries = lambda op: [json.loads(l) for l in open(log) if l.strip() and json.loads(l)["op"] == op] if os.path.exists(log) else []  # noqa: E731
        page, errors = self.open_song(base)
        other, errors2 = self.open_song(base)
        for p in (page, other):
            p.wait_for_function(f"{S}.remote.connected", timeout=10000)
        page.wait_for_function(f"{S}.remote.primary === true", timeout=10000)
        other.wait_for_function(f"{S}.remote.primary === false", timeout=10000)
        # the second tab says it only displays
        other.wait_for_function("document.querySelector('.role-st')?.textContent === 'display only'", timeout=5000)
        self.assertTrue(page.locator(".role-st").is_hidden())
        self.assertTrue(page.locator(".daw-st").is_hidden())
        lcd_page(page, "EXPORT", 4)
        self.assertIn("NO BRIDGE OPEN", page.locator(".daw-out").inner_text())
        self.assertTrue(page.locator(".daw-sync").is_disabled())
        page.locator(".daw-open").click()
        page.wait_for_function("document.querySelector('.daw-st')?.textContent === '→ Hydrogen (bank A)'", timeout=15000)
        self.assertEqual(len(entries("launch")), 1)
        other.wait_for_function("document.querySelector('.daw-st')?.textContent === '→ Hydrogen (bank A)'", timeout=15000)
        self.assertEqual(page.evaluate(f"{S}.remote.daw"), {"open": True, "bank": 0})
        self.assertEqual(page.evaluate(f"{S}.remote.dawName"), "hydrogen")
        self.assertIn("→ HYDROGEN (BANK A)", page.locator(".daw-out").inner_text())
        # SYNC pushes the edits, STOP stops its transport
        page.locator(".daw-sync").click()
        page.wait_for_function("document.querySelector('.daw-out')?.textContent.includes('SYNC')", timeout=15000)
        self.assertEqual(len(entries("push")), 1)
        page.locator(".daw-stop").click()
        deadline = time.time() + 10
        while time.time() < deadline and not [e for e in entries("transport") if e["command"] == "stop"]:
            time.sleep(0.05)
        self.assertTrue([e for e in entries("transport") if e["command"] == "stop"])
        self.assertEqual(errors + errors2, [], str(errors + errors2))

    # ------------------------------------------------------------------ the two fixes
    def test_play_key_is_not_a_second_stop(self):
        page, errors = self.open_song()
        play, stop = page.locator(".seq-play"), page.locator(".stop")
        self.assertIn("▶", play.inner_text())
        play.click()
        page.wait_for_function(f"{S}.seq.state.playing === true")
        self.assertEqual(play.locator("i").inner_text(), "❚❚", "playing shows pause bars, not the stop square")
        self.assertIn("Playing", play.inner_text())
        self.assertIn("■", play.inner_text(), "the older tests read the stop square from this key")
        self.assertEqual(stop.locator("i").inner_text(), "■")
        self.assertEqual(stop.locator("span").inner_text().strip().lower(), "stop")
        self.assertEqual(play.get_attribute("aria-pressed"), "true")
        self.assertNotEqual(play.locator("span").evaluate("(e) => e.firstChild.textContent"), "Stop")
        stop.click()
        page.wait_for_function(f"{S}.seq.state.playing === false")
        self.assertIn("▶", play.inner_text())
        self.assertEqual(errors, [], str(errors))

    def test_enter_activates_a_focused_control_and_plays_the_song_elsewhere(self):
        page, errors = self.open_song()
        # Tab to a control: Enter presses it (here PLAY) and does not start the song
        page.locator(".tkey.rec").focus()
        page.keyboard.press("Tab")
        self.assertEqual(page.evaluate("document.activeElement.dataset.act"), "pattern")
        page.keyboard.press("Enter")
        page.wait_for_function(f"{S}.seq.state.playing === true")
        self.assertFalse(page.evaluate(f"{S}.songPlaying"))
        page.keyboard.press("Enter")                    # still focused: it presses the key again
        page.wait_for_function(f"{S}.seq.state.playing === false")
        # on the page (focus on the body) Enter plays and pauses the song
        page.mouse.click(700, 20)
        page.keyboard.press("Enter")
        page.wait_for_function(f"{S}.songPlaying === true")
        page.keyboard.press("Enter")
        page.wait_for_function(f"{S}.songPlaying === false")
        # a mouse click on a control leaves Enter as the song key (as before)
        page.locator(".seq-undo").click()
        page.keyboard.press("Enter")
        page.wait_for_function(f"{S}.songPlaying === true")
        page.keyboard.press("Enter")
        page.wait_for_function(f"{S}.songPlaying === false")
        self.assertEqual(errors, [], str(errors))

    # ------------------------------------------------------------------ layout, names, screenshots
    def prepare_state(self, page):
        page.evaluate(f"""() => {{ const d = (a) => {S}.store.dispatch(a);
            d({{ type: 'tempo.set', bpm: 92 }});
            d({{ type: 'pad.set', pad: 2, params: {{ muteGroup: 2, decay: 0.4, sustain: 0.5, filter: {{ type: 'lp', cutoff: 1200, q: 4 }} }} }});
            d({{ type: 'pad.select', pad: 2 }});
            d({{ type: 'pattern.toggleStep', pad: 0, step: 0 }}); d({{ type: 'pattern.toggleStep', pad: 1, step: 4 }});
            d({{ type: 'song.addPattern', name: 'Chorus' }}); d({{ type: 'song.addPattern', name: 'Bridge', copy: true }});
            d({{ type: 'song.setChain', chain: [{{ pattern: 'p1', repeats: 2 }}, {{ pattern: 'p2', repeats: 1 }}, {{ pattern: 'p3', repeats: 4 }}] }});
            d({{ type: 'slice.src', bank: 0, pad: 4, src: '/media/x/resample/0123456789abcdef.wav', start: 0, end: 1 }});
            d({{ type: 'slice.stretch', bank: 0, pad: 5, stretch: {{ ratio: 1.1, url: '/media/x/stretch/0123456789abcdef.wav', fit: 92 }} }});
            d({{ type: 'slice.stretch', bank: 0, pad: 6, stretch: {{ ratio: 0.9, url: '/media/x/stretch/fedcba9876543210.wav' }} }}); }}""")

    def test_new_pages_fit_without_scrolling_and_have_names(self):
        for viewport in ((1500, 1000), (1920, 1080)):
            page, errors = self.open_song(viewport=viewport)
            self.prepare_state(page)
            for name, sub, tag in NEW_PAGES:
                lcd_page(page, name, sub)
                page.wait_for_timeout(120)
                m = page.evaluate("""() => { const b = document.querySelector('.lcd-body');
                    const nameless = [...document.querySelectorAll('.lcd-body input, .lcd-body button')].filter((e) => !(e.getAttribute('aria-label') || e.textContent.trim() || e.title)).length;
                    return { over: b.scrollHeight - b.clientHeight, pageOver: document.documentElement.scrollWidth - innerWidth, nameless }; }""")
                self.assertLessEqual(m["over"], 1, f"{viewport} {name} {sub} {tag}: the LCD body is clipped by {m['over']}px")
                self.assertLessEqual(m["pageOver"], 0, f"{viewport} {tag}: horizontal scroll")
                self.assertEqual(m["nameless"], 0, f"{tag}: a control has no accessible name")
            self.assertEqual(errors, [], str(errors))

    def test_screenshots_of_every_new_page_in_both_themes(self):
        if not os.environ.get("CHOPPAD_SHOTS"):
            self.skipTest("set CHOPPAD_SHOTS=1 to write docs/design/shots/built2-*.png")
        os.makedirs(SHOTS, exist_ok=True)
        for theme in ("light", "dark"):
            page, errors = self.open_song(color_scheme=theme)
            self.addCleanup(page.close)
            page.evaluate("document.documentElement.dataset.theme = %s" % json.dumps(theme))
            self.prepare_state(page)
            page.locator(".lvl-key").click()
            page.locator(".lvl-key").click()
            for name, sub, tag in NEW_PAGES:
                lcd_page(page, name, sub)
                page.wait_for_timeout(250)
                page.screenshot(path=os.path.join(SHOTS, f"built2-{theme}-{tag}.png"))
            self.assertEqual(errors, [], str(errors))
            page.close()   # the next theme's tab would otherwise be a display-only second tab


if __name__ == "__main__":
    unittest.main()
