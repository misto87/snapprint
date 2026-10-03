"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  profiles: null, upload: null, printer: null, job: null, sentAs: null,
  schema: null, presets: [], baseCache: {},
  procOverrides: {},      // process option overrides (key -> value)
  filOverrides: {},       // slot index -> filament option overrides
  projectOverrides: null, // overrides suggested by a U1 project 3MF
};

async function api(path, opts = {}) {
  const res = await fetch("api/" + path, opts);
  let body = {};
  try { body = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(body.error || `Fehler ${res.status}`);
  return body;
}
const jsonOpts = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), isErr ? 6000 : 3000);
}
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "style") e.style.cssText = v; else if (k in e) e[k] = v; else e.setAttribute(k, v);
  }
  for (const c of children) e.append(c);
  return e;
}

// ---------- Printer status ----------
const STATE_DE = { standby: "Bereit", printing: "Druckt", paused: "Pausiert", complete: "Fertig",
                   cancelled: "Abgebrochen", error: "Fehler" };
async function refreshPrinter() {
  const pill = $("printer-pill");
  try {
    const st = await api("printer/status");
    state.printer = st;
    const label = STATE_DE[st.state] || st.state || st.klippy;
    pill.textContent = label;
    pill.className = "pill " + (st.state === "printing" ? "busy" : st.state === "error" ? "err" : "ok");
    $("printer-state").textContent = st.state === "printing" || st.state === "paused"
      ? `${st.filename} · ${st.progress}%` + (st.total_layers ? ` · Schicht ${st.layer}/${st.total_layers}` : "")
      : label;
    const prog = $("print-progress");
    prog.classList.toggle("hidden", !(st.state === "printing" || st.state === "paused"));
    prog.firstElementChild.style.width = st.progress + "%";
    $("toolheads").replaceChildren(...st.toolheads.map((h) => {
      const f = h.filament;
      return el("div", { className: "head" },
        el("b", {}, h.label),
        el("div", { className: "swatch", style: `background:${f.loaded && f.color ? f.color : "transparent"}` }),
        el("div", {}, f.loaded ? `${f.type} ${f.sub_type || ""}`.trim() : "leer"),
        el("div", { className: "temp muted" }, `${Math.round(h.temperature)}°` + (h.target ? ` → ${Math.round(h.target)}°` : "")));
    }));
    $("bed").textContent = `Bett ${Math.round(st.bed.temperature)}°` + (st.bed.target ? ` → ${Math.round(st.bed.target)}°` : "");
    if (state.upload) renderSlotWarnings();
  } catch (e) {
    state.printer = null;
    pill.textContent = "offline";
    pill.className = "pill err";
    $("printer-state").textContent = e.message;
  }
}

// ---------- Profiles ----------
function machineName() { return $("sel-machine").value; }
function compatible(list) { return list.filter((p) => p.compatible.includes(machineName())); }

function fillProcesses(wanted) {
  const m = state.profiles.machines.find((x) => x.name === machineName());
  const sel = $("sel-process");
  const prev = wanted || sel.value;
  const sys = el("optgroup", { label: "Snapmaker-Profile" },
    ...compatible(state.profiles.processes).map((p) => el("option", { value: p.name }, p.name)));
  const own = state.presets.filter((p) => p.kind === "process" && state.profiles.processes.some(
    (q) => q.name === p.base && q.compatible.includes(machineName())));
  sel.replaceChildren(sys);
  if (own.length) sel.append(el("optgroup", { label: "Eigene Profile" },
    ...own.map((p) => el("option", { value: "preset:" + p.id }, "★ " + p.name))));
  sel.value = [...sel.options].some((o) => o.value === prev) ? prev : m.default_process;
}

/** Base system process for the current selection (resolves user presets). */
function processBase() {
  const v = $("sel-process").value;
  if (v.startsWith("preset:")) return state.presets.find((p) => "preset:" + p.id === v)?.base || "";
  return v;
}

function onProcessChange() {
  const v = $("sel-process").value;
  const preset = v.startsWith("preset:") && state.presets.find((p) => "preset:" + p.id === v);
  state.procOverrides = preset ? { ...preset.overrides } : {};
  state.projectOverrides = null;
  renderProjectNote();
  renderQuick();
}

