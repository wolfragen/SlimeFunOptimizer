"""Recipe-completeness auditor: reconcile in-bytecode registrations vs extracted recipes.

A zero-recipe scan can't catch a machine that has SOME recipes but is missing others. This
does: every recipe a machine registers is a call site in the jar
(`registerRecipe`/`addRecipe`/`addProduce`/`addRecipesToProcess`), so we count those per
class and compare to the recipes we extracted FROM that class (keyed by `source_class`).

  registered == extracted   -> proof we captured them all
  registered  > extracted   -> a missed recipe (or intentionally-dropped one -> annotate)
  registered  < extracted   -> a loop registered N from one site (fine), or a different lane

Lanes this does NOT cover (verified separately):
  * RecipeType / grid items (auto-crafters, workbenches): completeness = item-parse coverage,
    tracked in data/missing_recipes.json.
  * custom 1:N mechanics (tech-gen preSetup, mob-sim addDrop, chickens, gardens): their own
    extractors; counted here only as FYI.

Run:  python -m extractor.recipe_coverage          (full report)
Used by build_data.py to print a coverage summary every rebuild.
"""

from __future__ import annotations

import json
import zipfile
from collections import defaultdict
from pathlib import Path

from . import bytecode, classfile

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
DATA = ROOT / "data"

# recipe-registration methods whose call-count ~= number of recipes registered (1 site = 1
# recipe). Require an ItemStack in the descriptor so we don't catch unrelated same-named methods.
REG_METHODS = {"registerRecipe", "addRecipe", "addProduce", "addRecipesToProcess"}
# 1:N custom mechanics — counted for context, not reconciled 1:1
CUSTOM_METHODS = {"preSetup", "addDrop"}

# Base/framework classes that DEFINE or generically forward these methods — they aren't
# machines, so a call site in them is noise (the recipe is attributed to the real machine).
IGNORE_CLASSES = {
    "AContainer", "AbstractMachineBlock", "AbstractElectricMachine", "AbstractMachine",
    "MachineBlock", "CraftingBlock", "RecipeType", "SupportedRecipes", "PostSetup",
    "SlimefunItem", "TickingMenuBlock", "AbstractContainer",
}

# Real machines/extractors whose recipes ARE captured but under a DIFFERENT source_class (so the
# class's own call count looks unmatched). Annotated so the report stays clean and auditable.
KNOWN_OK = {
    "Machines": "re-attributed to real owners; Dust Extractor cycle recipes dropped on purpose",
    "ElectricPress": "cross-contaminated recipes dropped (canonical type is GrindStone/Crafting)",
    "ElectricFurnace": "registerRecipe is a loop over vanilla smelting -> 70 recipes via vanilla_recipes",
    "TechGenerator": "preSetup/addRecipesToProcess -> card recipes via supreme_techgen (SetupSimpleCard)",
    "MobTech": "buildRobotic addRecipe -> robotic-mob items captured as ENHANCED_CRAFTING_TABLE crafts",
    "MineralizedApiary": "per-ore variant ids generated at RUNTIME; registered as one type (known gap)",
}

# Custom-GENERATION producers (output via tick/getDisplayRecipes, not registerRecipe) that are
# intentionally not modeled as item recipes, with the reason — so lane B stays auditable.
KNOWN_OK_CUSTOM = {
    "DUST_EXTRACTOR": "outputs one RANDOM dust from cobblestone (not a deterministic recipe)",
    "INFINITY_DUST_EXTRACTOR": "random-dust output (see DUST_EXTRACTOR)",
    "GEO_MINER": "mines vanilla ores (raw inputs)", "ADVANCED_GEO_MINER": "vanilla ores (raw)",
    "GEO_QUARRY": "mines vanilla ores/stone (raw)",
    "ELECTRIC_DUST_FABRICATOR": "outputs a RANDOM dust (like the Dust Extractor)",
    "ORECHID": "converts stone -> a RANDOM vanilla ore (non-deterministic, ores are raw)",
    "BANDAID_MANAGER": "repairs/manages item bands -> not an item producer",
    "GEAR_TRANSFORMER": "transforms tools/armor in place -> not an item producer",
    "WEATHER_CONTROLLER": "consumes items to change weather -> not an item producer",
    "MATERIAL_HIVE": "takes 64x an ingot and meters 1 back out (storage/hive) -> net consumer",
    "SMART_FACTORY": "generic auto-crafter -> offered for grid recipes via RECIPE_TYPE_MACHINES",
}


