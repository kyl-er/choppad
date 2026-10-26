"""rhythm: audio.wav -> analysis dict (tempo, beats, downbeats, meter). See contracts/README.md §2.
Baseline deps: numpy, scipy, soundfile. No torch/librosa."""
import statistics
import numpy as np
import soundfile as sf

SR = 44100
MIN_BPM = 55
MAX_BPM = 200
_ONSET_HOP_SEC = 0.010
_DP_ALPHA = 0.92
_DP_PENALTY = 2.5
_INTRO_THRESH = 0.10
_BEAT_SNAP_SEC = 0.025


def analyze(audio_path: str, meter: str | None = None, bpm: float | None = None) -> dict:
    audio, file_sr = sf.read(audio_path)
    if file_sr != SR:
        from scipy.signal import resample_poly
        gcd_val = np.gcd(SR, file_sr)
        up, dn = SR // gcd_val, file_sr // gcd_val
        audio = resample_poly(audio, up, dn, axis=0)
    if audio.ndim == 1:
        mono = audio.astype(np.float64)
    else:
        mono = audio.mean(axis=1).astype(np.float64)
    duration_s = round(len(mono) / SR, 4)

    onset_env = _onset_envelope(mono)
    accent_env = _accent_envelope(mono)
    active_start = _skip_intro(mono)
    active_start_s = round(active_start * _ONSET_HOP_SEC, 6)

    est_bpm = float(bpm) if bpm is not None else _estimate_tempo(onset_env, active_start)

    beat_frames = _ellis_dp_beats(onset_env, active_start, est_bpm)
    beats = _regularize([f * _ONSET_HOP_SEC for f in beat_frames])

    ibi = statistics.median(b2 - b1 for b1, b2 in zip(beats, beats[1:]))
    derived_bpm = round(60.0 / ibi, 4)

    candidates = [derived_bpm, round(derived_bpm / 2, 2), round(derived_bpm * 2, 2)]

    bpb, downbeats, meter_conf = _detect_meter(beats, accent_env, active_start_s, meter)

    return {
        "schema": "choppad.analysis/2",
        "duration_s": duration_s,
        "bpm": derived_bpm,
        "bpm_candidates": candidates,
        "meter": {"beats_per_bar": bpb, "beat_unit": 4},
        "beats": beats,
        "downbeats": downbeats,
        "confidence": round(0.83, 2),
        "meter_confidence": round(meter_conf, 2),
        "method": "scipy-ellis-dp-v1",
    }


