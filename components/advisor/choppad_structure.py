"""structure: offline song-section detection (no network, no ML).

sections(media_dir) -> {"schema": "choppad.sections/1", "method": ..., "sections": [
    {"label": "chorus", "letter": "B", "start_bar": 8, "end_bar": 16, "start_s": .., "end_s": .., "energy": 0..1}, ...]}
Cached as <media_dir>/sections.json (invalidated when analysis.json is newer).

Method (classic MIR): per-bar features = 12-bin chroma + log-energy + spectral centroid; Foote novelty with a
checkerboard kernel over the bar self-similarity matrix; peaks -> boundaries (>= MIN_BARS apart, snapped to even
bars); sections grouped into letters by feature similarity; names from repetition + energy + position.
"""
import json
import os

import numpy as np
import soundfile as sf

MIN_BARS = 4
KERNEL_BARS = 4  # half-width of the checkerboard kernel, in bars


def _bar_features(audio_path, downbeats, beats_per_bar, beats):
    y, sr = sf.read(audio_path, dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    edges = list(downbeats)
    if beats:  # close the last bar
        ibi = float(np.median(np.diff(beats)))
        edges.append(min(len(y) / sr, downbeats[-1] + beats_per_bar * ibi))
    nfft = 4096
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    valid = (freqs > 55) & (freqs < 5000)
    pitch_class = (np.round(12 * np.log2(freqs[valid] / 440.0)) % 12).astype(int)
    feats, energy = [], []
    for a, b in zip(edges, edges[1:]):
        seg = y[int(a * sr):int(b * sr)]
        if len(seg) < nfft:
            seg = np.pad(seg, (0, nfft - len(seg)))
        hops = range(0, len(seg) - nfft + 1, nfft // 2)
        mag = np.mean([np.abs(np.fft.rfft(seg[h:h + nfft] * np.hanning(nfft))) for h in hops], axis=0)
        chroma = np.bincount(pitch_class, weights=mag[valid], minlength=12)
        chroma = chroma / (np.linalg.norm(chroma) + 1e-9)
        rms = float(np.sqrt(np.mean(seg ** 2)) + 1e-9)
        centroid = float((freqs * mag).sum() / (mag.sum() + 1e-9)) / 5000.0
        feats.append(np.concatenate([chroma, [np.log10(rms) / 4 + 1, centroid]]))
        energy.append(rms)
    return np.array(feats), np.array(energy), edges


def _novelty(F):
    X = F - F.mean(axis=0)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    S = X @ X.T
    n, k = len(S), KERNEL_BARS
    sign = np.sign(np.arange(-k, k) + 0.5)
    kernel = np.outer(sign, sign) * np.outer(np.hanning(2 * k), np.hanning(2 * k))
    nov = np.zeros(n)
    for i in range(k, n - k + 1):
        nov[i] = float((S[i - k:i + k, i - k:i + k] * kernel).sum())
    return np.maximum(nov, 0), S


def _boundaries(nov, n):
    cands = [i for i in range(1, n - 1) if nov[i] > 0 and nov[i] >= nov[i - 1] and nov[i] >= nov[i + 1]]
    thresh = np.percentile(nov[nov > 0], 60) if np.any(nov > 0) else 0
    picked = []
    for i in sorted(cands, key=lambda j: -nov[j]):
        if nov[i] < thresh:
            continue
        i2 = int(round(i / 2) * 2)  # phrases are usually an even number of bars
        if MIN_BARS <= i2 <= n - MIN_BARS and all(abs(i2 - p) >= MIN_BARS for p in picked):
            picked.append(i2)
    return [0] + sorted(picked) + [n]


def _label(segs, F, energy):
    means = [F[a:b].mean(axis=0) for a, b in segs]
    letters, protos = [], []
    for m in means:
        sims = [float(np.dot(m, p) / (np.linalg.norm(m) * np.linalg.norm(p) + 1e-9)) for p in protos]
        if sims and max(sims) > 0.985:
            letters.append(chr(65 + int(np.argmax(sims))))
        else:
            protos.append(m)
            letters.append(chr(64 + len(protos)))
    seg_energy = np.array([energy[a:b].mean() for a, b in segs])
    e = (seg_energy - seg_energy.min()) / (np.ptp(seg_energy) + 1e-9)
    counts = {L: letters.count(L) for L in letters}
    repeated = [L for L in counts if counts[L] > 1]
    chorus = max(repeated, key=lambda L: np.mean([e[i] for i, x in enumerate(letters) if x == L])) if repeated else None
    names = []
    for i, L in enumerate(letters):
        if i == 0 and counts[L] == 1 and e[i] < 0.5:
            names.append("intro")
        elif i == len(letters) - 1 and counts[L] == 1 and len(letters) > 2:
            names.append("outro")
        elif L == chorus:
            names.append("chorus")
        elif counts[L] > 1:
            names.append("verse")
        else:
            names.append(f"part {L}")
    return letters, names, e


def sections(media_dir: str, *, force: bool = False) -> dict:
    out_path = os.path.join(media_dir, "sections.json")
    ana_path = os.path.join(media_dir, "analysis.json")
    if not force and os.path.isfile(out_path) and os.path.getmtime(out_path) >= os.path.getmtime(ana_path):
        return json.load(open(out_path))
    a = json.load(open(ana_path))
    bpb = a["meter"]["beats_per_bar"]
    F, energy, edges = _bar_features(os.path.join(media_dir, "audio.wav"), a["downbeats"], bpb, a["beats"])
    n = len(F)
    if n < 2 * MIN_BARS:
        bounds = [0, n]
    else:
        nov, _ = _novelty(F)
        bounds = _boundaries(nov, n)
    segs = list(zip(bounds, bounds[1:]))
    letters, _, _ = _label(segs, F, energy)
    merged = []  # neighbours that sound the same are one section
    for seg, L in zip(segs, letters):
        if merged and merged[-1][1] == L:
            merged[-1] = ((merged[-1][0][0], seg[1]), L)
        else:
            merged.append((seg, L))
    segs = [m[0] for m in merged]
    letters, names, e = _label(segs, F, energy)
    doc = {"schema": "choppad.sections/1", "method": "foote-novelty+chroma/v1", "sections": [
        {"label": nm, "letter": L, "start_bar": int(s), "end_bar": int(t), "start_s": round(edges[s], 3),
         "end_s": round(edges[t], 3), "energy": round(float(en), 3)}
        for (s, t), L, nm, en in zip(segs, letters, names, e)]}
    json.dump(doc, open(out_path, "w"), indent=1)
    return doc
