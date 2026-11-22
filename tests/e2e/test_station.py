"""W4 (UX/UI overhaul) browser tests: the eight LCD pages, the vox view, themes, phone pad mode, the classic fallback,
pad-trigger latency, and robustness (long title, one bank, many banks, no horizontal scroll, accessible names, contrast).

Run with a Python that has Playwright installed (like test_smoke.py):
    python3 -m unittest tests/e2e/test_station.py
Skips when Playwright or a library song is missing. Songs are served from a temporary copy of their media folder.
"""
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_smoke as ts  # noqa: E402  (helpers only)

REPO, MEDIA = ts.REPO, ts.MEDIA
sync_playwright = ts.sync_playwright
S = "window.choppad.song.session"

# peak of the master analyser over ~300 ms, in the page
PEAK_JS = """async () => {
    const a = window.choppad.audio.analyser, buf = new Float32Array(a.fftSize);
    let peak = 0;
    for (let i = 0; i < 20; i++) { a.getFloatTimeDomainData(buf); for (const v of buf) peak = Math.max(peak, Math.abs(v)); await new Promise((r) => setTimeout(r, 15)); }
    return peak;
}"""
# contrast ratio of two CSS colours (alpha of fg blended over bg), through a canvas so any colour syntax works
CONTRAST_JS = """([fg, bg]) => {
    const c = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    const rgba = (col) => { c.clearRect(0, 0, 1, 1); c.fillStyle = "#000"; c.fillStyle = col; c.fillRect(0, 0, 1, 1); const d = c.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
    const B = rgba(bg), F = rgba(fg), a = F[3];
    const mix = [0, 1, 2].map((i) => F[i] * a + B[i] * (1 - a));
    const lum = (v) => { const [r, g, b] = v.map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
    const l1 = lum(mix), l2 = lum(B.slice(0, 3));
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}"""


