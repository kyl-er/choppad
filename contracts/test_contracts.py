"""The contracts themselves must be self-consistent: goldens validate, broken docs don't."""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from validate import ContractError, bank_size, validate_advisor, validate_analysis, validate_chops, validate_meta  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    return json.load(open(os.path.join(FIX, f"{name}.sample.json")))


class Goldens(unittest.TestCase):
    def test_goldens_valid(self):
        validate_meta(load("meta"))
        validate_analysis(load("analysis"), duration_s=load("meta")["duration_s"])
        validate_chops(load("chops"))
        validate_advisor(load("advisor"), chops=load("chops"))

    def test_golden_shape(self):
        c = load("chops")
        self.assertEqual(len(c["chops"]), 41)
        self.assertEqual([len(b) for b in c["banks"]], [16, 16, 9])

    def test_rejects_bad_bpm(self):
        a = load("analysis"); a["bpm"] = 100
        with self.assertRaises(ContractError):
            validate_analysis(a)

    def test_rejects_gap_between_chops(self):
        c = load("chops"); c["chops"][3]["start"] += 0.01
        with self.assertRaises(ContractError):
            validate_chops(c)

    def test_rejects_bank_off_downbeat(self):
        c = load("chops"); c["banks"] = [list(range(1, 17))] + c["banks"][1:]
        with self.assertRaises(ContractError):
            validate_chops(copy.deepcopy(c))

    def test_bank_size_rule(self):
        self.assertEqual(bank_size(4, "1/4"), 16)
        self.assertEqual(bank_size(3, "1/4"), 15)
        self.assertEqual(bank_size(3, "1/16"), 12)
        self.assertEqual(bank_size(2, "1/8"), 16)

    def test_rejects_video_with_audio_or_long_gop(self):
        for k, v in (("has_audio", True), ("gop", 60), ("b_frames", 2), ("height", 720)):
            m = load("meta"); m["video_info"][k] = v
            with self.assertRaises(ContractError):
                validate_meta(m)

    def test_bpm_must_be_derived_from_beats(self):
        a = load("analysis"); a["bpm"] = 121.5  # 1.25% off: allowed in v1, rejected in v2
        a["bpm_candidates"] = [121.5]
        with self.assertRaises(ContractError):
            validate_analysis(a)

    def test_rejects_meter_outside_v1_set(self):
        a = load("analysis"); a["meter"]["beats_per_bar"] = 6
        with self.assertRaises(ContractError):
            validate_analysis(a)

    def test_only_last_bank_may_be_short(self):
        c = load("chops"); c["banks"] = [list(range(0, 12)), list(range(12, 41))]
        with self.assertRaises(ContractError):
            validate_chops(c)

    def test_rejects_bad_advisor(self):
        c = load("chops")
        adv = load("advisor"); adv["suggested_bank"][0] = 999
        with self.assertRaises(ContractError):
            validate_advisor(adv, chops=c)
        adv2 = load("advisor"); adv2["pad_labels"] = ["only one"]
        with self.assertRaises(ContractError):
            validate_advisor(adv2, chops=c)


if __name__ == "__main__":
    unittest.main()