function guessFilament(loaded, slotType) {
  const names = compatible(state.profiles.filaments).map((f) => f.name);
  const nozzle = state.profiles.machines.find((x) => x.name === machineName()).nozzle;
  const tries = [];
  if (loaded && loaded.loaded && loaded.type) {
    const base = [loaded.vendor, loaded.type, loaded.sub_type].filter(Boolean).join(" ");
    tries.push(`${base} @U1`, `${base} @U1 ${nozzle} nozzle`);
    if (loaded.vendor && loaded.vendor !== "Generic") tries.push(`${loaded.vendor} ${loaded.type} @U1`, `${loaded.vendor} ${loaded.type} @U1 ${nozzle} nozzle`);
    tries.push(`Generic ${loaded.type}`, `Generic ${loaded.type} @U1 ${nozzle} nozzle`);
  }
  if (slotType) tries.push(`Generic ${slotType}`, `Generic ${slotType} @U1 ${nozzle} nozzle`);
  tries.push("Generic PLA", `Generic PLA @U1 ${nozzle} nozzle`);
  return tries.find((t) => names.includes(t)) || names[0];
}

function filamentSelect(id, selected, slot) {
  const sel = el("select", { id });
  sel.append(el("optgroup", { label: "Snapmaker-Profile" },
    ...compatible(state.profiles.filaments).map((f) => el("option", { value: f.name }, f.name))));
  const own = state.presets.filter((p) => p.kind === "filament" && compatible(state.profiles.filaments).some((f) => f.name === p.base));
  if (own.length) sel.append(el("optgroup", { label: "Eigene Profile" },
    ...own.map((p) => el("option", { value: "preset:" + p.id }, "★ " + p.name))));
  sel.value = selected;
  sel.addEventListener("change", () => {
    const preset = sel.value.startsWith("preset:") && state.presets.find((p) => "preset:" + p.id === sel.value);
    state.filOverrides[slot] = preset ? { ...preset.overrides } : {};
    updateSlotButton(slot);
    renderSlotWarnings();
  });
  return sel;
}

/** System filament profile behind a slot's selection (resolves user presets). */
function filamentBase(slot) {
  const sel = $(`fil-${slot}`);
  if (!sel) return "";
  if (sel.value.startsWith("preset:")) return state.presets.find((p) => "preset:" + p.id === sel.value)?.base || "";
  return sel.value;
}

function slotButton(slot) {
  const b = el("button", { id: `fil-btn-${slot}`, type: "button" }, "");
  b.addEventListener("click", () => openSheet("filament", slot));
  return el("div", { className: "slot-actions" }, b);
}

function updateSlotButton(slot) {
  const b = $(`fil-btn-${slot}`);
  if (!b) return;
  const n = Object.keys(state.filOverrides[slot] || {}).length;
  b.textContent = n ? `Filament anpassen · ${n} geändert` : "Filament anpassen";
}

function headFor(i) {
  return state.upload.kind === "stl" ? Number($("sel-toolhead").value) : i;
}

function renderSlots() {
  const up = state.upload;
  const box = $("slots");
  box.replaceChildren();
  const heads = state.printer ? state.printer.toolheads : [];
  if (up.kind === "stl") {
    const thSel = el("select", { id: "sel-toolhead" });
    for (let i = 0; i < 4; i++) {
      const f = heads[i] && heads[i].filament;
      thSel.append(el("option", { value: i }, `T${i + 1}` + (f && f.loaded ? ` · ${f.type} ${f.sub_type || ""}` : "")));
    }
    thSel.addEventListener("change", () => {
      const h = heads[Number(thSel.value)];
      $("fil-0").value = guessFilament(h && h.filament, "");
      renderSlotWarnings();
    });
    box.append(el("div", { className: "slot" },
      el("div", { className: "slot-head" }, "Einfarbig (STL)"),
      el("label", {}, "Toolhead", thSel),
      el("label", {}, "Material", filamentSelect("fil-0", guessFilament(heads[0] && heads[0].filament, ""), 0)),
      el("div", { className: "warn", id: "warn-0" }), slotButton(0)));
  } else {
    up.slots.forEach((s, i) => {
      if (!s.used) return;
      const h = heads[i] && heads[i].filament;
      box.append(el("div", { className: "slot" },
        el("div", { className: "slot-head" },
          el("span", { className: "swatch", style: `background:${s.color || "transparent"}` }),
          `Farbe ${i + 1} → T${i + 1}`,
          el("span", { className: "muted small" }, s.type ? `(${s.type})` : "")),
        el("label", {}, "Material", filamentSelect(`fil-${i}`, guessFilament(h, s.type), i)),
        el("div", { className: "warn", id: `warn-${i}` }), slotButton(i)));
    });
  }
  state.filOverrides = {};
  const n = up.kind === "stl" ? 1 : up.slots.length;
  for (let i = 0; i < n; i++) updateSlotButton(i);
  renderSlotWarnings();
}

