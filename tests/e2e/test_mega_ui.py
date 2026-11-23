"""Real-browser tests for the drum pad mega banks, step C (CHOP-13, contracts §15): the interface, driven through what a person sees.

The owner's words: three levels, SONG / DRUM PAD MEGA BANKS / then drum pad banks; "load up a drum break, start laying down a
break beat, then load up a new song and play the loop and bass and keep my drum loops"; on a song change either (a) a new mega bank
or (b) keep the used pads and bring the new song into the free ones; "i dont want it to completely refresh everytime we load up a song".

Run with a Python that has Playwright installed (like the other browser tests):
    /usr/bin/python3 -m unittest tests/e2e/test_mega_ui.py
MEGA_SHOTS=1 also writes the screenshots in docs/design/shots/mega-*.png (light and dark).
Songs are opened from a temporary copy of two library songs served by a server on a free port with its own media root, so the real
library and port 8960 are never touched. Skips when Playwright or two library songs are missing. Every failed HTTP request is
logged with its body; a test that expects one filters it by name.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_core2 as c2  # noqa: E402  (the harness of the sampler-core tests: songs, ports, page helpers)
from megastate import forget_projects  # noqa: E402
from test_mega import CHECK, MARK, S  # noqa: E402  (the identity marks of the step B tests)

REPO = c2.REPO
MEDIA = c2.MEDIA
SHOTS = os.path.join(REPO, "docs", "design", "shots")
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def song_doc(sid, name):
    with open(os.path.join(MEDIA, sid, name)) as f:
        return json.load(f)


def short(title):
    """The short name views use for a song (views/util.js shortSong): "Artist - Song" -> "Song", at most 16 characters."""
    m = re.sub(r"^.*?(?:\s[—–-]\s+|:\s+)", "", title.strip(), count=1, flags=re.S)
    return (m or title.strip())[:16].strip()


@unittest.skipIf(sync_playwright is None, "playwright is not installed for this Python")
@unittest.skipIf(len(c2.library_songs(2)) < 2, "need two library songs in media/")
class MegaStepC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.A, cls.B = c2.library_songs(2)
        cls.tmp = tempfile.mkdtemp(prefix="choppad-megaui-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.root = os.path.join(cls.tmp, "media")
        cls.pristine = {}
        for sid in (cls.A, cls.B):
            dst = os.path.join(cls.root, sid)
            os.makedirs(dst)
            src = os.path.join(MEDIA, sid)
            for f in os.listdir(src):
                if f in c2.HEAVY:
                    os.symlink(os.path.join(src, f), os.path.join(dst, f))
                elif f.endswith(".json") and f not in ("project.json", "onsets.json"):
                    shutil.copy(os.path.join(src, f), dst)
            cls.pristine[sid] = open(os.path.join(dst, "chops.json")).read()
        cls.port = c2.free_port()
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
            cls.browser = cls.pw.chromium.launch(args=args, executable_path=c2.chrome_path(), headless=True)
        cls.addClassCleanup(cls.browser.close)
        cls.meta = {sid: song_doc(sid, "meta.json") for sid in (cls.A, cls.B)}
        cls.chops = {sid: song_doc(sid, "chops.json") for sid in (cls.A, cls.B)}

    def setUp(self):  # every test starts from no mega banks at all and the original chop tables
        forget_projects(self.root)
        for sid in (self.A, self.B):
            open(os.path.join(self.root, sid, "chops.json"), "w").write(self.pristine[sid])

    # ------------------------------------------------------------------ helpers
    def tab(self, hash_=None, ctx=None, viewport=(1500, 1000), theme=None):
        """A page on the song screen. Returns (page, failed): `failed` lists page errors and every failed request."""
        if ctx is None:
            ctx = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
            self.addCleanup(ctx.close)
        page = ctx.new_page()
        failed = []
        page.on("pageerror", lambda e: failed.append(f"pageerror {e}"))
        page.on("console", lambda m: failed.append(f"console {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("response", lambda r: failed.append(f"{r.status} {r.request.method} {r.url} {(r.request.post_data or '')[:300]}")
                if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)
        if theme:
            page.add_init_script(f"try {{ localStorage.setItem('choppad.theme', '{theme}'); }} catch (e) {{}}")
        page.goto(f"{self.base}/{hash_ or '#/song/' + self.A}")
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty), .no-song:not([hidden])")
        page.evaluate(c2.PAGE_HELPERS)
        return page, failed

    def record_break(self, page, keep_playing=True):
        """Lay down four pads (1 to 4) into the sequence the way a person does: REC, hit Z X C V, REC off. The pattern loops."""
        page.locator('[data-act="rec"]').click()
        page.wait_for_function(f"{S}.seq.state.recording && {S}.seq.state.playing && window.choppad.audio.ctx.state === 'running'", timeout=10000)
        for key in ("KeyZ", "KeyX", "KeyC", "KeyV"):
            page.keyboard.press(key)
            page.wait_for_timeout(330)
        page.locator('[data-act="rec"]').click()
        page.evaluate(f"{S}.remote.flush()")
        pads = sorted({e["pad"] for e in page.evaluate(f"{S}.store.get().project.pattern.events")})
        self.assertEqual(pads, [0, 1, 2, 3], "four pads were recorded")
        if not keep_playing:
            page.locator('[data-act="stop"]').click()
        else:
            self.assertTrue(page.evaluate(f"{S}.seq.state.playing"))

    def click_song(self, page, sid):
        page.locator(f'#sidebar li.song[data-id="{sid}"] .row-open').click()

    def panel(self, page):
        return page.locator(".mchoice:not([hidden])")

    def wait_source(self, page, sid, timeout=60000):
        page.wait_for_function(f"window.choppad.song?.session?.source === {json.dumps(sid)}", timeout=timeout)

    def megas(self):
        return c2.api(self.base, "/api/mega")[1]

    def project(self, mid):
        return c2.api(self.base, f"/api/mega/{mid}")[1]["project"]

    def sid_mega(self, page):
        return page.evaluate(f"{S}.mega")

    def shot(self, page, name, theme=None, clip=None):
        """Screenshots of the interface (MEGA_SHOTS=1), light and dark."""
        if not os.environ.get("MEGA_SHOTS"):
            return
        os.makedirs(SHOTS, exist_ok=True)
        for t in ([theme] if theme else ["light", "dark"]):
            page.evaluate(f"document.documentElement.dataset.theme = '{t}'")
            page.wait_for_timeout(250)
            page.screenshot(path=os.path.join(SHOTS, f"mega-{name}-{t}.png"), clip=clip)
        page.evaluate("document.documentElement.dataset.theme = 'light'")

    # ------------------------------------------------------------------ a. the library
    def test_a_the_library_shows_songs_and_mega_banks_and_manages_them(self):
        page, failed = self.tab()
        lib = page.locator("#sidebar")
        self.assertEqual(lib.locator("#lib-h-songs").inner_text().lower(), "songs")
        self.assertEqual(lib.locator("#lib-h-megas").inner_text().lower(), "drum pad mega banks")
        self.assertEqual(lib.locator("li.song").count(), 2)
        rows = lib.locator("li.mega")
        self.assertEqual(rows.count(), 1)
        first = rows.first
        self.assertIn("active", first.get_attribute("class"))
        self.assertEqual(first.locator(".mega-tag").inner_text(), "OPEN", "the open mega bank is marked in words, not only in colour")
        self.assertEqual(first.locator(".mega-open").get_attribute("aria-current"), "true")
        self.assertEqual(first.locator(".title").inner_text(), self.meta[self.A]["title"], "named after its song")
        meta = first.locator(".meta").inner_text()
        self.assertIn(self.meta[self.A]["title"], meta, "it says which song it has as its source")
        self.assertIn("0 pads used", meta)
        mid = self.sid_mega(page)
        # pads used and last change follow the work
        self.record_break(page)
        page.wait_for_function("document.querySelector('#sidebar li.mega .meta').textContent.includes('4 pads used')", timeout=10000)
        self.shot(page, "library-lists")
        # rename, inline
        first.locator(".mega-ren").click()
        box = first.locator(".mega-name")
        self.assertTrue(box.is_visible())
        self.assertEqual(box.get_attribute("aria-label"), f"New name for drum pad mega bank {self.meta[self.A]['title']}")
        box.fill("My break")
        box.press("Enter")
        page.wait_for_function("document.querySelector('#sidebar li.mega .title')?.textContent === 'My break'", timeout=10000)
        self.assertEqual([m["name"] for m in self.megas()], ["My break"])
        self.assertEqual(page.locator(".s-mega").inner_text(), "My break", "the header names the renamed bank")
        # Esc leaves the name alone
        first.locator(".mega-ren").click()
        first.locator(".mega-name").fill("zzz")
        first.locator(".mega-name").press("Escape")
        page.wait_for_function("document.querySelector('#sidebar li.mega .title')?.textContent === 'My break'", timeout=10000)
        # + New mega bank: an empty one on the current song, opened (a real change of mega bank, no page reload)
        origin = page.evaluate("performance.timeOrigin")
        page.evaluate("window.__survive = 'same page'")
        lib.locator(".mega-new").click()
        page.wait_for_function(f"window.choppad.song?.session?.mega !== {json.dumps(mid)}", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        self.assertEqual(page.evaluate("performance.timeOrigin"), origin)
        self.assertEqual(page.evaluate("window.__survive"), "same page", "the page was not reloaded")
        mid2 = self.sid_mega(page)
        self.assertEqual(page.evaluate(f"{S}.source"), self.A)
        self.assertEqual(page.evaluate(f"Object.keys({S}.store.get().project.overrides).length + {S}.store.get().project.pattern.events.length"), 0, "empty")
        page.wait_for_function("document.querySelectorAll('#sidebar li.mega').length === 2", timeout=10000)
        names = {m["id"]: m["name"] for m in self.megas()}
        self.assertEqual(set(names), {mid, mid2})
        active = lib.locator("li.mega.active")
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.get_attribute("data-id"), mid2)
        # click the other one: it opens, the marks move
        lib.locator(f'li.mega[data-id="{mid}"] .mega-open').click()
        page.wait_for_function(f"window.choppad.song?.session?.mega === {json.dumps(mid)}", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        self.assertEqual(page.evaluate(f"{S}.store.get().project.pattern.events.length"), 4, "its pattern is there")
        page.wait_for_function(f"document.querySelector('#sidebar li.mega.active')?.dataset.id === {json.dumps(mid)}", timeout=10000)
        # remove: asks first, says "moved to trash", and the folder goes to the trash folder
        row2 = lib.locator(f'li.mega[data-id="{mid2}"]')
        row2.locator(".mega-del").click()
        ask = row2.locator(".mega-ask")
        self.assertIn("to trash?", ask.inner_text())
        ask.locator(".mega-no").click()
        self.assertEqual(len(self.megas()), 2, "Keep it: nothing was removed")
        row2.locator(".mega-del").click()
        row2.locator(".mega-yes").click()
        page.wait_for_function(f"!document.querySelector('#sidebar li.mega[data-id={json.dumps(mid2)}]')", timeout=10000)
        self.assertIn("moved to trash", lib.locator(".mega-note").inner_text())
        self.assertEqual([m["id"] for m in self.megas()], [mid])
        trash = os.path.join(self.root, "_mega", ".trash")
        self.assertTrue(any(n.startswith(mid2) for n in os.listdir(trash)), os.listdir(trash))
        self.assertTrue(os.path.isfile(os.path.join(trash, [n for n in os.listdir(trash) if n.startswith(mid2)][0], "mega.json")), "moved, not deleted")
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ b. the owner's scenario, option (b)
    def test_b_a_break_laid_down_on_the_pads_survives_the_next_song_and_the_page_is_not_rebuilt(self):
        page, failed = self.tab()
        self.record_break(page)
        page.evaluate(MARK)
        before = page.evaluate(f"({{ nav: performance.getEntriesByType('navigation').length, origin: performance.timeOrigin, mounts: choppad.stats.mounts,"
                               f" seq: {S}.seq.state.bpm, tempo: {S}.tempoBpm, user: {S}.store.get().project.tempo?.bpm ?? null }})")
        self.click_song(page, self.B)
        panel = self.panel(page)
        panel.wait_for(timeout=10000)
        text = panel.inner_text()
        self.assertIn(f"New mega bank for {short(self.meta[self.B]['title'])}", text)
        self.assertIn("Keep current drum pads", text)
        self.assertIn(f"Keep the 4 pads you used, bring {short(self.meta[self.B]['title'])} into the free pads", text)
        self.assertIn("Cancel", text)
        self.assertEqual(panel.get_attribute("aria-modal"), "false", "not a modal")
        self.assertEqual(page.evaluate(f"document.activeElement.closest('.mchoice') !== null"), True, "focus moved into the panel")
        self.shot(page, "choice-panel")
        # the pattern keeps playing while the question is open, and nothing has changed yet
        for _ in range(6):
            self.assertTrue(page.evaluate(f"{S}.seq.state.playing"), "the pattern plays while the panel is open")
            page.wait_for_timeout(250)
        self.assertEqual(page.evaluate(f"{S}.source"), self.A)
        # the panel does not cover the pads
        pads = page.locator(".pads").bounding_box()
        box = panel.bounding_box()
        self.assertTrue(box["x"] + box["width"] <= pads["x"] or box["x"] >= pads["x"] + pads["width"] or box["y"] + box["height"] <= pads["y"] or box["y"] >= pads["y"] + pads["height"], (box, pads))
        panel.locator('[data-choice="b"]').click()
        self.wait_source(page, self.B)
        line = page.locator(".mline:not([hidden]) .ml-text")
        line.wait_for(timeout=10000)
        self.assertRegex(line.inner_text(), rf"^Kept 4 pads from {re.escape(short(self.meta[self.A]['title']))}; \d+ free pads now play {re.escape(short(self.meta[self.B]['title']))}\.$")
        self.assertEqual(self.panel(page).count(), 0)
        self.assertTrue(page.evaluate(f"{S}.seq.state.playing"))
        # pads 1-4 are marked kept and say where they came from; the free pads play the new song
        for p in range(4):
            pad = page.locator(f".pad[data-pad='{p}']")
            self.assertIn("kept", pad.get_attribute("class"))
            self.assertEqual(pad.locator(".pad-tag").inner_text(), "KEPT")
            self.assertEqual(pad.locator(".pad-label").text_content(), short(self.meta[self.A]["title"])[:9], "the song it came from")
            self.assertIn(self.meta[self.A]["title"], pad.get_attribute("title"))
            self.assertIn(f"kept from {self.meta[self.A]['title']}", pad.get_attribute("aria-label"))
        free = page.locator(".pad[data-pad='8']")
        self.assertNotIn("kept", free.get_attribute("class"))
        self.assertTrue(free.locator(".pad-tag").is_hidden())
        self.assertEqual(page.evaluate(f"{S}.engine.getSlice(0, 8).start"), self.chops[self.B]["chops"][self.chops[self.B]["banks"][0][8]]["start"])
        self.shot(page, "kept-pads")
        # the same objects and page nodes, no navigation, no mount
        got = page.evaluate(CHECK)
        self.assertTrue(all(got["objects"].values()), got["objects"])
        for name, n in got["nodes"].items():
            if n is not None:
                self.assertEqual(n, {"inDocument": True, "marked": True}, name)
        self.assertEqual(got["page"]["survive"], "same page")
        self.assertEqual(got["page"]["origin"], before["origin"])
        self.assertEqual(got["page"]["navEntries"], before["nav"])
        self.assertEqual(got["mounts"], before["mounts"])
        self.assertEqual(got["destroys"], 0)
        self.assertEqual(got["shows"]["title"], self.meta[self.B]["title"])
        # the tempo did not move
        after = page.evaluate(f"({{ seq: {S}.seq.state.bpm, user: {S}.store.get().project.tempo?.bpm }})")
        self.assertEqual(after["seq"], before["seq"], "the sequencer's tempo is unchanged")
        self.assertAlmostEqual(after["user"], before["tempo"], places=3)
        self.assertEqual(len(self.megas()), 1, "option (b) makes no new mega bank")
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ c. option (a)
    def test_c_choosing_a_makes_a_new_mega_bank_and_leaves_the_first_one_alone(self):
        page, failed = self.tab()
        self.record_break(page)
        mid = self.sid_mega(page)
        old_events = page.evaluate(f"{S}.store.get().project.pattern.events")
        origin = page.evaluate("performance.timeOrigin")
        self.click_song(page, self.B)
        self.panel(page).wait_for(timeout=10000)
        page.keyboard.press("a")   # the keyboard works too
        page.wait_for_function(f"window.choppad.song?.session?.mega !== {json.dumps(mid)}", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        self.assertEqual(page.evaluate("performance.timeOrigin"), origin, "not a page reload")
        mid2 = self.sid_mega(page)
        self.assertEqual(page.evaluate(f"{S}.source"), self.B)
        self.assertEqual(page.evaluate(f"{S}.store.get().project.pattern.events.length"), 0, "the new mega bank starts empty")
        self.assertEqual(page.evaluate(f"Object.keys({S}.store.get().project.overrides).length"), 0)
        listed = {m["id"]: m for m in self.megas()}
        self.assertEqual(set(listed), {mid, mid2})
        self.assertEqual(listed[mid2]["source"], self.B)
        self.assertEqual(listed[mid]["source"], self.A)
        self.assertEqual(listed[mid]["used_pads"], 4)
        self.assertEqual(page.locator(".s-title").inner_text(), self.meta[self.B]["title"])
        self.assertEqual(page.locator("#sidebar li.mega.active").get_attribute("data-id"), mid2)
        self.assertIn("New mega bank", page.locator(".mline:not([hidden]) .ml-text").inner_text())
        self.assertIn("is untouched", page.locator(".mline:not([hidden]) .ml-text").inner_text())
        # the first one is exactly as it was: pattern and pads (checked on the server, then reopened from the list)
        old = self.project(mid)
        self.assertEqual(old["pattern"]["events"], old_events)
        self.assertEqual(old["overrides"], {}, "nothing was kept in it: it was never switched")
        page.locator(f'#sidebar li.mega[data-id="{mid}"] .mega-open').click()
        page.wait_for_function(f"window.choppad.song?.session?.mega === {json.dumps(mid)}", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        self.assertEqual(page.evaluate(f"{S}.source"), self.A)
        self.assertEqual(page.evaluate(f"{S}.store.get().project.pattern.events"), old_events)
        self.assertEqual(sorted({e["pad"] for e in old_events}), [0, 1, 2, 3])
        self.assertEqual(page.locator(".s-title").inner_text(), self.meta[self.A]["title"])
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ d. nothing to lose
    def test_d_a_mega_bank_without_used_pads_switches_at_once(self):
        page, failed = self.tab()
        self.assertEqual(page.evaluate(f"Object.keys({S}.store.get().project.overrides).length + {S}.store.get().project.pattern.events.length"), 0)
        page.evaluate("window.__panels = 0; new MutationObserver(() => { if (document.querySelector('.mchoice:not([hidden])')) window.__panels++; }).observe(document.body, { subtree: true, childList: true, attributes: true })")
        self.click_song(page, self.B)
        self.wait_source(page, self.B)
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate("window.__panels"), 0, "no question was asked")
        self.assertEqual(page.evaluate(f"{S}.mega"), self.megas()[0]["id"])
        self.assertEqual(len(self.megas()), 1)
        self.assertIn("Now chopping", page.locator(".mline:not([hidden]) .ml-text").inner_text())
        self.assertEqual(page.locator(".s-title").inner_text(), self.meta[self.B]["title"])
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ e. remember my choice
    def test_e_remembering_the_choice_means_the_next_switch_does_not_ask(self):
        page, failed = self.tab()
        self.record_break(page)
        self.click_song(page, self.B)
        panel = self.panel(page)
        panel.wait_for(timeout=10000)
        panel.locator(".mc-rem").check()
        panel.locator('[data-choice="b"]').click()
        self.wait_source(page, self.B)
        self.assertEqual(page.locator(".mline .ml-forget").count(), 0, "the first time it was asked: no 'ask again' yet")
        page.evaluate("window.__panels = 0; new MutationObserver(() => { if (document.querySelector('.mchoice:not([hidden])')) window.__panels++; }).observe(document.body, { subtree: true, childList: true, attributes: true })")
        self.click_song(page, self.A)
        self.wait_source(page, self.A)
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate("window.__panels"), 0, "the second switch did not ask")
        self.assertRegex(page.locator(".mline:not([hidden]) .ml-text").inner_text(), r"^Kept 4 pads; \d+ free pads now play")
        # and the way back: "Ask me again"
        page.locator(".mline .ml-forget").click()
        self.click_song(page, self.B)
        self.panel(page).wait_for(timeout=10000)
        page.keyboard.press("Escape")
        self.assertEqual(self.panel(page).count(), 0)
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ f. cancel and Esc
    def test_f_cancel_and_escape_change_nothing(self):
        page, failed = self.tab()
        self.record_break(page)
        page.evaluate(MARK)
        mid = self.sid_mega(page)
        project = page.evaluate(f"JSON.stringify({S}.store.get().project)")
        for how in ("cancel", "escape"):
            self.click_song(page, self.B)
            self.panel(page).wait_for(timeout=10000)
            if how == "cancel":
                self.panel(page).locator(".mc-cancel").click()
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            self.assertEqual(self.panel(page).count(), 0, how)
            self.assertEqual(page.evaluate(f"{S}.source"), self.A, how)
            self.assertEqual(page.evaluate(f"{S}.mega"), mid)
            self.assertEqual(page.locator("#sidebar li.song.active").get_attribute("data-id"), self.A, "the library still marks the open song")
            self.assertEqual(page.locator(".s-title").inner_text(), self.meta[self.A]["title"])
            self.assertTrue(page.evaluate(f"{S}.seq.state.playing"), "the pattern was never interrupted")
            self.assertEqual(page.evaluate(f"JSON.stringify({S}.store.get().project)"), project, "nothing in the project changed")
            self.assertIn(f"/song/{self.A}", page.evaluate("location.hash"))
            self.assertEqual(page.locator(".mline:not([hidden])").count(), 0, "no result line: nothing happened")
            self.assertIn("row-open", page.evaluate("document.activeElement.className"), "focus went back to where it was (the song row)")
        got = page.evaluate(CHECK)
        self.assertTrue(all(got["objects"].values()))
        self.assertEqual(got["mounts"], 1)
        self.assertEqual(len(self.megas()), 1)
        self.assertEqual(self.project(mid)["overrides"], {})
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ g. KEEP and RELEASE
    def goto_lcd(self, page, page_key, sub_name):
        for _ in range(14):
            key = page.locator(f'.mode[data-mode="{page_key}"]')
            if key.get_attribute("aria-pressed") == "true" and sub_name.upper() in page.locator(".lcd-sub").inner_text().upper():
                return
            key.click()
        self.fail(f"LCD page {page_key} has no sub-page {sub_name}")

    def test_g_the_keep_key_and_release_on_the_lcd(self):
        page, failed = self.tab()
        pad5 = page.locator(".pad[data-pad='4']")
        page.keyboard.press("KeyA")   # pad 5: plays it and selects it
        self.assertEqual(page.evaluate(f"{S}.store.get().selected"), 4)
        key = page.locator(".keep-key")
        self.assertEqual(key.is_enabled(), True)
        self.assertIn("Keep pad 5", key.get_attribute("aria-label"))
        key.click()
        page.wait_for_function(f"{S}.store.get().project.overrides['0:4']?.kept === true", timeout=20000)
        self.assertIn("kept", pad5.get_attribute("class"))
        self.assertTrue(key.is_disabled(), "already kept")
        self.assertIn("already kept", key.get_attribute("aria-label"))
        o = page.evaluate(f"{S}.store.get().project.overrides['0:4']")
        self.assertEqual(o["from"]["song"], self.A)
        self.assertTrue(page.evaluate(f"!!{S}.engine.getPadBuffer(0, 4)"), "bound to its own sample")
        self.assertEqual(self.project(self.sid_mega(page))["overrides"]["0:4"]["kept"], True)
        # the LCD PAD page: kept, source song, original position, level
        self.goto_lcd(page, "PAD", "kept")
        info = page.locator(".lcd-body .lcd-info").inner_text().upper()
        self.assertIn("PAD 5", info)
        self.assertIn("KEPT", info)
        self.assertNotIn("NOT KEPT", info)
        self.assertIn(self.meta[self.A]["title"].upper(), info, "the source song")
        self.assertRegex(info, r"AT \d+:\d\d\.\d–\d+:\d\d\.\d", "the original position")
        self.assertRegex(info, r"LEVEL [+-]\d+\.\d DB")
        self.shot(page, "lcd-kept-pad")
        # RELEASE: the pad is not in the sequence, so it goes at once and shows the song's own chop again
        page.locator(".kp-release").click()
        page.wait_for_function(f"!{S}.store.get().project.overrides['0:4']", timeout=10000)
        self.assertNotIn("kept", pad5.get_attribute("class"))
        self.assertEqual(self.panel(page).count(), 0, "no question for a pad the sequence does not play")
        self.assertIn("NOT KEPT", page.locator(".lcd-body .lcd-info").inner_text().upper())
        self.assertEqual(page.evaluate(f"{S}.engine.getSlice(0, 4).start"), self.chops[self.A]["chops"][self.chops[self.A]["banks"][0][4]]["start"])
        self.assertTrue(page.locator(".kp-release").is_disabled())
        # a pad the sequence plays: record pad 1, keep it with the LCD key, then release it
        self.record_break(page)
        page.keyboard.press("KeyZ")
        self.assertEqual(page.evaluate(f"{S}.store.get().selected"), 0)
        page.locator(".kp-keep").click()
        page.wait_for_function(f"{S}.store.get().project.overrides['0:0']?.kept === true", timeout=20000)
        page.locator(".kp-release").click()
        panel = self.panel(page)
        panel.wait_for(timeout=10000)
        self.assertIn("Release pad 1?", panel.inner_text())
        self.assertIn("hit", panel.inner_text(), "it says the sequence plays this pad")
        self.assertIn("kept", page.evaluate(f"document.querySelector('.pad[data-pad=\"0\"]').className"), "nothing happens before the answer")
        self.shot(page, "release-question")
        panel.locator(".mc-cancel").click()
        self.assertTrue(page.evaluate(f"{S}.store.get().project.overrides['0:0']?.kept === true"), "Keep it: still kept")
        page.locator(".kp-release").click()
        self.panel(page).wait_for(timeout=10000)
        page.keyboard.press("r")
        page.wait_for_function(f"!{S}.store.get().project.overrides['0:0']", timeout=10000)
        self.assertEqual(self.panel(page).count(), 0)
        self.assertEqual(page.evaluate(f"{S}.store.get().project.pattern.events.filter((e) => e.pad === 0).length > 0"), True, "its hits are still in the sequence")
        self.assertEqual(failed, [], failed)

    def test_g2_the_bank_overview_counts_kept_used_and_free(self):
        page, failed = self.tab()
        self.record_break(page, keep_playing=False)
        page.keyboard.press("KeyA")
        page.locator(".keep-key").click()
        page.wait_for_function(f"{S}.store.get().project.overrides['0:4']?.kept === true", timeout=20000)
        self.goto_lcd(page, "MAIN", "mega banks")
        rows = page.locator(".bankov button.bo-row")
        self.assertEqual(rows.count(), len(self.chops[self.A]["banks"]))
        first = rows.first
        n_pads = len(self.chops[self.A]["banks"][0])
        self.assertEqual(first.get_attribute("aria-label"), f"Bank A, 1 of {rows.count()}: 1 kept, 4 used, {n_pads - 5} free")
        self.assertEqual(first.get_attribute("aria-current"), "true")
        info = page.locator(".lcd-body .lcd-info").inner_text()
        self.assertIn(self.meta[self.A]["title"].upper(), info.upper())
        self.assertIn("MEGA BANK", info)
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ h. a mega bank with no source song
    def test_h_a_mega_bank_with_no_song_opens_and_plays_its_kept_pads(self):
        page, failed = self.tab()
        self.record_break(page, keep_playing=False)
        self.click_song(page, self.B)
        self.panel(page).wait_for(timeout=10000)
        page.keyboard.press("b")
        self.wait_source(page, self.B)
        mid = self.sid_mega(page)
        page.evaluate(f"{S}.remote.flush()")
        # the song is gone: the mega bank has no source (the server keeps the kept samples)
        path = os.path.join(self.root, "_mega", mid, "mega.json")
        doc = json.load(open(path))
        doc["source"] = None
        json.dump(doc, open(path, "w"))
        page2, f2 = self.tab(f"#/mega/{mid}")
        self.assertIsNone(page2.evaluate(f"{S}.source"))
        self.assertEqual(page2.evaluate("location.hash"), f"#/mega/{mid}")
        # the song-dependent parts say what to do
        for sel in (".vid .no-song", ".wave .no-song"):
            self.assertTrue(page2.locator(sel).is_visible(), sel)
            self.assertIn("pick a song to chop", page2.locator(sel).inner_text().lower())
        self.assertEqual(page2.locator(".lcd-main .lcd-title").text_content(), "Pick a song to chop")
        self.assertEqual(page2.locator(".s-title").inner_text(), "no song")
        self.shot(page2, "no-source")
        # kept pads are there and play, the sequence plays
        self.assertEqual(page2.evaluate(f"Object.entries({S}.store.get().project.overrides).filter(([k, o]) => o.kept).map(([k]) => k).sort()"), ["0:0", "0:1", "0:2", "0:3"])
        self.assertEqual(page2.locator(".pad.kept").count(), 4)
        page2.wait_for_function(f"[0, 1, 2, 3].every((p) => !!{S}.engine.getPadBuffer(0, p))", timeout=20000)
        page2.evaluate("""() => {
            const s = window.choppad.song.session, an = window.choppad.audio.analyser, buf = new Float32Array(an.fftSize);
            window.__trig = 0; window.__peak = 0;
            const orig = s.engine.trigger;
            s.engine.trigger = function (...a) { window.__trig++; return orig.apply(this, a); };
            setInterval(() => { an.getFloatTimeDomainData(buf); for (const v of buf) window.__peak = Math.max(window.__peak, Math.abs(v)); }, 50);
        }""")
        page2.locator('[data-act="pattern"]').click()
        page2.wait_for_function(f"{S}.seq.state.playing", timeout=10000)
        page2.wait_for_function("window.__trig >= 4", timeout=15000)
        self.assertGreater(page2.evaluate("window.__peak"), 0.01, "the kept pads are heard")
        page2.keyboard.press("KeyZ")
        self.assertEqual(page2.evaluate(f"{S}.engine.getSlice(0, 0).kept"), True)
        page2.locator('[data-act="stop"]').click()
        # the pointer to the SONGS list works, and choosing a song fills the free pads while the kept ones stay
        page2.locator(".vid .no-song-go").click()
        self.assertTrue(page2.evaluate("!!document.activeElement.closest('#sidebar .songs')"))
        page2.locator(f'#sidebar li.song[data-id="{self.A}"] .row-open').click()
        self.panel(page2).wait_for(timeout=10000)
        page2.keyboard.press("b")
        self.wait_source(page2, self.A)
        self.assertTrue(page2.locator(".no-song").first.is_hidden())
        self.assertGreater(page2.evaluate(f"{S}.peaks.duration_s"), 1)
        self.assertEqual(page2.locator(".pad.kept").count(), 4)
        self.assertEqual(page2.evaluate(f"{S}.engine.getSlice(0, 8).start"), self.chops[self.A]["chops"][self.chops[self.A]["banks"][0][8]]["start"])
        self.assertEqual(failed + f2, [e for e in failed + f2 if False], failed + f2)

    # ------------------------------------------------------------------ i. the header path, LCD MAIN, tempo
    def test_i_the_header_path_and_the_lcd_name_the_three_levels_and_follow_every_change(self):
        page, failed = self.tab()
        title_a, title_b = self.meta[self.A]["title"], self.meta[self.B]["title"]
        nb = len(self.chops[self.A]["banks"])
        path = page.locator("nav.path")
        self.assertIn("song", path.get_attribute("aria-label").lower())
        self.assertEqual(path.locator(".s-title").inner_text(), title_a)
        self.assertEqual(path.locator(".s-mega").inner_text(), title_a)
        self.assertEqual(path.locator(".s-bank").inner_text(), f"A · 1/{nb}")
        labels = [e.inner_text() for e in path.locator("small").all()]
        self.assertEqual(labels, ["SONG", "DRUM PAD MEGA BANK", "DRUM PAD BANK"], "the owner's names")
        # until the first change of song the bank follows its song's tempo
        self.assertEqual(page.locator(".s-meta .s-tempo").inner_text(), f"TEMPO {self.chops[self.A]['bpm']:.1f} · following {short(title_a)}")
        self.shot(page, "header-path", clip={"x": 0, "y": 0, "width": 1500, "height": 60})
        # the LCD MAIN page shows the same three names
        self.goto_lcd(page, "MAIN", "song")
        self.assertEqual(page.locator(".lcd-main .lcd-title").text_content(), title_a)
        self.assertEqual(page.locator(".lcd-main .mega-line").text_content(), f"MEGA BANK {title_a} ▸ BANK A · 1/{nb}")
        # each change updates both: bank, mega bank name, song
        page.locator('.bkey[data-bank="1"]').click()
        self.assertEqual(path.locator(".s-bank").inner_text(), f"B · 2/{nb}")
        self.assertIn(f"BANK B · 2/{nb}", page.locator(".lcd-main .mega-line").text_content())
        page.locator("#sidebar li.mega .mega-ren").click()
        page.locator("#sidebar li.mega .mega-name").fill("Funk bed")
        page.locator("#sidebar li.mega .mega-name").press("Enter")
        page.wait_for_function("document.querySelector('.s-mega').textContent === 'Funk bed'", timeout=10000)
        page.wait_for_function("document.querySelector('.lcd-main .mega-line').textContent.includes('Funk bed')", timeout=5000)
        self.click_song(page, self.B)    # no used pads: switches at once
        self.wait_source(page, self.B)
        self.assertEqual(path.locator(".s-title").inner_text(), title_b)
        self.assertEqual(page.locator(".lcd-main .lcd-title").text_content(), title_b)
        self.assertEqual(path.locator(".s-mega").inner_text(), "Funk bed", "the mega bank keeps its name")
        # each segment opens its picker
        path.locator('[data-seg="song"]').click()
        self.assertTrue(page.evaluate("!!document.activeElement.closest('#sidebar .songs')"))
        path.locator('[data-seg="mega"]').click()
        self.assertTrue(page.evaluate("!!document.activeElement.closest('#sidebar .megas')"))
        path.locator('[data-seg="bank"]').click()
        self.assertTrue(page.evaluate("document.activeElement.classList.contains('bkey')"))
        # tempo: following the song until the user sets one, then "yours"
        head = page.locator(".s-meta .s-tempo")
        # (the first change of song fixed the tempo the bank was running at: it is the user's from then on)
        self.assertEqual(head.inner_text(), f"TEMPO {self.chops[self.A]['bpm']:.1f} · yours")
        self.assertEqual(page.locator(".lcd-main .lcd-big small").first.text_content(), "TEMPO · YOURS")
        page.evaluate(f"{S}.setTempo(93)")
        page.wait_for_function("document.querySelector('.s-tempo').textContent === 'TEMPO 93.0 · yours'", timeout=10000)
        self.assertEqual(page.locator(".lcd-main .lcd-big small").first.text_content(), "TEMPO · YOURS")
        self.assertEqual(page.locator(".lcd-main .bpm").inner_text(), "93.0")
        self.goto_lcd(page, "GRID", "grid")
        self.assertIn(f"CHOP TEMPO {self.chops[self.B]['bpm']:.1f}", page.locator(".lcd-body .lcd-info").inner_text().upper())
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ j. layout, names, keyboard, themes
    def test_j_no_horizontal_scroll_every_control_is_named_and_reachable_both_themes(self):
        named = [".path .seg", "#sidebar .row-open", "#sidebar .mega-ren", "#sidebar .mega-del", "#sidebar .mega-new", ".keep-key",
                 ".mchoice .mc-opt", ".mchoice .mc-cancel", ".mchoice .mc-rem"]
        NAME = """(sel) => [...document.querySelectorAll(sel)].map((e) => {
            const lab = e.labels?.[0]?.textContent?.trim();
            return { sel, name: (e.getAttribute('aria-label') || lab || e.textContent || e.title || '').trim(), tab: e.tabIndex, disabled: !!e.disabled }; })"""
        for vp in ((1500, 1000), (1920, 1080)):
            for theme in ("light", "dark"):
                page, failed = self.tab(viewport=vp, theme=theme)
                self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), theme)
                self.record_break(page)
                self.click_song(page, self.B)
                self.panel(page).wait_for(timeout=10000)
                over = page.evaluate("({ w: document.documentElement.scrollWidth, bw: document.body.scrollWidth, iw: window.innerWidth })")
                self.assertLessEqual(over["w"], over["iw"], f"horizontal scroll {vp} {theme}: {over}")
                self.assertLessEqual(over["bw"], over["iw"], f"horizontal scroll {vp} {theme}: {over}")
                rect = self.panel(page).bounding_box()
                self.assertTrue(rect["x"] >= 0 and rect["x"] + rect["width"] <= vp[0] and rect["y"] >= 0 and rect["y"] + rect["height"] <= vp[1], rect)
                for sel in named:
                    items = page.evaluate(NAME, sel)
                    self.assertTrue(items, f"{sel} is on the page")
                    for it in items:
                        self.assertTrue(it["name"], f"{sel} has an accessible name")
                        self.assertGreaterEqual(it["tab"], 0, f"{sel} is reachable by keyboard")
                self.shot(page, f"layout-{vp[0]}", theme=theme)
                # the panel with the keyboard: Tab stays inside the reachable controls, Esc closes
                page.keyboard.press("Tab")
                self.assertTrue(page.evaluate("document.activeElement.closest('.mchoice') !== null"), "Tab goes from the first option to the next control of the panel")
                page.keyboard.press("Escape")
                self.assertEqual(self.panel(page).count(), 0)
                # keyboard: from the last song row Tab reaches the mega bank rows
                page.evaluate("document.querySelector('#sidebar .songs li:last-child .row-open').focus()")
                reached = False
                for _ in range(8):
                    page.keyboard.press("Tab")
                    if page.evaluate("document.activeElement.classList.contains('mega-open')"):
                        reached = True
                        break
                self.assertTrue(reached, "the mega bank list is reachable with Tab")
                page.keyboard.press("Enter")   # the open bank: nothing to do, no error
                # the empty library state and the keep key at both sizes do not overflow either
                over = page.evaluate("({ w: document.documentElement.scrollWidth, iw: window.innerWidth })")
                self.assertLessEqual(over["w"], over["iw"])
                self.assertEqual(failed, [], failed)


if __name__ == "__main__":
    unittest.main()
