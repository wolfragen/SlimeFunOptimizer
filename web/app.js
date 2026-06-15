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

function renderResult(res) {
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
