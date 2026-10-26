// Waveform view: wavesurfer.js v7 (vendored, BSD-3) bound to the muted <video> as its clock,
// drawn from server-side peaks (no second audio decode), with the current bank's chops as regions.
import WaveSurfer from "/vendor/wavesurfer.esm.js";
import RegionsPlugin from "/vendor/regions.esm.js";

const BAR_COLORS = ["rgba(80,220,120,0.22)", "rgba(80,160,255,0.22)", "rgba(255,180,60,0.22)", "rgba(230,90,200,0.22)"];

export function createWaveform(container, { peaks, duration, media, onPadClick, onSliceEdit, onUserSeek }) {
  const regions = RegionsPlugin.create();
  const ws = WaveSurfer.create({
    container,
    height: 96,
    waveColor: "#2f6b45",
    progressColor: "#5fd38a",
    cursorColor: "#ffd84a",
    cursorWidth: 2,
    barWidth: 2,
    barGap: 1,
    barRadius: 1,
    normalize: true,
    peaks: [peaks],
    duration,
    media,
    autoScroll: true,
    autoCenter: true,
    dragToSeek: true,
    plugins: [regions],
  });
  regions.on("region-clicked", (region, e) => {
    e.stopPropagation();
    onPadClick?.(Number(region.id));
  });
  regions.on("region-updated", (region) => onSliceEdit?.(Number(region.id), region.start, region.end));
  ws.on("interaction", (t) => onUserSeek?.(t));

  return {
    /** Show the current bank's pad slices [{pad, start, end, bar, custom}] — drag/resize edits a pad's slice. */
    showSlices(slices) {
      regions.clearRegions();
      for (const sl of slices) {
        const el = document.createElement("span");
        el.className = "region-label";
        el.textContent = String(sl.pad + 1) + (sl.custom ? "*" : "");
        regions.addRegion({ id: String(sl.pad), start: sl.start, end: sl.end, drag: true, resize: true, content: el,
          color: sl.custom ? "rgba(255,216,74,0.30)" : BAR_COLORS[sl.bar % 4] });
      }
    },
    flash(pad) {
      const r = regions.getRegions().find((x) => x.id === String(pad));
      if (!r?.element) return;
      r.element.classList.add("hit");
      setTimeout(() => r.element?.classList.remove("hit"), 140);
    },
    zoom(pxPerSec) { ws.zoom(pxPerSec); },
    scrollTo(t) { ws.setScrollTime?.(Math.max(0, t - 2)); },
    destroy() { ws.destroy(); },
  };
}
