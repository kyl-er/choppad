"""transient: onset-based chops (second chop mode next to the tempo grid). numpy/scipy only.

chop_transients(source, analysis, *, sensitivity=0.5, min_len_s=0.06, max_slices=None) -> chops

`source` is any of: an audio file path, `(samples, sr)`, an onset table `{"times": [...], "strengths": [...]}`
(as returned by `detect_onsets`), or a plain list of onset times (all treated as equally strong).
The result is a chops document (contracts/README.md §3) in "transient" mode: slices are contiguous
(`end[i] == start[i+1]`), banks hold 16 chops, and `bar`/`beat` come from the nearest 1/16 position of the
analysis grid. What a transient document cannot promise is the grid rule that bank n starts on a downbeat or
that `bar == index // steps_per_bar`: slice starts follow the music, not the grid.
Extra keys: `"mode": "transient"`, `"sensitivity"`.
"""
import numpy as np

N_FFT = 1024
HOP = 256            # 5.8 ms at 44.1 kHz: fine enough for +-10 ms onset accuracy
MEDIAN_S = 0.30      # window of the local-median baseline subtracted from the spectral flux
PADS_PER_BANK = 16
SUBDIV = 4           # nearest 1/16 note when filling bar/beat
FLOOR = 0.02         # weakest candidate kept, relative to the 90th percentile of candidate strengths


def _mono(samples):
    x = np.asarray(samples, dtype=np.float32)
    return x.mean(axis=1) if x.ndim == 2 else x


def _load(path):
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    return x.mean(axis=1), sr


def _flux(x, sr):
    """Half-wave rectified log-magnitude spectral flux per hop (bins below 10 kHz)."""
    hop, n_fft = HOP, N_FFT
    if len(x) < n_fft + hop:
        x = np.pad(x, (0, n_fft + hop - len(x)))
    win = np.hanning(n_fft).astype(np.float32)
    kmax = int(10000 / (sr / n_fft)) + 1
    n_frames = 1 + (len(x) - n_fft) // hop
    out = np.zeros(n_frames, dtype=np.float32)
    prev = None
    step = 4096
    for a in range(0, n_frames, step):
        b = min(n_frames, a + step)
        idx = (np.arange(a, b)[:, None] * hop) + np.arange(n_fft)[None, :]
        mag = np.abs(np.fft.rfft(x[idx] * win, axis=1))[:, :kmax]
        lm = np.log1p(1000.0 * mag).astype(np.float32)
        d = np.diff(lm, axis=0, prepend=(lm[:1] if prev is None else prev[None, :]))
        out[a:b] = np.maximum(d, 0).sum(axis=1)
        prev = lm[-1]
    return out


def _refine(x, sr, t_frame):
    """Move a flux peak to the steepest rise of the 1 ms RMS envelope just before/at it, minus 2 ms."""
    lo = max(0, int((t_frame - 0.035) * sr))
    hi = min(len(x), int((t_frame + 0.012) * sr))
    if hi - lo < 8:
        return t_frame
    seg = x[lo:hi].astype(np.float64) ** 2
    w = max(2, int(0.001 * sr))
    env = np.sqrt(np.convolve(seg, np.ones(w) / w, mode="same"))
    rise = np.diff(env)
    if rise.max() <= 0:
        return t_frame
    t = (lo + int(np.argmax(rise))) / sr - 0.002
    return max(0.0, t)


def detect_onsets(source, sr=None):
    """All onset candidates, strongest first-class citizens: {"times": [...], "strengths": [...]} with
    strengths normalised so the 90th percentile is 1.0. Selection by sensitivity happens in `select_onsets`."""
    if isinstance(source, str):
        x, sr = _load(source)
    elif isinstance(source, tuple):
        x, sr = _mono(source[0]), source[1]
    else:
        x = _mono(source)
    if sr is None:
        raise ValueError("sample rate needed")
    from scipy.ndimage import median_filter
    flux = _flux(x, sr)
    base = median_filter(flux, size=max(3, int(MEDIAN_S * sr / HOP)) | 1, mode="nearest")
    nov = np.maximum(flux - base, 0)
    # local maxima (ties: first of a plateau)
    peaks = np.flatnonzero((nov[1:-1] > nov[:-2]) & (nov[1:-1] >= nov[2:]) & (nov[1:-1] > 0)) + 1
    if not len(peaks):
        return {"times": [], "strengths": []}
    ref = float(np.percentile(nov[peaks], 90)) or 1.0
    strengths = nov[peaks] / ref
    keep = strengths >= FLOOR
    peaks, strengths = peaks[keep], strengths[keep]
    # frame i is centred at i*HOP + N_FFT/2; the flux peak lags the true start, _refine walks back
    times = []
    for p in peaks:
        times.append(round(_refine(x, sr, (p * HOP + N_FFT / 2) / sr), 6))
    order = np.argsort(times)
    return {"times": [times[i] for i in order], "strengths": [round(float(strengths[i]), 4) for i in order]}


