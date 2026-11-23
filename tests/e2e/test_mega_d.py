"""Real-browser tests for the drum pad mega banks, step D (CHOP-13, contracts §15): the pattern keeps its own bar length, the session
works through the mega routes (exports from the UI with kept pads, a mega bank with no source song, a resample stored under the mega
bank), and a mega bank removed under an open tab.

Run with a Python that has Playwright installed (like the other browser tests):
    /usr/bin/python3 -m unittest tests/e2e/test_mega_d.py
Songs are opened from a temporary copy of two library songs served by a server on a free port with its own media root (the harness of
test_mega.py), so the real library and port 8960 are never touched. Every failed HTTP request is logged with its body.
"""
import io
import json
import os
import sys
import unittest
import wave
import zipfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_core2 as c2  # noqa: E402
import test_mega as tm  # noqa: E402

S = tm.S


def frames(path_or_file, start=None, end=None):
    with wave.open(path_or_file, "rb") as w:
        sr, n = w.getframerate(), w.getnchannels() * w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if start is None:
        return raw
    return raw[round(start * sr) * n: round(end * sr) * n]


def middle(b):
    """The middle half: the 2.5 ms fade-in and 3 ms fade-out of a pad's file do not reach it."""
    return b[len(b) // 4: 3 * len(b) // 4]


class MegaStepD(tm.MegaStepB):
    # the tests of step B are not run again here (they run in test_mega.py)
    for _n in [n for n in dir(tm.MegaStepB) if n.startswith("test_")]:
        locals()[_n] = None
    del _n

    def regrid(self, sid, meter):
        code, r = c2.api(self.base, f"/api/songs/{sid}/regrid", {"grid": "1/4", "meter": meter})
        self.assertEqual(code, 200, r)
        return r

    def reload(self, page):
        page.reload()
        page.wait_for_function("window.choppad?.song?.session", timeout=30000)
        page.wait_for_selector(".pad")
        page.evaluate(c2.PAGE_HELPERS)

    def test_1_the_pattern_keeps_its_bar_length_across_a_song_of_another_meter_and_a_reload(self):
        self.assertEqual(self.regrid(self.A, "4/4")["beats_per_bar"], 4)
        self.assertEqual(self.regrid(self.B, "3/4")["beats_per_bar"], 3)      # a library song whose chop table says 3 beats per bar
        page, failed = self.tab()
        page.evaluate(f"""async () => {{
            const s = {S};
            s.store.dispatch({{ type: 'settings.set', params: {{ metronome: false }} }});
            s.store.dispatch({{ type: 'pattern.toggleStep', pad: 0, step: 0 }});     // a recorded / drawn pattern on a 4/4 song
            s.store.dispatch({{ type: 'pattern.toggleStep', pad: 1, step: 8 }});
            await s.remote.flush();
        }}""")
        before = page.evaluate(f"(() => {{ const s = {S}, p = s.store.get().project.pattern; return {{ bpb: p.beatsPerBar, bars: p.bars, seq: s.seq.state.beatsPerBar, loop: s.seq.state.bars * s.seq.state.beatsPerBar }}; }})()")
        self.assertEqual((before["bpb"], before["seq"], before["loop"]), (4, 4, before["bars"] * 4))
        self.choose(page, self.B)
        self.assertIsNone(page.evaluate("window.__failed"))
        after = page.evaluate(f"(() => {{ const s = {S}; return {{ chopsBpb: s.store.get().chops.beats_per_bar, bpb: s.store.get().project.pattern.beatsPerBar, seq: s.seq.state.beatsPerBar, api: s.patternBeatsPerBar }}; }})()")
        self.assertEqual(after, {"chopsBpb": 3, "bpb": 4, "seq": 4, "api": 4}, "the song says 3, the pattern keeps its 4")
        mid = self.mega_id(page)
        page.wait_for_timeout(300)
        self.assertEqual(self.project(mid)["pattern"]["beatsPerBar"], 4, "saved with the pattern")
        self.reload(page)
        again = page.evaluate(f"""(() => {{
            const s = {S}, st = s.store.get(), midi = Array.from(s.patternMidi());
            const at = midi.findIndex((b, i) => b === 0xff && midi[i + 1] === 0x58 && midi[i + 2] === 4);
            return {{ chopsBpb: st.chops.beats_per_bar, bpb: st.project.pattern.beatsPerBar, seq: s.seq.state.beatsPerBar, api: s.patternBeatsPerBar,
                     loop: s.seq.state.bars * s.seq.state.beatsPerBar, source: s.source, midiSig: midi[at + 3] }};
        }})()""")
        self.assertEqual((again["source"], again["chopsBpb"]), (self.B, 3))
        self.assertEqual((again["bpb"], again["seq"], again["api"], again["midiSig"]), (4, 4, 4, 4))
        self.assertEqual(again["loop"], before["loop"], "the loop length in beats did not change")
        self.assertEqual(failed, [], failed)

    def test_2_a_pattern_of_an_older_document_is_given_its_bar_length_before_the_song_changes(self):
        self.assertEqual(self.regrid(self.A, "4/4")["beats_per_bar"], 4)
        self.assertEqual(self.regrid(self.B, "3/4")["beats_per_bar"], 3)
        page, failed = self.tab()
        self.lay_break(page, play=False)          # pattern.load with no beatsPerBar: what an older document looks like
        self.assertIsNone(page.evaluate(f"{S}.store.get().project.pattern.beatsPerBar ?? null"))
        self.choose(page, self.B)
        self.assertEqual(page.evaluate(f"{S}.store.get().project.pattern.beatsPerBar"), 4)
        self.assertEqual(page.evaluate(f"{S}.seq.state.beatsPerBar"), 4)
        mid = self.mega_id(page)
        page.wait_for_timeout(300)
        self.assertEqual(self.project(mid)["pattern"]["beatsPerBar"], 4)
        self.assertEqual(failed, [], failed)

    def test_3_exports_from_the_ui_on_a_mega_bank_with_kept_pads_and_free_pads(self):
        page, failed = self.tab()
        self.lay_break(page, play=False)
        self.choose(page, self.B)
        self.assertIsNone(page.evaluate("window.__failed"))
        mid = self.mega_id(page)
        r = page.evaluate(f"{S}.exportBank('zip')")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mega"], mid)
        with urllib.request.urlopen(self.base + r["url"]) as resp:
            z = zipfile.ZipFile(io.BytesIO(resp.read()))
        names = sorted(z.namelist())
        self.assertEqual(names, [f"pad{i:02d}.wav" for i in range(1, 17)], "the zip has 16 WAVs")
        ov = self.project(mid)["overrides"]
        for p in range(4):                        # the four kept pads: song A's audio
            ch = self.chops[self.A]["chops"][self.chops[self.A]["banks"][0][p]]
            got = z.read(f"pad{p + 1:02d}.wav")
            kept = os.path.join(self.root, "_mega", mid, "samples", os.path.basename(ov[f"0:{p}"]["src"]))
            with wave.open(io.BytesIO(got)) as w:
                gotf = w.readframes(w.getnframes())
            self.assertEqual(gotf, frames(kept), f"pad {p + 1} is the kept sample")
            raw = frames(os.path.join(c2.MEDIA, self.A, "audio.wav"), ch["start"], ch["end"])
            self.assertEqual(middle(gotf), middle(raw), f"pad {p + 1} is song A's audio")
        for p in range(4, 16):                    # the free pads: song B's chops
            chb = self.chops[self.B]["chops"][self.chops[self.B]["banks"][0][p]]
            with wave.open(io.BytesIO(z.read(f"pad{p + 1:02d}.wav"))) as w:
                gotf = w.readframes(w.getnframes())
            self.assertEqual(middle(gotf), middle(frames(os.path.join(c2.MEDIA, self.B, "audio.wav"), chb["start"], chb["end"])), f"pad {p + 1} is song B's")
        # sfz and hydrogen go through the same route (hydrogen into a private folder is covered by the API tests)
        self.assertTrue(page.evaluate(f"{S}.exportBank('sfz')")["ok"])
        # a kit of the bank holds the kept pads, with their audio copied
        kit = page.evaluate(f"{S}.saveBankAsKit('Break kit')")
        self.assertTrue(kit["ok"], kit)
        self.assertEqual([("src" in p, "song_id" in p) for p in kit["kit"]["pads"][:5]], [(True, False)] * 4 + [(False, True)])
        self.assertEqual(len(os.listdir(os.path.join(self.root, "_kits", kit["kit"]["id"], "samples"))), 4)
        self.assertEqual(failed, [], failed)

    def make_no_source_mega(self):
        """A mega bank fed by A whose four hit pads are kept, then left with no source song (through the API)."""
        code, r = c2.api(self.base, "/api/mega", {"source": self.A})
        mid = r["mega"]["id"]
        c2.api(self.base, f"/api/mega/{mid}/actions", {"actions": [{"type": "pattern.addEvent", "event": e} for e in tm.BREAK]})
        self.assertEqual(c2.api(self.base, f"/api/mega/{mid}/source", {"song": self.B})[0], 200)
        self.assertEqual(c2.api(self.base, f"/api/mega/{mid}/source", {"song": None})[0], 200)
        return mid

    def test_4_a_mega_bank_with_no_source_song_works_and_says_pick_a_song_first(self):
        mid = self.make_no_source_mega()
        page, failed = self.tab(f"#/mega/{mid}")
        self.assertIsNone(page.evaluate(f"{S}.source"))
        out = page.evaluate(f"""async () => {{
            const s = {S};
            return {{ stems: await s.separateStems(), regrid: await s.regrid({{ grid: '1/8' }}), advise: await s.advise().catch((e) => String(e.message)),
                     zip: await s.exportBank('zip'), stretch: await s.stretchPad(0, 0, {{ ratio: 1.2 }}), kept: Object.values(s.store.get().project.overrides).filter((o) => o.kept).length }};
        }}""")
        self.assertEqual(out["stems"], {"ok": False, "error": "pick a song first"})
        self.assertFalse(out["regrid"])
        self.assertEqual(out["advise"], "pick a song first")
        self.assertTrue(out["zip"]["ok"], out["zip"])
        self.assertEqual(out["kept"], 4)
        self.assertFalse(out["stretch"]["ok"])
        with urllib.request.urlopen(self.base + out["zip"]["url"]) as resp:
            self.assertEqual(sorted(zipfile.ZipFile(io.BytesIO(resp.read())).namelist()), ["pad01.wav", "pad02.wav", "pad03.wav", "pad04.wav"])
        # a resample is stored under the mega bank, so it survives the song (there is none)
        r = page.evaluate(f"{S}.resample({{ bars: 1, pad: 8, play: false }})")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["url"].startswith(f"/media/_mega/{mid}/samples/"), r["url"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "_mega", mid, "samples", os.path.basename(r["url"]))))
        self.assertEqual(page.evaluate(f"{S}.store.get().project.overrides['0:8'].src"), r["url"])
        self.assertEqual(failed, [], failed)

    def test_5_a_mega_bank_removed_under_an_open_tab_is_left_cleanly(self):
        page, failed = self.tab()
        self.lay_break(page, play=False)
        mid = self.mega_id(page)
        # keep hold of the session: the screen leaves the mega bank when it is removed (it goes back to the library)
        page.evaluate(f"""() => {{ const s = {S}; window.__sess = s; window.__gone = []; s.on('mega-removed', (e) => window.__gone.push(e.detail)); }}""")
        reqs = []
        page.on("request", lambda r: reqs.append((r.method, r.url)) if f"/api/mega/{mid}" in r.url else None)
        code, r = c2.api(self.base, f"/api/mega/{mid}", method="DELETE")
        self.assertEqual((code, r["ok"]), (200, True))
        page.wait_for_function("window.__gone.length === 1", timeout=10000)
        self.assertEqual(page.evaluate("window.__gone[0].id"), mid)
        self.assertTrue(page.evaluate("window.__sess.removed"))
        page.wait_for_function("window.choppad.song == null || !window.choppad.song.session", timeout=10000)   # the screen left
        n = len(reqs)
        page.evaluate("""async () => { const s = window.__sess; s.store.dispatch({ type: 'pad.set', pad: 2, params: { gainDb: -5 } }); await s.remote.flush();
            s.store.dispatch({ type: 'pattern.clear' }); await s.remote.flush(); }""")
        page.wait_for_timeout(600)
        writes = [x for x in reqs[n:] if x[0] in ("POST", "PUT")]
        self.assertEqual(writes, [], "no write is sent to a mega bank that is gone")
        self.assertEqual([f for f in failed if "404" in f], [], failed)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "_mega", ".trash")))
        self.assertEqual(len(c2.api(self.base, "/api/mega")[1]), 0)


if __name__ == "__main__":
    unittest.main()
