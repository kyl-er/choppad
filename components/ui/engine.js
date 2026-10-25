// ui/engine: pad -> chop playback (WebAudio, sample-accurate) + video visual follow. See contracts/README.md §5.

/** Pure: chop for (bank, pad) or null when the pad is empty. */
export function resolveChop(chops, bank, pad) {
  throw new Error("not implemented: ui.resolveChop");
}

export function createEngine({ ctx, buffer, chops, video = null, mode = "oneshot", choke = true }) {
  throw new Error("not implemented: ui.createEngine");
}
