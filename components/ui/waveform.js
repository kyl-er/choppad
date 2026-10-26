// Waveform view: wavesurfer.js v7 (vendored, BSD-3) bound to the muted <video> as its clock,
// drawn from server-side peaks (no second audio decode), with the current bank's chops as regions.
import WaveSurfer from "/vendor/wavesurfer.esm.js";
import RegionsPlugin from "/vendor/regions.esm.js";

const BAR_COLORS = ["rgba(80,220,120,0.22)", "rgba(80,160,255,0.22)", "rgba(255,180,60,0.22)", "rgba(230,90,200,0.22)"];

export function createWaveform(container, { peaks, duration, media, onChopClick, onUserSeek }) {
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
    onChopClick?.(Number(region.id));
  });
  ws.on("interaction", (t) => onUserSeek?.(t));

  return {
    /** Show chops (array of {index,start,end,bar}) labelled with their pad numbers. */
    showChops(chops, padOf) {
      regions.clearRegions();
      for (const c of chops) {
        const el = document.createElement("span");
        el.className = "region-label";
        el.textContent = String(padOf(c.index) + 1);
        regions.addRegion({ id: String(c.index), start: c.start, end: c.end, color: BAR_COLORS[c.bar % 4],
          drag: false, resize: false, content: el });
      }
    },
    flash(chopIndex) {
      const r = regions.getRegions().find((x) => x.id === String(chopIndex));
      if (!r?.element) return;
      r.element.classList.add("hit");
      setTimeout(() => r.element?.classList.remove("hit"), 140);
    },
    zoom(pxPerSec) { ws.zoom(pxPerSec); },
    scrollTo(t) { ws.setScrollTime?.(Math.max(0, t - 2)); },
    destroy() { ws.destroy(); },
  };
}