def _scan():
    """Single pass over all jars.

    Returns (sites, display_classes):
      sites[class_simple][method] = count of recipe-registration call sites
      display_classes = {class_simple} that define getDisplayRecipes (custom-generation signal)
    """
    sites: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    display: set[str] = set()
    for jar in PLUGINS.glob("*.jar"):
        with zipfile.ZipFile(jar) as zf:
            for name in zf.namelist():
                if not name.endswith(".class"):
                    continue
                try:
                    cf = classfile.parse(zf.read(name))
                except Exception:  # noqa: BLE001
                    continue
                cp = cf.constant_pool
                simple = cf.name.split("/")[-1]
                if any(m.name == "getDisplayRecipes" for m in cf.methods):
                    display.add(simple)
                if simple in IGNORE_CLASSES:
                    continue
                for m in cf.methods:
                    if not m.code:
                        continue
                    for x in bytecode.iter_instructions(m.code):
                        if x.opcode not in (0xb6, 0xb7, 0xb8, 0xb9):
                            continue
                        try:
                            _, nm, desc = cp.method_ref(x.u16())
                        except Exception:  # noqa: BLE001
                            continue
                        if nm in REG_METHODS and "ItemStack" in desc:
                            sites[simple][nm] += 1
                        elif nm in CUSTOM_METHODS:
                            sites[simple][nm] += 1
    return sites, display


def _extracted_by_source():
    """{class_simple: count} of extracted MACHINE recipes, keyed by their source_class."""
    recipes = json.loads((DATA / "recipes.json").read_text(encoding="utf-8"))
    by: dict[str, int] = defaultdict(int)
    for r in recipes:
        if r.get("kind") != "machine":
            continue
        src = (r.get("source_class") or "").split("/")[-1].replace(".class", "")
        if src:
            by[src] += 1
    return by


def _extracted_machine_keys():
    """Every key a machine recipe is filed under: its `machine` field + source_class simple."""
    recipes = json.loads((DATA / "recipes.json").read_text(encoding="utf-8"))
    keys = set()
    for r in recipes:
        if r.get("kind") != "machine":
            continue
        if r.get("machine"):
            keys.add(r["machine"])
        src = (r.get("source_class") or "").split("/")[-1].replace(".class", "")
        if src:
            keys.add(src)
    return keys


def custom_producer_gaps(display: set[str]) -> list[dict]:
    """Lane B: energy CONSUMER machines that produce via custom tick logic (getDisplayRecipes)
    but have ZERO extracted recipes — the producers the registration lane can't see."""
    from . import electric_machines as em
    energy = em.extract()                     # item -> {category, addon}
    item_to_class = {v: k for k, v in em.class_items().items()}
    have = _extracted_machine_keys()
    out = []
    for item, info in sorted(energy.items()):
        if info.get("category") != "electric":
            continue                          # generators produce energy, not items
        cls = item_to_class.get(item)
        if not cls or cls not in display:
            continue                          # not a custom-generation producer
        if item in have or cls in have or item in KNOWN_OK_CUSTOM:
            continue
        out.append({"item": item, "class": cls, "addon": info.get("addon", "?")})
    return out


def report() -> dict:
    sites, display = _scan()
    extracted = _extracted_by_source()
    rows = []
    for cls, meths in sites.items():
        reg = sum(meths[m] for m in REG_METHODS if m in meths)
        if reg == 0:
            continue                          # only custom methods here -> separate lane
        got = extracted.get(cls, 0)
        rows.append({
            "class": cls,
            "registered": reg,
            "extracted": got,
            "methods": dict(meths),
            "shortfall": reg - got,
            "known_ok": KNOWN_OK.get(cls),
        })
    rows.sort(key=lambda r: -r["shortfall"])
    gaps = [r for r in rows if r["shortfall"] > 0 and not r["known_ok"]]
    return {"rows": rows, "gaps": gaps,
            "exact": [r for r in rows if r["shortfall"] == 0],
            "custom_gaps": custom_producer_gaps(display)}


def print_report(full: bool = True):
    rep = report()
    gaps, cgaps = rep["gaps"], rep["custom_gaps"]
    print(f"Recipe coverage (lane A, registrations): {len(rep['exact'])} classes reconcile "
          f"exactly; {len(gaps)} unexplained shortfall(s).")
    if gaps:
        print(f"\n  {'class':<30}{'reg':>5}{'got':>5}  methods")
        for r in gaps:
            print(f"  {r['class']:<30}{r['registered']:>5}{r['extracted']:>5}  {r['methods']}")
    print(f"Recipe coverage (lane B, custom producers): {len(cgaps)} energy machine(s) that "
          f"produce via tick logic but have 0 recipes.")
    if cgaps:
        from collections import defaultdict as _dd
        by = _dd(list)
        for r in cgaps:
            by[r["addon"]].append(r["item"])
        for a in sorted(by):
            print(f"  {a}: {sorted(by[a])}")
    if full:
        annotated = [r for r in rep["rows"] if r["known_ok"] and r["shortfall"] > 0]
        if annotated:
            print("\nlane A known/expected shortfalls (annotated):")
            for r in annotated:
                print(f"  {r['class']:<28} reg={r['registered']} got={r['extracted']} "
                      f"-- {r['known_ok']}")
    return rep


if __name__ == "__main__":
    print_report(full=True)