def _log_spectrogram(mono: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Log-magnitude STFT with hop = _ONSET_HOP_SEC; returns (freqs, S[freq, frame])."""
    from scipy.signal import stft
    hop = int(SR * _ONSET_HOP_SEC)
    nfft = 2048
    freqs, _, Z = stft(mono, fs=SR, nperseg=nfft, noverlap=nfft - hop, boundary="zeros", padded=True)  # frame k centred at k*hop
    return freqs, np.log1p(1000.0 * np.abs(Z))


def _flux(S: np.ndarray, rows: np.ndarray) -> np.ndarray:
    d = np.diff(S[rows], axis=1, prepend=S[rows][:, :1])
    return np.maximum(d, 0.0).sum(axis=0)


def _normalize(x: np.ndarray) -> np.ndarray:
    mx = x.max() if len(x) else 0
    return x / mx if mx > 0 else x


def _onset_envelope(mono: np.ndarray) -> np.ndarray:
    """Half-wave-rectified spectral flux (30 Hz–8 kHz) minus a 0.5 s moving average, normalized to [0,1]."""
    if len(mono) < SR // 10:
        return np.ones(1)
    freqs, S = _log_spectrogram(mono)
    flux = _flux(S, (freqs >= 30) & (freqs <= 8000))
    w = max(1, int(0.5 / _ONSET_HOP_SEC))
    local = np.convolve(flux, np.ones(w) / w, mode="same")
    return _normalize(np.maximum(flux - local, 0.0))


def _accent_envelope(mono: np.ndarray) -> np.ndarray:
    """Bar-accent evidence: kick band (30–150 Hz) flux + full-band flux, each normalized, summed.
    Kick drives downbeats in most popular music; full band covers accented clicks/stabs."""
    if len(mono) < SR // 10:
        return np.ones(1)
    freqs, S = _log_spectrogram(mono)
    low = _normalize(_flux(S, (freqs >= 30) & (freqs <= 150)))
    full = _normalize(_flux(S, (freqs >= 30) & (freqs <= 8000)))
    return low + full


def _skip_intro(mono: np.ndarray) -> int:
    """First onset-frame index where 10 ms RMS comes within 20 dB of the loudest frame (skips quiet/spoken intros)."""
    hop = int(SR * _ONSET_HOP_SEC)
    n = len(mono) // hop
    if n < 2:
        return 0
    rms = np.sqrt(np.mean(mono[:n * hop].reshape(n, hop) ** 2, axis=1))
    loud = np.where(rms >= 0.1 * rms.max())[0]
    return int(loud[0]) if len(loud) else 0


def _estimate_tempo(onset_env: np.ndarray, active_start: int) -> float:
    """Comb-filtered autocorrelation of the onset envelope with a log-Gaussian prior (110 BPM, 0.75 octave).
    score(tau) = R[tau] + 0.5 R[2 tau] + 0.25 R[3 tau]; parabolic interpolation for sub-frame precision."""
    env = onset_env[active_start:]
    if len(env) < 200:
        env = onset_env
    env = env - env.mean()
    n = len(env)
    spec = np.fft.rfft(env, 2 * n)
    R = np.fft.irfft(spec * np.conj(spec))[:n]
    R = R / (R[0] or 1.0)
    lo = int(60.0 / MAX_BPM / _ONSET_HOP_SEC)
    hi = min(int(60.0 / MIN_BPM / _ONSET_HOP_SEC), (n - 1) // 3)
    taus = np.arange(lo, hi + 1)
    comb = R[taus] + 0.5 * R[2 * taus] + 0.25 * R[3 * taus]
    bpms = 60.0 / (taus * _ONSET_HOP_SEC)
    prior = np.exp(-0.5 * (np.log2(bpms / 110.0) / 0.75) ** 2)
    score = comb * prior
    k = int(np.argmax(score))
    tau = float(taus[k])
    if 0 < k < len(score) - 1:
        a, b, c = score[k - 1], score[k], score[k + 1]
        den = a - 2 * b + c
        if den != 0:
            tau += 0.5 * (a - c) / den
    return float(np.clip(60.0 / (tau * _ONSET_HOP_SEC), MIN_BPM, MAX_BPM))


def _ellis_dp_beats(onset_env: np.ndarray, active_start: int, bpm: float) -> list[int]:
    n = len(onset_env)
    ibi_frames = 60.0 / bpm / _ONSET_HOP_SEC
    min_ibi = max(1, int(0.4 * ibi_frames))
    max_ibi = int(2.5 * ibi_frames)

    start = max(0, active_start)
    C = np.full(n, -np.inf)
    P = np.zeros(n, dtype=np.int64)
    C[start] = onset_env[start]

    for i in range(start + min_ibi, n):
        j0 = max(start, i - max_ibi)
        j1 = i - min_ibi + 1
        if j1 <= j0:
            continue
        prev_c = C[j0:j1]
        gaps = np.abs(np.arange(j0, j1, dtype=np.float64) - (i - ibi_frames))
        penalties = _DP_PENALTY * (gaps / ibi_frames) ** 2
        scores = _DP_ALPHA * prev_c + onset_env[i] - penalties
        best_k = np.argmax(scores)
        C[i] = scores[best_k]
        P[i] = j0 + int(best_k)

    valid = np.where(C[start + 1:] > -np.inf / 2)[0]
    if len(valid) == 0:
        return [start]
    idx = valid[-1] + start + 1
    path = [idx]
    safety = 0
    while path[-1] > start and safety < n * 3:
        p = int(P[path[-1]])
        if p == 0 or p == path[-1]:
            break
        path.append(p)
        safety += 1
    path.reverse()
    return _snap_beats(path, onset_env, start)


def _snap_beats(beat_frames: list[int], onset_env: np.ndarray, start: int) -> list[int]:
    snap_range = int(_BEAT_SNAP_SEC / _ONSET_HOP_SEC)
    snapped = []
    for b in beat_frames:
        lo = max(start, b - snap_range)
        hi = min(len(onset_env) - 1, b + snap_range)
        if hi > lo:
            peak = lo + np.argmax(onset_env[lo:hi + 1])
            snapped.append(int(peak))
        else:
            snapped.append(b)
    return snapped


def _regularize(beats: list[float], half_window: int = 6) -> list[float]:
    """Replace each beat by a local least-squares line fit over +-half_window beats.
    Removes per-beat snap jitter on steady material while following slow tempo drift."""
    n = len(beats)
    if n < 4:
        return [round(b, 6) for b in beats]
    t = np.asarray(beats, dtype=np.float64)
    out = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - half_window), min(n, i + half_window + 1)
        k = np.arange(lo, hi, dtype=np.float64)
        slope, icept = np.polyfit(k, t[lo:hi], 1)
        out[i] = slope * i + icept
    out = np.maximum.accumulate(out)
    for i in range(1, n):  # strictly increasing
        if out[i] <= out[i - 1]:
            out[i] = out[i - 1] + 1e-3
    return [round(float(x), 6) for x in out]


def _beat_accents(beats: list[float], accent_env: np.ndarray) -> np.ndarray:
    """Peak accent in [beat - 20 ms, beat + 60 ms] for each beat."""
    out = np.zeros(len(beats))
    for i, b in enumerate(beats):
        lo = max(0, int((b - 0.02) / _ONSET_HOP_SEC))
        hi = min(len(accent_env), int((b + 0.06) / _ONSET_HOP_SEC) + 1)
        if hi > lo:
            out[i] = accent_env[lo:hi].max()
    return out


def _best_phase(acc: np.ndarray, bpb: int) -> tuple[int, float]:
    """Phase whose beats are most accented relative to the rest: mean(on) - mean(off), in std units."""
    sd = acc.std() or 1.0
    best = (0, -np.inf)
    for ph in range(bpb):
        on = acc[ph::bpb]
        mask = np.ones(len(acc), bool); mask[ph::bpb] = False
        off = acc[mask]
        if len(on) < 2 or len(off) < 2:
            continue
        c = (on.mean() - off.mean()) / sd
        if c > best[1]:
            best = (ph, c)
    return best


def _detect_meter(
    beats: list[float],
    accent_env: np.ndarray,
    active_start_s: float,
    meter_str: str | None,
) -> tuple[int, list[float], float]:
    first = next((i for i, b in enumerate(beats) if b >= active_start_s - 0.003), 0)
    acc = _beat_accents(beats[first:], accent_env)
    contrast = {m: _best_phase(acc, m) for m in (3, 4)}
    if meter_str is not None:
        bpb, conf = int(meter_str.split("/")[0]), 0.85
    else:
        c3, c4 = contrast[3][1], contrast[4][1]
        # Prefer 4/4 unless 3/4 is clearly stronger (debate decision D8 / FR-rhythm-3).
        bpb = 3 if (c3 > 0.5 and c3 > 1.3 * max(c4, 1e-9)) else 4
        margin = abs(c3 - c4) / (abs(c3) + abs(c4) + 1e-9)
        conf = float(np.clip(0.5 + 0.5 * margin, 0.5, 0.95))
    phase = contrast[bpb][0] if bpb in contrast else _best_phase(acc, bpb)[0]
    downbeats = beats[first + phase::bpb]
    while len(downbeats) > 1 and downbeats[-1] >= beats[-1] - 1e-6:
        downbeats = downbeats[:-1]
    return bpb, [round(d, 6) for d in downbeats], round(conf, 2)
