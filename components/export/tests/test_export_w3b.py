"""W3b export: pad filter and envelope baked into Hydrogen and zip audio, written as SFZ opcodes, kept by kits."""
import json, os, shutil, sys, tempfile, unittest, wave, zipfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
import numpy as np  # noqa: E402
import choppad_voice as V  # noqa: E402
from choppad_export import bank_pads, export_hydrogen, export_sfz, export_zip, pad_pcm, _slice_declicked, _read_wav  # noqa: E402

SR = 44100


def write_noise(path, seconds=12, seed=3):
    rng = np.random.default_rng(seed)
    x = (rng.uniform(-0.5, 0.5, (int(seconds * SR), 2)) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(x.tobytes())


def read_pcm(path):
    with wave.open(path, "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, w.getnchannels()).astype(np.float64) / 32768


def band_energy(x, lo, hi):
    spec = np.abs(np.fft.rfft(x[:, 0])) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return spec[(f >= lo) & (f < hi)].mean()   # power per bin, so bands of different width compare


class Voice(unittest.TestCase):
    def test_defaults_are_neutral_so_nothing_is_baked(self):
        self.assertTrue(V.is_neutral({}))
        self.assertTrue(V.is_neutral({"attack": 0.0025, "release": 0.003, "decay": 0, "sustain": 1, "filter": {"type": "off", "cutoff": 8000, "q": 0.7}}))
        self.assertFalse(V.is_neutral({"filter": {"type": "lp", "cutoff": 900, "q": 1}}))
        self.assertFalse(V.is_neutral({"sustain": 0.5}))
        self.assertFalse(V.is_neutral({"attack": 0.05}))

    def test_filters_cut_the_right_band(self):
        rng = np.random.default_rng(1)
        x = rng.uniform(-0.5, 0.5, (SR * 2, 1)).astype(np.float32)
        lp = V.apply_filter(x, SR, {"type": "lp", "cutoff": 800, "q": 0.7})
        hp = V.apply_filter(x, SR, {"type": "hp", "cutoff": 6000, "q": 0.7})
        bp = V.apply_filter(x, SR, {"type": "bp", "cutoff": 2000, "q": 4})
        self.assertLess(band_energy(lp, 4000, 20000), 0.002 * band_energy(x, 4000, 20000))
        self.assertGreater(band_energy(lp, 100, 600), 0.6 * band_energy(x, 100, 600))
        self.assertLess(band_energy(hp, 50, 1500), 0.002 * band_energy(x, 50, 1500))
        self.assertGreater(band_energy(hp, 9000, 20000), 0.6 * band_energy(x, 9000, 20000))
        self.assertGreater(band_energy(bp, 1800, 2200), 20 * band_energy(bp, 5000, 20000))

    def test_envelope_shape(self):
        v = V.params_of({"attack": 0.01, "decay": 0.1, "sustain": 0.4, "release": 0.05})
        n = int(0.5 * SR)
        e = V.envelope(n, SR, v)
        at = lambda t: float(e[int(t * SR)])  # noqa: E731
        self.assertAlmostEqual(at(0.005), 0.5, delta=0.02)      # half way up the attack
        self.assertAlmostEqual(at(0.0101), 1.0, delta=0.02)     # peak
        self.assertAlmostEqual(at(0.06), 0.7, delta=0.03)       # half way down the decay
        self.assertAlmostEqual(at(0.3), 0.4, delta=0.01)        # sustain
        self.assertAlmostEqual(at(0.475), 0.2, delta=0.02)      # half way through the release
        self.assertLess(float(e[-1]), 0.01)

    def test_default_envelope_matches_the_plain_declick_shape(self):
        e = V.envelope(SR // 2, SR, V.params_of({}))
        self.assertAlmostEqual(float(e[SR // 4]), 1.0)
        self.assertLess(float(e[0]), 0.01)
        self.assertLess(float(e[-1]), 0.01)

    def test_sfz_opcodes(self):
        self.assertEqual(V.sfz_opcodes({}), "")
        ops = V.sfz_opcodes({"filter": {"type": "hp", "cutoff": 1200, "q": 4}, "attack": 0.02, "decay": 0.1, "sustain": 0.25, "release": 0.4})
        for want in ("fil_type=hpf_2p", "cutoff=1200.0", "resonance=15.1", "ampeg_attack=0.0200", "ampeg_decay=0.1000",
                     "ampeg_sustain=25.0", "ampeg_release=0.4000"):
            self.assertIn(want, ops)
        self.assertIn("fil_type=lpf_2p", V.sfz_opcodes({"filter": {"type": "lp", "cutoff": 500, "q": 0.5}}))
        self.assertIn("resonance=0.0", V.sfz_opcodes({"filter": {"type": "lp", "cutoff": 500, "q": 0.5}}))
        self.assertIn("bpf_2p", V.sfz_opcodes({"filter": {"type": "bp", "cutoff": 500, "q": 1}}))


class Exports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="choppad-export-w3b-")
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        cls.d = os.path.join(cls.root, "songA")
        os.makedirs(cls.d)
        write_noise(os.path.join(cls.d, "audio.wav"))
        json.dump({**load_fixture("meta"), "id": "songA"}, open(os.path.join(cls.d, "meta.json"), "w"))
        json.dump(load_fixture("chops"), open(os.path.join(cls.d, "chops.json"), "w"))

    def sounds(self, **per_pad):
        s = [{} for _ in range(16)]
        for p, v in per_pad.items():
            s[int(p[1:])] = v
        return s

    def test_neutral_pad_is_byte_for_byte_the_old_declicked_slice(self):
        pads = bank_pads(self.d, 0, {}, self.sounds())
        cache = {}
        p = pads[0]
        sr, nch, sw, pcm = pad_pcm(p, cache)
        _, _, _, frames = _read_wav(p["audio"], cache)
        want = _slice_declicked(frames, nch, sw, round(p["start"] * sr) * nch * sw, round(p["end"] * sr) * nch * sw, sr)
        self.assertEqual(pcm, want)

    def test_hydrogen_wavs_carry_the_filter_and_envelope(self):
        sounds = self.sounds(p1={"filter": {"type": "lp", "cutoff": 600, "q": 0.7}}, p2={"sustain": 0.1, "decay": 0.05})
        out = os.path.join(self.root, "h2")
        export_hydrogen(self.d, out, bank=0, overrides={}, sounds=sounds)
        plain, filtered, shaped = (read_pcm(os.path.join(out, f"chop_{i:02d}.wav")) for i in (0, 1, 2))
        self.assertLess(band_energy(filtered, 5000, 20000) / band_energy(filtered, 20, 600),
                        0.01 * band_energy(plain, 5000, 20000) / band_energy(plain, 20, 600))
        n = len(shaped)
        self.assertLess(np.abs(shaped[n // 2: n - n // 8]).max(), 0.2 * np.abs(plain[n // 2: n - n // 8]).max() + 0.01)

    def test_zip_wavs_carry_the_filter_too(self):
        sounds = self.sounds(p0={"filter": {"type": "hp", "cutoff": 6000, "q": 1}})
        z = export_zip(self.d, os.path.join(self.root, "b.zip"), bank=0, overrides={}, sounds=sounds)
        import io
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            with wave.open(io.BytesIO(zf.read("pad01.wav"))) as w:
                hp = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(np.float64)
            with wave.open(io.BytesIO(zf.read("pad02.wav"))) as w:
                plain = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2).astype(np.float64)
        self.assertEqual(sorted(names), [f"pad{i:02d}.wav" for i in range(1, 17)])
        self.assertLess(band_energy(hp, 50, 1500), 0.01 * band_energy(plain, 50, 1500))

    def test_sfz_regions_say_the_filter_and_envelope(self):
        sounds = self.sounds(p3={"filter": {"type": "bp", "cutoff": 1500, "q": 3}, "attack": 0.01, "sustain": 0.5})
        sfz = export_sfz(self.d, out_path=os.path.join(self.root, "x.sfz"), bank=0, overrides={}, sounds=sounds)
        lines = open(sfz).read().splitlines()
        r3 = [l for l in lines if "key=39 " in l][0]
        self.assertIn("fil_type=bpf_2p", r3)
        self.assertIn("cutoff=1500.0", r3)
        self.assertIn("ampeg_sustain=50.0", r3)
        r0 = [l for l in lines if "key=36 " in l][0]
        self.assertNotIn("cutoff", r0)
        self.assertNotIn("ampeg_", r0)

    def test_the_saved_project_pads_are_used_when_none_are_passed(self):
        json.dump({"pads": self.sounds(p0={"filter": {"type": "lp", "cutoff": 700, "q": 1}}), "overrides": {}},
                  open(os.path.join(self.d, "project.json"), "w"))
        self.addCleanup(os.remove, os.path.join(self.d, "project.json"))
        self.assertEqual(bank_pads(self.d, 0)[0]["params"]["filter"]["type"], "lp")


class Kits(unittest.TestCase):
    def test_kit_keeps_filter_envelope_and_clamps_them(self):
        import kits
        root = tempfile.mkdtemp(prefix="choppad-kit-w3b-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "s1"))
        json.dump({"id": "s1"}, open(os.path.join(root, "s1", "meta.json"), "w"))
        doc = kits.normalize({"name": "k", "pads": [{"song_id": "s1", "start": 0, "end": 1, "params": {
            "decay": 99, "sustain": 0.3, "filter": {"type": "lp", "cutoff": 5, "q": 2}, "bogus": 1}}]}, root)
        p = doc["pads"][0]["params"]
        self.assertEqual(p["decay"], 10)
        self.assertEqual(p["sustain"], 0.3)
        self.assertEqual(p["filter"], {"type": "lp", "cutoff": 20, "q": 2})
        self.assertNotIn("bogus", p)


if __name__ == "__main__":
    unittest.main()
