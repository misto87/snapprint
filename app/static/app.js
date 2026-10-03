"use strict";
const $ = (id) => document.getElementById(id);
const state = { profiles: null, upload: null, printer: null, job: null, sentAs: null };

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

function fillProcesses() {
  const m = state.profiles.machines.find((x) => x.name === machineName());
  const sel = $("sel-process");
  const prev = sel.value;
  sel.replaceChildren(...compatible(state.profiles.processes).map((p) => el("option", { value: p.name }, p.name)));
  sel.value = [...sel.options].some((o) => o.value === prev) ? prev : m.default_process;
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

function filamentSelect(id, selected) {
  const sel = el("select", { id });
  for (const f of compatible(state.profiles.filaments)) sel.append(el("option", { value: f.name }, f.name));
  sel.value = selected;
  sel.addEventListener("change", renderSlotWarnings);
  return sel;
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
      el("label", {}, "Material", filamentSelect("fil-0", guessFilament(heads[0] && heads[0].filament, ""))),
      el("div", { className: "warn", id: "warn-0" })));
  } else {
    up.slots.forEach((s, i) => {
      const h = heads[i] && heads[i].filament;
      box.append(el("div", { className: "slot" },
        el("div", { className: "slot-head" },
          el("span", { className: "swatch", style: `background:${s.color || "transparent"}` }),
          `Farbe ${i + 1} → T${i + 1}`,
          el("span", { className: "muted small" }, s.type ? `(${s.type})` : "")),
        el("label", {}, "Material", filamentSelect(`fil-${i}`, guessFilament(h, s.type))),
        el("div", { className: "warn", id: `warn-${i}` })));
    });
  }
  renderSlotWarnings();
}

function renderSlotWarnings() {
  if (!state.upload || !state.profiles) return;
  const n = state.upload.kind === "stl" ? 1 : state.upload.slots.length;
  for (let i = 0; i < n; i++) {
    const w = $(`warn-${i}`);
    const sel = $(`fil-${i}`);
    if (!w || !sel) continue;
    const prof = state.profiles.filaments.find((f) => f.name === sel.value);
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
    let info = up.kind === "stl" ? "STL · wird einfarbig gedruckt" : `3MF · ${up.slots.length} Farbe(n)`;
    if (up.kind === "3mf" && up.plates > 1) info += ` · ${up.plates} Platten`;
    if (up.kind === "3mf" && up.slots.length > 1 && !up.painted) info += " · Farben pro Objekt zugewiesen";
    $("model-info").textContent = info;
    if (up.too_many) {
      $("model-info").textContent = `Die 3MF nutzt ${up.slots.length} Filamente – der U1 hat nur 4 Toolheads.`;
      return;
    }
    $("row-arrange").classList.remove("hidden");
    $("chk-arrange").checked = up.kind === "stl";
    $("row-plate").classList.toggle("hidden", up.plates <= 1);
    $("sel-plate").replaceChildren(...Array.from({ length: up.plates }, (_, i) => el("option", { value: i + 1 }, `Platte ${i + 1}`)));
    renderSlots();
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
  for (let i = 0; i < n; i++) {
    let color = up.kind === "3mf" ? up.slots[i].color : "";
    const head = state.printer && state.printer.toolheads[headFor(i)];
    if (!color && head && head.filament.loaded) color = head.filament.color;
    filaments.push({ name: $(`fil-${i}`).value, color });
  }
  const req = {
    upload_id: up.upload_id, machine: machineName(), process: $("sel-process").value, filaments,
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
    : job.filaments.map((f, i) => `T${i + 1}: ${f.name}`);
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

// ---------- Init ----------
async function init() {
  $("file").addEventListener("change", onFile);
  $("btn-slice").addEventListener("click", onSlice);
  $("btn-send").addEventListener("click", onSend);
  $("btn-start").addEventListener("click", onStart);
  $("dlg-confirm").addEventListener("close", onConfirmClose);
  $("btn-settings").addEventListener("click", openSettings);
  $("dlg-settings").addEventListener("close", onSettingsClose);
  $("sel-machine").addEventListener("change", () => { fillProcesses(); if (state.upload) renderSlots(); });
  try {
    state.profiles = await api("profiles");
    const sel = $("sel-machine");
    sel.replaceChildren(...state.profiles.machines.map((m) => el("option", { value: m.name }, `${m.nozzle} mm`)));
    sel.value = state.profiles.machines.find((m) => m.nozzle === "0.4")?.name || sel.options[0].value;
    fillProcesses();
  } catch (e) { toast("Profile konnten nicht geladen werden: " + e.message, true); }
  await refreshPrinter();
  setInterval(() => { if (!document.hidden) refreshPrinter(); }, 5000);
}
init();