function renderSlotWarnings() {
  if (!state.upload || !state.profiles) return;
  const n = state.upload.kind === "stl" ? 1 : state.upload.slots.length;
  for (let i = 0; i < n; i++) {
    const w = $(`warn-${i}`);
    const sel = $(`fil-${i}`);
    if (!w || !sel) continue;
    const prof = state.profiles.filaments.find((f) => f.name === filamentBase(i));
    const head = state.printer && state.printer.toolheads[headFor(i)];
    let msg = "";
    if (head) {
      const f = head.filament;
      if (!f.loaded) msg = `${head.label} ist leer.`;
      else if (prof && f.type && prof.type && f.type.toUpperCase() !== prof.type.toUpperCase())
        msg = `${head.label} hat ${f.type} geladen, gewählt ist ${prof.type}.`;
    }
    w.textContent = msg ? "⚠︎ " + msg : "";
  }
}

// ---------- Upload ----------
async function onFile() {
  const file = $("file").files[0];
  if (!file) return;
  $("file-label").innerHTML = '<span class="spinner"></span>Lade hoch …';
  $("card-settings").classList.add("hidden");
  $("card-result").classList.add("hidden");
  const fd = new FormData();
  fd.append("file", file);
  try {
    const up = await api("uploads", { method: "POST", body: fd });
    state.upload = up;
    $("file-label").textContent = file.name;
    const usedCount = up.slots.filter((s) => s.used).length;
    let info = up.kind === "stl" ? "STL · wird einfarbig gedruckt" : `3MF · ${usedCount} Farbe(n)` + (up.painted ? " · bemalt" : "");
    if (up.kind === "3mf" && up.plates > 1) info += ` · ${up.plates} Platten`;
    if (up.kind === "3mf" && usedCount > 1 && !up.painted) info += " · Farben pro Objekt zugewiesen";
    $("model-info").textContent = info;
    if (up.too_many) {
      $("model-info").textContent = "Die 3MF nutzt Filamente über Nr. 4 hinaus – der U1 hat nur T1–T4.";
      return;
    }
    $("row-arrange").classList.toggle("hidden", up.kind !== "stl");
    $("chk-arrange").checked = true;
    $("row-plate").classList.toggle("hidden", up.plates <= 1);
    $("sel-plate").replaceChildren(...Array.from({ length: up.plates }, (_, i) => el("option", { value: i + 1 }, `Platte ${i + 1}`)));
    if (up.suggested) {
      $("sel-machine").value = up.suggested.machine;
      fillProcesses(up.suggested.process);
      state.procOverrides = { ...up.suggested.process_overrides };
      state.projectOverrides = { ...up.suggested.process_overrides };
    } else {
      fillProcesses();
      state.procOverrides = {};
      state.projectOverrides = null;
    }
    renderProjectNote();
    renderSlots();
    renderQuick();
    $("card-settings").classList.remove("hidden");
  } catch (e) {
    $("file-label").textContent = "3MF oder STL auswählen";
    toast(e.message, true);
  }
}

