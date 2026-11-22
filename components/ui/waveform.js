// Waveform view: wavesurfer.js v7 (vendored, BSD-3) bound to the muted <video> as its clock,
// drawn from server-side peaks (no second audio decode), with the current bank's chops as regions.
import WaveSurfer from "/vendor/wavesurfer.esm.js";
import RegionsPlugin from "/vendor/regions.esm.js";

const BAR_COLORS = ["rgba(80,220,120,0.22)", "rgba(80,160,255,0.22)", "rgba(255,180,60,0.22)", "rgba(230,90,200,0.22)"];

/** `style` (optional, used by the new screen): { height, wave, progress, cursor, region, custom, label, sel } as CSS colours.
 *  Without it the classic colours apply. */
export function createWaveform(container, { peaks, duration, media, onPadClick, onSliceEdit, onUserSeek, style = null }) {
  const regions = RegionsPlugin.create();
  const ws = WaveSurfer.create({
    container,
    height: style?.height ?? 96,
    waveColor: style?.wave ?? "#2f6b45",
    progressColor: style?.progress ?? "#5fd38a",
    cursorColor: style?.cursor ?? "#ffd84a",
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
  // zoom/scroll before wavesurfer has processed the peaks throws "No audio loaded": defer until ready.
  let ready = false;
  let pending = null;
  ws.on("ready", () => { ready = true; if (pending) { pending(); pending = null; } });
  const whenReady = (fn) => { if (ready) { try { fn(); } catch { /* not laid out yet */ } } else pending = fn; };
  // keyed version: before "ready", keep only the latest call per key (regions added earlier are dropped by wavesurfer)
  const queued = new Map();
  const whenReadyLatest = (key, fn) => { if (ready) fn(); else queued.set(key, fn); };
  ws.on("ready", () => { for (const fn of queued.values()) fn(); queued.clear(); });
  let lastSlices = [], selectedPad = -1, cur = style;
  const paintSelected = () => {
    for (const r of regions.getRegions()) {
      if (!r.element) continue;
      const on = Number(r.id) === selectedPad;
      r.element.style.boxShadow = on && cur ? `inset 0 0 0 2px ${cur.sel}` : "";
      r.element.style.backgroundImage = on && cur ? `linear-gradient(${cur.selFill ?? "transparent"}, ${cur.selFill ?? "transparent"})` : "";
    }
  };
  function drawSlices(slices) {
    lastSlices = slices;
    regions.clearRegions();
    for (const sl of slices) {
      const el = document.createElement("span");
      el.className = "region-label";
      el.textContent = sl.label ?? String(sl.pad + 1) + (sl.custom ? "*" : "");
      if (cur) el.style.cssText = `font:600 10px ui-monospace,Menlo,Consolas,monospace;color:${cur.label};padding:2px 4px;white-space:nowrap;pointer-events:none`;
      regions.addRegion({ id: String(sl.pad), start: sl.start, end: sl.end, drag: true, resize: true, content: el,
        color: cur ? (sl.custom ? cur.custom : cur.region) : sl.custom ? "rgba(255,216,74,0.30)" : BAR_COLORS[sl.bar % 4] });
    }
    paintSelected();
  }

  return {
    /** Show the current bank's pad slices [{pad, start, end, bar, custom}] — drag/resize edits a pad's slice. */
    showSlices(slices) {
      whenReadyLatest("slices", () => drawSlices(slices));
    },
    flash(pad) {
      const r = regions.getRegions().find((x) => x.id === String(pad));
      if (!r?.element) return;
      r.element.classList.add("hit");
      if (cur?.play) r.element.style.outline = `3px solid ${cur.play}`;
      setTimeout(() => { r.element?.classList.remove("hit"); if (r.element) r.element.style.outline = ""; }, 140);
    },
    /** new screen: recolour for a theme change, resize, mark the selected pad's region */
    setStyle(next) {
      cur = next;
      ws.setOptions({ waveColor: next.wave, progressColor: next.progress, cursorColor: next.cursor, height: next.height });
      if (lastSlices.length) drawSlices(lastSlices);
    },
    setHeight(h) { if (cur) { cur = { ...cur, height: h }; ws.setOptions({ height: h }); } },
    markSelected(pad) { selectedPad = pad; paintSelected(); },
    zoom(pxPerSec, scrollTime = null) {
      whenReady(() => { ws.zoom(pxPerSec); if (scrollTime != null) ws.setScrollTime?.(Math.max(0, scrollTime - 0.5)); });
    },
    scrollTo(t) { whenReady(() => ws.setScrollTime?.(Math.max(0, t - 0.5))); },
    destroy() { ws.destroy(); },
  };
}
