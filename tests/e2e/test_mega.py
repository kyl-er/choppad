"""Real-browser tests for the drum pad mega banks, step B (CHOP-13, contracts §15): the session outlives the song.

The owner's words: "i dont want it to completely refresh everytime we load up a song". Choosing another song while a mega bank
is open must not rebuild the page. These tests prove it by object and node identity, with a pattern playing across the switch,
and prove what the switch does to the pads, the tempo, other tabs, a failing load and a reload.

Run with a Python that has Playwright installed (like the other browser tests):
    /usr/bin/python3 -m unittest tests/e2e/test_mega.py
Songs are opened from a temporary copy of two library songs (audio/video linked, json copied) served by a server on a free port
with its own media root, so the real library and port 8960 are never touched. Skips when Playwright or two library songs are
missing. Every failed HTTP request is logged with its body; a test that expects one filters it by name.
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
import test_core2 as c2  # noqa: E402  (the harness of the sampler-core tests: songs, ports, page helpers)
from megastate import forget_projects  # noqa: E402

REPO = c2.REPO
MEDIA = c2.MEDIA
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

S = "window.choppad.song.session"
# four pad hits, two beats apart, on bank 0 (pads 1 to 4), an 8-beat loop: "a drum break laid down on the pads"
BREAK = [{"beat": 2 * i, "pad": i, "bank": 0, "vel": 1, "pass": -1} for i in range(4)]
# what to remember about the page before a switch, and what must still be true after it
MARK = """() => {
  const s = window.choppad.song.session, c = window.choppad;
  window.__survive = 'same page';
  window.__marks = { ctx: c.audio.ctx, master: c.audio.master, analyser: c.audio.analyser, session: s, engine: s.engine, seq: s.seq,
                     store: s.store, video: s.video, chop: c.song };
  const q = (sel) => document.querySelector(sel);
  const nodes = { sheet: q('.sheet'), header: q('header.hd'), library: q('[data-slot=library]'), libraryHost: q('#sidebar'), pads: q('.pads'),
    banks: q('.banks'), lcd: q('.lcd'), keypad: q('.keypad'), wheel: q('.wk.inc'), transport: q('[data-slot=transport]'), steps: q('.stepgrid'),
    vox: q('[data-slot=vox] canvas') || q('[data-slot=vox]'), vu: q('[data-slot=vu]'), knobs: q('[data-slot=knobs]'), video: q('.vid video'),
    wave: q('.wave'), wavestrip: q('[data-slot=wavestrip]'), platter: q('.vin') };
  window.__nodes = nodes;
  for (const [k, n] of Object.entries(nodes)) { if (n) n.__mark = 'mark-' + k; }
  return Object.fromEntries(Object.entries(nodes).map(([k, n]) => [k, !!n]));
}"""
CHECK = """() => {
  const m = window.__marks, s = window.choppad.song.session, c = window.choppad;
  const q = (sel) => document.querySelector(sel);
  const nav = performance.getEntriesByType('navigation');
  return {
    objects: { ctx: c.audio.ctx === m.ctx, master: c.audio.master === m.master, analyser: c.audio.analyser === m.analyser,
               session: s === m.session, engine: s.engine === m.engine, seq: s.seq === m.seq, store: s.store === m.store, video: s.video === m.video,
               chopView: c.song === m.chop, sessionViaWindow: window.choppad.song.session === m.session },
    nodes: Object.fromEntries(Object.entries(window.__nodes).map(([k, n]) => [k, !n ? null : { inDocument: document.body.contains(n), marked: n.__mark === 'mark-' + k }])),
    page: { survive: window.__survive, origin: performance.timeOrigin, navEntries: nav.length, navType: nav[0]?.type, loadEnd: nav[0]?.loadEventEnd },
    mounts: c.stats.mounts, destroys: c.stats.destroys, switches: c.stats.switches,
    shows: { title: q('.s-title')?.textContent, lcdTitle: q('.lcd-title')?.textContent, hash: location.hash, videoSrc: s.video.src, source: s.source,
             banksInStrip: document.querySelectorAll('.banks-track .bank-seg').length, platter: [...document.querySelectorAll('.p-lt.s')].map((e) => e.textContent),
             waveDur: s.peaks.duration_s, sheetSource: q('.sheet')?.dataset.source, videoIsInMonitor: !!q('.vid .frame video') && q('.vid .frame video') === s.video },
  };
}"""
# a recorder that samples the page every 100 ms across a switch
SAMPLER = """() => {
  const s = window.choppad.song.session, an = window.choppad.audio.analyser, buf = new Float32Array(an.fftSize);
  const log = (window.__log = []);
  let trig = 0, flash = 0;
  const orig = s.engine.trigger;
  s.engine.trigger = function (...a) { trig++; return orig.apply(this, a); };   // every scheduled hit (live and sequenced) goes through here
  s.on('flash', () => { flash++; });
  window.__ready = false; window.__failed = null; window.__tClick = null; window.__tReady = null;
  s.on('source', () => { window.__ready = true; window.__tReady = performance.now(); });
  s.on('source-error', (e) => { window.__failed = e.detail.error; });
  document.addEventListener('click', () => { if (window.__tClick === null) window.__tClick = performance.now(); }, true);
  window.__sampler = setInterval(() => {
    an.getFloatTimeDomainData(buf);
    let r = 0; for (let i = 0; i < buf.length; i++) r += buf[i] * buf[i];
    log.push({ t: performance.now(), playing: s.seq.state.playing, ctxState: window.choppad.audio.ctx.state, trig, flash, rms: Math.sqrt(r / buf.length), src: s.source });
  }, 100);
}"""


def song_doc(sid, name):
    with open(os.path.join(MEDIA, sid, name)) as f:
        return json.load(f)


@unittest.skipIf(sync_playwright is None, "playwright is not installed for this Python")
@unittest.skipIf(len(c2.library_songs(2)) < 2, "need two library songs in media/")
class MegaStepB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.A, cls.B = c2.library_songs(2)
        cls.tmp = tempfile.mkdtemp(prefix="choppad-mega-")
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
    def tab(self, hash_=None, ctx=None):
        """A page on the song screen. Returns (page, failed) where `failed` lists page errors and every failed request."""
        if ctx is None:
            ctx = self.browser.new_context(viewport={"width": 1500, "height": 1000})
            self.addCleanup(ctx.close)
        page = ctx.new_page()
        failed = []
        page.on("pageerror", lambda e: failed.append(f"pageerror {e}"))
        page.on("console", lambda m: failed.append(f"console {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("response", lambda r: failed.append(f"{r.status} {r.request.method} {r.url} {(r.request.post_data or '')[:300]}")
                if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)
        page.goto(f"{self.base}/{hash_ or '#/song/' + self.A}")
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        page.evaluate(c2.PAGE_HELPERS)
        return page, failed

    def lay_break(self, page, events=None, play=True):
        """Put a four-hit pattern on bank 0 (pads 1-4), metronome off, and start it."""
        page.evaluate(f"""async () => {{
            const s = {S};
            s.store.dispatch({{ type: 'settings.set', params: {{ metronome: false }} }});
            s.store.dispatch({{ type: 'pattern.load', pattern: {{ bars: 2, events: {json.dumps(events or BREAK)} }} }});
            await s.remote.flush();
            if ({'true' if play else 'false'}) s.togglePattern();
        }}""")
        if play:
            page.wait_for_function(f"{S}.seq.state.playing && window.choppad.audio.ctx.state === 'running'", timeout=10000)

    def slow_audio(self, page, sid, seconds=1.5):
        """Make the audio of `sid` take `seconds` to arrive, so the load window is long enough to hear the pattern across it."""
        def handler(route):
            time.sleep(seconds)
            route.continue_()
        page.route(f"**/media/{sid}/audio.wav", handler)

    def keep_pads(self, page):
        """Step C: choosing another song while the open mega bank has used pads asks (a) new mega bank or (b) keep the drum pads.
        These tests are about (b), the behaviour step B built: press it in the panel."""
        page.locator('.mchoice:not([hidden]) [data-choice="b"]').click(timeout=15000)

    def choose(self, page, sid, timeout=60000):
        """Choose a song in the library pane, as a person does; returns when the session says the new source is in."""
        page.evaluate("() => { window.__ready = false; window.__failed = null; window.__tReady = null; window.__tClick = null;"
                      " const s = window.choppad.song.session; if (!window.__armed) { window.__armed = true;"
                      " s.on('source', () => { window.__ready = true; window.__tReady = performance.now(); });"
                      " s.on('source-error', (e) => { window.__failed = e.detail.error; }); } }")
        page.locator(f'#sidebar li.song[data-id="{sid}"]').click()
        self.keep_pads(page)
        t0 = page.evaluate("performance.now()")
        page.wait_for_function("window.__ready === true || window.__failed !== null", timeout=timeout)
        self.last_switch_ms = (page.evaluate("window.__tReady ?? performance.now()") - t0)

    def mega_id(self, page):
        return page.evaluate(f"{S}.mega")

    def project(self, mid):
        return c2.api(self.base, f"/api/mega/{mid}")[1]["project"]

    # ------------------------------------------------------------------ 1. no rebuild
    def test_1_choosing_another_song_does_not_rebuild_the_page(self):
        page, failed = self.tab()
        self.lay_break(page)
        marked = page.evaluate(MARK)
        self.assertTrue(all(marked[k] for k in ("sheet", "library", "pads", "lcd", "transport", "steps", "vox", "wave", "platter")), marked)
        before = page.evaluate("({ nav: performance.getEntriesByType('navigation').length, origin: performance.timeOrigin, mounts: choppad.stats.mounts })")
        self.assertEqual(before["mounts"], 1)
        page.evaluate(SAMPLER)
        self.slow_audio(page, self.B, 1.0)
        page.locator(f'#sidebar li.song[data-id="{self.B}"]').click()
        self.keep_pads(page)
        page.wait_for_function("window.__ready === true || window.__failed !== null", timeout=60000)
        self.assertIsNone(page.evaluate("window.__failed"))
        got = page.evaluate(CHECK)
        # the same objects
        self.assertTrue(all(got["objects"].values()), got["objects"])
        # the same nodes, still in the document, still carrying what was put on them
        for name, n in got["nodes"].items():
            if n is not None:
                self.assertEqual(n, {"inDocument": True, "marked": True}, name)
        # no navigation, no reload, no mount
        self.assertEqual(got["page"]["survive"], "same page")
        self.assertEqual(got["page"]["origin"], before["origin"])
        self.assertEqual(got["page"]["navEntries"], before["nav"])
        self.assertEqual(got["mounts"], before["mounts"], "mountSong was not called again")
        self.assertEqual(got["destroys"], 0)
        self.assertEqual(got["switches"], 1)
        # and the parts that show the song show song B
        shows = got["shows"]
        self.assertEqual(shows["title"], self.meta[self.B]["title"])
        self.assertEqual(shows["lcdTitle"], self.meta[self.B]["title"])
        self.assertTrue(shows["videoSrc"].endswith(f"/media/{self.B}/video.mp4"), shows["videoSrc"])
        self.assertTrue(shows["videoIsInMonitor"])
        self.assertAlmostEqual(shows["waveDur"], self.meta[self.B]["duration_s"], delta=0.1)
        self.assertEqual(shows["banksInStrip"], len(self.chops[self.B]["banks"]))
        self.assertEqual(shows["source"], self.B)
        self.assertEqual(shows["sheetSource"], self.B)
        self.assertIn(f"/song/{self.B}", shows["hash"])
        self.assertNotIn(f"/song/{self.A}", shows["hash"])
        self.assertTrue(any(self.meta[self.B]["title"].replace(" ", "")[:8].upper()[:4] in t.replace(" ", "").upper() for t in shows["platter"]) or shows["platter"], shows["platter"])
        # the switch made no mega bank of its own for song B
        self.assertEqual(len(c2.api(self.base, "/api/mega")[1]), 1)
        # the waveform's container stayed; a new wavesurfer drew B (its peaks are B's)
        self.assertEqual(page.evaluate(f"{S}.peaks.peaks.length"), 6000)
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ 2. playback continues
    def test_2_the_pattern_keeps_playing_across_the_switch(self):
        page, failed = self.tab()
        self.lay_break(page)
        loop_s = page.evaluate(f"(() => {{ const s = {S}; return s.seq.state.bars * s.store.get().chops.beats_per_bar * 60 / s.seq.state.bpm; }})()")
        page.evaluate(SAMPLER)
        self.slow_audio(page, self.B, 1.5)
        page.wait_for_timeout(1500)   # a stretch of plain playing first
        page.locator(f'#sidebar li.song[data-id="{self.B}"]').click()
        self.keep_pads(page)
        page.wait_for_function("window.__ready === true || window.__failed !== null", timeout=60000)
        self.assertIsNone(page.evaluate("window.__failed"))
        page.wait_for_timeout(2300)   # and 2 s after song B is ready
        page.evaluate("clearInterval(window.__sampler)")
        log = page.evaluate("window.__log")
        t_click, t_ready = page.evaluate("[window.__tClick, window.__tReady]")
        self.assertIsNotNone(t_click)
        window = [x for x in log if x["t"] >= t_click - 1000 and x["t"] <= t_ready + 2000]
        self.assertGreater(len(window), 30, "samples across the switch")
        self.assertTrue(all(x["playing"] for x in window), "seq.state.playing never went false")
        self.assertTrue(all(x["ctxState"] == "running" for x in window))
        # hits: the times at which the trigger count went up
        times = [window[0]["t"]] + [b["t"] for a, b in zip(window, window[1:]) if b["trig"] > a["trig"]]
        gaps = [(b - a) / 1000 for a, b in zip(times, times[1:])] + [(window[-1]["t"] - times[-1]) / 1000]
        self.assertLess(max(gaps), loop_s, f"hits never stopped for a whole loop ({loop_s:.2f} s); gaps {gaps}")
        in_load = [x for x in window if t_click <= x["t"] <= t_ready]
        self.assertGreaterEqual(in_load[-1]["trig"] - in_load[0]["trig"], 1, "pattern hits were scheduled during the load")
        flashes = [x["flash"] for x in window]
        self.assertGreater(flashes[-1] - flashes[0], 4)
        # the master analyser shows signal during the load
        self.assertGreater(max(x["rms"] for x in in_load), 1e-4, [round(x["rms"], 5) for x in in_load])
        after = [x for x in window if x["t"] > t_ready]
        self.assertGreater(max(x["rms"] for x in after), 1e-4)
        self.assertEqual(failed, [], failed)
        print(f"\n  [2] load window {(t_ready - t_click) / 1000:.2f} s, samples {len(window)}, hits in load {in_load[-1]['trig'] - in_load[0]['trig']}, "
              f"longest hit gap {max(gaps):.2f} s (loop {loop_s:.2f} s), peak rms during load {max(x['rms'] for x in in_load):.4f}")

    # ------------------------------------------------------------------ 3. kept pads are the drum break
    def test_3_kept_pads_are_the_drum_break_and_free_pads_are_the_new_song(self):
        page, failed = self.tab()
        self.lay_break(page, play=False)
        self.choose(page, self.B)
        self.assertIsNone(page.evaluate("window.__failed"))
        ov = page.evaluate(f"{S}.store.get().project.overrides")
        for p in range(4):
            o = ov[f"0:{p}"]
            self.assertTrue(o["kept"], o)
            self.assertTrue(o["src"].startswith("/media/_mega/"), o)
            self.assertEqual(o["from"]["song"], self.A)
        self.assertEqual(sorted(ov), [f"0:{p}" for p in range(4)], "only the pads that were used are kept")
        # free pads resolve to song B's chops (the engine's table AND the store's)
        cb = self.chops[self.B]
        for p in (4, 9, 15):
            want = cb["chops"][cb["banks"][0][p]]
            got = page.evaluate(f"{S}.engine.getSlice(0, {p})")
            self.assertEqual((got["start"], got["end"]), (want["start"], want["end"]))
            self.assertFalse(got.get("kept"))
        # the kept pads play their own samples, loaded before song A's audio was dropped
        self.assertEqual(page.evaluate(f"[0,1,2,3].map((p) => !!{S}.engine.getPadBuffer(0, p))"), [True] * 4)
        held = page.evaluate(f"{S}.heldBuffers()")
        self.assertNotIn(f"/media/{self.A}/audio.wav", held)
        self.assertIn(f"/media/{self.B}/audio.wav", held)
        # offline render of the pattern: each hit matches song A at its slice, and not song B at the same slice (negative control)
        res = page.evaluate(f"""async () => {{
            const s = {S}, T = window.__t;
            const sig = (await T.decode(Array.from(await s.renderPatternWav()))).getChannelData(0);
            const sr = s.buffer.sampleRate, bpm = s.seq.state.bpm;
            const load = async (id) => (await window.choppad.audio.ctx.decodeAudioData(await (await fetch('/media/' + id + '/audio.wav')).arrayBuffer())).getChannelData(0);
            const chopsOf = async (id) => (await fetch('/media/' + id + '/chops.json')).json();
            const A = await load({json.dumps(self.A)}), B = await load({json.dumps(self.B)});
            const cA = await chopsOf({json.dumps(self.A)}), cB = await chopsOf({json.dumps(self.B)});
            const n = Math.round(0.5 * sr), out = [];   // 0.5 s of each hit (the chops are about 0.6 s)
            for (const e of s.store.get().project.pattern.events) {{
                const expect = Math.round(e.beat * 60 / bpm * sr);
                const ca = cA.chops[cA.banks[0][e.pad]], cb = cB.chops[cB.banks[0][e.pad]];
                const refA = A.subarray(Math.round(ca.start * sr), Math.round(ca.start * sr) + n);
                const refB = B.subarray(Math.round(cb.start * sr), Math.round(cb.start * sr) + n);
                const a = T.lagOf(sig, refA, expect, Math.round(0.03 * sr), n), b = T.lagOf(sig, refB, expect, Math.round(0.03 * sr), n);
                out.push({{ pad: e.pad, corrA: a.corr, corrB: b.corr, lagMs: a.lag / sr * 1000 }});
            }}
            return out;
        }}""")
        self.assertEqual(len(res), 4)
        for r in res:
            self.assertGreater(r["corrA"], 0.9, r)
            self.assertLess(r["corrB"], 0.5, r)
            self.assertLess(abs(r["lagMs"]), 15, r)
        self.assertEqual(failed, [], failed)
        print(f"\n  [3] switch took {self.last_switch_ms:.0f} ms (no artificial delay); render vs A / vs B (control): " + ", ".join(f"pad {r['pad'] + 1}: {r['corrA']:.3f} / {r['corrB']:.3f}" for r in res))

    # ------------------------------------------------------------------ 4. a kept pad's sound is its own
    def test_4_a_kept_pad_edits_its_own_sound_and_keeps_it_through_a_reload(self):
        page, failed = self.tab()
        events = BREAK + [{"beat": 1, "pad": 0, "bank": 1, "vel": 1, "pass": -1}]   # the same pad number on another bank
        self.lay_break(page, events=events, play=False)
        self.choose(page, self.B)
        mid = self.mega_id(page)
        page.keyboard.press("KeyZ")   # plays and selects pad 1 (bank 0): the inspector now edits that kept pad
        self.assertEqual(page.evaluate(f"{S}.store.get().bank"), 0)
        page.locator('.mode[data-mode="PAD"]').click()
        page.locator('.soft[data-soft="1"]').click()   # F2 = PITCH
        for _ in range(3):
            page.locator(".wk.inc").click()
        got = page.evaluate(f"({{ kept: {S}.store.get().project.overrides['0:0'].params.pitch, free: {S}.store.get().project.pads[0].pitch,"
                            f" other: {S}.store.get().project.overrides['1:0'].params.pitch, engine: {S}.engine.getOverrides()['0:0'].params.pitch,"
                            f" otherEngine: {S}.engine.getOverrides()['1:0'].params.pitch, shown: document.querySelector('.inspector [data-out=\"pitch\"]')?.textContent }})")
        self.assertEqual(got["kept"], 3, got)
        self.assertEqual(got["free"], 0, "pads[0] (shared by the banks) is untouched")
        self.assertEqual(got["other"], 0, "the same pad number on another bank is unaffected")
        self.assertEqual(got["engine"], 3, "the engine plays the kept pad with its own params")
        self.assertEqual(got["otherEngine"], 0)
        self.assertEqual(got["shown"], "+3 st")
        page.evaluate(f"{S}.remote.flush()")
        saved = self.project(mid)
        self.assertEqual(saved["overrides"]["0:0"]["params"]["pitch"], 3)
        self.assertEqual(saved["pads"][0].get("pitch", 0), 0)
        self.assertEqual(saved["overrides"]["1:0"]["params"]["pitch"], 0)
        # reload: still so
        page.reload()
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        after = page.evaluate(f"({{ source: {S}.source, kept: {S}.store.get().project.overrides['0:0'].params.pitch, free: {S}.store.get().project.pads[0].pitch,"
                              f" other: {S}.store.get().project.overrides['1:0'].params.pitch, engine: {S}.engine.getOverrides()['0:0'].params.pitch }})")
        self.assertEqual(after, {"source": self.B, "kept": 3, "free": 0, "other": 0, "engine": 3})
        self.assertEqual([f for f in failed if "pageerror" in f], [])
        # the pad grid tags a kept pad
        self.assertEqual(page.locator(".pad[data-pad='0'] .pad-tag").inner_text(), "KEPT")
        self.assertTrue(page.locator(".pad[data-pad='5'] .pad-tag").is_hidden())

    # ------------------------------------------------------------------ 5. tempo stays
    def test_5_the_users_tempo_stays_through_a_switch(self):
        page, failed = self.tab()
        bpm_a, bpm_b = self.chops[self.A]["bpm"], self.chops[self.B]["bpm"]
        self.assertGreater(abs(bpm_a - bpm_b), 5, "the two songs have different detected tempos")
        self.lay_break(page)
        before = page.evaluate(f"({{ user: {S}.store.get().project.tempo?.bpm ?? null, seq: {S}.seq.state.bpm, tempo: {S}.tempoBpm }})")
        self.assertIsNone(before["user"], "no user tempo yet")
        self.assertAlmostEqual(before["seq"], bpm_a, places=3)
        self.assertIn("SONG", page.locator(".s-meta").inner_text())
        self.assertEqual(page.locator(".lcd-main .lcd-big small").first.inner_text(), "TEMPO · FOLLOWS SONG")
        self.choose(page, self.B)
        after = page.evaluate(f"({{ user: {S}.store.get().project.tempo, seq: {S}.seq.state.bpm, tempo: {S}.tempoBpm, song: {S}.store.get().chops.bpm }})")
        self.assertAlmostEqual(after["user"]["bpm"], bpm_a, places=3, msg="the first switch writes the tempo it was running at")
        self.assertFalse(after["user"]["follow"])
        self.assertEqual(after["seq"], before["seq"], "the sequencer's bpm is unchanged")
        self.assertAlmostEqual(after["song"], bpm_b, places=3, msg="the chop table (and so the grid tools) is the new song's own")
        self.assertTrue(page.evaluate(f"{S}.seq.state.playing"))
        head = page.locator(".s-meta").inner_text()
        self.assertIn(f"USER {bpm_a:.1f} BPM (song {bpm_b:.1f})", head)
        self.assertEqual(page.locator(".lcd-main .lcd-big small").first.inner_text(), "TEMPO · YOURS")
        self.assertEqual(page.locator(".lcd-main .bpm").inner_text(), f"{bpm_a:.1f}")
        # the user sets a tempo; switching back to A does not change it, and A's own tempo is only for A's grid
        page.evaluate(f"{S}.setTempo(93)")
        self.choose(page, self.A)
        back = page.evaluate(f"({{ user: {S}.store.get().project.tempo.bpm, seq: {S}.seq.state.bpm, song: {S}.store.get().chops.bpm, shown: document.querySelector('.s-meta').textContent }})")
        self.assertEqual(back["user"], 93)
        self.assertEqual(back["seq"], 93)
        self.assertAlmostEqual(back["song"], bpm_a, places=3)
        self.assertIn(f"USER 93.0 BPM (song {bpm_a:.1f})", back["shown"])
        self.assertEqual(failed, [], failed)
        print(f"\n  [5] song A {bpm_a:.2f}, song B {bpm_b:.2f}; after the first switch user tempo {after['user']['bpm']:.2f}, seq {after['seq']:.2f}; after the user set 93 and went back: {back['user']}")

    # ------------------------------------------------------------------ 6. two tabs
    def test_6_a_second_tab_follows_the_switch_without_reloading(self):
        ctx = self.browser.new_context(viewport={"width": 1500, "height": 1000})
        self.addCleanup(ctx.close)
        p1, f1 = self.tab(ctx=ctx)
        self.lay_break(p1, play=False)
        p2, f2 = self.tab(ctx=ctx)
        self.assertEqual(p2.evaluate(f"{S}.mega"), p1.evaluate(f"{S}.mega"))
        p2.evaluate(MARK)
        mounts = p2.evaluate("choppad.stats.mounts")
        self.choose(p1, self.B)
        p2.wait_for_function(f"{S}.source === {json.dumps(self.B)}", timeout=60000)
        got = p2.evaluate(CHECK)
        self.assertTrue(all(got["objects"].values()), got["objects"])
        self.assertEqual(got["page"]["survive"], "same page", "the second tab was not reloaded")
        self.assertEqual(got["mounts"], mounts)
        self.assertEqual(got["shows"]["title"], self.meta[self.B]["title"])
        self.assertEqual(got["shows"]["lcdTitle"], self.meta[self.B]["title"])
        self.assertTrue(got["shows"]["videoSrc"].endswith(f"/media/{self.B}/video.mp4"))
        self.assertIn(f"/song/{self.B}", got["shows"]["hash"])
        ov = p2.evaluate(f"Object.entries({S}.store.get().project.overrides).filter(([k, o]) => o.kept).map(([k]) => k).sort()")
        self.assertEqual(ov, [f"0:{p}" for p in range(4)])
        self.assertEqual(p2.evaluate(f"[0,1,2,3].map((p) => !!{S}.engine.getPadBuffer(0, p))"), [True] * 4)
        self.assertEqual(f1 + f2, [], f1 + f2)

    # ------------------------------------------------------------------ 7. failed load
    def test_7_a_failed_load_leaves_everything_on_the_old_song(self):
        page, failed = self.tab()
        self.lay_break(page)
        page.evaluate(MARK)
        page.evaluate(SAMPLER)
        mid = self.mega_id(page)
        hash_before = page.evaluate("location.hash")
        # the router: an address inside the same mega bank that names a song which does not exist
        page.evaluate(f"location.hash = '#/mega/{mid}/song/no-such-song-zz'")
        page.wait_for_function("window.__failed !== null", timeout=30000)
        # the session API says so too
        out = page.evaluate(f"{S}.setSource('no-such-song-zz')")
        self.assertFalse(out["ok"])
        self.assertIn("no-such-song-zz", out["error"])
        got = page.evaluate(CHECK)
        self.assertTrue(all(got["objects"].values()), got["objects"])
        self.assertEqual(got["page"]["survive"], "same page")
        self.assertEqual(got["mounts"], 1)
        self.assertEqual(got["destroys"], 0)
        self.assertEqual(got["shows"]["source"], self.A)
        self.assertEqual(got["shows"]["title"], self.meta[self.A]["title"])
        self.assertTrue(got["shows"]["videoSrc"].endswith(f"/media/{self.A}/video.mp4"))
        self.assertEqual(got["shows"]["hash"], hash_before, "the address went back to the open song")
        self.assertTrue(page.evaluate(f"{S}.lastSourceError.error").endswith("HTTP 404") or "404" in page.evaluate(f"{S}.lastSourceError.error"))
        toast = page.locator(".toast").inner_text()
        self.assertIn("Couldn't load", toast)
        self.assertEqual(page.evaluate(f"{S}.store.get().source"), self.A)
        self.assertEqual(page.evaluate(f"Object.keys({S}.store.get().project.overrides).length"), 0, "nothing was kept: the server was never asked")
        # the old song still works: a pad plays it, the pattern goes on
        page.wait_for_timeout(3500)
        page.evaluate("clearInterval(window.__sampler)")
        log = page.evaluate("window.__log")
        self.assertTrue(all(x["playing"] for x in log))
        self.assertGreaterEqual(log[-1]["trig"] - log[0]["trig"], 3)
        self.assertEqual(c2.api(self.base, f"/api/mega/{mid}")[1]["mega"]["source"], self.A)
        # only the media requests for the song that does not exist failed
        expected = [f for f in failed if "no-such-song-zz" in f]
        self.assertEqual([f for f in failed if f not in expected], [], failed)

    # ------------------------------------------------------------------ 8. reload
    def test_8_a_reload_comes_back_to_the_mega_bank_on_song_b_with_the_kept_pads(self):
        page, failed = self.tab()
        self.lay_break(page, play=False)
        self.choose(page, self.B)
        mid = self.mega_id(page)
        page.evaluate(f"{S}.remote.flush()")
        page.reload()
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad .pad-label:not(:empty)")
        got = page.evaluate(f"({{ mega: {S}.mega, source: {S}.source, title: document.querySelector('.s-title').textContent, hash: location.hash,"
                            f" kept: Object.entries({S}.store.get().project.overrides).filter(([k, o]) => o.kept).map(([k]) => k).sort(),"
                            f" buffers: [0,1,2,3].map((p) => !!{S}.engine.getPadBuffer(0, p)), free: {S}.engine.getSlice(0, 8).start,"
                            f" events: {S}.store.get().project.pattern.events.length }})")
        self.assertEqual(got["mega"], mid)
        self.assertEqual(got["source"], self.B)
        self.assertEqual(got["title"], self.meta[self.B]["title"])
        self.assertEqual(got["kept"], [f"0:{p}" for p in range(4)])
        self.assertEqual(got["buffers"], [True] * 4)
        self.assertEqual(got["events"], 4)
        cb = self.chops[self.B]
        self.assertEqual(got["free"], cb["chops"][cb["banks"][0][8]]["start"])
        # the address of song B alone opens that song's most recent mega bank: the same one
        p2, f2 = self.tab(f"#/song/{self.B}")
        self.assertEqual(p2.evaluate(f"{S}.mega"), mid)
        # and #/mega/<mid> opens it too
        p3, f3 = self.tab(f"#/mega/{mid}")
        self.assertEqual(p3.evaluate(f"{S}.source"), self.B)
        self.assertEqual(failed + f2 + f3, [], failed + f2 + f3)

    # ------------------------------------------------------------------ 9. the old song's audio is released
    def test_9_the_old_songs_decoded_audio_is_released(self):
        page, failed = self.tab()
        self.lay_break(page, play=False)
        page.evaluate(f"""() => {{
            window.__probe = new WeakRef({S}.buffer);   // song A's decoded audio, held only through the session
            window.__probeFree = new FinalizationRegistry(() => {{ window.__finalized = true; }});
            window.__probeFree.register({S}.buffer, 'A');
        }}""")
        held0 = page.evaluate(f"{S}.heldBuffers()")
        self.assertIn(f"/media/{self.A}/audio.wav", held0)
        self.choose(page, self.B)
        held1 = page.evaluate(f"{S}.heldBuffers()")
        self.assertNotIn(f"/media/{self.A}/audio.wav", held1, "the session dropped song A's buffer")
        self.assertEqual(sorted(u for u in held1 if "_mega" not in u), [f"/media/{self.B}/audio.wav"],
                         "only the source song plus the kept samples stay decoded")
        self.assertEqual(len([u for u in held1 if "/_mega/" in u]), 4)
        cdp = page.context.new_cdp_session(page)
        gone = False
        for _ in range(8):
            page.wait_for_timeout(200)
            cdp.send("HeapProfiler.collectGarbage")
            if page.evaluate("window.__probe.deref() === undefined"):
                gone = True
                break
        self.assertTrue(gone, "a forced garbage collection reclaimed song A's AudioBuffer: nothing holds it any more")
        self.assertEqual(failed, [], failed)

    # ------------------------------------------------------------------ 10. what a kept pad does not do
    def test_10_kept_pads_are_skipped_by_stretch_and_fit_and_a_kit_of_them_says_why(self):
        page, failed = self.tab()
        self.lay_break(page, play=False)
        self.choose(page, self.B)
        self.assertIsNone(page.evaluate("window.__failed"))
        out = page.evaluate(f"""async () => {{
            const s = {S};
            await s.setTempo(90);
            const one = await s.stretchPad(0, 0, {{ ratio: 1.2 }});
            const fit = await s.fitToTempo({{ scope: 'pattern' }});
            const bank = await s.stretchBank({{ ratio: 1.2 }});
            const kit = await s.saveBankAsKit('mine');
            return {{ one, fit: {{ ok: fit.ok, fitted: fit.fitted.length, skipped: fit.skipped.length }}, banked: bank.filter((r) => r.pad < 4).length, kit,
                     stretchedKept: Object.entries(s.store.get().project.overrides).filter(([k, o]) => o.kept && o.stretch).length,
                     stretchedFree: Object.entries(s.store.get().project.overrides).filter(([k, o]) => !o.kept && o.stretch).length }};
        }}""")
        self.assertFalse(out["one"]["ok"])
        self.assertIn("kept", out["one"]["error"])
        self.assertEqual(out["fit"], {"ok": True, "fitted": 0, "skipped": 4})
        self.assertEqual(out["banked"], 0, "stretchBank leaves the kept pads out")
        self.assertFalse(out["kit"]["ok"])
        self.assertIn("kept", out["kit"]["error"], "the server's words are shown")
        self.assertEqual(out["stretchedKept"], 0)
        self.assertEqual(out["stretchedFree"], 12, "the free pads of the bank were stretched as before")
        # no request for a kept pad was made (the server would have answered 400): only the kit save was refused
        self.assertEqual([f for f in failed if "savekit" not in f], [], failed)
        self.assertEqual(len([f for f in failed if "savekit" in f]), 1)


if __name__ == "__main__":
    unittest.main()
