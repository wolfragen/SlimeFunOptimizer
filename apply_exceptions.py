"""Project-level data exceptions applied during/after bytecode extraction.

Each entry here works around a bug in an upstream ADDON itself — NOT in our
extractor, which faithfully reproduces whatever the addon's bytecode says. We do
not patch the jars and we do not change the extractor's *parsing*; we only
override the extracted data in this project. See exception.md for the rationale.

These overrides are applied from a single chokepoint so they can't be lost:

  * `extractor/run.py` calls `apply_extracted(all_items, all_recipes)` right before
    it writes items.json/recipes.json. EVERY regeneration path goes through there —
    `python build_data.py` AND `python -m extractor.run` — so a direct extractor
    run can no longer wipe the overrides.

Standalone re-apply to the committed data/ (no jar rebuild needed, also patches
machines.json directly so the catalog isn't reordered):

    python apply_exceptions.py
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

# --- exception #1: Supreme "Card Machine Stone" actually generates COBBLESTONE -
# The Supreme addon registers CARD_STONE ("Card Machine Stone") but wires its
# tech-generator output to cobblestone, not stone (an addon bug). We relabel that
# card as the cobblestone card it really is, and add a corrected stone card that
# produces stone. The buggy card keeps its real in-game id (CARD_STONE); the
# corrected one is a project-only addition.
COBBLE_CARD_ID = "CARD_STONE"            # the buggy addon card (keeps its real id)
STONE_CARD_ID = "CARD_STONE_FIXED"       # project-added corrected card
EXC_SOURCE = "project-exception:apply_exceptions"


def _stone_card_item() -> dict:
    return {
        "id": STONE_CARD_ID,
        "name": "&bCard Machine Stone",
        "amount": 1,
        "source_class": EXC_SOURCE,
        "texture": None,
        "addon": "Supreme",
    }


def _stone_card_crafting_recipe() -> dict:
    # mirror the original card's crafting shape (an Enhanced Crafting Table card)
    return {
        "kind": "crafting",
        "output_id": STONE_CARD_ID,
        "output_amount": 1,
        "recipe_type": "ENHANCED_CRAFTING_TABLE",
        "machine": None,
        "time_seconds": None,
        "ingredients": [
            {"kind": "vanilla", "ref": "STONE", "amount": 8},
            {"kind": "slimefun", "ref": "CENTER_CARD_SIMPLE", "amount": 1},
        ],
        "outputs": [],
        "ctor_class": "",
        "source_class": EXC_SOURCE,
        "fixtures": [],
        "addon": "Supreme",
    }


def _stone_card_techgen_recipe() -> dict:
    # same shape the extractor produces for SimpleCard tech-gen recipes:
    # pure energy -> 64 of the product per cycle (1800s). See extractor/supreme_techgen.
    return {
        "kind": "machine",
        "output_id": "STONE",
        "output_amount": 64,
        "recipe_type": None,
        "machine": "TECH_GENERATOR",
        "time_seconds": 1800,
        "ingredients": [],
        "outputs": [{"kind": "vanilla", "ref": "STONE", "amount": 64}],
        "ctor_class": "TechGenerator",
        "source_class": EXC_SOURCE,
        "fixtures": [{"id": STONE_CARD_ID, "name": "Card Stone",
                      "product": "STONE", "category": "card"}],
        "addon": "Supreme",
    }


def _patch_recipes(recipes: list[dict]) -> None:
    """Relabel the cobblestone-card fixture and append the stone-card recipes
    (idempotent), in place."""
    for r in recipes:
        for fx in r.get("fixtures", []):
            if fx.get("id") == COBBLE_CARD_ID:
                fx["name"] = "Card Cobblestone"
    if not any(r.get("output_id") == STONE_CARD_ID for r in recipes):
        recipes.append(_stone_card_crafting_recipe())
    if not any(r.get("kind") == "machine"
               and any(fx.get("id") == STONE_CARD_ID for fx in r.get("fixtures", []))
               for r in recipes):
        recipes.append(_stone_card_techgen_recipe())


def apply_extracted(all_items: dict[str, dict], all_recipes: list[dict]) -> None:
    """Apply the exceptions to the extractor's in-memory data (items keyed by id,
    recipes as a list), in place. Called from extractor/run.py before writing."""
    cobble = all_items.get(COBBLE_CARD_ID)
    if cobble:
        cobble["name"] = "&bCard Machine Cobblestone"
    if STONE_CARD_ID not in all_items:
        all_items[STONE_CARD_ID] = _stone_card_item()
    _patch_recipes(all_recipes)


def apply(items: list[dict], recipes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Apply the exceptions to list-form data (items as a JSON array), in place.
    Used by the standalone patch_files() path."""
    items_by_id = {it["id"]: it for it in items}
    cobble = items_by_id.get(COBBLE_CARD_ID)
    if cobble:
        cobble["name"] = "&bCard Machine Cobblestone"
    if STONE_CARD_ID not in items_by_id:
        items.append(_stone_card_item())
    _patch_recipes(recipes)
    return items, recipes


def apply_machines(machines: list[dict]) -> list[dict]:
    """Patch the machine catalog (data/machines.json) in place (idempotent).

    The catalog's card entries are normally derived from the recipe fixtures, so a
    full `build_data.py` run picks these up automatically. This direct patch keeps
    the standalone re-apply from regenerating (and reordering) the whole catalog.
    """
    by_id = {m["id"]: m for m in machines}
    cobble = by_id.get(COBBLE_CARD_ID)
    if cobble:
        cobble["display"] = "Card Cobblestone"
    if STONE_CARD_ID not in by_id and cobble:
        entry = dict(cobble)
        entry["id"] = STONE_CARD_ID
        entry["display"] = "Card Stone"
        machines.insert(machines.index(cobble) + 1, entry)
    return machines


def patch_files() -> None:
    """Apply the exceptions to data/items.json + data/recipes.json on disk."""
    items = json.loads((DATA / "items.json").read_text(encoding="utf-8"))
    recipes = json.loads((DATA / "recipes.json").read_text(encoding="utf-8"))
    apply(items, recipes)
    (DATA / "items.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    (DATA / "recipes.json").write_text(json.dumps(recipes, indent=1), encoding="utf-8")


def main():
    patch_files()
    # standalone: machines.json isn't rebuilt from jars here, so patch it directly
    # (minimal change, no reordering of the committed catalog).
    machines = json.loads((DATA / "machines.json").read_text(encoding="utf-8"))
    apply_machines(machines)
    (DATA / "machines.json").write_text(
        json.dumps(machines, indent=1), encoding="utf-8")
    print(f"exceptions applied: relabeled {COBBLE_CARD_ID} -> cobblestone card, "
          f"added {STONE_CARD_ID} (real stone card)")


if __name__ == "__main__":
    main()
