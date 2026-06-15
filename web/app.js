// Slimefun Automation Calculator — front end

let ICON_MAP = {};
let SELECTED = null;            // selected item id
let SELECTED_ITEM = null;       // full selected item {id, name, addon} (for persistence)
const BANNED = new Set();
// 4 boost slots; default to 4x cloner T1 (the standard fill)
const TECHGEN = [{category: "cloning", tier: 1}, {category: "cloning", tier: 1},
                 {category: "cloning", tier: 1}, {category: "cloning", tier: 1}];
let STACKABLE = false;          // Mob Simulation Chamber: up to 64 data cards per chamber

const $ = (id) => document.getElementById(id);

// ---- local settings (persist across server restarts via the browser) ------
// The server holds no per-user state, so the banned list / tech-gen config /
// last query live in localStorage — restarting or rebuilding the server never
// wipes them. Every relevant change calls saveSettings().
const SETTINGS_KEY = "slimefun_calc_settings_v1";
function saveSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({
      banned: [...BANNED],
      techgen: TECHGEN,
      stackable: STACKABLE,
      query: SELECTED_ITEM ? {
        id: SELECTED_ITEM.id, name: SELECTED_ITEM.name, addon: SELECTED_ITEM.addon || "",
        quantity: $("quantity").value, minutes: $("minutes").value,
      } : null,
    }));
  } catch (e) { /* storage unavailable/full — fail silently */ }
}
function loadSettings() {
  try {
    const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
    if (!s) return null;
    if (Array.isArray(s.banned)) { BANNED.clear(); s.banned.forEach((id) => BANNED.add(id)); }
    if (Array.isArray(s.techgen)) { for (let i = 0; i < 4; i++) TECHGEN[i] = s.techgen[i] || null; }
    if (typeof s.stackable === "boolean") STACKABLE = s.stackable;
    return s;
  } catch (e) { return null; }
}
// restore BANNED + TECHGEN now, before the UI (tech-gen slots / ban list) is built
const SAVED = loadSettings();

// ---- icons ----------------------------------------------------------------
const VANILLA_CDN = "https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/1.20.6/assets/minecraft/textures";

function letteredPlaceholder(span, id) {
  const name = (id || "?").replace(/_/g, " ");
  span.textContent = name.slice(0, 2).toUpperCase();
  span.style.background = "linear-gradient(135deg,#3a9bdc,#5fd35a)";
}

function iconEl(id, small) {
  const span = document.createElement("span");
  span.className = "icon" + (small ? " sm" : "");
  const info = ICON_MAP[id];
  if (info && info.type === "pack") {
    span.style.backgroundImage = `url(/icons/${info.file})`;
  } else if (info && info.type === "head") {
    // crop the 8x8 face out of the 64x64 Mojang skin texture
    const box = small ? 24 : 32;
    span.style.backgroundImage = `url(https://textures.minecraft.net/texture/${info.tex})`;
    span.style.backgroundSize = `${box * 8}px ${box * 8}px`;
    span.style.backgroundPosition = `-${box * 2}px -${box * 2}px`;
    span.style.imageRendering = "pixelated";
  } else if (info && info.type === "vanilla") {
    // vanilla texture from the versioned asset CDN: try item/, then block/, then letters
    const img = document.createElement("img");
    img.className = "vicon";
    img.loading = "lazy";
    img.src = `${VANILLA_CDN}/item/${info.name}.png`;
    img.dataset.stage = "item";
    img.onerror = () => {
      if (img.dataset.stage === "item") {
        img.dataset.stage = "block";
        img.src = `${VANILLA_CDN}/block/${info.name}.png`;
      } else {
        img.remove();
        letteredPlaceholder(span, id);
      }
    };
    span.appendChild(img);
  } else {
    letteredPlaceholder(span, id);
  }
  return span;
}

// ---- search / autocomplete ------------------------------------------------
// Reusable item-autocomplete: wires an <input> + a suggestions <div> to the
// /api/items endpoint and calls onPick(item) on selection. Used by both the
// calculator's Item field and the reverse-search page.
function attachItemSearch(input, box, onPick) {
  let timer = null, idx = 0, opts = [];
  input.addEventListener("input", (e) => {
    clearTimeout(timer);
    const q = e.target.value.trim();
    if (q.length < 2) { box.classList.add("hidden"); return; }
    timer = setTimeout(async () => {
      const r = await fetch(`/api/items?q=${encodeURIComponent(q)}`);
      opts = (await r.json()).items; idx = 0;
      render();
    }, 140);
  });
  input.addEventListener("keydown", (e) => {
    if (box.classList.contains("hidden")) return;
    if (e.key === "ArrowDown") { idx = Math.min(idx + 1, opts.length - 1); active(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { idx = Math.max(idx - 1, 0); active(); e.preventDefault(); }
    else if (e.key === "Enter") { if (opts[idx]) pick(opts[idx]); e.preventDefault(); }
    else if (e.key === "Escape") box.classList.add("hidden");
  });
  function render() {
    box.innerHTML = "";
    if (!opts.length) { box.classList.add("hidden"); return; }
    opts.forEach((it, i) => {
      const opt = document.createElement("div");
      opt.className = "opt" + (i === 0 ? " active" : "");
      opt.appendChild(iconEl(it.id, true));
      const meta = document.createElement("div"); meta.className = "meta";
      meta.innerHTML = `<span class="nm">${esc(it.name)}</span><span class="ad">${esc(it.addon)}</span>`;
      opt.appendChild(meta);
      const tag = document.createElement("span");
      tag.className = "tag" + (it.producible ? "" : " raw");
      tag.textContent = it.producible ? "craftable" : "raw";
      opt.appendChild(tag);
      opt.onclick = () => pick(it);
      box.appendChild(opt);
    });
    box.classList.remove("hidden");
  }
  function active() { [...box.children].forEach((c, i) => c.classList.toggle("active", i === idx)); }
  function pick(it) { box.classList.add("hidden"); onPick(it); }
}
// any outside click closes all open suggestion boxes
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) {
    document.querySelectorAll(".suggestions").forEach((b) => b.classList.add("hidden"));
  }
});

attachItemSearch($("search"), $("suggestions"), selectItem);

function selectItem(it) {
  SELECTED = it.id;
  SELECTED_ITEM = { id: it.id, name: it.name, addon: it.addon || "" };
  $("search").value = it.name;
  hideSuggestions();
  const sel = $("selected");
  sel.innerHTML = "";
  sel.appendChild(iconEl(it.id));
  const m = document.createElement("div");
  m.innerHTML = `<div class="nm">${esc(it.name)}</div><div class="ad muted">${esc(it.addon)} · ${esc(it.id)}</div>`;
  sel.appendChild(m);
  sel.classList.remove("hidden");
  saveSettings();
}