def select_onsets(table, sensitivity=0.5, min_len_s=0.06, max_slices=None):
    """Pick onsets from a candidate table. Higher sensitivity keeps weaker onsets (so the count never drops as the
    sensitivity rises); candidates closer than `min_len_s` to a stronger one are dropped; `max_slices` keeps the
    strongest. Returns sorted times."""
    s = float(sensitivity)
    if not 0.0 <= s <= 1.0:
        raise ValueError("sensitivity must be in [0, 1]")
    thr = (1.0 - s) ** 1.5 * 1.2 + FLOOR  # s=0: only the strongest (about 1 slice per beat), s=1: everything above the floor
    t = np.asarray(table["times"], dtype=np.float64)
    st = np.asarray(table["strengths"], dtype=np.float64)
    idx = np.flatnonzero(st >= thr)
    chosen = []
    for i in idx[np.argsort(-st[idx], kind="stable")]:
        if all(abs(t[i] - t[j]) >= min_len_s for j in chosen):
            chosen.append(i)
            if max_slices and len(chosen) >= max_slices:
                break
    return sorted(round(float(t[i]), 6) for i in chosen)


def _table(source):
    if isinstance(source, dict):
        return source
    if isinstance(source, (list, np.ndarray)) and (len(source) == 0 or np.isscalar(source[0])):
        return {"times": [float(v) for v in source], "strengths": [1.0] * len(source)}
    return detect_onsets(source)


def _beat_position(analysis, bar1_beats):
    """time -> beats since bar 1 (float), interpolating the analysis beats; extrapolates at the ends."""
    beats = np.asarray(analysis["beats"], dtype=np.float64)
    down1 = float(analysis["downbeats"][0])
    i1 = int(np.argmin(np.abs(beats - down1))) + bar1_beats
    ibi = float(np.median(np.diff(beats)))

    def pos(t):
        if t <= beats[0]:
            k = (t - beats[0]) / ibi
        elif t >= beats[-1]:
            k = len(beats) - 1 + (t - beats[-1]) / ibi
        else:
            j = int(np.searchsorted(beats, t, side="right")) - 1
            k = j + (t - beats[j]) / (beats[j + 1] - beats[j])
        return k - i1
    return pos


def chop_transients(source, analysis, *, sensitivity=0.5, min_len_s=0.06, max_slices=None, meter=None,
                    beat_offset=0, duration_s=None):
    """See module docstring. `analysis` supplies tempo, meter and beat positions for bar/beat labels and the
    audio duration for the last slice; `meter` ("3/4") and `beat_offset` act like in the grid chop."""
    table = _table(source)
    times = select_onsets(table, sensitivity, min_len_s, max_slices)
    end_of_song = float(duration_s or analysis.get("duration_s") or 0)
    if end_of_song and times:
        times = [t for t in times if t < end_of_song - min_len_s]
    if not times:
        raise ValueError("no transients found; raise the sensitivity or use the grid chop")
    bpb = int(str(meter).split("/")[0]) if meter else analysis["meter"]["beats_per_bar"]
    end_last = max(end_of_song, times[-1] + min_len_s)
    pos = _beat_position(analysis, int(beat_offset))
    edges = times + [round(end_last, 6)]
    steps_per_bar = bpb * SUBDIV
    chops = []
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        q = int(round(pos(a) * SUBDIV))        # nearest 1/16 since bar 1
        q = max(q, 0)
        chops.append({"index": i, "start": a, "end": b, "bar": q // steps_per_bar, "beat": q % steps_per_bar})
    banks = [list(range(i, min(i + PADS_PER_BANK, len(chops)))) for i in range(0, len(chops), PADS_PER_BANK)]
    return {
        "schema": "choppad.chops/2",
        "mode": "transient",
        "sensitivity": round(float(sensitivity), 4),
        "grid": "1/16",
        "bpm": round(float(analysis["bpm"]), 4),
        "beats_per_bar": bpb,
        "bpm_mult": 1,
        "beat_offset": int(beat_offset),
        "pads_per_bank": PADS_PER_BANK,
        "bank_size": PADS_PER_BANK,
        "chops": chops,
        "banks": banks,
    }
