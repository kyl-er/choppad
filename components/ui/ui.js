import { cellToPad, padToCell } from "./layout.js";

/**
 * Mount the ChopPad browser UI inside `rootEl`.
 *
 * @param {Element} rootEl - container element
 * @param {{ meta: object, analysis: object, chops: object, engine: object, input: EventTarget, onRegrid: function|undefined }} deps
 * @returns {{ updateChops: function, destroy: function }}
 */
export function mountUI(rootEl, { meta, analysis, chops, engine, input, onRegrid }) {
  let currentBank = 0;
  let currentChops = chops;
  let learnActive = false;
  let learnPad = 0;
  let devices = [];
  let activePads = new Set();

  function padLabel(p) {
    const chop = resolvePad(p);
    return chop ? `${padToCell(p).row * 4 + padToCell(p).col + 1}` : "";
  }

  function resolvePad(p) {
    const bank = currentChops.banks[currentBank];
    if (!bank || !(p in bank)) return null;
    return currentChops.chops[bank[p]] ?? null;
  }

  // ---- build DOM ----
  rootEl.innerHTML = "";

  // LCD
  const lcd = el("div", "lcd");
  const lcdTitle = el("div", "lcd-title", "", truncate(meta?.title || "ChopPad", 40));
  const lcdMeta = el("div", "lcd-meta");
  const bpmLabel = el("span", "", "", `${meta?.bpm ?? analysis?.bpm ?? "—"} BPM`);
  const meterLabel = el("span", "", "", `${analysis?.meter?.beats_per_bar ?? "—"}/${analysis?.meter?.beat_unit ?? "—"}`);
  const confLabel = el("span", "", "", `${((analysis?.confidence ?? 0) * 100) | 0}%`);
  lcdMeta.append(bpmLabel, meterLabel, confLabel);
  const lcdChop = el("div", "lcd-chop", "", "");
  lcd.append(lcdTitle, lcdMeta, lcdChop);

  // pad grid
  const grid = el("div", "pad-grid");
  const padEls = [];
  for (let row = 0; row < 4; row++) {
    for (let col = 0; col < 4; col++) {
      const p = cellToPad(row, col);
      const pad = el("button", "pad");
      pad.setAttribute("data-pad", p);
      const label = el("span", "pad-label", "", "");
      pad.append(label);
      padEls.push(pad);

      pad.addEventListener("pointerdown", () => {
        if (resolvePad(p)) engine.trigger(p, 1);
      });
      pad.addEventListener("pointerup", () => {
        engine.release(p);
      });
      pad.addEventListener("pointerleave", () => {
        engine.release(p);
      });
      grid.append(pad);
    }
  }

  // bank
  const bankSection = el("div", "bank-section");
  const bankDown = el("button", "bank-btn", "←", "←");
  const bankDisplay = el("div", "bank-display", "", "A");
  const bankUp = el("button", "bank-btn", "→", "→");
  bankDown.addEventListener("click", () => changeBank(-1));
  bankUp.addEventListener("click", () => changeBank(+1));
  bankSection.append(bankDown, bankDisplay, bankUp);

  // regrid
  const regrid = el("div", "regrid-section");
  regrid.append(el("h3", "", "", "Regrid"));
  const regridBtns = el("div", "btn-row");
  for (const g of ["1/4", "1/8", "1/16"]) {
    const b = el("button", "", "", g);
    b.addEventListener("click", () => onRegrid?.("grid", g));
    if (currentChops.grid === g) b.classList.add("on");
    regridBtns.append(b);
  }
  for (const m of ["2/4", "3/4", "4/4"]) {
    const b = el("button", "", "", m);
    b.addEventListener("click", () => onRegrid?.("meter", m));
    regridBtns.append(b);
  }
  for (const [label, op, val] of [["÷2", "bpm_mult", 0.5], ["×2", "bpm_mult", 2], ["Beat −1", "beat_offset", -1], ["Beat +1", "beat_offset", +1], ["Set Bar 1", "beat_offset", "bar1"]]) {
    const b = el("button", "", "", label);
    b.addEventListener("click", () => {
      if (op === "beat_offset" && val === "bar1") onRegrid?.("set_bar1");
      else onRegrid?.(op, val);
    });
    regridBtns.append(b);
  }
  regrid.append(regridBtns);

  // mode toggles
  const controls = el("div", "controls-section");
  controls.append(el("h3", "", "", "Mode"));
  const ctrlRow = el("div", "ctrl-row");

  const modeSelect = el("select", "");
  for (const m of ["oneshot", "gate", "playthrough"]) {
    const o = el("option", "", "", m);
    o.value = m;
    if (engine._mode === m) o.selected = true;
    modeSelect.append(o);
  }
  modeSelect.addEventListener("change", () => engine.setMode(modeSelect.value));

  const chokeCb = checkbox("choke", true, (v) => engine.setChoke(v));
  const fullLevelCb = checkbox("fullLevel", true, (v) => engine.setFullLevel(v));

  ctrlRow.append(labelWrap("mode", modeSelect), labelWrap("choke", chokeCb), labelWrap("fullLevel", fullLevelCb));
  controls.append(ctrlRow);

  // MIDI
  const midi = el("div", "midi-section");
  midi.append(el("h3", "", "", "MIDI"));
  const deviceList = el("div", "device-list");
  const midiBtns = el("div", "");
  const learnBtn = el("button", "midi-btn", "", "Learn");
  const resetBtn = el("button", "midi-btn", "", "Reset");

  learnBtn.addEventListener("click", () => { input.startLearn?.(); });
  resetBtn.addEventListener("click", () => { input.resetMap?.(); });
  midiBtns.append(learnBtn, resetBtn);
  midi.append(deviceList, midiBtns);

  // audio banner
  const banner = el("div", "audio-banner", "", "Click or press any key to enable audio");
  banner.style.display = "none";
  document.body.appendChild(banner);

  rootEl.append(lcd, grid, bankSection, regrid, controls, midi);

  // ---- event handlers ----
  function onPadOn(e) {
    const { pad, velocity } = e.detail;
    engine.trigger(pad, velocity);
    activePads.add(pad);
    updatePadStates();
  }

  function onPadOff(e) {
    const { pad } = e.detail;
    engine.release(pad);
    activePads.delete(pad);
    updatePadStates();
  }

  function onBank(e) {
    changeBank(e.detail.delta);
  }

  function onPanic() {
    engine.stopAll();
    activePads.clear();
    updatePadStates();
  }

  function onDevices(e) {
    devices = e.detail.devices || [];
    renderDevices();
  }

  function onLearnState(e) {
    learnActive = e.detail.active;
    learnPad = e.detail.nextPad;
    if (learnActive) learnBtn.classList.add("learn-active");
    else learnBtn.classList.remove("learn-active");
  }

  function changeBank(delta) {
    currentBank += delta;
    if (currentBank < 0) currentBank = 0;
    if (currentBank >= currentChops.banks.length) currentBank = currentChops.banks.length - 1;
    if (currentBank < 0) currentBank = 0;
    engine.setBank(currentBank);
    bankDisplay.textContent = bankLetter(currentBank);
    updatePadLabels();
    updatePadStates();
  }

  function bankLetter(n) {
    return String.fromCharCode(65 + (n % 26));
  }

  function updatePadLabels() {
    for (const p of padEls) {
      const pad = Number(p.getAttribute("data-pad"));
      const ch = resolvePad(pad);
      if (ch) {
        p.classList.remove("empty");
        const lbl = p.querySelector(".pad-label");
        lbl.textContent = padLabel(pad);
      } else {
        p.classList.add("empty");
        const lbl = p.querySelector(".pad-label");
        lbl.textContent = "";
      }
    }
  }

  function updatePadStates() {
    for (const p of padEls) {
      const pad = Number(p.getAttribute("data-pad"));
      p.classList.toggle("active", activePads.has(pad));
    }
  }

  function renderDevices() {
    deviceList.innerHTML = "";
    for (const d of devices) {
      const de = el("div", "device" + (d.state === "connected" ? " connected" : ""), "", `${d.name} (${d.state})`);
      deviceList.append(de);
    }
  }

  function updateBanner() {
    if (engine._ctx?.state !== "running") {
      banner.style.display = "flex";
    } else {
      banner.style.display = "none";
    }
  }

  let bannerCtx = null;
  function resumeAudio() {
    if (bannerCtx) {
      bannerCtx.resume().then(() => updateBanner()).catch(() => {});
    }
  }

  if (engine._ctx) {
    bannerCtx = engine._ctx;
    updateBanner();
  }

  banner.addEventListener("click", resumeAudio);
  document.addEventListener("keydown", resumeAudio, { once: true });

  // kiss = keep it simple style: expose ctx via a property so banner can read it
  if (!engine._ctx && engine.trigger) {
    // engine created with FakeCtx won't have _ctx; banner check is best-effort
  }

  input.addEventListener("pad-on", onPadOn);
  input.addEventListener("pad-off", onPadOff);
  input.addEventListener("bank", onBank);
  input.addEventListener("panic", onPanic);
  input.addEventListener("devices", onDevices);
  input.addEventListener("learn-state", onLearnState);

  // init
  updatePadLabels();

  return {
    updateChops(c) {
      currentChops = c;
      engine.setChops(c);
      currentBank = 0;
      bankDisplay.textContent = bankLetter(currentBank);
      updatePadLabels();
      updatePadStates();
    },
    destroy() {
      input.removeEventListener("pad-on", onPadOn);
      input.removeEventListener("pad-off", onPadOff);
      input.removeEventListener("bank", onBank);
      input.removeEventListener("panic", onPanic);
      input.removeEventListener("devices", onDevices);
      input.removeEventListener("learn-state", onLearnState);
      engine.stopAll();
      rootEl.innerHTML = "";
      banner.remove();
    },
  };
}

// ---- DOM helpers ----
function el(tag, cls = "", text = "", html = null) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== null) e.innerHTML = html;
  else if (text) e.textContent = text;
  return e;
}

function labelWrap(text, child) {
  const l = document.createElement("label");
  l.textContent = text + " ";
  l.append(child);
  return l;
}

function checkbox(name, initial, onChange) {
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = initial;
  cb.addEventListener("change", () => onChange(cb.checked));
  return cb;
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "\u2026" : s;
}