// ---- ban panel ------------------------------------------------------------
let MACHINES = [];
// only real machines are bannable — NOT the held items (chickens, cards, plants, tools);
// to forbid those, ban the machine that uses them (Excitation Chamber, Tech Generator…).
const BANNABLE_CATS = new Set(["electric", "generator", "automation"]);
// machines are grouped by PLUGIN (addon). nicer labels + a preferred order (Slimefun first).
const ADDON_LABEL = {
  Slimefun4: "Slimefun", "GeneticChickengineering-Reborn": "Genetic Chickengineering",
};
const ADDON_ORDER = ["Slimefun4", "InfinityExpansion", "Supreme", "DynaTech",
                     "FluffyMachines", "Networks", "ExtraTools",
                     "GeneticChickengineering-Reborn", "Other"];
const addonLabel = (a) => ADDON_LABEL[a] || a;
const addonRank = (a) => { const i = ADDON_ORDER.indexOf(a); return i < 0 ? ADDON_ORDER.length : i; };

$("toggle-ban").onclick = () => $("ban-panel").classList.toggle("hidden");
async function loadMachines() {
  const r = await fetch("/api/machines");
  MACHINES = (await r.json()).machines;
  renderBanList("");
}
$("ban-search").addEventListener("input", (e) => renderBanList(e.target.value));
$("ban-clear").onclick = () => {
  BANNED.clear();
  $("ban-count").textContent = 0;
  renderBanList($("ban-search").value);
  saveSettings();
};

function renderBanList(filter) {
  const q = filter.trim().toLowerCase();
  const list = $("ban-list");
  list.innerHTML = "";
  const byAddon = {};
  for (const m of MACHINES) {
    if (!BANNABLE_CATS.has(m.category)) continue;   // skip held items (chicken/card/plant/tool)
    if (q && !(m.name + " " + m.id).toLowerCase().includes(q)) continue;
    const a = m.addon || "Other";
    (byAddon[a] || (byAddon[a] = [])).push(m);
  }
  const addons = Object.keys(byAddon).sort((a, b) => addonRank(a) - addonRank(b) || a.localeCompare(b));
  for (const addon of addons) {
    const ms = byAddon[addon].sort((a, b) => a.name.localeCompare(b.name));
    const h = document.createElement("div");
    h.className = "ban-cat";
    h.textContent = `${addonLabel(addon)} (${ms.length})`;
    list.appendChild(h);
    for (const m of ms) {
      const row = document.createElement("label");
      row.className = "ban-item" + (BANNED.has(m.id) ? " on" : "");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = BANNED.has(m.id);
      cb.onchange = () => {
        if (cb.checked) BANNED.add(m.id); else BANNED.delete(m.id);
        row.classList.toggle("on", cb.checked);
        $("ban-count").textContent = BANNED.size;
        saveSettings();
      };
      row.append(cb, document.createTextNode(" " + m.name));
      list.appendChild(row);
    }
  }
  if (!list.children.length) {
    list.innerHTML = '<div class="ban-cat">no machines match</div>';
  }
}

// ---- tech generator boost slots -------------------------------------------
$("toggle-techgen").onclick = () => $("techgen-panel").classList.toggle("hidden");
function buildTechgenSlots() {
  const wrap = $("techgen-slots");
  wrap.innerHTML = "";                 // idempotent: rebuild when a config is loaded
  const cats = [["", "— empty —"], ["cloning", "Cloning (+output)"],
                ["acceleration", "Acceleration (+speed, +energy)"],
                ["efficiency", "Efficiency (−energy)"]];
  for (let i = 0; i < 4; i++) {
    const slot = document.createElement("div");
    slot.className = "techgen-slot";
    const lbl = document.createElement("label"); lbl.textContent = "Slot " + (i + 1);
    const cat = document.createElement("select");
    cats.forEach(([v, t]) => { const o = document.createElement("option"); o.value = v; o.textContent = t; cat.appendChild(o); });
    const tier = document.createElement("select");
    for (let t = 1; t <= 9; t++) { const o = document.createElement("option"); o.value = t; o.textContent = "T" + t; tier.appendChild(o); }
    // reflect the current default (4x cloner T1)
    if (TECHGEN[i]) { cat.value = TECHGEN[i].category; tier.value = TECHGEN[i].tier; }
    tier.disabled = !cat.value;
    const sync = () => {
      tier.disabled = !cat.value;
      TECHGEN[i] = cat.value ? { category: cat.value, tier: parseInt(tier.value) } : null;
      $("techgen-count").textContent = TECHGEN.filter(Boolean).length;
      saveSettings();
    };
    cat.onchange = sync; tier.onchange = sync;
    slot.append(lbl, cat, tier);
    wrap.appendChild(slot);
  }
  $("techgen-count").textContent = TECHGEN.filter(Boolean).length;
}
buildTechgenSlots();

// ---- stackable data cards (Mob Simulation Chamber) ------------------------
// A toggle button next to Banned machines / Tech generator slots.
const stackBtn = $("toggle-stackable");
function renderStackState() {
  const s = $("stackable-state");
  if (s) s.textContent = STACKABLE ? "On" : "Off";
  if (stackBtn) stackBtn.classList.toggle("on", STACKABLE);
}
if (stackBtn) {
  stackBtn.onclick = () => { STACKABLE = !STACKABLE; renderStackState(); saveSettings(); };
}
renderStackState();

