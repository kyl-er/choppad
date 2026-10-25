import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
from choppad_advisor import DEFAULT_MODEL, advise, build_prompt, estimate_cost_usd  # noqa: E402


class Advisor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="choppad-advisor-")
        cls.analysis = load_fixture("analysis")
        cls.chops = load_fixture("chops")
        click_track(os.path.join(cls.tmp, "audio.wav"), 120)
        json.dump(cls.analysis, open(os.path.join(cls.tmp, "analysis.json"), "w"))
        json.dump(cls.chops, open(os.path.join(cls.tmp, "chops.json"), "w"))

    def test_prompt_includes_bar_and_chop_table(self):
        p = build_prompt(self.analysis, self.chops)
        self.assertIn("120", p)
        self.assertIn("1.5", p)
        self.assertIn("suggested_bank", p)

    def test_cost_estimate_under_cap_and_enforced(self):
        cost = estimate_cost_usd(240.0, DEFAULT_MODEL)
        self.assertGreater(cost, 0.0)
        self.assertLessEqual(cost, 0.05)
        with self.assertRaises(ValueError):
            advise(self.tmp, client=lambda *a: {}, max_cost_usd=1e-9, force=True)

    def test_mock_client_produces_and_caches_valid_advisor(self):
        calls = []
        def fake_client(prompt, audio_bytes, model):
            calls.append((prompt, len(audio_bytes), model))
            return load_fixture("advisor")

        out1 = advise(self.tmp, client=fake_client, force=True)
        validate_advisor(out1, chops=self.chops)
        self.assertEqual(len(calls), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "advisor.json")))
        out2 = advise(self.tmp, client=fake_client, force=False)
        self.assertEqual(len(calls), 1, "second call must return cached advisor.json without calling client")
        self.assertEqual(out1, out2)

    def test_clamps_or_filters_invalid_chop_indices_from_llm(self):
        raw = load_fixture("advisor")
        raw["suggested_bank"] = [0, 999, 2, -5, 4]
        raw["pad_labels"] = ["A", "Bad1", "B", "Bad2", "C"]
        out = advise(self.tmp, client=lambda *a: json.dumps(raw), force=True)
        validate_advisor(out, chops=self.chops)
        self.assertTrue(all(0 <= i < len(self.chops["chops"]) for i in out["suggested_bank"]))


if __name__ == "__main__":
    unittest.main()
