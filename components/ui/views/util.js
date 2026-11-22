// Shared helpers for the view modules. Presentation only: nothing here touches sound, state or the network.
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
export const fmt = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
export const SVGNS = "http://www.w3.org/2000/svg";
export function svg(tag, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.append(e);
  return e;
}
export const cssVar = (name, el = document.documentElement) => getComputedStyle(el).getPropertyValue(name).trim();
export const reducedMotion = () => globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
export const store = (key, fallback = null) => { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } };
export const remember = (key, value) => { try { localStorage.setItem(key, String(value)); } catch { /* private mode */ } };

/** Collects listeners and subscriptions so a view's destroy() can undo all of them. */
export function createDisposer() {
  const fns = [];
  return {
    add(fn) { fns.push(fn); return fn; },
    on(target, type, fn, opts) { target.addEventListener(type, fn, opts); fns.push(() => target.removeEventListener(type, fn, opts)); },
    run() { while (fns.length) { try { fns.pop()(); } catch { /* already gone */ } } },
  };
}

/** One requestAnimationFrame loop shared by the views that animate. Callbacks get (now, dt seconds). */
export function createTicker() {
  const fns = new Set();
  let raf = 0, last = 0;
  const loop = (now) => {
    raf = requestAnimationFrame(loop);
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    for (const f of [...fns]) {
      try { f(now, dt); } catch (err) { console.error(err); }
    }
  };
  return {
    add(fn) {
      fns.add(fn);
      if (!raf) { last = performance.now(); raf = requestAnimationFrame(loop); }
      return () => fns.delete(fn);
    },
    stop() { cancelAnimationFrame(raf); raf = 0; fns.clear(); },
  };
}

/** Tiny observable value, for the few things views share that are not song state (zoom, theme, volume). */
export function createCell(initial) {
  let v = initial;
  const subs = new Set();
  return {
    get: () => v,
    set(next) { if (next === v) return; v = next; for (const f of [...subs]) f(v); },
    on(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}

/** Section label -> the highlighter it gets: chorus-like sections are "live" (orange), the rest "placed" (blue). */
export const sectionIsLive = (label) => /chorus|hook|drop/i.test(label ?? "");
export function sectionAt(sections, t) {
  return sections.find((s) => s.start_s <= t && t < s.end_s) ?? null;
}