// ---- named configs saved on disk (for test baselines) ---------------------
// The browser localStorage above is per-user and can't be read off-disk, so the
// Save button ALSO writes the current bans + tech-gen + stackable to the server
// (configs/<name>.json). That on-disk copy is what the baseline tests/CLI load.
function configStatus(msg, isErr) {
  const s = $("config-status");
  if (!s) return;
  s.textContent = msg || "";
  s.classList.toggle("err", !!isErr);
}
async function refreshConfigList(selected) {
  const sel = $("config-load");
  if (!sel) return;
  try {
    const names = (await (await fetch("/api/configs")).json()).configs || [];
    sel.innerHTML = '<option value="">— load saved —</option>';
    names.forEach((n) => {
      const o = document.createElement("option");
      o.value = n; o.textContent = n;
      sel.appendChild(o);
    });
    if (selected && names.includes(selected)) sel.value = selected;
  } catch (e) { /* server offline — leave the dropdown empty */ }
}
// Apply a loaded config object {banned, tech_gen, stackable_cards} to the whole UI.
function applyConfig(c) {
  BANNED.clear();
  (c.banned || []).forEach((id) => BANNED.add(id));
  for (let i = 0; i < 4; i++) TECHGEN[i] = (c.tech_gen && c.tech_gen[i]) || null;
  STACKABLE = !!c.stackable_cards;
  $("ban-count").textContent = BANNED.size;
  renderBanList($("ban-search").value);
  buildTechgenSlots();
  renderStackState();
  saveSettings();
}
if ($("config-save")) {
  $("config-save").onclick = async () => {
    const name = $("config-name").value.trim();
    if (!name) { configStatus("Enter a name first.", true); return; }
    configStatus("Saving…");
    try {
      const r = await fetch("/api/configs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name, banned: [...BANNED], tech_gen: TECHGEN, stackable_cards: STACKABLE,
        }),
      });
      const data = await r.json();
      if (!data.ok) { configStatus(data.error || "Save failed.", true); return; }
      await refreshConfigList(data.name);
      configStatus(`Saved as "${data.name}".`);
    } catch (e) { configStatus("Save failed: " + e, true); }
  };
}
if ($("config-load")) {
  $("config-load").onchange = async (e) => {
    const name = e.target.value;
    if (!name) return;
    configStatus("Loading…");
    try {
      const c = await (await fetch(`/api/configs/${encodeURIComponent(name)}`)).json();
      if (c.ok === false) { configStatus(c.error || "Load failed.", true); return; }
      applyConfig(c);
      $("config-name").value = name;
      configStatus(`Loaded "${name}".`);
    } catch (e) { configStatus("Load failed: " + e, true); }
  };
}
if ($("config-delete")) {
  $("config-delete").onclick = async () => {
    const name = $("config-load").value || $("config-name").value.trim();
    if (!name) { configStatus("Pick a saved config to delete.", true); return; }
    try {
      await fetch(`/api/configs/${encodeURIComponent(name)}`, { method: "DELETE" });
      await refreshConfigList();
      configStatus(`Deleted "${name}".`);
    } catch (e) { configStatus("Delete failed: " + e, true); }
  };
}

// ---- solve ----------------------------------------------------------------
$("solve").onclick = solve;
["quantity", "minutes"].forEach((id) => $(id).addEventListener("input", saveSettings));
async function solve() {
  if (!SELECTED) { showStatus("Pick an item first.", "err"); return; }
  saveSettings();
  showStatus("Optimising…", "loading");
  $("results").classList.add("hidden");
  const body = {
    item: SELECTED,
    quantity: parseFloat($("quantity").value) || 1,
    minutes: parseFloat($("minutes").value) || 1,
    banned: [...BANNED],
    leaves: [],
    tech_gen: TECHGEN,
    stackable_cards: STACKABLE,
  };
  try {
    const r = await fetch("/api/solve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const res = await r.json();
    if (!res.ok) { showStatus(res.message || res.error || "No solution.", "err"); return; }
    renderResult(res);
  } catch (e) { showStatus("Error: " + e, "err"); }
}

function showStatus(msg, cls) {
  const s = $("status");
  s.textContent = msg;
  s.className = "status " + (cls || "");
}

let LAST_RESULT = null;          // last solve result, for the production graph

function renderResult(res) {
  LAST_RESULT = res;
  $("status").classList.add("hidden");
  $("results").classList.remove("hidden");
  $("total-machines").textContent = res.total_machines.toLocaleString();

  const mt = $("machine-totals"); mt.innerHTML = "";
  let firstFixture = true;
  res.machine_totals.forEach((m) => {
    if (m.is_fixture && firstFixture) {
      firstFixture = false;
      const sep = document.createElement("div");
      sep.className = "chip-sep";
      sep.textContent = "held items (not machines):";
      mt.appendChild(sep);
    }
    const c = document.createElement("div");
    c.className = "chip" + (m.is_fixture ? " fixture" : "");
    c.innerHTML = `<span class="c">${m.count}</span> ${esc(m.machine_name)}`;
    mt.appendChild(c);
  });

  const tgEl = $("techgen-summary");
  const tg = res.tech_gen;
  const usesTg = res.machine_totals.some((m) => m.machine_id === "TECH_GENERATOR");
  if (tg && usesTg) {
    const cards = tg.boost_cards.length
      ? tg.boost_cards.map((c) => `${c.count}× ${esc(c.name)}`).join(", ")
      : "no boost cards";
    tgEl.innerHTML = `🧬 <strong>Tech Generator</strong>: ${tg.output_stacks} stack(s) every `
      + `${tg.cycle_minutes} min = <strong>${tg.per_min_per_gen}/min</strong> per generator · `
      + `${tg.energy_per_tick} J/t · slots: ${cards}`;
    tgEl.classList.remove("hidden");
  } else {
    tgEl.classList.add("hidden");
  }

  const steps = $("steps"); steps.innerHTML = "";
  res.steps.forEach((s) => {
    const el = document.createElement("div");
    el.className = "step" + (s.output_id === res.target ? " target" : "");
    const out = document.createElement("div"); out.className = "out";
    out.appendChild(iconEl(s.output_id, true));
    out.insertAdjacentHTML("beforeend",
      `<div><div>${esc(s.output_name)}</div><div class="rate">${s.produced_per_min}/min</div></div>`);
    // secondary outputs (byproducts). Show the ones USED elsewhere clearly (this is how a
    // Nether Growth Chamber's nether-wart-block byproduct feeds the grinder); dim pure waste.
    if (s.byproducts && s.byproducts.length) {
      const bp = document.createElement("div"); bp.className = "byp";
      [...s.byproducts].sort((a, b) => (b.used === a.used ? 0 : b.used ? 1 : -1)).forEach((b) => {
        const chip = document.createElement("span");
        chip.className = "bp" + (b.used ? " used" : " waste");
        chip.title = b.used ? `${b.name} — also produced here, used elsewhere`
                            : `${b.name} — surplus byproduct (not used)`;
        chip.appendChild(iconEl(b.ref, true));
        chip.insertAdjacentHTML("beforeend", `<span class="q">+${b.per_min}/min</span>`);
        bp.appendChild(chip);
      });
      out.appendChild(bp);
    }
    const ings = document.createElement("div"); ings.className = "ings";
    s.ingredients.forEach((g) => {
      const ig = document.createElement("div"); ig.className = "ing";
      ig.appendChild(iconEl(g.ref, true));
      ig.insertAdjacentHTML("beforeend", `<span class="q">${g.per_min}</span>`);
      ig.title = `${g.name} (${g.per_op}/craft)`;
      ings.appendChild(ig);
    });
    el.innerHTML = `<div class="count">${s.machines}×</div>`;
    const mn = document.createElement("div"); mn.className = "mname"; mn.textContent = s.machine_name;
    if (s.fixtures && s.fixtures.length) {
      mn.insertAdjacentHTML("beforeend",
        s.fixtures.map((f) => ` <span class="fixture">+ ${f.count}× ${esc(f.name)}</span>`).join(""));
    }
    // Mob Simulation Chamber: show data cards (× chamber) + energy draw
    if (s.cards != null) {
      const stacked = s.cards_per_machine > 1 ? ` across ${s.machines} chamber(s)` : "";
      mn.insertAdjacentHTML("beforeend",
        ` <span class="note">${s.cards} card(s)${stacked} · ${s.energy_per_tick} J/t</span>`);
    }
    el.appendChild(mn);
    el.appendChild(out);
    el.insertAdjacentHTML("beforeend", `<span class="arrow">⟵</span>`);
    el.appendChild(ings);
    steps.appendChild(el);
  });

  const raw = $("raw"); raw.innerHTML = "";
  res.raw_inputs.forEach((ri) => {
    const r = document.createElement("div"); r.className = "r";
    r.appendChild(iconEl(ri.ref, true));
    r.insertAdjacentHTML("beforeend", `<span>${esc(ri.name)}</span><span class="q">${ri.per_min}</span>`);
    raw.appendChild(r);
  });
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function esc(s) { return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---- production graph -----------------------------------------------------
// A layered DAG of the production chain: raw inputs on the left flow rightwards
// through the machines that consume them, ending at the target on the right.
// Each machine node shows the icon + rate of the item it produces; hovering a
// node reveals its machine name, speed (item/min) and full input ⟶ output recipe.
const GRAPH = { COL_W: 240, NODE_W: 176, ROW_H: 26, HEAD_PAD: 9, MIN_H: 44, ROW_GAP: 24, PAD: 40 };
let GRAPH_VIEW = null;     // { inner, scale, baseW, baseH, zoom } for the current graph

$("show-graph").onclick = openGraph;
$("graph-close").onclick = closeGraph;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("graph-overlay").classList.contains("hidden")) closeGraph();
});

