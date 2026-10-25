"""The contracts themselves must be self-consistent: goldens validate, broken docs don't."""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from validate import ContractError, validate_analysis, validate_chops, validate_meta  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    return json.load(open(os.path.join(FIX, f"{name}.sample.json")))


class Goldens(unittest.TestCase):
    def test_goldens_valid(self):
        validate_meta(load("meta"))
        validate_analysis(load("analysis"), duration_s=load("meta")["duration_s"])
        validate_chops(load("chops"))

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


if __name__ == "__main__":
    unittest.main()
