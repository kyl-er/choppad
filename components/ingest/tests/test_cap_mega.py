"""CHOP-13: the cache cap never evicts a song a mega bank still needs (contracts §15). Pure folders, no media tools."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
from choppad_ingest import enforce_cap  # noqa: E402
import json, shutil, time  # noqa: E402

KB = 1024


class Rig(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="choppad-cap-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        now = time.time()
        for i, sid in enumerate(("old1", "old2", "old3", "old4", "old5", "fresh")):
            d = os.path.join(self.root, sid)
            os.makedirs(d)
            with open(os.path.join(d, "audio.wav"), "wb") as f:
                f.write(b"x" * KB)
            os.utime(d, (now - 1000 + i, now - 1000 + i))   # the oldest first

    def mega(self, mid, source, project, folder="_mega"):
        d = os.path.join(self.root, folder, mid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "mega.json"), "w") as f:
            json.dump({"schema": "choppad.mega/1", "id": mid, "name": mid, "source": source, "project": project}, f)

    def left(self):
        return sorted(n for n in os.listdir(self.root) if not n.startswith("_"))

    def test_without_mega_banks_the_oldest_go_first(self):
        removed = enforce_cap(self.root, 2 * KB, keep="fresh")
        self.assertEqual(removed, ["old1", "old2", "old3", "old4"])

    def test_source_of_a_mega_bank_with_used_pads_is_never_evicted(self):
        self.mega("m1", "old1", {"overrides": {"0:0": {"start": 0, "end": 1}}})
        self.mega("m2", "old2", {"overrides": {}, "pattern": {"bars": 2, "events": [{"beat": 0, "pad": 1, "bank": 0}]}})
        self.mega("m3", "old3", {"overrides": {}, "pattern": {"bars": 2, "events": []},
                                 "song": {"patterns": [{"id": "p1", "events": [{"beat": 0, "pad": 1}]}]}})
        self.mega("m4", "old4", {"overrides": {}, "pattern": {"bars": 2, "events": []}})      # nothing used: not protected
        removed = enforce_cap(self.root, 1, keep="fresh")
        self.assertEqual(sorted(removed), ["old4", "old5"])
        self.assertEqual(self.left(), ["fresh", "old1", "old2", "old3"])

    def test_songs_an_override_refers_to_are_protected(self):
        self.mega("m1", None, {"overrides": {
            "0:0": {"start": 0, "end": 1, "song": "old1"},
            "0:1": {"start": 0, "end": 1, "stretch": {"ratio": 2, "url": "/media/old2/stretch/0123456789abcdef.wav"}},
            "0:2": {"start": 0, "end": 1, "src": "/media/old3/resample/0123456789abcdef.wav"},
            "0:3": {"start": 0, "end": 1, "src": "/media/_mega/m1/samples/0123456789abcdef.wav", "kept": True, "from": {"song": "old4", "start": 0, "end": 1}},
            "0:4": "junk"}})
        removed = enforce_cap(self.root, 1, keep="fresh")
        self.assertEqual(sorted(removed), ["old4", "old5"])      # `from` is provenance only; the kept sample is its own file
        self.assertEqual(self.left(), ["fresh", "old1", "old2", "old3"])

    def test_trash_unreadable_and_foreign_entries_protect_nothing_and_do_not_crash(self):
        self.mega("m1", "old1", {"overrides": {"0:0": {"start": 0, "end": 1}}}, folder=os.path.join("_mega", ".trash"))
        os.makedirs(os.path.join(self.root, "_mega", "broken"))
        with open(os.path.join(self.root, "_mega", "broken", "mega.json"), "w") as f:
            f.write("{not json")
        with open(os.path.join(self.root, "_mega", "migrated.json"), "w") as f:
            json.dump({"songs": ["old2"]}, f)
        removed = enforce_cap(self.root, 1, keep="fresh")
        self.assertEqual(sorted(removed), ["old1", "old2", "old3", "old4", "old5"])
        self.assertTrue(os.path.isdir(os.path.join(self.root, "_mega")))      # `_` folders are never evicted

    def test_a_protected_song_stays_even_when_that_leaves_the_cache_over_the_cap(self):
        self.mega("m1", "old1", {"overrides": {"0:0": {"start": 0, "end": 1}}})
        removed = enforce_cap(self.root, 1, keep=None)
        self.assertNotIn("old1", removed)
        self.assertIn("old1", self.left())
        self.assertEqual(len(removed), 5)


if __name__ == "__main__":
    unittest.main()