// left-click drag to pan around the graph (in addition to wheel / scrollbars)
(function enableGraphPan() {
  const canvas = $("graph-canvas");
  let panning = false, startX = 0, startY = 0, startL = 0, startT = 0;
  canvas.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;        // left button only
    panning = true;
    startX = e.clientX; startY = e.clientY;
    startL = canvas.scrollLeft; startT = canvas.scrollTop;
    canvas.classList.add("panning");
    hideGraphTooltip();
    e.preventDefault();                // don't start a text selection while dragging
  });
  window.addEventListener("mousemove", (e) => {
    if (!panning) return;
    canvas.scrollLeft = startL - (e.clientX - startX);
    canvas.scrollTop = startT - (e.clientY - startY);
  });
  const stop = () => { if (panning) { panning = false; canvas.classList.remove("panning"); } };
  window.addEventListener("mouseup", stop);
  window.addEventListener("blur", stop);

  // mouse-wheel zoom, anchored on the cursor so the point under it stays put
  canvas.addEventListener("wheel", (e) => {
    const v = GRAPH_VIEW;
    if (!v) return;
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    const z0 = v.zoom;
    const z1 = Math.min(2.5, Math.max(0.2, z0 * (e.deltaY < 0 ? 1.1 : 1 / 1.1)));
    if (z1 === z0) return;
    const contentX = (canvas.scrollLeft + cx) / z0;
    const contentY = (canvas.scrollTop + cy) / z0;
    v.zoom = z1;
    applyGraphZoom();
    canvas.scrollLeft = contentX * z1 - cx;
    canvas.scrollTop = contentY * z1 - cy;
    hideGraphTooltip();
  }, { passive: false });
})();

function openGraph() {
  if (!LAST_RESULT) return;
  buildGraph(LAST_RESULT);
  $("graph-overlay").classList.remove("hidden");
  document.body.style.overflow = "hidden";   // freeze the page behind the overlay
}
function closeGraph() {
  $("graph-overlay").classList.add("hidden");
  document.body.style.overflow = "";
  hideGraphTooltip();
}

