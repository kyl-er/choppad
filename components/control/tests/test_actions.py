"""The Python reducer passes contracts/fixtures/actions.json (same matching rules as components/ui/test/actions.test.js)."""
import json, os, sys, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
sys.path.insert(0, os.path.join(ROOT, "components", "control"))  # noqa: F405
import choppad_actions as A  # noqa: E402

FIXTURE = json.load(open(os.path.join(FIX, "actions.json")))  # noqa: F405


def matches(tc, actual, expected, path="state"):
    """Objects match on the listed keys only; arrays and scalars must be equal."""
    if isinstance(expected, list):
        tc.assertIsInstance(actual, list, f"{path} should be a list")
        tc.assertEqual(len(actual), len(expected), f"{path}.length")
        for i, e in enumerate(expected):
            matches(tc, actual[i], e, f"{path}[{i}]")
    elif isinstance(expected, dict):
        tc.assertIsInstance(actual, dict, f"{path} should be an object")
        if not expected:
            tc.assertEqual(actual, {}, path)
        for k, v in expected.items():
            tc.assertIn(k, actual, f"{path}.{k} missing")
            matches(tc, actual[k], v, f"{path}.{k}")
    else:
        tc.assertEqual(actual, expected, path)
        tc.assertEqual(type(actual) is bool, type(expected) is bool, f"{path} bool-ness")


class Contract(unittest.TestCase):
    def test_fixture_cases(self):
        for c in FIXTURE["cases"]:
            with self.subTest(c["name"]):
                start = A.initial_state(project=c.get("project"), chops=FIXTURE["chops"])
                end = start
                for act in c["actions"]:
                    end = A.reduce(end, act)
                if c.get("unchanged"):
                    self.assertIs(end, start, "state object must be returned untouched")
                if "expect" in c:
                    matches(self, end, c["expect"])

    def test_reduce_never_mutates(self):
        s = A.initial_state(chops=FIXTURE["chops"])
        for c in FIXTURE["cases"]:
            for act in c["actions"]:
                before = json.dumps(s, sort_keys=True)
                nxt = A.reduce(s, act)
                self.assertEqual(json.dumps(s, sort_keys=True), before, f"mutated by {act.get('type')}")
                s = nxt

    def test_initial_state_and_doc(self):
        s = A.initial_state(project={"pads": [{"pitch": 7}]}, chops=FIXTURE["chops"])
        self.assertEqual(len(s["project"]["pads"]), 16)
        self.assertEqual(s["project"]["pads"][0], {**A.DEFAULT_PAD, "pitch": 7})
        self.assertEqual(s["project"]["pads"][15], A.DEFAULT_PAD)
        self.assertEqual(sorted(A.project_doc(s)), ["fx", "overrides", "pads", "pattern"])

    def test_copy_to_all_and_reset(self):
        s = A.initial_state(chops=FIXTURE["chops"])
        s = A.reduce(s, {"type": "pad.set", "pad": 2, "params": {"gainDb": -3, "reverse": True}})
        s = A.reduce(s, {"type": "pad.copyToAll", "pad": 2})
        self.assertTrue(all(P["gainDb"] == -3 and P["reverse"] is True for P in s["project"]["pads"]))
        s = A.reduce(s, {"type": "pad.reset", "pad": 5})
        self.assertEqual(s["project"]["pads"][5], A.DEFAULT_PAD)
        self.assertEqual(s["project"]["pads"][4]["gainDb"], -3)

    def test_step_of(self):
        self.assertEqual(A.step_of({"beat": 0}), 0)
        self.assertEqual(A.step_of({"beat": 1.25}), 5)

    def test_diff_project_round_trips(self):
        old = A.initial_state(chops=FIXTURE["chops"])["project"]
        new = A.initial_state(project={
            "pads": [{"pitch": 4}, {}, {"reverse": True}], "overrides": {"1:2": {"start": 1.0, "end": 2.0}},
            "fx": {"reverb": 0.3}, "pattern": {"bars": 1, "events": [{"beat": 0, "pad": 1, "bank": 0, "vel": 1, "pass": -1}]}},
            chops=FIXTURE["chops"])["project"]
        st = A.initial_state(project=old, chops=FIXTURE["chops"])
        for act in A.diff_project(old, new):
            st = A.reduce(st, act)
        self.assertEqual(st["project"], new)
        back = A.initial_state(project=new, chops=FIXTURE["chops"])
        for act in A.diff_project(new, old):
            back = A.reduce(back, act)
        self.assertEqual(back["project"], old)
        self.assertEqual(A.diff_project(new, new), [])


if __name__ == "__main__":
    unittest.main()
