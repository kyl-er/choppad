"""chop: analysis dict -> chops dict (grid slices + downbeat-aligned banks). See contracts/README.md §3.
Pure function of its inputs (no audio access) so /api/regrid can call it in < 50 ms."""


def chop(analysis: dict, grid: str = "1/4", *, meter: str | None = None, beat_offset: int = 0,
         bpm_mult: float = 1) -> dict:
    """meter: override beats_per_bar ("3/4"). beat_offset: move bar 1 by -3..+3 beats.
    bpm_mult: 2 = split every beat in two (double time), 0.5 = keep every other beat from bar 1 (half time)."""
    raise NotImplementedError("chop")