function buildGraph(res) {
  const canvas = $("graph-canvas");
  canvas.innerHTML = "";
  const steps = res.steps || [];

  // node ids: "s<idx>" for a machine step, "raw:<ref>" for a raw/gathered input.
  // producersOf maps an item id to EVERY step that yields it — its dedicated main
  // output AND any machine that drops it as a byproduct (main producers first). A
  // consumer links to all of them, so e.g. string is fed both by its Excitation
  // Chamber and as a Virtual Aquarium byproduct; neither producer floats unlinked.
  const producersOf = {};
  const addProd = (ref, id) => { const a = producersOf[ref] || (producersOf[ref] = []); if (!a.includes(id)) a.push(id); };
  steps.forEach((s, i) => addProd(s.output_id, "s" + i));
  steps.forEach((s, i) => (s.byproducts || []).forEach((b) => addProd(b.ref, "s" + i)));

  const nodes = {};
  steps.forEach((s, i) => { nodes["s" + i] = { id: "s" + i, kind: "machine", step: s }; });
  (res.raw_inputs || []).forEach((ri) => {
    const id = "raw:" + ri.ref;
    if (!nodes[id]) nodes[id] = { id, kind: "raw", raw: ri };
  });

  // edges: every (producer ⟶ consumer) pair, tagged with the item ref so a node can
  // show ONLY the outputs it actually feeds to another machine. An output a machine
  // feeds back into itself (a Nether Growth Chamber's fungus) is a self-loop, skipped.
  const edges = [];
  const outRefsOf = {};
  const addEdge = (from, to, ref) => { edges.push({ from, to, ref }); (outRefsOf[from] || (outRefsOf[from] = [])).push(ref); };
  steps.forEach((s, i) => {
    const to = "s" + i;
    (s.ingredients || []).forEach((g) => {
      const prods = producersOf[g.ref];
      if (!prods || !prods.length) {
        const from = "raw:" + g.ref;
        if (!nodes[from]) nodes[from] = { id: from, kind: "raw", raw: { ref: g.ref, name: g.name, per_min: g.per_min } };
        addEdge(from, to, g.ref);
      } else {
        prods.forEach((from) => { if (from !== to) addEdge(from, to, g.ref); });
      }
    });
  });

  // Cycle handling: recipes can loop (fishing-rod ⟶ aquarium ⟶ string ⟶ fishing-rod).
  // A DFS flags the edges that close a cycle as "back" edges; only forward edges drive
  // the layering, so layers stay a clean left→right DAG. Back edges are later drawn
  // against the flow with an arrowhead.
  const ekey = (e) => e.from + "" + e.to + "" + e.ref;
  const adjAll = {};
  edges.forEach((e) => (adjAll[e.from] || (adjAll[e.from] = [])).push(e));
  const colour = {}, isBack = new Set();
  const dfs = (id) => {
    colour[id] = 1;
    (adjAll[id] || []).forEach((e) => {
      if (colour[e.to] === 1) isBack.add(ekey(e));        // edge to an on-stack ancestor → back
      else if (!colour[e.to]) dfs(e.to);
    });
    colour[id] = 2;
  };
  Object.keys(nodes).forEach((id) => { if (!colour[id]) dfs(id); });
  const childrenOf = {};
  edges.forEach((e) => { if (!isBack.has(ekey(e))) (childrenOf[e.from] || (childrenOf[e.from] = [])).push(e.to); });

  // The outputs to draw on a node = the distinct items that leave it for another
  // machine. The target (and any terminal node) feeds nothing, so fall back to its
  // own main output. Each entry is {ref, name, per_min} for the icon + rate.
  function outInfo(node, ref) {
    if (node.kind === "raw") return { ref: node.raw.ref, name: node.raw.name, per_min: node.raw.per_min };
    const s = node.step;
    if (ref === s.output_id) return { ref, name: s.output_name, per_min: s.produced_per_min };
    const b = (s.byproducts || []).find((x) => x.ref === ref);
    return b ? { ref, name: b.name, per_min: b.per_min } : { ref, name: ref, per_min: 0 };
  }
  Object.values(nodes).forEach((n) => {
    if (n.kind === "raw") { n.outs = [outInfo(n)]; return; }
    const refs = [];
    (outRefsOf[n.id] || []).forEach((r) => { if (!refs.includes(r)) refs.push(r); });
    if (!refs.length) refs.push(n.step.output_id);   // target / terminal node
    n.outs = refs.map((r) => outInfo(n, r));
  });

  // ===== Sugiyama-style layered layout with crossing minimisation ===========
  // 1) LAYER ASSIGNMENT — ALAP: longest path TO a sink, so every node sits as far
  //    right as it can. Producers land in lower layers than what consumes them, and
  //    raw inputs hug the chain that uses them. Memoised, cycle-guarded.
  const sinkRank = {}, visiting = new Set();
  function rank(id) {
    if (sinkRank[id] != null) return sinkRank[id];
    if (visiting.has(id)) return 0;
    visiting.add(id);
    let r = 0;
    (childrenOf[id] || []).forEach((c) => { r = Math.max(r, rank(c) + 1); });
    visiting.delete(id);
    return (sinkRank[id] = r);
  }
  Object.keys(nodes).forEach(rank);
  let L = 0;
  Object.values(sinkRank).forEach((r) => { L = Math.max(L, r); });
  const layerOf = {};
  Object.keys(nodes).forEach((id) => { layerOf[id] = L - sinkRank[id]; });

  // 2) DUMMY NODES — split every edge that spans more than one layer into a chain
  //    of dummies, one per layer it passes through. Afterwards all edges connect
  //    only ADJACENT layers, which is what the crossing heuristics assume and what
  //    lets long edges route cleanly through reserved vertical slots.
  const layers = [];
  for (let i = 0; i <= L; i++) layers[i] = [];
  Object.keys(nodes).forEach((id) => layers[layerOf[id]].push(id));
  const dummies = new Set();
  const up = {}, down = {};                 // adjacent-layer neighbour lists
  const addAdj = (a, b) => { (down[a] || (down[a] = [])).push(b); (up[b] || (up[b] = [])).push(a); };
  const routes = [];                        // {e, chain:[from, ...dummies, to]} for drawing
  let dseq = 0;
  edges.forEach((e) => {
    const lf = layerOf[e.from], lt = layerOf[e.to];
    const back = isBack.has(ekey(e)) || lt <= lf;          // runs against the left→right flow
    const lo = Math.min(lf, lt), hi = Math.max(lf, lt);
    const lowNode = lf <= lt ? e.from : e.to;              // endpoint in the lower (left) layer
    const highNode = lf <= lt ? e.to : e.from;
    const mids = [];
    let prev = lowNode;
    for (let l = lo + 1; l < hi; l++) {                    // dummies span the layers in between
      const d = "d" + (dseq++);
      dummies.add(d); layerOf[d] = l; layers[l].push(d);
      addAdj(prev, d); mids.push(d); prev = d;
    }
    if (hi > lo) addAdj(prev, highNode);                  // (same-layer back edges add no adjacency)
    const inc = [lowNode, ...mids, highNode];             // left→right node sequence
    routes.push({ e, back, chain: e.from === lowNode ? inc : inc.slice().reverse() });
  });

  // 3) ORDERING — minimise crossings. Repeated median sweeps (each node pulled to
  //    the median row of its neighbours in the adjacent layer) followed by adjacent
  //    transposition (swap neighbours when it lowers the crossing count). Keep the
  //    best ordering seen across all iterations.
  let order = {};
  layers.forEach((col) => col.forEach((id, i) => { order[id] = i; }));

  function pairCrossings(la, lb) {           // crossings on edges between layers la|lb
    const es = [];
    layers[la].forEach((id) => (down[id] || []).forEach((t) => es.push([order[id], order[t]])));
    es.sort((p, q) => p[0] - q[0] || p[1] - q[1]);
    let c = 0;
    for (let i = 0; i < es.length; i++)
      for (let j = i + 1; j < es.length; j++) if (es[i][1] > es[j][1]) c++;
    return c;
  }
  const totalCrossings = () => { let c = 0; for (let l = 0; l < L; l++) c += pairCrossings(l, l + 1); return c; };

  function median(vals) {
    if (!vals.length) return -1;
    vals.sort((a, b) => a - b);
    const m = Math.floor(vals.length / 2);
    if (vals.length % 2) return vals[m];
    if (vals.length === 2) return (vals[0] + vals[1]) / 2;
    const lft = vals[m - 1] - vals[0], rgt = vals[vals.length - 1] - vals[m];
    return (lft + rgt) === 0 ? (vals[m - 1] + vals[m]) / 2
      : (vals[m - 1] * rgt + vals[m] * lft) / (lft + rgt);
  }
  function medianSweep(useUp) {              // useUp: order each layer by its left neighbours
    const idxs = layers.map((_, l) => l);
    if (!useUp) idxs.reverse();
    idxs.forEach((l) => {
      const adj = useUp ? up : down;
      const med = {};
      layers[l].forEach((id) => { med[id] = median((adj[id] || []).map((n) => order[n]).filter((x) => x >= 0)); });
      // nodes with no neighbour in that direction stay put; the rest sort by median
      const movable = layers[l].filter((id) => med[id] >= 0).sort((a, b) => med[a] - med[b]);
      let mi = 0;
      layers[l] = layers[l].map((id) => (med[id] >= 0 ? movable[mi++] : id));
      layers[l].forEach((id, i) => { order[id] = i; });
    });
  }
  const localCross = (l) => (l > 0 ? pairCrossings(l - 1, l) : 0) + (l < L ? pairCrossings(l, l + 1) : 0);
  function transpose() {
    let improved = true, guard = 0;
    while (improved && guard++ < 4) {
      improved = false;
      for (let l = 0; l <= L; l++) {
        for (let i = 0; i < layers[l].length - 1; i++) {
          const a = layers[l][i], b = layers[l][i + 1];
          const before = localCross(l);
          layers[l][i] = b; layers[l][i + 1] = a; order[a] = i + 1; order[b] = i;
          if (localCross(l) < before) improved = true;
          else { layers[l][i] = a; layers[l][i + 1] = b; order[a] = i; order[b] = i + 1; }
        }
      }
    }
  }
  // The median/transpose pass is order-sensitive and gets stuck in local minima, so
  // run it from several shuffled starts and keep the fewest-crossings result. Seeded
  // RNG → the same graph always lays out the same way across reloads.
  const baseLayers = layers.map((col) => col.slice());
  const rngFor = (seed) => () => {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const shuffle = (a, rnd) => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } };
  const setLayers = (src) => {
    src.forEach((col, l) => { layers[l] = col.slice(); });
    order = {};
    layers.forEach((col) => col.forEach((id, i) => { order[id] = i; }));
  };
  let bestSnap = null, bestC = Infinity;
  for (let R = 0; R < 24; R++) {
    const start = baseLayers.map((col) => col.slice());
    if (R > 0) { const rnd = rngFor(R * 1009 + 7); start.forEach((col) => shuffle(col, rnd)); }
    setLayers(start);
    let localBest = Infinity, localSnap = null;
    for (let it = 0; it < 16; it++) {
      medianSweep(it % 2 === 0);
      transpose();
      const c = totalCrossings();
      if (c < localBest) { localBest = c; localSnap = layers.map((col) => col.slice()); }
      if (c === 0) break;
    }
    if (localBest < bestC) { bestC = localBest; bestSnap = localSnap; }
    if (bestC === 0) break;
  }
  setLayers(bestSnap);

  // 4) COORDINATES — x by layer; y by the priority method (Sugiyama). Each node is
  //    pulled to the median row of its neighbours in the adjacent layer without ever
  //    displacing an already-placed higher-priority node, so edges straighten out and
  //    crossings that remain stay clean. Dummy spines have top priority → long edges
  //    run nearly horizontal. Sweeps alternate up/down a number of times.
  const { COL_W, NODE_W, ROW_H, HEAD_PAD, MIN_H, ROW_GAP, PAD } = GRAPH;
  const DUMMY_H = 12;
  const hOf = (id) => dummies.has(id) ? DUMMY_H : Math.max(MIN_H, nodes[id].outs.length * ROW_H + HEAD_PAD * 2);
  const sepAt = (col, i) => (hOf(col[i - 1]) + hOf(col[i])) / 2 + ROW_GAP;   // centre-to-centre

  // initial centres: simple sequential packing per layer
  const center = {};
  layers.forEach((col) => {
    let y = 0;
    col.forEach((id, i) => { if (i) y += sepAt(col, i); center[id] = y; });
  });

  const degOf = (id) => (up[id] ? up[id].length : 0) + (down[id] ? down[id].length : 0);
  const prioOf = (id) => dummies.has(id) ? 1e6 : degOf(id);
  const medianOf = (vals) => {
    if (!vals.length) return null;
    vals.sort((a, b) => a - b);
    const m = vals.length >> 1;
    return vals.length % 2 ? vals[m] : (vals[m - 1] + vals[m]) / 2;
  };
  function alignLayer(col, adj) {
    const n = col.length;
    const gap = col.map((_, i) => (i ? sepAt(col, i) : 0));   // gap[i] = sep(col[i-1],col[i])
    const desired = col.map((id) => medianOf((adj[id] || []).map((x) => center[x])));
    const cur = col.map((id) => center[id]);
    const placed = new Array(n).fill(false);
    col.map((_, i) => i).sort((a, b) => prioOf(col[b]) - prioOf(col[a])).forEach((i) => {
      const d = desired[i];
      if (d != null && d > cur[i]) {                 // pull down
        let limit = Infinity, cum = 0;
        for (let j = i + 1; j < n; j++) { cum += gap[j]; if (placed[j]) { limit = cur[j] - cum; break; } }
        cur[i] = Math.min(d, limit);
        let c = 0;
        for (let j = i + 1; j < n; j++) { c += gap[j]; const mn = cur[i] + c; if (!placed[j] && cur[j] < mn) cur[j] = mn; else break; }
      } else if (d != null && d < cur[i]) {          // pull up
        let limit = -Infinity, cum = 0;
        for (let j = i - 1; j >= 0; j--) { cum += gap[j + 1]; if (placed[j]) { limit = cur[j] + cum; break; } }
        cur[i] = Math.max(d, limit);
        let c = 0;
        for (let j = i - 1; j >= 0; j--) { c += gap[j + 1]; const mx = cur[i] - c; if (!placed[j] && cur[j] > mx) cur[j] = mx; else break; }
      }
      placed[i] = true;
    });
    col.forEach((id, i) => { center[id] = cur[i]; });
  }
  for (let it = 0; it < 14; it++) {
    const useUp = it % 2 === 0;
    const idxs = layers.map((_, l) => l);
    if (!useUp) idxs.reverse();
    idxs.forEach((l) => alignLayer(layers[l], useUp ? up : down));
  }

  // normalise to start at PAD; size the canvas to the actual extent
  let minC = Infinity, maxC = -Infinity;
  layers.forEach((col) => col.forEach((id) => {
    minC = Math.min(minC, center[id] - hOf(id) / 2);
    maxC = Math.max(maxC, center[id] + hOf(id) / 2);
  }));
  const height = PAD * 2 + (maxC - minC);
  const width = PAD * 2 + L * COL_W + NODE_W;

  const pos = {};
  layers.forEach((col, l) => col.forEach((id) => {
    const h = hOf(id);
    pos[id] = { x: PAD + l * COL_W, y: PAD + (center[id] - h / 2) - minC, h };
  }));

  const inner = document.createElement("div");
  inner.className = "graph-inner";
  // the scaled child holds the edges + nodes; zooming scales it while `inner`
  // (sized in applyGraphZoom) keeps the scroll area in sync.
  const scale = document.createElement("div");
  scale.className = "graph-scale";
  scale.style.width = width + "px";
  scale.style.height = height + "px";

  // y of a specific output row on a node's right edge, so multi-output machines
  // route each edge from the icon it actually corresponds to.
  const outRowY = (id, ref) => {
    const node = nodes[id], p = pos[id];
    const i = Math.max(0, node.outs.findIndex((o) => o.ref === ref));
    const top = p.y + (p.h - node.outs.length * ROW_H) / 2;
    return top + i * ROW_H + ROW_H / 2;
  };

  // 5) DRAW EDGES — each route is a smooth spline through its dummy points, with
  //    horizontal tangents at every waypoint so it never cuts across a column.
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "graph-edges");
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  const edgePath = (pts) => {
    let d = `M${pts[0].x},${pts[0].y}`;
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i], mx = (a.x + b.x) / 2;
      d += ` C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}`;
    }
    return d;
  };
  // arrowhead for back edges only — forward edges read left→right by default, so they
  // need no marker; back edges run the other way and get a head to show the direction.
  const defs = document.createElementNS(NS, "defs");
  defs.innerHTML = '<marker id="garrow" viewBox="0 0 8 8" refX="6.5" refY="4" '
    + 'markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#3a9bdc"/></marker>';
  svg.appendChild(defs);
  routes.forEach((r) => {
    const from = r.chain[0], to = r.chain[r.chain.length - 1];
    if (!pos[from] || !pos[to]) return;
    // forward edges leave the producer's right and enter the consumer's left; back
    // edges leave the left and enter the consumer's right, so the head points inward.
    const sx = r.back ? pos[from].x : pos[from].x + NODE_W;
    const ex = r.back ? pos[to].x + NODE_W : pos[to].x;
    const pts = [{ x: sx, y: outRowY(from, r.e.ref) }];
    for (let k = 1; k < r.chain.length - 1; k++) {
      const d = r.chain[k];
      pts.push({ x: pos[d].x + NODE_W / 2, y: pos[d].y + pos[d].h / 2 });
    }
    pts.push({ x: ex, y: pos[to].y + pos[to].h / 2 });
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", edgePath(pts));
    path.setAttribute("class", "graph-edge" + (r.back ? " back" : ""));
    if (r.back) path.setAttribute("marker-end", "url(#garrow)");
    svg.appendChild(path);
  });
  scale.appendChild(svg);

  Object.values(nodes).forEach((node) => {
    const p = pos[node.id];
    const el = document.createElement("div");
    el.className = "gnode " + node.kind;
    if (node.kind === "machine" && node.step.output_id === res.target) el.classList.add("target");
    el.style.cssText = `left:${p.x}px;top:${p.y}px;width:${NODE_W}px;height:${p.h}px`;
    if (node.kind === "machine") {
      el.innerHTML = `<span class="gcount">${node.step.machines}×</span>`;
    }
    const body = document.createElement("div"); body.className = "gbody";
    const rawTag = node.kind === "raw" ? " · raw" : "";
    node.outs.forEach((o) => {
      const row = document.createElement("div"); row.className = "gout-row";
      row.appendChild(iconEl(o.ref, true));
      row.insertAdjacentHTML("beforeend",
        `<span class="gout"><span class="gname">${esc(o.name)}</span>`
        + `<span class="grate">${o.per_min}/min${rawTag}</span></span>`);
      body.appendChild(row);
    });
    el.appendChild(body);
    if (node.kind === "machine") {
      el.addEventListener("mouseenter", (ev) => showGraphTooltip(ev, node.step));
      el.addEventListener("mousemove", moveGraphTooltip);
      el.addEventListener("mouseleave", hideGraphTooltip);
    }
    scale.appendChild(el);
  });

  inner.appendChild(scale);
  canvas.appendChild(inner);
  GRAPH_VIEW = { inner, scale, baseW: width, baseH: height, zoom: 1 };
  applyGraphZoom();
}

