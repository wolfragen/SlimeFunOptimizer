"""Drive the extractor over every jar in plugins/ and emit JSON.

Usage:
    python -m extractor.run                 # all jars, write data/*.json
    python -m extractor.run --jar Fluffy    # only jars whose name contains 'Fluffy'
    python -m extractor.run --report        # print coverage report, no write
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from . import (classfile, model, supreme_mobtech, supreme_cards, supreme_gear,
               supreme_techgen, supreme_virtual, supreme_quarries, infinity_machines,
               mobsim, passive_generators, quarries, multiblock_recipes, vanilla_recipes,
               gce, gce_chickens, machine_tiers, electric_machines, networks_items,
               extra_machines)

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
DATA = ROOT / "data"


def addon_label(jar_name: str) -> str:
    """Short stable addon key from a jar filename."""
    base = jar_name
    for sep in (" - ", ".jar"):
        base = base.split(sep)[0]
    return base.strip()


def iter_class_entries(jar_path: Path):
    # Include inner classes ($) too — some addons build recipes in them.
    with zipfile.ZipFile(jar_path) as zf:
        for name in zf.namelist():
            if name.endswith(".class"):
                yield name, zf.read(name)


def process_jar(jar_path: Path):
    recipes = []
    item_defs = []
    aliases = {}
    errors = 0
    classes = 0
    for name, data in iter_class_entries(jar_path):
        classes += 1
        try:
            cf = classfile.parse(data)
        except Exception:  # noqa: BLE001 - keep going, report at end
            errors += 1
            continue
        try:
            ex = model.extract(cf)
        except Exception:
            errors += 1
            continue
        recipes.extend(ex.recipes)
        item_defs.extend(ex.item_defs)
        aliases.update(ex.aliases)
    # multiblock processing recipes (input->output pairs incl. vanilla outputs) — all jars
    with zipfile.ZipFile(jar_path) as zf:
        recipes.extend(multiblock_recipes.extract(zf))
    # custom-generation machines the generic pass misses (Seed Plucker, Stoneworks Factory,
    # Resource Synthesizer, Produce Collector, Oil Pump) — dispatches by addon internally
    with zipfile.ZipFile(jar_path) as zf:
        recipes.extend(extra_machines.extract(zf, jar_path.name))
    # addon-specific recognizers for custom frameworks the generic pass can't see
    if "supreme" in jar_path.name.lower():
        with zipfile.ZipFile(jar_path) as zf:
            mt_items, mt_recipes = supreme_mobtech.extract(zf)
            card_recipes = supreme_cards.extract(zf)
            core_recipes = supreme_cards.extract_cores(zf)
            gear_recipes = supreme_gear.extract(zf)
            _, techgen_recipes = supreme_techgen.extract(zf)
            _, garden_recipes = supreme_virtual.extract(zf)
            _, quarry_recipes = supreme_quarries.extract(zf)
        item_defs.extend(mt_items)
        recipes.extend(mt_recipes)
        recipes.extend(card_recipes)
        recipes.extend(core_recipes)
        recipes.extend(gear_recipes)
        recipes.extend(techgen_recipes)
        recipes.extend(garden_recipes)
        recipes.extend(quarry_recipes)
    if "infinityexpansion" in jar_path.name.lower().replace(" ", ""):
        with zipfile.ZipFile(jar_path) as zf:
            recipes.extend(infinity_machines.extract(zf))
            _, gen_recipes = passive_generators.extract(zf)
            recipes.extend(gen_recipes)
            _, quarry_recipes = quarries.extract(zf)
            recipes.extend(quarry_recipes)
            # Mob Simulation Chamber: data card -> weighted mob drops (energy-only)
            mob_items, mob_recipes = mobsim.extract(zf)
            item_defs.extend(mob_items)
            recipes.extend(mob_recipes)
            owners = infinity_machines.machineblock_owners(zf)
        # The generic-"Machines" recipes are really several MachineBlock machines. Re-attribute
        # each to its real owner (Decompressor, Cobble Press, Ingot Former, Uranium Extractor,
        # Extreme Freezer, ...). DROP the Dust Extractor ones: its in-game recipe is just
        # cobblestone -> a random dust, so the extracted cobble<->andesite<->stone<->diorite<->
        # granite "cycle" (its internal progression) and the mis-parsed cobble->circuit/void are
        # bogus. Unmapped ones are dropped too (parse failures, not real recipes).
        DUST = {"DUST_EXTRACTOR", "INFINITY_DUST_EXTRACTOR"}
        fixed = []
        for r in recipes:
            if r.kind == "machine" and r.machine == "Machines":
                inp = r.ingredients[0].ref if r.ingredients else None
                amt = r.ingredients[0].amount if r.ingredients else 1
                own = owners.get((r.output_id, inp, amt))
                if own is None or own in DUST:
                    continue
                r.machine = own
            fixed.append(r)
        recipes = fixed
    if "networks" in jar_path.name.lower():
        # Networks has its own power system + a themed item builder, so the generic pass and
        # the energy-net enumerator both miss it. Recover its items (incl. the Auto Crafter).
        with zipfile.ZipFile(jar_path) as zf:
            item_defs.extend(networks_items.extract(zf))
    if "chickengineering" in jar_path.name.lower():
        with zipfile.ZipFile(jar_path) as zf:
            g_items, g_recipes = gce.extract(zf)
            c_items, c_recipes = gce_chickens.extract(zf)
        item_defs.extend(g_items)
        item_defs.extend(c_items)
        recipes.extend(g_recipes)
        recipes.extend(c_recipes)
    return recipes, item_defs, aliases, classes, errors


def load_manual_recipes() -> list[dict]:
    """Curated recipes from data/recipes_manual.json (the long-tail supplement).

    Entry: {output_id, recipe_type|machine, output_amount?, time_seconds?,
            ingredients:[{ref, kind, amount}]}. Normalized to the recipe schema.
    """
    path = DATA / "recipes_manual.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in raw:
        if not r.get("output_id") or not r.get("ingredients"):
            continue
        is_machine = bool(r.get("machine"))
        out.append({
            "kind": "machine" if is_machine else "crafting",
            "output_id": r["output_id"],
            "output_amount": r.get("output_amount", 1),
            "recipe_type": r.get("recipe_type"),
            "machine": r.get("machine"),
            "time_seconds": r.get("time_seconds"),
            "ingredients": [{"ref": i["ref"], "kind": i.get("kind", "slimefun"),
                             "amount": i.get("amount", 1)} for i in r["ingredients"]],
            "outputs": [],
            "ctor_class": "", "source_class": "manual", "addon": r.get("addon", "manual"),
        })
    return out


def resolve_alias(name, aliases, _depth=0):
    """Follow an alias chain to its canonical field name."""
    seen = set()
    while name in aliases and name not in seen and _depth < 20:
        seen.add(name)
        name = aliases[name]
        _depth += 1
    return name


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jar", default=None, help="substring filter on jar filename")
    ap.add_argument("--report", action="store_true", help="print report, do not write")
    args = ap.parse_args(argv)

    jars = sorted(PLUGINS.glob("*.jar"))
    if args.jar:
        jars = [j for j in jars if args.jar.lower() in j.name.lower()]
    if not jars:
        print("No jars found.", file=sys.stderr)
        return 1

    all_recipes = []
    all_items = {}
    all_aliases = {}
    print(f"{'addon':<22} {'classes':>8} {'items':>7} {'recipes':>8} {'alias':>6} {'err':>5}")
    print("-" * 60)
    for jar in jars:
        addon = addon_label(jar.name)
        recipes, item_defs, aliases, classes, errors = process_jar(jar)
        all_aliases.update(aliases)
        for it in item_defs:
            # first definition with a real name wins; never let a bare key clobber it
            existing = all_items.get(it.id)
            if existing is None or (not existing.get("name") and it.name):
                d = asdict(it)
                d["addon"] = addon
                all_items[it.id] = d
        for r in recipes:
            d = asdict(r)
            d["addon"] = addon
            all_recipes.append(d)
        print(f"{addon:<22} {classes:>8} {len(item_defs):>7} {len(recipes):>8} "
              f"{len(aliases):>6} {errors:>5}")

    # vanilla Minecraft crafting recipes (from the bundled 1.20.6 server jar)
    vanilla = vanilla_recipes.extract()
    for r in vanilla:
        d = asdict(r)
        d["addon"] = "Vanilla"
        all_recipes.append(d)
    print(f"{'Vanilla (1.20.6 crafts)':<22} {'':>8} {'':>7} {len(vanilla):>8}")

    # resolve alias references in recipes so the graph keys are canonical
    for r in all_recipes:
        if r["output_id"]:
            r["output_id"] = resolve_alias(r["output_id"], all_aliases)
        for ing in r["ingredients"] + r["outputs"]:
            if ing["kind"] == "slimefun" and ing["ref"]:
                ing["ref"] = resolve_alias(ing["ref"], all_aliases)

    # Route Slimefun Smeltery recipes the way PostSetup.addSmelteryRecipe does: a recipe with
    # exactly one input that is a *_DUST item is a dust->ingot smelt, which the game registers
    # on the ELECTRIC_INGOT_FACTORY (and Makeshift Smeltery / Ingot Pulverizer) — NOT on any
    # Electric Smeltery, which only does alloys (multi-input). Reassigning the recipe_type makes
    # the solver offer the Ingot Factory tiers for these instead of the smelteries.
    rerouted = 0
    for r in all_recipes:
        if (r.get("kind") == "crafting" and r.get("recipe_type") == "SMELTERY"
                and r.get("addon") == "Slimefun4"):
            ings = [i for i in r["ingredients"]
                    if i.get("kind") == "slimefun" and i.get("ref")]
            if len(ings) == 1 and ings[0]["ref"].endswith("_DUST"):
                r["recipe_type"] = "ELECTRIC_INGOT_FACTORY"
                rerouted += 1
    if rerouted:
        print(f"routed {rerouted} dust->ingot Smeltery recipe(s) to the Ingot Factory")

    # add vanilla materials to the catalog (searchable). Flagged vanilla so the solver
    # only ever produces them via VANILLA_CRAFTING recipes (else treats them as raw) —
    # this keeps base materials (ores/ingots/logs) as raw inputs while letting crafted
    # vanilla items (planks, sticks, tools, blocks) chain down for SF recipes.
    vanilla_names = set()
    for r in all_recipes:
        if r.get("recipe_type") == "VANILLA_CRAFTING" and r["output_id"]:
            vanilla_names.add(r["output_id"])
        for ing in r["ingredients"] + r.get("outputs", []):
            if ing["kind"] == "vanilla" and ing["ref"]:
                vanilla_names.add(ing["ref"])
    for oid in vanilla_names:
        if oid not in all_items:
            all_items[oid] = {"id": oid, "name": oid.replace("_", " ").title(),
                              "amount": 1, "addon": "Vanilla", "vanilla": True}

    # merge curated manual recipes (the supplement for items the extractor can't
    # reach reliably — singularities, runes, etc.). Same schema; output_id + ingredients required.
    manual = load_manual_recipes()
    if manual:
        all_recipes.extend(manual)
        print(f"merged {len(manual)} manual recipe(s) from data/recipes_manual.json")

    # Drop CROSS-CONTAMINATED machine recipes. The Electric Press (an electric Compressor)
    # registers recipes for items whose CANONICAL recipe (declared in *ItemSetup) is actually
    # the Grind Stone or Enhanced Crafting Table — the magical/ender lumps, copper wire,
    # uranium. Those belong to the Electric Ore Grinder / (auto) crafting table, not the press.
    # NOTE: this targets only the Press; the Tech Generator / Excitation Chamber producing
    # such items is INTENTIONAL (card / chicken alternate paths) and is left alone.
    canon_types = defaultdict(set)
    for r in all_recipes:
        if r["kind"] == "crafting" and "ItemSetup" in (r.get("source_class") or ""):
            canon_types[r["output_id"]].add(r.get("recipe_type"))
    _PRESS_WRONG = {"GRIND_STONE", "ENHANCED_CRAFTING_TABLE"}
    _before = len(all_recipes)
    all_recipes = [r for r in all_recipes if not (
        r["kind"] == "machine" and r.get("machine") == "ElectricPress"
        and (canon_types.get(r["output_id"], set()) & _PRESS_WRONG))]
    if _before != len(all_recipes):
        print(f"dropped {_before - len(all_recipes)} cross-contaminated Electric Press recipe(s)")

    # drop recipes we couldn't pin an output to (unusable downstream)
    usable = [r for r in all_recipes if r["output_id"]]

    # recipe coverage diagnostics
    by_kind = defaultdict(int)
    by_type = defaultdict(int)
    by_machine = defaultdict(int)
    unknown_ing = 0
    for r in usable:
        by_kind[r["kind"]] += 1
        if r["kind"] == "crafting":
            by_type[r["recipe_type"]] += 1
        else:
            by_machine[r["machine"]] += 1
        for ing in r["ingredients"]:
            if ing["kind"] == "unknown":
                unknown_ing += 1
    print("-" * 56)
    print(f"TOTAL items={len(all_items)}  usable_recipes={len(usable)}/"
          f"{len(all_recipes)}  unknown_ingredients={unknown_ing}")
    print(f"by kind: {dict(by_kind)}")
    print("\ncrafting recipes by RecipeType (top 20):")
    for t, c in sorted(by_type.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {str(t):<32} {c}")
    print("\nmachine recipes by machine class (top 20):")
    for t, c in sorted(by_machine.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {str(t):<32} {c}")
    all_recipes = usable

    if not args.report:
        DATA.mkdir(exist_ok=True)
        (DATA / "items.json").write_text(
            json.dumps(list(all_items.values()), indent=1), encoding="utf-8")
        (DATA / "recipes.json").write_text(
            json.dumps(all_recipes, indent=1), encoding="utf-8")
        (DATA / "aliases.json").write_text(
            json.dumps(all_aliases, indent=1), encoding="utf-8")
        # machine tiers: {class -> [(item_id, speed)]} so the solver offers each tier
        (DATA / "machine_tiers.json").write_text(
            json.dumps(machine_tiers.extract(), indent=1), encoding="utf-8")
        # every electric machine + generator (incl. ones with no item recipe) -> ban list
        (DATA / "electric_machines.json").write_text(
            json.dumps(electric_machines.extract(), indent=1), encoding="utf-8")
        # {machine class -> real item id} so a machine recipe is keyed by its item id (no
        # GROWTH_CHAMBER_MK2_END vs GrowthChamberEndMK2 duplicate; icons resolve).
        (DATA / "machine_items.json").write_text(
            json.dumps(electric_machines.class_items(), indent=1), encoding="utf-8")

        # report: named/referenced items that have no recipe (candidate supplement
        # entries). Excludes vanilla refs (always raw).
        produced = {r["output_id"] for r in all_recipes}
        referenced = set()
        for r in all_recipes:
            for ing in r["ingredients"]:
                if ing["kind"] == "slimefun" and ing["ref"]:
                    referenced.add(ing["ref"])
        missing = sorted(
            iid for iid in (set(all_items) | referenced)
            if iid not in produced and iid in all_items)
        miss_rows = [{"id": iid, "name": all_items[iid].get("name"),
                      "addon": all_items[iid].get("addon")} for iid in missing]
        (DATA / "missing_recipes.json").write_text(
            json.dumps(miss_rows, indent=1), encoding="utf-8")
        print(f"\nWrote items.json ({len(all_items)}), recipes.json "
              f"({len(all_recipes)}), aliases.json ({len(all_aliases)}), "
              f"missing_recipes.json ({len(miss_rows)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
