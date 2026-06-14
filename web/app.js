// Slimefun Automation Calculator — front end

let ICON_MAP = {};
let SELECTED = null;            // selected item id
let SELECTED_ITEM = null;       // full selected item {id, name, addon} (for persistence)
const BANNED = new Set();
// 4 boost slots; default to 4x cloner T1 (the standard fill)
const TECHGEN = [{category: "cloning", tier: 1}, {category: "cloning", tier: 1},
                 {category: "cloning", tier: 1}, {category: "cloning", tier: 1}];

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
let searchTimer = null, activeIdx = -1, currentOpts = [];
$("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (q.length < 2) { hideSuggestions(); return; }
  searchTimer = setTimeout(() => doSearch(q), 140);
});
$("search").addEventListener("keydown", (e) => {
  const box = $("suggestions");
  if (box.classList.contains("hidden")) return;
  if (e.key === "ArrowDown") { activeIdx = Math.min(activeIdx + 1, currentOpts.length - 1); renderActive(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { activeIdx = Math.max(activeIdx - 1, 0); renderActive(); e.preventDefault(); }
  else if (e.key === "Enter") { if (currentOpts[activeIdx]) selectItem(currentOpts[activeIdx]); e.preventDefault(); }
  else if (e.key === "Escape") hideSuggestions();
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) hideSuggestions();
});

async function doSearch(q) {
  const r = await fetch(`/api/items?q=${encodeURIComponent(q)}`);
  const data = await r.json();
  currentOpts = data.items; activeIdx = 0;
  const box = $("suggestions");
  box.innerHTML = "";
  if (!currentOpts.length) { hideSuggestions(); return; }
  currentOpts.forEach((it, i) => {
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
    opt.onclick = () => selectItem(it);
    box.appendChild(opt);
  });
  box.classList.remove("hidden");
}
function renderActive() {
  [...$("suggestions").children].forEach((c, i) => c.classList.toggle("active", i === activeIdx));
}
function hideSuggestions() { $("suggestions").classList.add("hidden"); }

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

// ---- init -----------------------------------------------------------------
(async function init() {
  ICON_MAP = await (await fetch("/api/icon_map")).json();
  await loadMachines();
  $("ban-count").textContent = BANNED.size;        // reflect restored bans
  // restore the last search and re-run it (icons are ready now)
  if (SAVED && SAVED.query && SAVED.query.id) {
    if (SAVED.query.quantity != null) $("quantity").value = SAVED.query.quantity;
    if (SAVED.query.minutes != null) $("minutes").value = SAVED.query.minutes;
    selectItem({ id: SAVED.query.id, name: SAVED.query.name, addon: SAVED.query.addon });
    solve();
  }
})();