function applyGraphZoom() {
  const v = GRAPH_VIEW;
  if (!v) return;
  v.scale.style.transform = `scale(${v.zoom})`;
  v.inner.style.width = v.baseW * v.zoom + "px";
  v.inner.style.height = v.baseH * v.zoom + "px";
}

// Hover tooltip — reuses the production-step visual: ingredients ⟶ output.
function showGraphTooltip(ev, s) {
  const tt = $("graph-tooltip");
  tt.innerHTML = "";
  const title = document.createElement("div");
  title.className = "gtt-title";
  title.textContent = s.machine_name;
  tt.appendChild(title);
  const speed = document.createElement("div");
  speed.className = "gtt-speed";
  speed.textContent = `${s.produced_per_min}/min · ${s.machines}× machine(s)`;
  tt.appendChild(speed);

  const io = document.createElement("div");
  io.className = "gtt-io";
  if (s.ingredients && s.ingredients.length) {
    const ings = document.createElement("div"); ings.className = "gtt-ings";
    s.ingredients.forEach((g) => {
      const ig = document.createElement("div"); ig.className = "ing";
      ig.appendChild(iconEl(g.ref, true));
      ig.insertAdjacentHTML("beforeend", `<span class="q">${g.per_min}</span>`);
      ig.title = `${g.name} (${g.per_op}/craft)`;
      ings.appendChild(ig);
    });
    io.appendChild(ings);
  } else {
    io.insertAdjacentHTML("beforeend", `<span class="muted">⚡ energy only — no item input</span>`);
  }
  io.insertAdjacentHTML("beforeend", `<span class="arrow">⟶</span>`);
  const out = document.createElement("div"); out.className = "out";
  out.appendChild(iconEl(s.output_id, true));
  out.insertAdjacentHTML("beforeend",
    `<div><div>${esc(s.output_name)}</div><div class="rate">${s.produced_per_min}/min</div></div>`);
  io.appendChild(out);
  tt.appendChild(io);

  tt.classList.remove("hidden");
  moveGraphTooltip(ev);
}
function moveGraphTooltip(ev) {
  const tt = $("graph-tooltip");
  if (tt.classList.contains("hidden")) return;
  const pad = 14;
  const r = tt.getBoundingClientRect();
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > window.innerWidth) x = ev.clientX - r.width - pad;
  if (y + r.height > window.innerHeight) y = ev.clientY - r.height - pad;
  tt.style.left = Math.max(8, x) + "px";
  tt.style.top = Math.max(8, y) + "px";
}
function hideGraphTooltip() { $("graph-tooltip").classList.add("hidden"); }

