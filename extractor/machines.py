"""Machine metadata + throughput model.

Builds `data/machines.json`: one entry per "machine" the solver can place, where a
machine is either an electric machine class (recipes via registerRecipe) or a
RecipeType workbench/multiblock (recipes via the crafting constructor).

Throughput (items/min) is rate-accurate up to a small set of clearly-named, in-game
calibratable constants (Slimefun's exact tick cadence varies by version/config):

  electric recipe:  rate = output_amount * 60 * speed / time_seconds
  crafting recipe:  rate = output_amount * 60 / seconds_per_op(recipe_type)

`speed` is read from each machine's `getSpeed()` when it is a constant override
(else defaults to 1). `seconds_per_op` is a per-RecipeType default (auto-crafter
cadence) that the user can tune. Every machine has `banned: false` by default so
the GUI can toggle availability.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from . import bytecode, classfile

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
DATA = ROOT / "data"

# Default seconds-per-operation for crafting RecipeTypes (auto-crafter cadence).
# Tunable after in-game calibration. Multiblocks that are slow rituals get more.
DEFAULT_SECONDS_PER_OP = 2.0
RECIPE_TYPE_SECONDS = {
    "ENHANCED_CRAFTING_TABLE": 2.0,
    "MAGIC_WORKBENCH": 2.0,
    "ARMOR_FORGE": 2.0,
    "GRIND_STONE": 2.0,
    "ORE_CRUSHER": 2.0,
    "COMPRESSOR": 2.0,
    "PRESSURE_CHAMBER": 4.0,
    "SMELTERY": 4.0,
    "ANCIENT_ALTAR": 12.0,
    "JUICER": 2.0,
    "MOB_DROP": 1.0,
    "GOLD_PAN": 2.0,
}

# RecipeTypes that are not real production (item just appears / not automatable here)
NON_PRODUCING = {"NULL", "NONE", "INTERACT", None}

# Slimefun MULTIBLOCKS are MANUAL and never used for automation. Grid/ritual multiblocks
# are automated by FluffyMachines' Auto-* machines, which each craft 1 item per Slimefun
# tick (= 120/min, verified: AutoCrafter/AutoCraftingTable/AutoAncientAltar all tick once
# per call with no interval). Map the RecipeType to that auto machine.
# Each grid/ritual recipe type can run on SEVERAL auto machines (Slimefun's own
# Auto-Crafters and FluffyMachines' Auto-* machines); all craft 1 item/Slimefun tick =
# 120/min (verified in code — no auto-crafter is actually faster). The solver offers each
# as a separate bannable option so you can use whichever you have.
# NTW_AUTO_CRAFTER (Networks) crafts any grid recipe (vanilla + slimefun) and is the user's
# preferred crafter (easiest to set up); it's listed first so the solver prefers it. It can't
# do ANCIENT_ALTAR rituals.
# FluffyMachines Smart Factory is a programmable auto-crafter that reads a vanilla or
# Slimefun grid recipe and repeats it, so it's an option for both crafting-grid types.
RECIPE_TYPE_MACHINES = {
    "VANILLA_CRAFTING": ["NTW_AUTO_CRAFTER", "VANILLA_AUTO_CRAFTER", "AUTO_CRAFTING_TABLE", "SMART_FACTORY"],
    "ENHANCED_CRAFTING_TABLE": ["NTW_AUTO_CRAFTER", "ENHANCED_AUTO_CRAFTER", "AUTO_ENHANCED_CRAFTING_TABLE", "SMART_FACTORY"],
    "MAGIC_WORKBENCH": ["NTW_AUTO_CRAFTER", "AUTO_MAGIC_WORKBENCH"],
    "ARMOR_FORGE": ["NTW_AUTO_CRAFTER", "ARMOR_AUTO_CRAFTER", "AUTO_ARMOR_FORGE"],
    "ANCIENT_ALTAR": ["AUTO_ANCIENT_ALTAR"],
}

# Multiblocks with a dedicated TIERED electric version. Unlike RECIPE_TYPE_MACHINES (flat
# auto-crafters), these are real AContainer machines whose throughput depends on the tier's
# processing speed AND the recipe's registration time, via Slimefun's integer-tick formula
# (see graph._ops_per_min). Value = (base item id, machine_tiers class, base seconds).
# Base seconds extracted from Slimefun's PostSetup / the machine's findNextRecipe:
#   grinder/washer = 4s; smeltery ~8s (dust->ingot; alloys are 12s — approximated).
# NOTE: PostSetup.loadOreGrinderRecipes() merges BOTH GrindStone.getRecipes() AND
# OreCrusher.getRecipes() into the Electric Ore Grinder, so ORE_CRUSHER recipes
# (ore doubling, sifted-ore chain, CARBON->COAL, ...) are automatable there too.
MULTIBLOCK_ELECTRIC = {
    "GRIND_STONE": ("ELECTRIC_ORE_GRINDER", "ElectricOreGrinder", 4),
    "ORE_CRUSHER": ("ELECTRIC_ORE_GRINDER", "ElectricOreGrinder", 4),
    "ORE_WASHER":  ("ELECTRIC_DUST_WASHER", "ElectricDustWasher", 4),
    "SMELTERY":    ("ELECTRIC_SMELTERY", "ElectricSmeltery", 8),
}
# the auto-crafters proper (used only for the 'automation' catalog category). Listed
# explicitly so electric mirror machines (Electric Ore Grinder) stay categorised as electric.
_AUTO_CRAFTERS = {
    "NTW_AUTO_CRAFTER", "VANILLA_AUTO_CRAFTER", "AUTO_CRAFTING_TABLE",
    "ENHANCED_AUTO_CRAFTER", "AUTO_ENHANCED_CRAFTING_TABLE", "AUTO_MAGIC_WORKBENCH",
    "ARMOR_AUTO_CRAFTER", "AUTO_ARMOR_FORGE", "AUTO_ANCIENT_ALTAR", "SMART_FACTORY",
}

# PROCESS multiblocks have no FluffyMachines auto-crafter; their Slimefun electric versions
# (Electric Smeltery / Ore Grinder / Dust Washer / Gold Pan) read the multiblock recipes but
# expose no extractable per-operation time (0 registered recipes), so we do NOT guess a rate.
# Per the rule "if it can't be made by an electric machine it's a raw input", these recipe
# types are dropped — their items are produced only if some OTHER electric machine makes them.
MULTIBLOCK_MANUAL = {
    "COMPRESSOR",
    "PRESSURE_CHAMBER", "JUICER", "GOLD_PAN", "TABLE_SAW", "MOB_DROP",
}

# default cadence for a few extra crafting RecipeTypes added during coverage work
RECIPE_TYPE_SECONDS_EXTRA = {
    "INFINITY_WORKBENCH": 4.0,
    "TECH_MUTATION": 8.0,
    "SINGULARITY_CONSTRUCTOR": 8.0,
    "ELECTRIC_GEAR_FABRICATOR": 6.0,
    "ELECTRIC_MAGICAL_FABRICATOR": 6.0,
    "ELECTRIC_ORE_GRINDER": 2.0,        # electric Grind Stone (calibration default)
    "ELECTRIC_SMELTERY": 2.0,           # electric Smeltery   (calibration default)
    "ELECTRIC_DUST_WASHER": 2.0,        # electric Ore Washer (calibration default)
    # all auto-crafters: 1 craft per Slimefun tick = 120/min (0.5s/op)
    "AUTO_CRAFTING_TABLE": 0.5, "AUTO_ENHANCED_CRAFTING_TABLE": 0.5,
    "AUTO_MAGIC_WORKBENCH": 0.5, "AUTO_ARMOR_FORGE": 0.5, "AUTO_ANCIENT_ALTAR": 0.5,
    "VANILLA_AUTO_CRAFTER": 0.5, "ENHANCED_AUTO_CRAFTER": 0.5, "ARMOR_AUTO_CRAFTER": 0.5,
    "NTW_AUTO_CRAFTER": 0.5,
}

# nicer display names for synthetic machine ids
DISPLAY_OVERRIDES = {
    "NTW_AUTO_CRAFTER": "Network Auto Crafter",
    "EXCITATION_CHAMBER": "Excitation Chamber",
    "EXCITATION_CHAMBER_2": "Excitation Chamber II",
    "EXCITATION_CHAMBER_3": "Excitation Chamber III",
    "TECH_GENERATOR": "Tech Generator",
}


def _const_return(method, cp):
    """If a method body is essentially `return <int constant>`, return that int."""
    if not method.code:
        return None
    val = None
    n_ops = 0
    for instr in bytecode.iter_instructions(method.code):
        op = instr.opcode
        n_ops += 1
        if op in bytecode.ICONST_VALUES:
            val = bytecode.ICONST_VALUES[op]
        elif op == 0x10:
            val = instr.s8()
        elif op == 0x11:
            val = instr.s16()
        elif op in (0x12, 0x13):
            idx = instr.u8() if op == 0x12 else instr.u16()
            kind, v = cp.ldc_value(idx)
            if kind == "int":
                val = v
    # constant-return methods are short (load const; ireturn)
    return val if n_ops <= 3 else None


def extract_machine_speeds() -> dict[str, int]:
    """{normalized machine id -> speed} from constant getSpeed() overrides."""
    speeds: dict[str, int] = {}
    for jar in PLUGINS.glob("*.jar"):
        with zipfile.ZipFile(jar) as zf:
            for name in zf.namelist():
                if not name.endswith(".class"):
                    continue
                cf = classfile.parse(zf.read(name))
                for m in cf.methods:
                    if m.name == "getSpeed":
                        val = _const_return(m, cf.constant_pool)
                        if val and val > 0:
                            simple = cf.name.split("/")[-1]
                            speeds[normalize(simple)] = val
    return speeds


def normalize(class_simple_name: str) -> str:
    """CamelCase class name -> UPPER_SNAKE, so it can match a RecipeType name.

    e.g. HeatedPressureChamber -> HEATED_PRESSURE_CHAMBER (merges the multiblock
    RecipeType with its AContainer implementation).
    """
    # already an UPPER_SNAKE id (e.g. a synthetic chamber id) -> leave untouched
    if re.fullmatch(r"[A-Z0-9_]+", class_simple_name):
        return class_simple_name
    # camelCase -> snake, keeping runs of capitals/digits together so "GrowthChamberMK2"
    # becomes GROWTH_CHAMBER_MK2 (the real item id) and not GROWTH_CHAMBER_M_K2.
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", class_simple_name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.upper()


_class_items_cache: dict | None = None


def _machine_id(cls: str) -> str:
    """A machine class -> its catalog id: the real item id if known, else normalize(cls).

    Keying by the item id (from electric_machines.class_items) avoids duplicates like
    GrowthChamberEndMK2 (-> GROWTH_CHAMBER_END_MK2) vs the real item GROWTH_CHAMBER_MK2_END,
    and lets the machine's icon resolve.
    """
    global _class_items_cache
    if _class_items_cache is None:
        p = DATA / "machine_items.json"
        _class_items_cache = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _class_items_cache.get(cls) or normalize(cls)


def build(recipes: list[dict]) -> dict:
    """Build the machine catalog from the extracted recipes + speeds."""
    speeds = extract_machine_speeds()
    tiers = {}
    tpath = DATA / "machine_tiers.json"
    if tpath.exists():
        tiers = json.loads(tpath.read_text(encoding="utf-8"))
    # real item names give readable tier labels ("Virtual Garden III" not "... Machine Iii");
    # items.json also gives each machine's addon (plugin) for the ban-list grouping.
    item_names = {}
    item_addon = {}
    ipath = DATA / "items.json"
    if ipath.exists():
        for it in json.loads(ipath.read_text(encoding="utf-8")):
            if it.get("name"):
                item_names[it["id"]] = re.sub(r"[&§].", "", it["name"]).strip()
            if it.get("addon") and it["addon"] not in ("Vanilla", "manual"):
                item_addon[it["id"]] = it["addon"]

    def display_for(mid):
        if mid in DISPLAY_OVERRIDES:
            return DISPLAY_OVERRIDES[mid]
        nm = item_names.get(mid)
        if nm and not nm.rstrip().endswith(".") and len(nm.split()) <= 5:
            return nm                      # the real item name (best for tiers)
        return mid.replace("_", " ").title()

    machines: dict[str, dict] = {}
    recipe_addon: dict[str, str] = {}        # mid -> addon, from the recipe that uses it
    elec_addon: dict[str, str] = {}          # mid -> addon, from the energy-net scan

    def ensure(mid, category):
        if mid not in machines:
            machines[mid] = {
                "id": mid,
                "display": display_for(mid),
                "category": category,
                "speed": speeds.get(mid, 1),
                "seconds_per_op": RECIPE_TYPE_SECONDS.get(
                    mid, RECIPE_TYPE_SECONDS_EXTRA.get(mid, DEFAULT_SECONDS_PER_OP)),
                "banned": False,
            }
        return machines[mid]

    def note_addon(mid, addon):
        if addon and addon not in ("Vanilla", "manual") and mid not in recipe_addon:
            recipe_addon[mid] = addon

    for r in recipes:
        if r["kind"] == "machine":
            cls = r["machine"]
            if cls and cls in tiers:
                # one bannable machine per tier (Electric Press / Electric Press II, ...)
                for item_id, spd in tiers[cls]:
                    m = ensure(item_id, "electric")
                    m["speed"] = spd
                    note_addon(item_id, r.get("addon"))
            elif cls:
                mid = _machine_id(cls)
                ensure(mid, "electric")
                note_addon(mid, r.get("addon"))
        else:
            rt = r["recipe_type"]
            if rt in NON_PRODUCING or rt in MULTIBLOCK_MANUAL:
                continue
            if rt in MULTIBLOCK_ELECTRIC:
                base_id, tiers_class, _ = MULTIBLOCK_ELECTRIC[rt]
                for item_id, spd in tiers.get(tiers_class, [(base_id, 1)]):
                    m = ensure(item_id, "electric")
                    m["speed"] = spd
                    note_addon(item_id, r.get("addon"))
            else:
                for mid in RECIPE_TYPE_MACHINES.get(rt, [rt]):
                    ensure(mid, "automation" if mid in _AUTO_CRAFTERS else "workbench")
                    note_addon(mid, r.get("addon"))
        # fixtures (e.g. a chicken in a chamber, a card in a tech generator) are
        # listed and banned like machines, so the user can exclude ones they
        # haven't unlocked.
        for fx in r.get("fixtures", []):
            m = ensure(fx["id"], fx.get("category", "chicken"))
            if fx.get("name"):
                m["display"] = fx["name"]

    # add EVERY electric machine + generator (incl. ones with no item recipe, e.g. energy
    # generators) so the ban list is complete. The energy-net classification is AUTHORITATIVE:
    # it OVERRIDES any recipe-derived guess (e.g. NUCLEAR_REACTOR shows up as a crafting
    # RecipeType "workbench" but is really a generator).
    epath = DATA / "electric_machines.json"
    if epath.exists():
        for item_id, info in json.loads(epath.read_text(encoding="utf-8")).items():
            cat = info["category"]
            m = ensure(item_id, cat)        # cat is "electric" or "generator"
            elec_addon[item_id] = info.get("addon")
            # don't demote the functional "auto-crafter" grouping: Slimefun's & FluffyMachines'
            # auto-crafters are electric machines too, but we keep them under Auto-crafters.
            if m["category"] != "automation":
                m["category"] = cat

    # resolve each machine's addon (plugin) for the ban-list grouping: prefer the real item's
    # addon, then the energy-net scan, then the recipe that uses it.
    for mid, m in machines.items():
        m["addon"] = (item_addon.get(mid) or elec_addon.get(mid)
                      or recipe_addon.get(mid) or "Other")
    return machines


def machine_id_for(recipe: dict) -> str | None:
    """The machine a recipe runs on (the solver's placement unit)."""
    if recipe["kind"] == "machine":
        return _machine_id(recipe["machine"]) if recipe["machine"] else None
    rt = recipe["recipe_type"]
    if rt in NON_PRODUCING or rt in MULTIBLOCK_MANUAL:
        return None      # manual multiblock -> not automatable here
    if rt in MULTIBLOCK_ELECTRIC:
        return MULTIBLOCK_ELECTRIC[rt][0]    # the base tier (graph offers all tiers)
    return RECIPE_TYPE_MACHINES.get(rt, [rt])[0]


if __name__ == "__main__":
    recipes = json.loads((DATA / "recipes.json").read_text(encoding="utf-8"))
    machines = build(recipes)
    (DATA / "machines.json").write_text(
        json.dumps(list(machines.values()), indent=1), encoding="utf-8")
    print(f"Wrote {DATA/'machines.json'} with {len(machines)} machines")
    for m in sorted(machines.values(), key=lambda x: x["id"])[:40]:
        print(f"  {m['id']:<30} cat={m['category']:<10} speed={m['speed']}")