// ---------- Slicing ----------
async function onSlice() {
  const up = state.upload;
  const n = up.kind === "stl" ? 1 : up.slots.length;
  const filaments = [];
  const firstUsed = up.kind === "3mf" ? Math.max(0, up.slots.findIndex((s) => s.used)) : 0;
  for (let i = 0; i < n; i++) {
    const sel = $(`fil-${i}`) || $(`fil-${firstUsed}`);  // unused slots: any compatible profile
    let color = up.kind === "3mf" ? up.slots[i].color : "";
    const head = state.printer && state.printer.toolheads[headFor(i)];
    if (!color && head && head.filament.loaded) color = head.filament.color;
    const slot = $(`fil-${i}`) ? i : firstUsed;
    filaments.push({ name: filamentBase(slot), color, overrides: state.filOverrides[slot] || {} });
  }
  const req = {
    upload_id: up.upload_id, machine: machineName(), process: processBase(), filaments,
    process_overrides: state.procOverrides,
    arrange: $("chk-arrange").checked, plate: Number($("sel-plate").value || 1),
  };
  if (up.kind === "stl") req.toolhead = Number($("sel-toolhead").value);
  $("btn-slice").disabled = true;
  try {
    state.job = await api("jobs", jsonOpts("POST", req));
    state.sentAs = null;
    $("card-result").classList.remove("hidden");
    $("job-actions").classList.add("hidden");
    $("start-box").classList.add("hidden");
    $("job-log").classList.add("hidden");
    $("card-result").scrollIntoView({ behavior: "smooth" });
    pollJob();
  } catch (e) {
    toast(e.message, true);
    $("btn-slice").disabled = false;
  }
}

