"""CHOP-13 reducer helpers and diff_project for kept pads (the fixture cases live in contracts/fixtures/actions.json)."""
import os, sys, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
sys.path.insert(0, os.path.join(ROOT, "components", "control"))  # noqa: F405
import choppad_actions as A  # noqa: E402

SRC = "/media/_mega/m1/samples/0123456789abcdef.wav"
CHOPS = {"grid": "1/4", "bpm": 120, "beats_per_bar": 4, "banks": [[0], [1], [2]]}


class Helpers(unittest.TestCase):
    def test_used_pads_counts_overrides_and_events_of_every_pattern(self):
        project = {"overrides": {"1:3": {"start": 0, "end": 1}, "x": {}, "0:16": {}},
                   "pattern": {"bars": 2, "events": [{"beat": 0, "pad": 2, "bank": 0}, {"beat": 1, "pad": 3}, {"beat": 1, "pad": 99}]},
                   "song": {"patterns": [{"id": "p1", "events": [{"beat": 0, "pad": 5, "bank": 2}]}, {"id": "p2", "events": []}]}}
        self.assertEqual(A.used_pads(project), [(0, 2), (0, 3), (1, 3), (2, 5)])
        self.assertEqual(A.used_pads({}), [])
        self.assertEqual(A.used_pads(A.initial_state(project)["project"]), [(0, 2), (0, 3), (1, 3), (2, 5)])

    def test_bank_count(self):
        self.assertEqual(A.bank_count({"overrides": {}}, CHOPS), 3)
        self.assertEqual(A.bank_count({"overrides": {"6:0": {}}}, CHOPS), 7)
        self.assertEqual(A.bank_count({"overrides": {}}, None), 1)
        self.assertEqual(A.bank_count({"overrides": {}}, {"banks": []}), 1)

    def test_initial_state_has_source_and_mega(self):
        s = A.initial_state({}, CHOPS, source="a", mega="m1")
        self.assertEqual((s["source"], s["mega"]), ("a", "m1"))
        s = A.initial_state({}, CHOPS)
        self.assertEqual((s["source"], s["mega"]), (None, None))

    def test_kept_pad_survives_a_round_trip_through_initial_state(self):
        s = A.reduce(A.initial_state({}, CHOPS), {"type": "slice.keep", "bank": 1, "pad": 2, "src": SRC, "start": 0, "end": 2,
                                                  "params": {"gainDb": -2}, "from": {"song": "a", "start": 1, "end": 3}})
        again = A.initial_state(A.project_doc(s), CHOPS)
        self.assertEqual(again["project"]["overrides"], s["project"]["overrides"])


class Diff(unittest.TestCase):
    def test_kept_overrides_travel_whole(self):
        old = A.initial_state({}, CHOPS)["project"]
        s = A.reduce(A.initial_state({}, CHOPS), {"type": "slice.keep", "bank": 0, "pad": 1, "src": SRC, "start": 0, "end": 1.5,
                                                  "params": {"gainDb": -4, "pitch": 2}, "from": {"song": "a", "start": 2, "end": 3.5}})
        s = A.reduce(s, {"type": "slice.set", "bank": 1, "pad": 0, "start": 1, "end": 2})
        new = A.project_doc(s)
        changes = A.diff_project(old, new)
        keep = [c for c in changes if c["type"] == "slice.keep"]
        self.assertEqual(len(keep), 1)
        self.assertEqual({k: keep[0][k] for k in ("bank", "pad", "src", "start", "end", "from")},
                         {"bank": 0, "pad": 1, "src": SRC, "start": 0, "end": 1.5, "from": {"song": "a", "start": 2, "end": 3.5}})
        self.assertEqual((keep[0]["params"]["gainDb"], keep[0]["params"]["pitch"]), (-4, 2))
        # replaying the difference on the old project gives the new one
        replay = A.initial_state(old, CHOPS)
        for c in changes:
            replay = A.reduce(replay, c)
        self.assertEqual(replay["project"]["overrides"], new["overrides"])

    def test_clearing_a_kept_pad_is_a_slice_clear(self):
        s = A.reduce(A.initial_state({}, CHOPS), {"type": "slice.keep", "bank": 0, "pad": 1, "src": SRC, "start": 0, "end": 1})
        gone = A.reduce(s, {"type": "slice.clear", "bank": 0, "pad": 1})
        self.assertEqual(A.diff_project(A.project_doc(s), A.project_doc(gone)), [{"type": "slice.clear", "bank": 0, "pad": 1}])

    def test_other_overrides_still_diff_as_before(self):
        old = A.initial_state({}, CHOPS)["project"]
        s = A.reduce(A.initial_state({}, CHOPS), {"type": "slice.set", "bank": 0, "pad": 1, "start": 1, "end": 2})
        self.assertEqual([c["type"] for c in A.diff_project(old, A.project_doc(s))], ["slice.setMany"])


if __name__ == "__main__":
    unittest.main()
