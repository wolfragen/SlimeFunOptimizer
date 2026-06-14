"""Load the extracted data into a recipe graph the solver can reason about.

Normalizes recipes to a uniform shape, attaches each to its placement machine,
computes per-machine throughput (items/min), de-duplicates equivalent recipes
(e.g. a multiblock RecipeType and its AContainer twin), and identifies leaf items
(no producing recipe -> raw/gathered inputs).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from extractor import machines as machines_mod

DATA = Path(__file__).resolve().parent.parent / "data"


def _amt(v):
    """Stack count; keeps positive fractionals (weighted yields), else defaults to 1."""
    return v if isinstance(v, (int, float)) and v > 0 else 1


def _ops_per_min(seconds, speed):
    """Operations/min for a Slimefun AContainer machine, matching its exact integer-tick model.

    A recipe registered with `seconds` becomes a MachineRecipe of `seconds*2` ticks; placing it
    in a machine of processing `speed` divides that (INTEGER division) -> `ticks//speed`, floored
    at 1 (a machine needs at least one Slimefun tick). Each Slimefun tick = 0.5s, so a machine
    does at most 1 op / 0.5s = 120 ops/min regardless of how high its speed is. Verified against
    the in-game Electric Ore Grinder T3 (speed 10, 4s recipe): (4*2)//10 -> 0 -> 1 tick = 0.5s/op.
    """
    base_ticks = max(1, round((seconds or 1) * 2))
    eff_ticks = max(1, base_ticks // max(1, int(round(speed or 1))))
    return 120.0 / eff_ticks


@dataclass
class Ing:
    ref: str
    kind: str
    amount: int


@dataclass
class Recipe:
    rid: int
    output_id: str                   # primary output (for display)
    output_amount: int
    machine_id: str
    ingredients: list[Ing]
    outputs: list[Ing]               # ALL outputs (machines can yield several items)
    ops_per_min_per_machine: float   # how many crafts one machine does per minute
    addon: str
    energy_only: bool = False        # produced from energy alone (no item input)
    fixtures: list = field(default_factory=list)  # held machine-like items (chickens)

    def net(self) -> dict[str, int]:
        """outputs - inputs per item; >0 net producer, <0 net consumer of that item."""
        n: dict[str, int] = {}
        for o in self.outputs:
            n[o.ref] = n.get(o.ref, 0) + o.amount
        for i in self.ingredients:
            n[i.ref] = n.get(i.ref, 0) - i.amount
        return n


class Graph:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.machines: dict[str, dict] = {}
        self.machine_tiers: dict[str, list] = {}
        self.recipes: list[Recipe] = []
        self.by_output: dict[str, list[Recipe]] = {}

    # -- loading ----------------------------------------------------------
    @classmethod
    def load(cls) -> "Graph":
        g = cls()
        items = json.loads((DATA / "items.json").read_text(encoding="utf-8"))
        recipes = json.loads((DATA / "recipes.json").read_text(encoding="utf-8"))
        machines = json.loads((DATA / "machines.json").read_text(encoding="utf-8"))
        g.items = {it["id"]: it for it in items}
        g.machines = {m["id"]: m for m in machines}
        tpath = DATA / "machine_tiers.json"
        g.machine_tiers = (json.loads(tpath.read_text(encoding="utf-8"))
                           if tpath.exists() else {})
        g._build_recipes(recipes)
        return g

    def _machine_options(self, recipe: dict):
        """[(machine_id, ops_per_min)] — one entry per machine that can run this recipe.

        A tiered machine class (Electric Press I/II, Carbon Press I/II/III...) yields one
        option per tier at its own speed, so the solver can pick the fastest (fewest
        machines) and the user can ban tiers they lack.
        """
        if recipe["kind"] == "machine":
            t = recipe.get("time_seconds") or 1
            cls = recipe.get("machine")
            tiers = self.machine_tiers.get(cls)
            if tiers:
                return [(item_id, _ops_per_min(t, spd)) for item_id, spd in tiers]
            mid = machines_mod.machine_id_for(recipe)
            machine = self.machines.get(mid)
            speed = machine["speed"] if machine else 1
            return [(mid, _ops_per_min(t, speed))]
        # crafting recipe.
        rt = recipe["recipe_type"]
        # Multiblocks with a dedicated TIERED electric machine (Grind Stone -> Electric Ore
        # Grinder, Smeltery -> Electric Smeltery, ...): offer each tier at its real throughput.
        me = machines_mod.MULTIBLOCK_ELECTRIC.get(rt)
        if me:
            base_id, tiers_class, seconds = me
            tiers = self.machine_tiers.get(tiers_class) or [(base_id, 1)]
            return [(item_id, _ops_per_min(seconds, spd)) for item_id, spd in tiers]
        # a grid/ritual recipe type can run on several auto machines (Slimefun's Auto-Crafters
        # + FluffyMachines' Auto-* machines) — offer each as an option.
        mids = machines_mod.RECIPE_TYPE_MACHINES.get(rt, [machines_mod.machine_id_for(recipe)])
        opts = []
        for mid in mids:
            machine = self.machines.get(mid)
            spo = machine["seconds_per_op"] if machine else machines_mod.DEFAULT_SECONDS_PER_OP
            opts.append((mid, 60.0 / spo))
        return opts

    def _build_recipes(self, raw: list[dict]) -> None:
        seen = set()
        rid = 0
        for r in raw:
            if not r.get("output_id"):
                continue
            if machines_mod.machine_id_for(r) is None:
                continue
            ings = [Ing(i["ref"], i["kind"], _amt(i.get("amount", 1)))
                    for i in r["ingredients"]
                    if i["kind"] in ("slimefun", "vanilla") and i["ref"]]
            # full output list (machines can yield several items, e.g. a growth
            # chamber); crafting recipes have a single output_id x output_amount.
            # Amounts may be FRACTIONAL for weighted/expected-yield producers (a quarry
            # or aquarium that yields a random item per cycle — see solver model).
            outs = [Ing(o["ref"], o.get("kind", "slimefun"), _amt(o.get("amount", 1)))
                    for o in r.get("outputs", [])
                    if o.get("ref")]
            if not outs:
                outs = [Ing(r["output_id"], "slimefun", _amt(r.get("output_amount", 1)))]
            # empty ingredients are only valid for energy-only machine recipes
            # (generators/harvesters); empty crafting recipes are parse failures.
            energy_only = not ings
            if energy_only and r["kind"] != "machine":
                continue
            fxkey = tuple(sorted(f["id"] for f in r.get("fixtures", [])))
            # one recipe per machine option (tiers); the solver chooses the cheapest
            for mid, opm in self._machine_options(r):
                # de-dup equivalent recipes (same output, machine, ingredient multiset,
                # and fixtures — pools differ only by their determining item, e.g. an
                # aquarium's FISHING_ROD vs TRIDENT loot pool share a primary output)
                key = (r["output_id"], mid,
                       tuple(sorted((i.ref, i.amount) for i in ings)), fxkey)
                if key in seen:
                    continue
                seen.add(key)
                rec = Recipe(
                    rid=rid,
                    output_id=r["output_id"],
                    output_amount=max(1, r.get("output_amount", 1)),
                    machine_id=mid,
                    ingredients=ings,
                    outputs=outs,
                    ops_per_min_per_machine=opm,
                    addon=r.get("addon", ""),
                    energy_only=energy_only,
                    fixtures=r.get("fixtures", []),
                )
                self.recipes.append(rec)
                for o in outs:                   # index by EVERY output item
                    self.by_output.setdefault(o.ref, []).append(rec)
                rid += 1

    # -- queries ----------------------------------------------------------
    def display_name(self, item_id: str) -> str:
        it = self.items.get(item_id)
        if it and it.get("name"):
            return _strip_color(it["name"])
        return item_id.replace("_", " ").title()

    def is_vanilla(self, item_id: str) -> bool:
        it = self.items.get(item_id)
        return bool(it and it.get("vanilla"))

    def is_leaf(self, item_id: str, banned: set[str]) -> bool:
        # an item is a leaf only if it has no (non-banned) recipe at all; the solver
        # additionally treats cycle-stuck items as raw via a producibility fixpoint.
        return not any(r.machine_id not in banned for r in self.by_output.get(item_id, []))

    def search(self, query: str, limit: int = 30) -> list[dict]:
        q = query.lower().strip().replace("_", " ")
        out = []
        for iid, it in self.items.items():
            name = _strip_color(it.get("name") or "")
            hay = (name + " " + iid).lower().replace("_", " ")
            if q in hay:
                producible = bool(self.by_output.get(iid))
                out.append({
                    "id": iid,
                    "name": name or iid.replace("_", " ").title(),
                    "addon": it.get("addon", ""),
                    "producible": producible,
                    "rank": 0 if name.lower().startswith(q) else 1,
                })
        out.sort(key=lambda x: (x["rank"], len(x["name"])))
        return out[:limit]


def _strip_color(s: str) -> str:
    import re
    return re.sub(r"[&§][0-9a-fk-orA-FK-OR]", "", s).strip()