// ---- page tabs ------------------------------------------------------------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    const page = tab.dataset.page;
    document.querySelectorAll(".page").forEach((p) => p.classList.toggle("hidden", p.id !== "page-" + page));
    if (page === "search") loadRecipeMachines();   // populate the picker on first visit
  };
});

// ---- shared recipe card ---------------------------------------------------
// One card = one recipe: "ingredients ⟶ outputs" under a machine header.
// `highlight` (optional) is an item id to emphasise among the outputs (reverse search).
function recipeCard(rec, highlight) {
  const el = document.createElement("div");
  el.className = "rcard";

  const head = document.createElement("div"); head.className = "rcard-head";
  const dur = fmtOpDuration(rec.op_seconds);
  // addon · <one-operation duration> · <items produced per minute>
  const meta = [esc(rec.addon || ""), dur && `${dur}/op`, `${rec.output_per_min}/min`]
    .filter(Boolean).join(" · ");
  head.innerHTML = `<span class="mn">${esc(rec.machine_name)}</span>`
    + `<span class="muted">${meta}</span>`;
  el.appendChild(head);

  const body = document.createElement("div"); body.className = "rcard-body";

  const ings = document.createElement("div"); ings.className = "rio";
  if (rec.energy_only || !rec.ingredients.length) {
    ings.innerHTML = `<span class="muted">⚡ energy only — no item input</span>`;
  } else {
    rec.ingredients.forEach((g) => ings.appendChild(ioChip(g, false)));
  }
  body.appendChild(ings);

  const arrow = document.createElement("span"); arrow.className = "arrow"; arrow.textContent = "⟶";
  body.appendChild(arrow);

  const outs = document.createElement("div"); outs.className = "rio";
  rec.outputs.forEach((o) => outs.appendChild(ioChip(o, o.ref === highlight)));
  body.appendChild(outs);

  el.appendChild(body);

  if (rec.fixtures && rec.fixtures.length) {
    const fx = document.createElement("div"); fx.className = "rcard-fx muted";
    fx.textContent = "uses: " + rec.fixtures.map((f) => `${f.count || 1}× ${f.name || f.id}`).join(", ");
    el.appendChild(fx);
  }
  return el;
}
// Duration of one machine operation: seconds under a minute, else minutes.
function fmtOpDuration(secs) {
  if (secs == null) return "";
  const trim = (n) => (Math.round(n * 100) / 100).toString();
  return secs < 60 ? `${trim(secs)} s` : `${trim(secs / 60)} min`;
}
function ioChip(g, hl) {
  const chip = document.createElement("div");
  chip.className = "io-chip" + (hl ? " hl" : "");
  chip.appendChild(iconEl(g.ref, true));
  chip.insertAdjacentHTML("beforeend",
    `<span class="nm">${esc(g.name)}</span><span class="q">×${g.amount}</span>`);
  return chip;
}

