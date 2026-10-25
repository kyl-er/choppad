"""rhythm: audio.wav -> analysis dict (tempo, beats, downbeats, meter). See contracts/README.md §2."""


def analyze(audio_path: str, meter: str | None = None) -> dict:
    """meter: None = auto-detect, or "3/4", "4/4", ... to force beats_per_bar."""
    raise NotImplementedError("rhythm")
