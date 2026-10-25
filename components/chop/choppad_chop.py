"""chop: analysis dict -> chops dict (grid slices + downbeat-aligned banks). See contracts/README.md §3.
Pure function of its inputs (no audio access) so /api/regrid can call it in < 50 ms."""

from validate import ContractError, validate_analysis

STEPS = {"1/4": 1, "1/8": 2, "1/16": 4}
PADS_PER_BANK = 16


def _bank_size(beats_per_bar: int, grid: str) -> int:
    spb = beats_per_bar * STEPS[grid]
    return (PADS_PER_BANK // spb) * spb if spb <= PADS_PER_BANK else PADS_PER_BANK


def chop(analysis: dict, grid: str = "1/4", *, meter: str | None = None, beat_offset: int = 0,
         bpm_mult: float = 1) -> dict:
    """meter: override beats_per_bar ("3/4"). beat_offset: move bar 1 by -3..+3 beats.
    bpm_mult: 2 = split every beat in two (double time), 0.5 = keep every other beat from bar 1 (half time)."""
    validate_analysis(analysis)
    if grid not in STEPS:
        raise ContractError("grid must be 1/4, 1/8 or 1/16")
    if not isinstance(beat_offset, int) or isinstance(beat_offset, bool) or not -3 <= beat_offset <= 3:
        raise ContractError("beat_offset must be an int in [-3,3]")
    if bpm_mult not in (0.5, 1, 2) or isinstance(bpm_mult, bool):
        raise ContractError("bpm_mult must be 0.5, 1 or 2")

    beats = analysis["beats"]
    by_time = {round(b, 6): i for i, b in enumerate(beats)}
    down1 = round(analysis["downbeats"][0], 6)
    if down1 not in by_time:
        raise ContractError("first downbeat is not in beats")
    bar1_idx = by_time[down1]

    first = max(0, min(bar1_idx + beat_offset, len(beats) - 1))

    if bpm_mult == 2:
        grid_beats = []
        for b, nxt in zip(beats[first:], beats[first + 1:]):
            grid_beats.append(round(b, 6))
            grid_beats.append(round((b + nxt) / 2, 6))
    elif bpm_mult == 0.5:
        grid_beats = [round(b, 6) for b in beats[first::2]]
    else:
        grid_beats = [round(b, 6) for b in beats[first:]]

    if meter is not None:
        try:
            beats_per_bar = int(str(meter).split("/")[0])
        except (ValueError, IndexError):
            raise ContractError(f"meter must look like '3/4', got {meter!r}") from None
    else:
        beats_per_bar = analysis["meter"]["beats_per_bar"]

    steps = STEPS[grid]
    steps_per_bar = beats_per_bar * steps

    chops = []
    for start, end in zip(grid_beats, grid_beats[1:]):
        for s in range(steps):
            chops.append({
                "index": len(chops),
                "start": round(start + (end - start) * s / steps, 6),
                "end": round(start + (end - start) * (s + 1) / steps, 6),
            })
    for i, ch in enumerate(chops):
        ch["bar"] = i // steps_per_bar
        ch["beat"] = i % steps_per_bar

    bank_size = _bank_size(beats_per_bar, grid)
    banks = [list(range(i, min(i + bank_size, len(chops)))) for i in range(0, len(chops), bank_size)]

    return {
        "schema": "choppad.chops/2",
        "grid": grid,
        "bpm": round(analysis["bpm"] * bpm_mult, 4),
        "beats_per_bar": beats_per_bar,
        "bpm_mult": bpm_mult,
        "beat_offset": beat_offset,
        "pads_per_bank": PADS_PER_BANK,
        "bank_size": bank_size,
        "chops": chops,
        "banks": banks,
    }
