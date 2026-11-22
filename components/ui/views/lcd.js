// The control surface: monochrome LCD with page keys and six soft keys, the data wheel and the numeric keypad.
// Pages are data (lcdpages.js). Every field is a real control (range / checkbox / buttons / button / text input),
// so it works with the mouse, the data wheel, the keypad, the soft keys, the arrow keys and Tab.
import { buildPages, PAGE_ORDER } from "./lcdpages.js";
import { clamp, createDisposer, esc } from "./util.js";

const slug = (v) => String(v).replace(/[^\w]+/g, "-").replace(/^-|-$/g, "").toLowerCase();
const optOf = (o) => (o !== null && typeof o === "object" ? o : { v: o, l: String(o) });
const snap = (v, f) => { const s = f.step ?? 1; return clamp(+(Math.round((v - (f.min ?? 0)) / s) * s + (f.min ?? 0)).toFixed(4), f.min ?? -Infinity, f.max ?? Infinity); };

export function mount(root, session, env) {
  const d = createDisposer();
  const { store } = session;
  const st = () => store.get();
  const dispatch = (a, m) => store.dispatch(a, m);
  const L = { page: "MAIN", sub: 0, field: -1, keybuf: "" };
  const ctx = {
    session, st, dispatch, env, out: {}, tapState: { taps: [], bpm: 0, text: "" },
    setting: (params) => dispatch({ type: "settings.set", params }),
    fxSet: (params) => dispatch({ type: "fx.set", params }),
    toast: env.toast,
    refresh: () => refresh(),
  };
  ctx.out.devices = devicesText(env.input.getDevices());
  const PAGES = buildPages(ctx);
  env.pages = PAGES;   // other views (and tests) can read the page list; append fields there to extend the LCD
  let fields = [], rows = [], mainBody = null;

  root.innerHTML = `<span class="fignum tag">FIG. 4 — CONTROL SURFACE · FRONT ELEVATION</span>
    <div class="lcdu" data-c="1">
      <div class="modes" data-c="2" role="group" aria-label="LCD pages"></div>
      <div class="lcd" role="group" aria-label="LCD display">
        <div class="lcd-head"><span class="lcd-pg"></span><span class="lcd-sub"></span><span class="lcd-msg" role="status"></span><span class="lcd-in"></span></div>
        <div class="lcd-body"></div>
        <div class="lcd-softs"></div>
      </div>
      <div class="softs"></div>
    </div>
    <div class="wk-zone">
      <div class="wheel-wrap" data-c="3"><div data-wheel></div><span class="cap">Data</span></div>
      <div data-c="4" style="position:relative"><div class="keypad" role="group" aria-label="Numeric keypad"></div></div>
    </div>`;
  const $ = (s) => root.querySelector(s);
  const modesEl = $(".modes"), bodyEl = $(".lcd-body"), lcdSofts = $(".lcd-softs"), softsEl = $(".softs");
  const pgEl = $(".lcd-pg"), subEl = $(".lcd-sub"), msgEl = $(".lcd-msg"), inEl = $(".lcd-in");

  for (const m of PAGE_ORDER) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "mode"; b.dataset.mode = m; b.textContent = m;
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", () => gotoPage(m));
    modesEl.append(b);
  }
  for (let i = 0; i < 6; i++) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "soft"; b.dataset.soft = i;
    b.innerHTML = `<span>F${i + 1}</span>`;
    b.setAttribute("aria-label", `Soft key F${i + 1}`);
    b.addEventListener("click", () => softPress(i));
    softsEl.append(b);
  }
  const keypad = $(".keypad");
  for (const k of ["7", "8", "9", "4", "5", "6", "1", "2", "3", "0", ".", "ENT"]) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "key" + (k === "ENT" ? " ent" : ""); b.textContent = k;
    b.setAttribute("aria-label", k === "ENT" ? "Enter: apply the typed value" : `Key ${k}`);
    b.addEventListener("click", () => { if (k === "ENT") keypadEnter(); else { L.keybuf = (L.keybuf + k).slice(0, 8); showIn(); } });
    keypad.append(b);
  }

  // ---------------------------------------------------------------- pages
  const sub = () => { const p = PAGES[L.page]; return p.subs[L.sub % p.subs.length]; };
  function gotoPage(p, subIndex = null) {
    if (L.page === p && subIndex === null) L.sub = (L.sub + 1) % PAGES[p].subs.length;
    else { L.page = p; L.sub = subIndex ?? 0; }
    L.field = -1; L.keybuf = "";
    build();
  }
  env.actions.gotoPage = gotoPage;
  env.actions.tap = () => { ctx.tap(); gotoPage("GRID", 1); };

  function build() {
    const page = PAGES[L.page], s = sub();
    bodyEl.className = `lcd-body ${page.cls ?? ""} ${s.cls ?? ""}`.trim();
    bodyEl.dataset.page = L.page;
    bodyEl.innerHTML = "";
    rows = [];
    apis.length = 0;
    mainBody = null;
    fields = s.fields(ctx);
    // a page with its own body (MAIN) still gets rows for its fields, kept off screen: soft keys and the wheel drive them
    fields.forEach((f, i) => { if (f.t !== "x") { const r = makeRow(f, i); rows[i] = r; if (!s.body) bodyEl.append(r.el); } });
    if (s.body) { mainBody = s.body(ctx); bodyEl.append(mainBody.el); }
    if (s.info) { const info = document.createElement("div"); info.className = `lcd-line lcd-info ${s.info.cls ?? ""}`; bodyEl.append(info); rows.info = { el: info, html: s.info.html }; }
    lcdSofts.innerHTML = fields.map((f, i) => `<span class="ls${f.t === "x" ? " nil" : ""}" data-i="${i}"></span>`).join("");
    modesEl.querySelectorAll(".mode").forEach((b) => { const on = b.dataset.mode === L.page; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
    pgEl.textContent = page.title;
    subEl.textContent = `${(L.sub % page.subs.length) + 1}/${page.subs.length}`;
    refresh(true);
  }

  const labelOf = (f) => (typeof f.label === "function" ? f.label() : f.label);
  function makeRow(f, i) {
    let el, upd;
    const lab = () => esc(labelOf(f));
    const select = () => { if (L.field !== i) { L.field = i; markSel(); } };
    if (f.t === "n" || f.t === "i") {
      const range = f.t === "n";
      el = document.createElement("label");
      el.className = `lcd-row ${range ? "n" : "i"}`;
      el.innerHTML = `<span class="lb">${lab()}</span><i></i>${range ? `<output data-out="${f.name}"></output>` : ""}<input name="${f.name}" class="${f.cls ?? ""}" aria-label="${esc(labelOf(f))}" type="${range ? "range" : "number"}" min="${f.min}" max="${f.max}" step="${range ? f.step : "any"}">`;
      const input = el.querySelector("input"), out = el.querySelector("output");
      let timer = 0, pending = null;
      const show = (v) => { if (out) out.textContent = f.fmt ? f.fmt(v) : String(v); if (range) el.style.setProperty("--fill", `${((v - f.min) / (f.max - f.min)) * 100}%`); };
      const commit = () => { clearTimeout(timer); timer = 0; if (pending !== null) { const v = pending; pending = null; f.set(v); } };
      // set(v): the one path wheel, keypad, drag, keys and the native input all use
      const setValue = (v, immediate = false) => {
        v = range ? snap(v, f) : clamp(v, f.min ?? -Infinity, f.max ?? Infinity);
        input.value = String(v);
        show(v);
        if (f.deferred) { pending = v; if (immediate) commit(); else if (range) { clearTimeout(timer); timer = setTimeout(commit, 300); } }
        else f.set(v);
      };
      row_api(i, { setValue, step: (dir) => setValue(Number(input.value || f.get()) + dir * (f.step ?? 1)), el, input });
      input.addEventListener("input", () => { const v = Number(input.value); if (Number.isFinite(v) && input.value !== "") setValue(v, false); });
      input.addEventListener("change", () => { const v = Number(input.value); if (Number.isFinite(v)) setValue(v, true); });
      input.addEventListener("keydown", (e) => { if (!range && e.key === "Enter") { setValue(Number(input.value), true); e.preventDefault(); } });
      if (range) {
        // drag across the row = scrub; a plain click only selects the field
        let x0 = null, v0 = 0;
        el.addEventListener("pointerdown", (e) => { select(); x0 = e.clientX; v0 = Number(input.value); try { el.setPointerCapture(e.pointerId); } catch { /* touch */ } });
        el.addEventListener("pointermove", (e) => { if (x0 === null || !el.hasPointerCapture?.(e.pointerId)) return; const w = el.getBoundingClientRect().width || 300; if (Math.abs(e.clientX - x0) > 3) setValue(v0 + ((e.clientX - x0) / w) * (f.max - f.min)); });
        el.addEventListener("pointerup", () => { x0 = null; });
        el.addEventListener("click", (e) => { if (e.target === el || e.target.closest(".lb,output,i")) { e.preventDefault(); input.focus(); } });
      }
      upd = () => {
        const v = f.get();
        if (pending === null && document.activeElement !== input) input.value = String(range ? v : +v.toFixed(3));
        if (pending === null) show(v);
        const ov = el.querySelector("output"); if (ov && pending === null) ov.textContent = f.fmt ? f.fmt(v) : String(v);
      };
    } else if (f.t === "b") {
      el = document.createElement("label");
      el.className = "lcd-row b";
      el.innerHTML = `<span class="lb">${lab()}</span><i></i><output data-out="${f.name}"></output><input type="checkbox" name="${f.name}" class="${f.cls ?? ""}" aria-label="${esc(labelOf(f))}">`;
      const input = el.querySelector("input");
      input.addEventListener("change", () => { f.set(input.checked); refresh(); });
      row_api(i, { toggle: () => { f.set(!f.get()); refresh(); }, el, input });
      upd = () => { const v = !!f.get(); input.checked = v; el.querySelector("output").textContent = v ? "ON" : "OFF"; el.classList.toggle("is-on", v); };
    } else if (f.t === "e") {
      el = document.createElement("div");
      el.className = `lcd-row e ${f.cls ?? ""}`;
      el.setAttribute("role", "group"); el.setAttribute("aria-label", labelOf(f));
      el.innerHTML = `<span class="lb">${lab()}</span><i></i><span class="seg"></span>`;
      const seg = el.querySelector(".seg");
      const opts = f.opts.map(optOf);
      for (const o of opts) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = `opt${f.optClsPrefix ? " " + f.optClsPrefix + slug(o.l) : ""}`;
        b.textContent = o.l;
        if (f.optAttr) b.setAttribute(f.optAttr, o.v);
        b.addEventListener("click", () => { select(); f.set(o.v); refresh(); });
        seg.append(b);
      }
      const cycle = (dir) => { const cur = opts.findIndex((o) => String(o.v) === String(f.get())); f.set(opts[(cur + dir + opts.length) % opts.length].v); refresh(); };
      row_api(i, { cycle, el, opts, set: (v) => { f.set(v); refresh(); } });
      upd = () => { const cur = f.get(); [...seg.children].forEach((b, k) => { const on = cur !== null && String(opts[k].v) === String(cur); b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); }); };
    } else if (f.t === "a") {
      el = document.createElement("button");
      el.type = "button";
      el.className = `lcd-row a ${f.cls ?? ""}`;
      el.innerHTML = `<span class="lb"></span><i></i><output></output>`;
      el.addEventListener("click", () => { select(); if (!el.disabled) { f.fn(); refresh(); } });
      row_api(i, { run: () => { if (!el.disabled) { f.fn(); refresh(); } }, el });
      upd = () => {
        const lb = el.querySelector(".lb"), t = labelOf(f); if (lb.textContent !== t) lb.textContent = t;
        const v = typeof f.val === "function" ? f.val() : f.val ?? "▸"; const o = el.querySelector("output"); if (o.textContent !== String(v)) o.textContent = v;
        el.disabled = !!(f.disabled && f.disabled());
      };
    } else { // txt
      el = document.createElement("label");
      el.className = "lcd-row t";
      el.innerHTML = `<span class="lb">${lab()}</span><i></i><input type="text" name="${f.name}" class="${f.cls ?? ""}" placeholder="${esc(f.placeholder ?? "")}" aria-label="${esc(labelOf(f))}" autocomplete="off" maxlength="40">`;
      const input = el.querySelector("input");
      input.addEventListener("input", () => f.set(input.value));
      row_api(i, { el, input });
      upd = () => { if (document.activeElement !== input && input.value !== f.get()) input.value = f.get(); };
    }
    el.dataset.f = i;
    el.addEventListener("focusin", () => { select(); });
    return { el, upd };
  }
  const apis = [];
  function row_api(i, api) { apis[i] = api; }

  function markSel() {
    for (const k of Object.keys(rows)) if (k !== "info") rows[k].el.classList.toggle("sel", Number(k) === L.field);
    lcdSofts.querySelectorAll(".ls").forEach((s, i) => s.classList.toggle("on", i === L.field));
  }

  // ---------------------------------------------------------------- refresh
  let msg = { text: "", t: 0 };
  function showIn() { inEl.textContent = L.keybuf ? `IN ${L.keybuf}` : ""; }
  function refresh() {
    for (const k of Object.keys(rows)) {
      if (k === "info") { const h = rows.info.html(ctx); if (rows.info.last !== h) { rows.info.el.innerHTML = h; rows.info.last = h; } continue; }
      rows[k].upd();
    }
    mainBody?.update();
    lcdSofts.querySelectorAll(".ls").forEach((s, i) => {
      const f = fields[i]; if (!f || f.t === "x") return;
      const t = labelOf(f); if (s.textContent !== t) s.textContent = t;
      s.classList.toggle("lit", f.t === "b" && !!f.get());
      s.classList.toggle("on", i === L.field);
    });
    const live = performance.now() - msg.t < 1800 ? msg.text : "";
    if (msgEl.textContent !== live) msgEl.textContent = live;
    modesEl.querySelectorAll(".mode").forEach((b) => b.classList.toggle("on", b.dataset.mode === L.page));
  }
  d.add(env.message.on((m) => { msg = m; refresh(); }));
  d.add(env.ticker.add((now) => { if (now - (refresh._t ?? 0) > 80) { refresh._t = now; refresh(); } }));
  d.add(store.subscribe(() => refresh()));
  for (const t of ["seq", "song", "key"]) { session.on(t, refresh); d.add(() => session.off(t, refresh)); }

  // ---------------------------------------------------------------- soft keys, wheel, keypad
  function softPress(i) {
    const f = fields[i];
    if (!f || f.t === "x") return;
    const a = apis[i];
    if (f.t === "a") { a.run(); return; }
    if (f.t === "b") { a.toggle(); return; }
    if (f.t === "e") { if (L.field === i) a.cycle(1); }
    L.field = i; markSel();
    if (f.t === "i" || f.t === "t") a.input.focus();
    refresh();
  }
  function wheel(dir) {
    const f = fields[L.field], a = apis[L.field];
    if (!f) { if (L.page === "MAIN") env.volume.set(clamp(+(env.volume.get() + dir * 0.01).toFixed(3), 0, 1.5)); return; }
    if (f.t === "n" || f.t === "i") a.step(dir);
    else if (f.t === "e") a.cycle(dir);
    else if (f.t === "b") a.toggle();
  }
  function keypadEnter() {
    const f = fields[L.field], a = apis[L.field];
    const buf = L.keybuf; L.keybuf = ""; showIn();
    if (!f || buf === "") return;
    if (f.t === "n" || f.t === "i") a.setValue(parseFloat(buf) || 0, true);
    else if (f.t === "e") {
      const o = a.opts.find((x) => String(x.v) === buf || x.l === buf) ?? a.opts[Number(buf) - 1];
      if (o) a.set(o.v);
    }
  }
  // data wheel (drag to turn, mouse wheel, the - and + keys beside it)
  const wh = $("[data-wheel]");
  wh.innerHTML = `<div class="w-cap" aria-hidden="true"><i class="w-dot"></i></div><button type="button" class="wk dec" aria-label="Data wheel down">−</button><button type="button" class="wk inc" aria-label="Data wheel up">+</button>`;
  const cap = wh.querySelector(".w-cap");
  let ang = 0, drag = false, a0 = 0, acc = 0;
  const angleOf = (e) => { const r = cap.getBoundingClientRect(); return (Math.atan2(e.clientY - (r.top + r.height / 2), e.clientX - (r.left + r.width / 2)) * 180) / Math.PI; };
  const turn = (deg) => { ang += deg; cap.style.setProperty("--wr", `${ang}deg`); };
  d.on(cap, "pointerdown", (e) => { cap.setPointerCapture(e.pointerId); drag = true; a0 = angleOf(e); acc = 0; });
  d.on(cap, "pointermove", (e) => {
    if (!drag) return;
    const a = angleOf(e); let dd = a - a0; if (dd > 180) dd -= 360; if (dd < -180) dd += 360; a0 = a; turn(dd); acc += dd;
    while (acc > 14) { wheel(1); acc -= 14; } while (acc < -14) { wheel(-1); acc += 14; }
  });
  d.on(cap, "pointerup", () => { drag = false; });
  d.on(wh, "wheel", (e) => { e.preventDefault(); const dir = -Math.sign(e.deltaY); turn(dir * 14); wheel(dir); }, { passive: false });
  wh.querySelector(".dec").addEventListener("click", () => { turn(-14); wheel(-1); });
  wh.querySelector(".inc").addEventListener("click", () => { turn(14); wheel(1); });
  // the LCD itself also takes the mouse wheel (turns the focused field)
  d.on($(".lcd"), "wheel", (e) => { if (L.field < 0 && L.page !== "MAIN") return; e.preventDefault(); wheel(-Math.sign(e.deltaY)); }, { passive: false });

  d.on(document, "keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tg = e.target, tag = tg?.tagName ?? "";
    if (/^F[1-6]$/.test(e.key)) { softPress(Number(e.key[1]) - 1); e.preventDefault(); return; }
    if (e.key === "Escape") { L.keybuf = ""; L.field = -1; showIn(); markSel(); return; }
    if (tg === document.body || (tg?.closest?.(".wk-zone") && tag !== "INPUT")) {
      if (e.key === "ArrowUp" || e.key === "+" || e.key === "=") { wheel(1); e.preventDefault(); }
      else if (e.key === "ArrowDown" || e.key === "-") { wheel(-1); e.preventDefault(); }
    }
  });

  // MIDI page info comes from the input object
  const onDevices = (e) => { ctx.out.devices = devicesText(e.detail.devices); refresh(); };
  const onLearn = (e) => { ctx.out.learn = e.detail.active ? `LEARNING: TAP PAD ${e.detail.nextPad + 1} ON YOUR CONTROLLER…` : "PAD MAP SAVED"; refresh(); };
  env.input.addEventListener("devices", onDevices);
  env.input.addEventListener("learn-state", onLearn);
  d.add(() => { env.input.removeEventListener("devices", onDevices); env.input.removeEventListener("learn-state", onLearn); });
  build();
  return { destroy() { d.run(); root.innerHTML = ""; } };
}

function devicesText(devs) {
  return devs.length ? devs.map((x) => `▸ ${esc(x.name)} (${esc(x.state)})`).join(" · ") : "NO MIDI DEVICES · PLUG IN A CONTROLLER (USB-MIDI)";
}
