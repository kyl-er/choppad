"""rhythm: audio.wav -> analysis dict (tempo, beats, downbeats, meter). See contracts/README.md §2.
Baseline deps: numpy, scipy, soundfile (librosa only if it installs from wheels). No torch."""


def analyze(audio_path: str, meter: str | None = None, bpm: float | None = None) -> dict:
    """meter: None = auto-detect from {2,3,4}, or "3/4" etc. to force beats_per_bar.
    bpm: None = auto, or a value to force the tempo (beats are re-tracked at that period)."""
    raise NotImplementedError("rhythm")