@unittest.skipIf(sync_playwright is None, "playwright is not installed for this Python")
@unittest.skipIf(ts.library_song() is None, "no library song in media/")
class Station(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sid = ts.library_song()
        cls.tmp = tempfile.mkdtemp(prefix="choppad-station-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        dst = os.path.join(cls.tmp, "media", cls.sid)
        os.makedirs(dst)
        src = os.path.join(MEDIA, cls.sid)
        for f in os.listdir(src):
            if f in ts.HEAVY:
                os.symlink(os.path.join(src, f), os.path.join(dst, f))
            elif f.endswith(".json") and f != "project.json":
                shutil.copy(os.path.join(src, f), dst)
        cls.dst = dst
        cls.pristine = {f: open(os.path.join(dst, f)).read() for f in ("chops.json", "meta.json")}
        cls.port = ts.free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = subprocess.Popen(
            [os.path.join(REPO, ".venv", "bin", "python"), os.path.join(REPO, "components", "integrate", "choppad.py"), "--serve", "--port", str(cls.port)],
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

    def setUp(self):
        for f in ("project.json",):
            if os.path.exists(os.path.join(self.dst, f)):
                os.remove(os.path.join(self.dst, f))
        for f, text in self.pristine.items():
            open(os.path.join(self.dst, f), "w").write(text)

    def open_song(self, query="", viewport=(1500, 1000), **ctx_args):
        ctx = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, **ctx_args)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url} {(r.request.post_data or \'\')[:400]}") if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)  # every failed request, with its body, so a flake names itself
        page.goto(f"{self.base}/{query}#/song/{self.sid}")
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        return page, errors

    def store(self, page, expr):
        return page.evaluate(f"{S}.store.get().{expr}")

    # ------------------------------------------------------------------ the eight LCD pages
    def test_all_lcd_pages_change_a_value(self):
        page, errors = self.open_song()
        soft = lambda i: page.locator(f'.soft[data-soft="{i}"]').click()  # noqa: E731
        # MAIN: F3 toggles FOLLOW (settings.follow)
        ts.lcd_page(page, "MAIN")
        self.assertTrue(self.store(page, "settings.follow"))
        soft(2)
        self.assertFalse(self.store(page, "settings.follow"))
        soft(2)
        self.assertTrue(self.store(page, "settings.follow"))
        # PAD: F2 selects PITCH, the data wheel (+ key) steps it, the keypad types a value
        page.keyboard.press("KeyZ")
        ts.lcd_page(page, "PAD")
        soft(1)
        page.locator(".wk.inc").click()
        page.locator(".wk.inc").click()
        self.assertEqual(self.store(page, "project.pads[0].pitch"), 2)
        for k in ("5", "ENT"):
            page.locator(f'.keypad .key:text-is("{k}")').click()
        self.assertEqual(self.store(page, "project.pads[0].pitch"), 5)
        self.assertEqual(page.locator('.inspector [data-out="pitch"]').inner_text(), "+5 st")
        # FX: F1 selects TONE; keypad 0.5 ENT; the knob on the deck follows
        ts.lcd_page(page, "FX")
        soft(0)
        for k in ("0", ".", "5", "ENT"):
            page.locator(f'.keypad .key:text-is("{k}")').click()
        self.assertAlmostEqual(self.store(page, "project.fx.tone"), 0.5)
        self.assertEqual(page.locator('[data-knob="tone"]').get_attribute("aria-valuenow"), "0.50")
        # SEQ: QUANT is a segmented row; click 1/8
        ts.lcd_page(page, "SEQ")
        page.locator(".seq-q .opt", has_text="1/8").click()
        self.assertEqual(self.store(page, "settings.quant"), "1/8")
        # GRID: GRID 1/8 re-chops (server round trip)
        ts.lcd_page(page, "GRID")
        page.locator(".grid-btns button.grid-1-8").click()
        page.wait_for_function(f"{S}.store.get().chops.grid === '1/8'", timeout=30000)
        # EXPORT: > ZIP shows a download link on the LCD
        ts.lcd_page(page, "EXPORT")
        page.locator(".exp-zip").click()
        page.wait_for_selector(".exp-out a.exp-download", timeout=60000)
        # AI: analyze is offered, load-bank is disabled until an analysis exists (no network in tests, so we do not run it)
        ts.lcd_page(page, "AI")
        self.assertTrue(page.locator(".ai-run").is_enabled())
        self.assertTrue(page.locator(".ai-bank").is_disabled())
        # MIDI: LEARN starts the learn mode on the input object and the LCD says so; RESET MAP answers too
        ts.lcd_page(page, "MIDI")
        page.locator(".learn").click()
        page.wait_for_function("document.querySelector('.midi-out')?.textContent.includes('LEARNING')", timeout=3000)
        page.locator(".reset").click()
        page.wait_for_function("document.querySelector('.midi-out')?.textContent.includes('DEFAULT MAP')", timeout=3000)
        self.assertEqual(errors, [])

    def test_lcd_fields_reachable_by_keyboard_and_wheel(self):
        page, errors = self.open_song()
        ts.lcd_page(page, "FX")
        # Tab into the first field (a native range input): arrow keys change it
        page.locator('.fx input[name="reverb"]').focus()
        before = self.store(page, "project.fx.reverb")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
        self.assertGreater(self.store(page, "project.fx.reverb"), before)
        # the mouse wheel over the data wheel steps the selected field
        page.locator(".fx .lcd-row", has_text="TONE").locator(".lb").click()
        t0 = self.store(page, "project.fx.tone")
        page.locator("[data-wheel]").hover()
        page.mouse.wheel(0, -100)
        self.assertGreater(self.store(page, "project.fx.tone"), t0)
        # page keys, soft keys and the pad keys have focus rings
        page.locator('.mode[data-mode="FX"]').focus()
        page.keyboard.press("Tab")
        self.assertNotEqual(page.evaluate("getComputedStyle(document.activeElement).outlineStyle"), "none")
        self.assertEqual(errors, [])

    def test_platter_scrub_stepgrid_keyboard_and_knobs(self):
        page, errors = self.open_song("?noauto&vox=off")
        # dragging the record scrubs the song: 3 o'clock is a quarter of the way through
        box = page.locator(".plat .vin").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        dur = page.evaluate(f"{S}.meta.duration_s")
        page.mouse.move(cx, cy - box["height"] / 3)
        page.mouse.down()
        page.mouse.move(cx + box["width"] / 3, cy, steps=6)
        page.mouse.up()
        pos = page.evaluate(f"{S}.position()")
        self.assertAlmostEqual(pos / dur, 0.25, delta=0.03)
        # keyboard on the record: arrow right moves a bar
        page.locator(".plat .vin").focus()
        page.keyboard.press("ArrowRight")
        self.assertGreater(page.evaluate(f"{S}.position()"), pos + 0.5)
        # the step grid works from the keyboard: focus, move the cursor, Enter toggles
        page.locator(".stepgrid").focus()
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Enter")
        events = self.store(page, "project.pattern.events")
        self.assertEqual([(e["pad"], e["beat"]) for e in events], [(0, 0.25)])
        page.keyboard.press("Enter")
        self.assertEqual(self.store(page, "project.pattern.events"), [])
        # knobs: arrow keys move TONE, double-click resets it
        page.locator('[data-knob="tone"]').focus()
        page.keyboard.press("ArrowUp")
        self.assertGreater(self.store(page, "project.fx.tone"), 0)
        page.locator('[data-knob="tone"]').dblclick()
        self.assertEqual(self.store(page, "project.fx.tone"), 0)
        # master volume knob drives the master gain
        before = page.evaluate("window.choppad.audio.master.gain.value")
        page.locator('[data-knob="vol"]').focus()
        page.keyboard.press("ArrowDown")
        self.assertLess(page.evaluate("window.choppad.audio.master.gain.value"), before)
        # note variation changes the velocity of mouse hits
        page.evaluate(f"() => {{ window.__v = []; {S}.on('hit', (e) => window.__v.push(e.detail.velocity)); }}")
        page.locator(".nv").fill("64")
        page.locator('.pad[data-pad="3"]').dispatch_event("pointerdown", {"pointerId": 1, "isPrimary": True})
        page.wait_for_function("window.__v.length > 0")
        self.assertAlmostEqual(page.evaluate("window.__v[0]"), 64 / 127, places=2)
        self.assertEqual(errors, [])

    # ------------------------------------------------------------------ vox view
    def test_vox_view(self):
        page, errors = self.open_song("?noauto")   # no automatic drop, so the quality we ask for is the quality we measure
        vox = ".voxfig"
        self.assertEqual(page.locator(f"{vox} .vox-holder canvas").count(), 1)
        page.wait_for_function(f"document.querySelector('{vox}').dataset.engine !== undefined", timeout=15000)
        frames = lambda: int(page.evaluate(f"document.querySelector('{vox}').dataset.frames || 0"))  # noqa: E731
        # frames advance while a pattern plays, and the field reacts to the real analyser
        page.evaluate(f"{S}.store.dispatch({{ type: 'pattern.toggleStep', pad: 0, step: 0, bank: 0 }})")
        page.keyboard.press("KeyP")
        page.wait_for_function(f"{S}.seq.state.playing === true")
        f0 = frames()
        page.wait_for_timeout(700)
        self.assertGreater(frames(), f0 + 5, "vox frame counter did not advance")
        # quality switch: lite, full, then off stops all rendering
        page.locator('[data-quality] [data-q="lite"]').click()
        page.wait_for_function(f"document.querySelector('{vox}').dataset.quality === 'lite'")
        f0 = frames(); page.wait_for_timeout(400)
        self.assertGreater(frames(), f0)
        page.locator('[data-quality] [data-q="full"]').click()
        page.wait_for_function(f"document.querySelector('{vox}').dataset.quality === 'full'")
        page.locator('[data-quality] [data-q="off"]').click()
        page.wait_for_function(f"document.querySelector('{vox}').dataset.quality === 'off'")
        page.wait_for_timeout(200)
        f0 = frames(); page.wait_for_timeout(600)
        self.assertEqual(frames(), f0, "off must stop rendering")
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('.vox-holder')).visibility"), "hidden")
        # the setting is remembered
        self.assertEqual(page.evaluate("localStorage.getItem('choppad.vox')"), "off")
        page.reload()
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_function(f"document.querySelector('{vox}').dataset.quality === 'off'")
        self.assertEqual(errors, [])

    def test_vox_pauses_under_reduced_motion_and_falls_back_to_2d(self):
        page, errors = self.open_song("?noauto", reduced_motion="reduce")
        page.wait_for_function("document.querySelector('.voxfig').dataset.engine !== undefined", timeout=15000)
        page.wait_for_timeout(500)
        f0 = int(page.evaluate("document.querySelector('.voxfig').dataset.frames || 0"))
        page.wait_for_timeout(600)
        self.assertEqual(int(page.evaluate("document.querySelector('.voxfig').dataset.frames || 0")), f0, "reduced motion: the view must be paused")
        self.assertEqual(page.locator('[data-act="pause"]').get_attribute("aria-pressed"), "true")
        page.locator('[data-act="pause"]').click()     # the person can resume it
        page.wait_for_function(f"+document.querySelector('.voxfig').dataset.frames > {f0 + 3}", timeout=5000)
        # no WebGL (or no three.js): the same view draws in a 2D canvas
        page2, errors2 = self.open_song("?noauto&vox=2d")
        page2.wait_for_function("document.querySelector('.voxfig').dataset.engine === '2d'", timeout=15000)
        page2.wait_for_function("+document.querySelector('.voxfig').dataset.frames > 5", timeout=5000)
        self.assertEqual(errors + errors2, [])

    # ------------------------------------------------------------------ themes
    def test_theme_switch_and_first_load_preference(self):
        # first load follows prefers-color-scheme
        page, errors = self.open_song(color_scheme="dark")
        self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), "dark")
        paper_dark = page.evaluate("getComputedStyle(document.body).backgroundColor")
        page.locator('[data-act="theme"]').click()
        self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), "light")
        self.assertNotEqual(page.evaluate("getComputedStyle(document.body).backgroundColor"), paper_dark)
        self.assertEqual(page.evaluate("localStorage.getItem('choppad.theme')"), "light")
        # the choice is stored and wins over the OS preference after a reload
        page.reload()
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), "light")
        page2, errors2 = self.open_song(color_scheme="light")
        self.assertEqual(page2.evaluate("document.documentElement.dataset.theme"), "light")
        self.assertEqual(errors + errors2, [])

    def test_contrast_in_both_themes(self):
        page, errors = self.open_song()
        checks = {  # selector -> (foreground property source, background source)
            ".lcd": (".lcd", ".lcd"), ".pad-label": (".pad-label", ".pad"), ".bank-label": (".bank-label", ".pad-col"), ".hd .sub": (".hd .sub", "body"),
            ".k-out": (".k-out", ".row2"), ".fps": (".fps", ".qbar"), ".song .meta": (".song .meta", ".lib"), ".section-seg": (".section-seg", ".wv"),
        }
        for theme in ("light", "dark"):
            page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
            for name, (fg_sel, bg_sel) in checks.items():
                fg = page.evaluate(f"(() => {{ const e = document.querySelector('{fg_sel}'); return e ? getComputedStyle(e).color : null }})()")
                bg = page.evaluate(f"(() => {{ let e = document.querySelector('{bg_sel}'); while (e) {{ const c = getComputedStyle(e).backgroundColor; if (c && !/rgba\\(.*, 0\\)|transparent/.test(c)) return c; e = e.parentElement }} return getComputedStyle(document.body).backgroundColor }})()")
                if fg is None:
                    continue
                ratio = page.evaluate(CONTRAST_JS, [fg, bg])
                self.assertGreaterEqual(ratio, 4.5, f"{theme}: {name} contrast {ratio:.2f} ({fg} on {bg})")
        self.assertEqual(errors, [])

    # ------------------------------------------------------------------ phone pad mode
    def test_phone_pad_mode_touch_plays_and_multi_touch(self):
        page, errors = self.open_song("?mode=pad", viewport=(412, 915), has_touch=True, is_mobile=True, device_scale_factor=2)
        self.assertEqual(page.locator(".phone .pad").count(), 16)
        self.assertEqual(page.locator(".sheet").count(), 0)
        box = page.locator(".ph-pads").bounding_box()
        self.assertGreater(box["width"], 360)                      # the grid fills the phone screen
        self.assertEqual(page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), True)
        page.evaluate(f"() => {{ window.__hits = []; {S}.on('hit', (e) => window.__hits.push(e.detail.pad)); }}")
        page.wait_for_timeout(1500)                                # let the previous (silent) state settle
        page.locator('.ph-pads .pad[data-pad="1"]').tap()
        page.wait_for_function("window.__hits.includes(1)", timeout=3000)
        self.assertGreater(page.evaluate(PEAK_JS), 0.001, "a touch on a pad made no sound")
        # two fingers at once: both pads sound
        cdp = page.context.new_cdp_session(page)
        b1, b2 = page.locator('.ph-pads .pad[data-pad="2"]').bounding_box(), page.locator('.ph-pads .pad[data-pad="6"]').bounding_box()
        pts = [{"x": b1["x"] + 20, "y": b1["y"] + 20, "id": 1}, {"x": b2["x"] + 20, "y": b2["y"] + 20, "id": 2}]
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": pts})
        page.wait_for_function("window.__hits.includes(2) && window.__hits.includes(6)", timeout=3000)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        # bank keys work from the phone too
        page.locator(".ph-next").tap()
        page.wait_for_function(f"{S}.store.get().bank === 1")
        self.assertEqual(errors, [])

    # ------------------------------------------------------------------ the classic screen is still there
    def test_classic_ui_smoke(self):
        page, errors = self.open_song("?ui=classic")
        self.assertEqual(page.locator(".song-view").count(), 1)
        self.assertEqual(page.locator(".sheet").count(), 0)
        page.keyboard.press("KeyX")
        page.wait_for_function("window.choppad.song.state.lastChop !== null")
        self.assertGreater(page.evaluate(PEAK_JS), 0.001)
        page.locator('.inspector input[name="pitch"]').fill("3")
        self.assertEqual(self.store(page, "project.pads[1].pitch"), 3)
        page.locator(".grid-btns button.grid-1-8").click()
        page.wait_for_function(f"{S}.store.get().chops.grid === '1/8'", timeout=30000)
        self.assertEqual(page.evaluate("document.querySelector('link[rel=stylesheet]').getAttribute('href')"), "/style-classic.css")
        self.assertEqual(errors, [])

    # ------------------------------------------------------------------ latency: input event -> engine.trigger
    LATENCY_JS = """async () => {
        const s = window.choppad.song.session, input = window.choppad.input;
        const orig = s.engine.trigger.bind(s.engine);
        let t1 = 0;
        s.engine.trigger = (...a) => { t1 = performance.now(); return orig(...a); };
        const lat = [];
        for (let i = 0; i < 50; i++) {
            const pad = i % 16, t0 = performance.now();
            t1 = 0;
            input.dispatchEvent(new CustomEvent("pad-on", { detail: { type: "pad-on", pad, velocity: 1, time: t0, source: "midi" } }));
            lat.push(t1 - t0);
            input.dispatchEvent(new CustomEvent("pad-off", { detail: { type: "pad-off", pad, time: performance.now(), source: "midi" } }));
            await new Promise((r) => setTimeout(r, 45));
        }
        s.engine.trigger = orig;
        lat.sort((a, b) => a - b);
        return { median: lat[25], p90: lat[45], max: lat[49], bad: lat.filter((x) => !(x >= 0)).length };
    }"""

    def test_trigger_latency_not_worse_than_classic(self):
        res = {}
        for name, query in (("classic", "?ui=classic"), ("station + vox full", "?noauto&vox=full")):
            page, errors = self.open_song(query)
            page.wait_for_timeout(2500)           # let the view settle (vox warm-up, wavesurfer)
            res[name] = page.evaluate(self.LATENCY_JS)
            self.assertEqual(res[name]["bad"], 0, res[name])
            self.assertEqual(errors, [])
        print("\n[trigger latency] median / p90 / max ms: " + "; ".join(f"{k}: {v['median']:.3f} / {v['p90']:.3f} / {v['max']:.3f}" for k, v in res.items()), flush=True)
        self.assertLessEqual(res["station + vox full"]["median"], res["classic"]["median"] + 1.0)

    # ------------------------------------------------------------------ robustness
    def test_no_horizontal_scroll_names_and_no_console_errors(self):
        for size in ((1500, 1000), (1920, 1080)):
            page, errors = self.open_song("?noauto", viewport=size)
            page.wait_for_timeout(500)
            sw = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth, document.documentElement.scrollHeight, document.documentElement.clientHeight]")
            self.assertLessEqual(sw[0], sw[1], f"horizontal scroll at {size}: {sw}")
            self.assertLessEqual(sw[2], sw[3], f"vertical scroll at {size}: {sw}")
            unnamed = page.evaluate("""() => [...document.querySelectorAll('button, input, select, textarea, [role=slider]')].filter((e) => {
                if (e.type === 'file' || e.hidden) return false;
                const name = e.getAttribute('aria-label') || e.getAttribute('title') || e.textContent.trim() || (e.labels && e.labels.length && e.labels[0].textContent.trim()) || e.getAttribute('placeholder');
                return !name;
            }).map((e) => e.outerHTML.slice(0, 80))""")
            self.assertEqual(unnamed, [], f"controls without a name at {size}")
            self.assertEqual(errors, [])

    def test_long_title_one_bank_and_many_banks(self):
        # a very long title must not break the header
        meta = json.loads(self.pristine["meta.json"])
        meta["title"] = "An extraordinarily long song title " * 8
        open(os.path.join(self.dst, "meta.json"), "w").write(json.dumps(meta))
        page, errors = self.open_song("?noauto")
        hd = page.locator(".hd").bounding_box()
        self.assertLess(hd["height"], 45)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 1500)
        page.close()
        # one bank: a chop table with 16 chops
        ch = json.loads(self.pristine["chops.json"])
        ch["chops"] = ch["chops"][:16]
        ch["banks"] = [list(range(len(ch["chops"])))]
        open(os.path.join(self.dst, "chops.json"), "w").write(json.dumps(ch))
        page, errors2 = self.open_song("?noauto")
        self.assertEqual(self.store(page, "chops.banks.length"), 1)
        self.assertTrue(page.locator(".bank-next").is_disabled())
        self.assertEqual(page.locator(".bank-seg").count(), 1)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 1500)
        page.close()
        # many banks: the finest grid gives dozens
        open(os.path.join(self.dst, "chops.json"), "w").write(self.pristine["chops.json"])
        page, errors3 = self.open_song("?noauto")
        page.evaluate(f"{S}.regrid({{ grid: '1/16' }})")
        page.wait_for_function(f"{S}.store.get().chops.banks.length > 40", timeout=60000)
        page.wait_for_function("document.querySelectorAll('.bank-seg').length > 40")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 1500)
        self.assertTrue(page.locator(".bank-seg").first.is_visible())
        page.locator(".bank-next").click()
        page.wait_for_function(f"{S}.store.get().bank === 4")
        self.assertEqual(errors + errors2 + errors3, [])


if __name__ == "__main__":
    unittest.main()