// the reverse-search / search pages share the calculator's Tech Generator boost
// config so their Tech Generator recipes show the same boosted output & rate.
function techgenParam() {
  return `&tech_gen=${encodeURIComponent(JSON.stringify(TECHGEN))}`;
}

// ---- reverse search (item -> recipes that produce it) ---------------------
let REV_RECIPES = [];        // recipes for the current item, in server (default) order
let REV_ITEM_ID = null;      // the searched item id (highlighted in cards)

// Order REV_RECIPES per the sort dropdown without mutating the default order.
function sortedRevRecipes() {
  const mode = $("rev-sort").value;
  if (mode === "default") return REV_RECIPES;
  const dir = mode === "speed-asc" ? 1 : -1;
  return [...REV_RECIPES].sort((a, b) => dir * (a.output_per_min - b.output_per_min));
}

function renderRevCards() {
  const cards = $("rev-cards"); cards.innerHTML = "";
  if (!REV_RECIPES.length) {
    cards.innerHTML = `<p class="muted">No recipe produces this item — it's a raw/gathered input.</p>`;
    return;
  }
  sortedRevRecipes().forEach((rec) => cards.appendChild(recipeCard(rec, REV_ITEM_ID)));
}
$("rev-sort").onchange = renderRevCards;

attachItemSearch($("rev-search"), $("rev-suggestions"), async (it) => {
  $("rev-search").value = it.name;
  $("rev-status").className = "status loading";
  $("rev-status").textContent = "Loading…";
  $("rev-results").classList.add("hidden");
  const r = await fetch(`/api/recipes_for?item=${encodeURIComponent(it.id)}${techgenParam()}`);
  const data = await r.json();
  REV_RECIPES = data.recipes;
  REV_ITEM_ID = it.id;
  $("rev-status").classList.add("hidden");
  $("rev-results").classList.remove("hidden");
  $("rev-heading").textContent = `${data.item_name} — ${data.recipes.length} recipe(s)`;
  renderRevCards();
});

// ---- search (machine -> its recipes) --------------------------------------
let RECIPE_MACHINES_LOADED = false;
async function loadRecipeMachines() {
  if (RECIPE_MACHINES_LOADED) return;
  RECIPE_MACHINES_LOADED = true;
  const r = await fetch("/api/recipe_machines");
  const rows = (await r.json()).machines;
  const sel = $("machine-select");
  const byAddon = {};
  rows.forEach((m) => (byAddon[m.addon] || (byAddon[m.addon] = [])).push(m));
  Object.keys(byAddon)
    .sort((a, b) => addonRank(a) - addonRank(b) || a.localeCompare(b))
    .forEach((addon) => {
      const grp = document.createElement("optgroup"); grp.label = addonLabel(addon);
      byAddon[addon].forEach((m) => {
        const o = document.createElement("option");
        o.value = m.id;
        o.textContent = `${m.name} (${m.count})`;
        grp.appendChild(o);
      });
      sel.appendChild(grp);
    });
}
$("machine-select").onchange = async (e) => {
  const mid = e.target.value;
  if (!mid) { $("search-results").classList.add("hidden"); return; }
  $("search-status").className = "status loading";
  $("search-status").textContent = "Loading…";
  $("search-results").classList.add("hidden");
  const r = await fetch(`/api/recipes_by_machine?machine=${encodeURIComponent(mid)}${techgenParam()}`);
  const data = await r.json();
  const cards = $("search-cards"); cards.innerHTML = "";
  $("search-status").classList.add("hidden");
  $("search-results").classList.remove("hidden");
  $("search-heading").textContent = `${data.machine_name} — ${data.recipes.length} recipe(s)`;
  data.recipes.forEach((rec) => cards.appendChild(recipeCard(rec)));
};

// ---- init -----------------------------------------------------------------
(async function init() {
  ICON_MAP = await (await fetch("/api/icon_map")).json();
  await loadMachines();
  refreshConfigList();                              // populate the saved-config dropdown
  $("ban-count").textContent = BANNED.size;        // reflect restored bans
  // restore the last search and re-run it (icons are ready now)
  if (SAVED && SAVED.query && SAVED.query.id) {
    if (SAVED.query.quantity != null) $("quantity").value = SAVED.query.quantity;
    if (SAVED.query.minutes != null) $("minutes").value = SAVED.query.minutes;
    selectItem({ id: SAVED.query.id, name: SAVED.query.name, addon: SAVED.query.addon });
    solve();
  }
})();
