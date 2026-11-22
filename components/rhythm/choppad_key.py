"""key: musical key of a recording by chroma / Krumhansl-Kessler profile correlation (numpy only, no ML).

detect_key(samples, sr) -> {"tonic": "A", "mode": "minor", "confidence": 0.7}
detect_key_file(path)   -> same, from an audio file
analysis["key"] holds this dict (optional in choppad.analysis/2); `key_label(key)` -> "A min".

Method: mean-normalised log-compressed FFT magnitudes (C2 - B6) folded into 12 pitch classes per frame, frames
averaged with equal weight, then Pearson correlation against the 24 rotated major/minor profiles. confidence mixes the
best correlation with its margin over the best key of the *other* mode's runner-up (relative keys are the usual confusion).
"""
import numpy as np

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
N_FFT = 8192
HOP = 4096
MAX_SECONDS = 600  # a long file is sampled evenly rather than read whole


def chroma(samples, sr):
    """Mean 12-bin chroma (index 0 = C) of a mono signal."""
    x = np.asarray(samples, dtype=np.float32)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
    with np.errstate(divide="ignore"):
        midi_all = np.round(69 + 12 * np.log2(np.maximum(freqs, 1e-3) / 440.0)).astype(int)
    sel = (midi_all >= 36) & (midi_all <= 95)      # C2..B6: exactly five octaves, so no pitch class is favoured
    pc = midi_all[sel] % 12
    win = np.hanning(N_FFT).astype(np.float32)
    n_frames = 1 + (len(x) - N_FFT) // HOP
    step = max(1, int(np.ceil(n_frames * HOP / (MAX_SECONDS * sr))))  # very long files: every n-th frame
    acc = np.zeros(12)
    used = 0
    for a in range(0, n_frames, 256 * step):
        idx = np.arange(a, min(n_frames, a + 256 * step), step)
        frames = x[(idx[:, None] * HOP) + np.arange(N_FFT)[None, :]] * win
        full = np.abs(np.fft.rfft(frames, axis=1))
        # spectral peaks only (tonal energy; noise and the skirts of a peak are left out), square-root compressed
        peak = (full[:, 1:-1] > full[:, :-2]) & (full[:, 1:-1] >= full[:, 2:])
        full[:, 1:-1] *= peak
        full[:, 0] = full[:, -1] = 0
        mag = np.sqrt(full[:, sel])
        mag[mag < 0.1 * mag.max(axis=1, keepdims=True)] = 0
        for row in mag:
            c = np.bincount(pc, weights=row, minlength=12)
            n = np.linalg.norm(c)
            if n > 1e-6:
                acc += c / n
                used += 1
    return acc / max(used, 1)


def _corr(a, b):
    a, b = a - a.mean(), b - b.mean()
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / d) if d > 0 else 0.0


def key_from_chroma(c):
    scores = []
    for tonic in range(12):
        scores.append((_corr(c, np.roll(MAJOR, tonic)), tonic, "major"))
        scores.append((_corr(c, np.roll(MINOR, tonic)), tonic, "minor"))
    scores.sort(reverse=True)
    best, runner = scores[0], scores[1]
    conf = 0.5 * max(0.0, best[0]) + 2.0 * (best[0] - runner[0])
    return {"tonic": NAMES[best[1]], "mode": best[2], "confidence": round(float(min(1.0, max(0.0, conf))), 2)}


def detect_key(samples, sr):
    return key_from_chroma(chroma(samples, sr))


def detect_key_file(path):
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    return detect_key(x.mean(axis=1), sr)


def key_label(key):
    return f"{key['tonic']} {'min' if key['mode'] == 'minor' else 'maj'}" if key else ""