async function pollJob() {
  const id = state.job.id;
  let job;
  try { job = await api(`jobs/${id}`); } catch (e) { toast(e.message, true); return; }
  if (!state.job || state.job.id !== id) return;
  state.job = job;
  const status = $("job-status");
  if (job.state === "queued" || job.state === "slicing") {
    const secs = job.started ? Math.round(Date.now() / 1000 - job.started) : 0;
    status.innerHTML = `<span class="spinner"></span>${job.state === "queued" ? "In der Warteschlange" : `Slice läuft … ${secs}s`}`;
    setTimeout(pollJob, 2000);
    return;
  }
  $("btn-slice").disabled = false;
  if (job.state === "error") {
    status.innerHTML = "";
    status.append(el("p", { className: "warn" }, "Slicen fehlgeschlagen: " + job.message));
    if (job.log) { $("job-log").textContent = job.log; $("job-log").classList.remove("hidden"); }
    return;
  }
  status.textContent = "Fertig gesliced ✓";
  const s = job.stats || {};
  const rows = [["Druckzeit", s.print_time], ["Filament", s.filament_g ? `${s.filament_g} g` : ""],
                ["Schichten", s.layers], ["Größe", job.size ? `${(job.size / 1048576).toFixed(1)} MB` : ""]];
  $("job-stats").replaceChildren(...rows.filter((r) => r[1]).flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
  $("inp-filename").value = job.gcode_name;
  $("btn-download").href = `api/jobs/${job.id}/gcode`;
  const img = $("job-thumb");
  img.classList.add("hidden");
  img.onload = () => img.classList.remove("hidden");
  img.src = `api/jobs/${job.id}/thumbnail.png`;
  $("job-actions").classList.remove("hidden");
}

async function onSend() {
  const btn = $("btn-send");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Sende …';
  try {
    const res = await api(`jobs/${state.job.id}/send`, jsonOpts("POST", { filename: $("inp-filename").value }));
    state.sentAs = res.filename;
    $("inp-filename").value = res.filename;
    $("sent-name").textContent = res.filename;
    $("start-box").classList.remove("hidden");
    toast("Auf den U1 übertragen – Druck noch nicht gestartet");
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "An U1 senden";
  }
}

function onStart() {
  if (!state.sentAs) return;
  const job = state.job;
  $("confirm-text").textContent = `„${state.sentAs}“ auf dem U1 drucken` +
    (job.stats && job.stats.print_time ? ` (ca. ${job.stats.print_time})` : "") + ".";
  const items = job.kind === "stl"
    ? [`T${job.toolhead + 1}: ${job.filaments[0].name}`]
    : job.filaments.map((f, i) => (state.upload.slots[i] && !state.upload.slots[i].used) ? null : `T${i + 1}: ${f.name}`).filter(Boolean);
  $("confirm-heads").replaceChildren(...items.map((t) => el("li", {}, t)));
  const dlg = $("dlg-confirm");
  dlg.returnValue = "";
  dlg.showModal();
}

async function onConfirmClose() {
  if ($("dlg-confirm").returnValue !== "ok") return;
  try {
    await api("printer/start", jsonOpts("POST", { filename: state.sentAs, confirm: true }));
    toast("Druck gestartet");
    $("start-box").classList.add("hidden");
    refreshPrinter();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---------- Settings ----------
async function openSettings() {
  const s = await api("settings");
  $("inp-host").value = s.printer_host || "";
  $("inp-port").value = s.printer_port || 7125;
  $("settings-info").textContent = "";
  api("printer/info").then((i) => {
    $("settings-info").textContent = `Verbunden: ${i.hostname} · Firmware ${i.firmware} · Moonraker ${i.moonraker_version}`;
  }).catch((e) => { $("settings-info").textContent = e.message; });
  $("dlg-settings").showModal();
}
async function onSettingsClose() {
  if ($("dlg-settings").returnValue !== "save") return;
  try {
    await api("settings", jsonOpts("PUT", { printer_host: $("inp-host").value.trim(), printer_port: Number($("inp-port").value) }));
    toast("Gespeichert");
    refreshPrinter();
  } catch (e) { toast(e.message, true); }
}

// ---------- Settings (Orca options) ----------
const QUICK_KEYS = ["layer_height", "sparse_infill_density", "wall_loops", "sparse_infill_pattern",
                    "enable_support", "support_type", "brim_type", "seam_position"];
const PERCENT_TYPES = new Set(["coPercent", "coPercents"]);
const sheet = { kind: "process", slot: 0, base: {}, overrides: {}, page: 0 };

async function baseValues(kind, name) {
  const key = kind + "|" + name;
  if (!state.baseCache[key]) {
    state.baseCache[key] = await api(`profile-values?kind=${kind}&name=${encodeURIComponent(name)}`);
  }
  return state.baseCache[key];
}

function shown(opt, v) {
  let x = Array.isArray(v) ? v[0] : v;
  if (x === undefined || x === null || x === "nil") return "";
  x = String(x);
  if (PERCENT_TYPES.has(opt.type) && x.endsWith("%")) x = x.slice(0, -1);
  return x;
}

function same(a, b) {
  const na = Number(a), nb = Number(b);
  if (a !== "" && b !== "" && !isNaN(na) && !isNaN(nb)) return na === nb;
  return String(a) === String(b);
}

function optLabel(opt) {
  return opt.full_label && opt.label && opt.full_label !== opt.label && opt.label.length < 22
    ? `${opt.full_label} – ${opt.label}` : (opt.full_label || opt.label || "");
}

/** One editable option row. `ov` is mutated in place; onChange runs after every edit. */
function renderField(key, base, ov, onChange, compact = false) {
  const opt = state.schema.options[key];
  const baseShown = shown(opt, base[key]);
  const box = el("div", { className: "field" });
  const ctrl = el("div", { className: "field-ctrl" });
  const reset = el("button", { className: "reset", type: "button", title: "Zurücksetzen" }, "↺");
  let input;

  const current = () => (key in ov ? shown(opt, ov[key]) : baseShown);
  const commit = (value) => {
    if (same(value, baseShown)) delete ov[key];
    else ov[key] = PERCENT_TYPES.has(opt.type) && value !== "" && !String(value).endsWith("%") ? value + "%" : value;
    refresh();
    onChange();
  };
  const refresh = () => {
    const changed = key in ov;
    box.classList.toggle("changed", changed);
    reset.classList.toggle("hidden", !changed);
  };

  const t = opt.type.replace(/s$/, "");
  if (t === "coBool") {
    input = el("input", { type: "checkbox", className: "switch" });
    input.checked = current() === "1";
    input.addEventListener("change", () => commit(input.checked ? "1" : "0"));
  } else if (t === "coEnum" && opt.enum) {
    input = el("select", {}, ...opt.enum.map((e) => el("option", { value: e.value }, e.label)));
    input.value = current();
    input.addEventListener("change", () => commit(input.value));
  } else {
    const numeric = ["coFloat", "coInt", "coPercent"].includes(t);
    input = el("input", { type: "text", inputMode: numeric ? "decimal" : "text", autocomplete: "off",
                          placeholder: baseShown === "" ? "Standard" : "" });
    input.value = current();
    input.addEventListener("change", () => commit(input.value.trim().replace(",", ".")));
  }
  reset.addEventListener("click", () => {
    delete ov[key];
    if (input.type === "checkbox") input.checked = baseShown === "1"; else input.value = baseShown;
    refresh();
    onChange();
  });
  ctrl.append(input);
  if (opt.sidetext && t !== "coBool" && t !== "coEnum") ctrl.append(el("span", { className: "unit" }, opt.sidetext));
  ctrl.append(reset);

  const label = el("div", { className: "field-label" }, optLabel(opt));
  if (opt.tooltip && !compact) {
    const tip = el("div", { className: "field-tip hidden" }, opt.tooltip);
    const info = el("button", { className: "info", type: "button", "aria-label": "Info" }, "ⓘ");
    info.addEventListener("click", () => tip.classList.toggle("hidden"));
    label.append(info);
    box.append(el("div", { className: "field-row" }, label, ctrl), tip);
  } else {
    box.append(el("div", { className: "field-row" }, label, ctrl));
  }
  refresh();
  return box;
}

async function renderQuick() {
  const box = $("quick");
  const btn = $("btn-all-settings");
  const n = Object.keys(state.procOverrides).length;
  btn.textContent = n ? `Alle Druckeinstellungen · ${n} geändert` : "Alle Druckeinstellungen";
  if (!state.schema || !processBase()) { box.replaceChildren(); return; }
  try {
    const base = await baseValues("process", processBase());
    const keys = QUICK_KEYS.filter((k) => state.schema.options[k]);
    box.replaceChildren(...keys.map((k) => renderField(k, base, state.procOverrides, () => {
      const c = Object.keys(state.procOverrides).length;
      btn.textContent = c ? `Alle Druckeinstellungen · ${c} geändert` : "Alle Druckeinstellungen";
    }, true)));
  } catch (e) { box.replaceChildren(); }
}

function renderProjectNote() {
  const note = $("project-note");
  const n = state.projectOverrides ? Object.keys(state.projectOverrides).length : 0;
  note.classList.toggle("hidden", !n);
  if (!n) return;
  const drop = el("button", { type: "button" }, "Verwerfen");
  drop.addEventListener("click", () => {
    for (const k of Object.keys(state.projectOverrides)) delete state.procOverrides[k];
    state.projectOverrides = null;
    renderProjectNote();
    renderQuick();
  });
  note.replaceChildren(`${n} Einstellungen aus der 3MF übernommen.`, drop);
}

async function openSheet(kind, slot = 0) {
  sheet.kind = kind;
  sheet.slot = slot;
  sheet.page = 0;
  const name = kind === "process" ? processBase() : filamentBase(slot);
  if (kind === "filament") state.filOverrides[slot] = state.filOverrides[slot] || {};
  sheet.overrides = kind === "process" ? state.procOverrides : state.filOverrides[slot];
  try { sheet.base = await baseValues(kind, name); } catch (e) { toast(e.message, true); return; }
  $("sheet-title").textContent = kind === "process" ? "Druckeinstellungen" : `Filament · Farbe ${slot + 1}`;
  $("sheet-sub").textContent = name;
  $("sheet-search").value = "";
  renderSheet();
  $("dlg-sheet").showModal();
}

function renderSheet() {
  const pages = state.schema[sheet.kind];
  const expert = $("sheet-expert").checked;
  const query = $("sheet-search").value.trim().toLowerCase();
  const visible = (key) => {
    const o = state.schema.options[key];
    if (key in sheet.overrides) return true;
    if (query) return (optLabel(o) + " " + (o.tooltip || "") + " " + key).toLowerCase().includes(query);
    return expert ? o.mode !== "develop" : o.mode === "simple";
  };
  $("sheet-tabs").replaceChildren(...pages.map((p, i) => {
    const b = el("button", { type: "button", className: i === sheet.page && !query ? "active" : "" }, p.title);
    b.addEventListener("click", () => { sheet.page = i; $("sheet-search").value = ""; renderSheet(); });
    return b;
  }));
  const onChange = () => { updateSheetCount(); };
  const body = $("sheet-body");
  body.replaceChildren();
  const list = query ? pages : [pages[sheet.page]];
  for (const page of list) {
    for (const group of page.groups) {
      const keys = group.options.filter(visible);
      if (!keys.length) continue;
      body.append(el("h4", {}, query ? `${page.title} · ${group.title}` : group.title));
      for (const k of keys) body.append(renderField(k, sheet.base, sheet.overrides, onChange));
    }
  }
  if (!body.children.length) {
    body.append(el("p", { className: "empty" }, query ? "Keine passende Einstellung." :
      "Keine Einstellungen in dieser Ansicht – „Experte“ einschalten für alle Optionen."));
  }
  body.scrollTop = 0;
  updateSheetCount();
}

function updateSheetCount() {
  const n = Object.keys(sheet.overrides).length;
  $("sheet-reset").disabled = !n;
  $("sheet-reset").textContent = n ? `Zurücksetzen (${n})` : "Zurücksetzen";
}

function closeSheet() {
  $("dlg-sheet").close();
  if (sheet.kind === "process") {
    if (state.projectOverrides) {
      for (const k of Object.keys(state.projectOverrides)) {
        if (!(k in state.procOverrides)) delete state.projectOverrides[k];
      }
    }
    renderProjectNote();
    renderQuick();
  } else {
    updateSlotButton(sheet.slot);
  }
}

async function saveSheetPreset() {
  const isProc = sheet.kind === "process";
  const base = isProc ? processBase() : filamentBase(sheet.slot);
  const name = prompt(`Name für das eigene ${isProc ? "Druck" : "Filament"}profil (Basis: ${base})`);
  if (!name) return;
  try {
    const preset = await api("presets", jsonOpts("POST", {
      name, kind: sheet.kind, base, machine: machineName(), overrides: sheet.overrides }));
    state.presets = await api("presets");
    if (isProc) {
      fillProcesses("preset:" + preset.id);
    } else {
      const sel = $(`fil-${sheet.slot}`);
      const fresh = filamentSelect(`fil-${sheet.slot}`, "preset:" + preset.id, sheet.slot);
      sel.replaceWith(fresh);
    }
    toast(`Profil „${preset.name}“ gespeichert`);
  } catch (e) { toast(e.message, true); }
}

function initSheet() {
  $("sheet-close").addEventListener("click", closeSheet);
  $("sheet-done").addEventListener("click", closeSheet);
  $("dlg-sheet").addEventListener("cancel", (e) => { e.preventDefault(); closeSheet(); });
  $("sheet-expert").addEventListener("change", renderSheet);
  let timer;
  $("sheet-search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(renderSheet, 200); });
  $("sheet-reset").addEventListener("click", () => {
    for (const k of Object.keys(sheet.overrides)) delete sheet.overrides[k];
    renderSheet();
  });
  $("sheet-save").addEventListener("click", saveSheetPreset);
  try { $("sheet-expert").checked = localStorage.getItem("snapprint.expert") === "1"; } catch (_) { /* ignore */ }
  $("sheet-expert").addEventListener("change", () => {
    try { localStorage.setItem("snapprint.expert", $("sheet-expert").checked ? "1" : "0"); } catch (_) { /* ignore */ }
  });
}

// ---------- Init ----------
async function init() {
  $("file").addEventListener("change", onFile);
  $("btn-slice").addEventListener("click", onSlice);
  $("btn-send").addEventListener("click", onSend);
  $("btn-start").addEventListener("click", onStart);
  $("dlg-confirm").addEventListener("close", onConfirmClose);
  $("btn-settings").addEventListener("click", openSettings);
  $("dlg-settings").addEventListener("close", onSettingsClose);
  $("sel-machine").addEventListener("change", () => { fillProcesses(); onProcessChange(); if (state.upload) renderSlots(); });
  $("sel-process").addEventListener("change", onProcessChange);
  $("btn-all-settings").addEventListener("click", () => openSheet("process"));
  initSheet();
  try {
    [state.profiles, state.schema, state.presets] = await Promise.all([api("profiles"), api("schema"), api("presets")]);
    const sel = $("sel-machine");
    sel.replaceChildren(...state.profiles.machines.map((m) => el("option", { value: m.name }, `${m.nozzle} mm`)));
    sel.value = state.profiles.machines.find((m) => m.nozzle === "0.4")?.name || sel.options[0].value;
    fillProcesses();
  } catch (e) { toast("Profile konnten nicht geladen werden: " + e.message, true); }
  await refreshPrinter();
  setInterval(() => { if (!document.hidden) refreshPrinter(); }, 5000);
}
init();
