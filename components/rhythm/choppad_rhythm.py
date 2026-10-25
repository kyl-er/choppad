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
    active_start = _skip_intro(onset_env)
    active_start_s = round(active_start * _ONSET_HOP_SEC, 6)

    est_bpm = float(bpm) if bpm is not None else _estimate_tempo(onset_env, active_start)

    beat_frames = _ellis_dp_beats(onset_env, active_start, est_bpm)
    beats = [round(f * _ONSET_HOP_SEC, 6) for f in beat_frames]

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


def _onset_envelope(mono: np.ndarray) -> np.ndarray:
    hop = int(SR * _ONSET_HOP_SEC)
    n_frames = len(mono) // hop
    if n_frames < 2:
        return np.ones(1)
    padded = mono[:n_frames * hop].reshape(n_frames, hop)
    rms = np.sqrt(np.mean(padded ** 2, axis=1))
    diff = np.diff(rms, prepend=rms[0])
    onset = np.maximum(diff, 0.0)
    mx = onset.max()
    if mx > 0:
        onset /= mx
    return onset


def _accent_envelope(mono: np.ndarray) -> np.ndarray:
    from scipy.signal import butter, lfilter
    b, a = butter(4, 2500.0 / (SR / 2), btype="high")
    high = lfilter(b, a, mono)
    hop = int(SR * _ONSET_HOP_SEC)
    n_frames = len(high) // hop
    if n_frames < 2:
        return np.ones(1)
    padded = high[:n_frames * hop].reshape(n_frames, hop)
    rms = np.sqrt(np.mean(padded ** 2, axis=1))
    diff = np.diff(rms, prepend=rms[0])
    accent = np.maximum(diff, 0.0)
    mx = accent.max()
    if mx > 0:
        accent /= mx
    return accent


def _skip_intro(onset_env: np.ndarray) -> int:
    mx = onset_env.max()
    if mx <= 0:
        return 0
    thresh = _INTRO_THRESH * mx
    gt = np.where(onset_env >= thresh)[0]
    if len(gt) == 0:
        return 0
    return int(gt[0])


def _estimate_tempo(onset_env: np.ndarray, active_start: int) -> float:
    env = onset_env[active_start:]
    if len(env) < 100:
        env = onset_env
    n = len(env)

    freqs = np.fft.rfftfreq(n, d=_ONSET_HOP_SEC)
    mag = np.abs(np.fft.rfft(env))

    prior = np.ones_like(freqs)
    for i, f in enumerate(freqs):
        bpm_val = f * 60.0
        if bpm_val <= 0:
            prior[i] = 0
            continue
        if bpm_val < MIN_BPM - 5 or bpm_val > MAX_BPM + 5:
            prior[i] = 0
            continue
        log_pos = np.log(bpm_val / 95.0) if bpm_val > 0 else -10
        base = np.exp(-0.5 * (log_pos / 0.30) ** 2)
        if bpm_val > 140:
            base *= np.exp(-0.5 * ((bpm_val - 140) / 20) ** 2)
        elif bpm_val < 60:
            base *= np.exp(-0.5 * ((60 - bpm_val) / 15) ** 2)
        prior[i] = base

    scored = mag * prior
    scored[:1] = 0
    peak_idx = int(np.argmax(scored[1:]) + 1)
    est_bpm = freqs[peak_idx] * 60.0

    if est_bpm > 160:
        while est_bpm > 130:
            est_bpm /= 2.0

    return float(np.clip(est_bpm, MIN_BPM, MAX_BPM))


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


def _detect_meter(
    beats: list[float],
    accent_env: np.ndarray,
    active_start_s: float,
    meter_str: str | None,
) -> tuple[int, list[float], float]:
    bpb = int(meter_str.split("/")[0]) if meter_str is not None else _score_meter(beats, accent_env, active_start_s)

    first_beat_idx = 0
    for i, b in enumerate(beats):
        if b >= active_start_s - 0.003:
            first_beat_idx = i
            break

    best_score = -np.inf
    best_downbeats: list[float] = []

    for phase in range(bpb):
        if first_beat_idx + phase >= len(beats):
            break
        dbeats = beats[first_beat_idx + phase::bpb]
        if len(dbeats) < 2:
            continue
        score = _score_phase_accent(dbeats, accent_env)
        if score > best_score:
            best_score = score
            best_downbeats = dbeats

    if not best_downbeats:
        best_downbeats = beats[first_beat_idx::bpb]

    while len(best_downbeats) > 1 and abs(best_downbeats[-1] - beats[-1]) < 0.0001:
        best_downbeats = best_downbeats[:-1]

    downbeats = [round(d, 6) for d in best_downbeats]
    meter_conf = round(0.6 + 0.2 * max(0, min(1, best_score / 10)), 2) if meter_str is None else 0.85

    return bpb, downbeats, meter_conf


def _score_phase_accent(dbeats: list[float], accent_env: np.ndarray) -> float:
    score = 0.0
    for d in dbeats:
        frame = int(d / _ONSET_HOP_SEC)
        if 0 <= frame < len(accent_env):
            score += accent_env[frame] * 10.0
    return score


def _score_meter(beats: list[float], accent_env: np.ndarray, active_start_s: float) -> int:
    first_beat_idx = 0
    for i, b in enumerate(beats):
        if b >= active_start_s - 0.003:
            first_beat_idx = i
            break

    scores = {}
    for bpb in (2, 3, 4):
        best = -np.inf
        for phase in range(bpb):
            if first_beat_idx + phase >= len(beats):
                continue
            dbeats = beats[first_beat_idx + phase::bpb]
            if len(dbeats) < 2:
                continue
            score = _score_phase_accent(dbeats, accent_env)
            if score > best:
                best = score
        scores[bpb] = best

    if 3 in scores and 4 in scores:
        if scores[3] > scores[4] * 1.15:
            return 3
        return 4
    if 3 in scores and scores[3] >= scores.get(4, -np.inf):
        return 3
    return 4
