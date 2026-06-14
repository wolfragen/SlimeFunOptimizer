"""MILP: meet a target production rate with the fewest total machines.

Recipe choice and integer machine counts are solved jointly:

  minimize   sum_r machines[r]
  s.t.       production_i >= consumption_i + demand_i   (for every produced item i)
             machines[r]  >= ops[r] / ops_per_min_per_machine[r]   (integer)

Recipes can have MULTIPLE outputs (e.g. a growth chamber yields saplings + logs +
apples + sticks), so production/consumption is summed over all outputs/inputs.

What counts as a free "raw" input is decided here, not pre-marked:
  * An item is energy-producible (set E) if it can be made starting from free
    producers — recipes whose net (outputs - inputs) has no negative item, i.e. they
    sustain themselves from energy (growth chambers, harvesters, generators) — and
    propagating through recipes whose net-negative inputs are all in E.
  * A VANILLA item with no energy path is a raw input the user supplies (e.g. grass,
    bamboo, ores). Energy-producible vanilla items (logs, planks, sticks...) are built
    via energy paths only — so sticks come from a chamber -> planks, never from raw
    bamboo. This is recomputed per query (banning machines changes it).
"""

from __future__ import annotations

from collections import defaultdict

import pulp

from .graph import Graph

MAX_ITEMS = 8000


def _reachable(graph: Graph, target: str, banned: set[str]):
    """Recipes usable to make `target` (following every ingredient), and the items."""
    usable = []
    seen = set()
    items = set()
    stack = [target]
    visited = set()
    while stack:
        item = stack.pop()
        if item in visited:
            continue
        visited.add(item)
        items.add(item)
        if len(items) > MAX_ITEMS:
            break
        for r in graph.by_output.get(item, []):
            if r.machine_id in banned or any(f["id"] in banned for f in r.fixtures):
                continue
            if r.rid not in seen:
                seen.add(r.rid)
                usable.append(r)
            for ing in r.ingredients:
                items.add(ing.ref)
                stack.append(ing.ref)
            for o in r.outputs:
                items.add(o.ref)
    return usable, items


TECH_GENERATOR = "TECH_GENERATOR"
TG_BASE_CYCLE_MIN = 30.0   # base: 1 stack / 30 min at boost divisor D=1
TG_STACK = 64
TG_BASE_ENERGY = 2000      # J/tick


def tech_gen_params(config):
    """From the global boost-slot config -> (D, output_stacks, energy/tick, boost cards).

    Each of the 4 slots holds a full stack (64) of a boost card or is empty.
    Speed divisor D (cycle = 30/D min): every filled slot subtracts >=1; cloning and
    efficiency subtract 1, acceleration subtracts tier+1. Output stacks: cloning adds
    1 (+1 more at tier>=4, >=6, >=9). Energy: acceleration +(t+1)*10%, efficiency
    -(t+1)*10%, applied per slot in order.
    """
    D = 1
    output_stacks = 1
    energy = float(TG_BASE_ENERGY)
    boost: dict[str, int] = {}
    for slot in (config or []):
        if not slot:
            continue
        cat = slot.get("category")
        t = int(slot.get("tier", 1))
        if cat == "cloning":
            D += 1
            output_stacks += 1 + (t >= 4) + (t >= 6) + (t >= 9)
        elif cat == "acceleration":
            D += t + 1
            energy += round(energy * (t + 1) * 10 / 100)
        elif cat == "efficiency":
            D += 1
            energy -= round(energy * (t + 1) * 10 / 100)
        else:
            continue
        fid = f"TECHCARD_{cat.upper()}_T{t}"
        boost[fid] = boost.get(fid, 0) + 1
    return D, output_stacks, max(0.0, energy), boost


def _boost_name(fid: str) -> str:
    _, cat, tier = fid.split("_")
    return f"{cat.title()} Card ({tier})"


def solve(graph: Graph, target: str, rate_per_min: float,
          banned: set[str] | None = None, extra_leaves: set[str] | None = None,
          tech_gen_config=None):
    banned = set(banned or ())
    extra_leaves = set(extra_leaves or ())

    tg_D, tg_stacks, tg_energy, tg_boost = tech_gen_params(tech_gen_config)
    tg_ops_per_min = tg_D / TG_BASE_CYCLE_MIN          # cycles/min per generator
    tg_out_amount = tg_stacks * TG_STACK              # items produced per cycle

    def is_tg(r):
        return r.machine_id == TECH_GENERATOR

    def eff_ops(r):
        return tg_ops_per_min if is_tg(r) else r.ops_per_min_per_machine

    def eff_amt(r, o):
        # tech generators clone the resource output by the boost config
        return tg_out_amount if (is_tg(r) and o.ref == r.output_id) else o.amount

    if target not in graph.items and target not in graph.by_output:
        return {"ok": False, "error": f"Unknown item: {target}"}

    usable, items = _reachable(graph, target, banned)
    usable = [r for r in usable if r.output_id not in extra_leaves
              and not any(o.ref in extra_leaves for o in r.outputs)]
    # Iterate items in a STABLE order everywhere a decision depends on it. `items` is a set,
    # so raw iteration order is hash-seed-randomized across processes — which made the greedy
    # cycle-breaking below pick different forced-raw items on ties, giving a different machine
    # count run-to-run. Sorting makes the whole solve reproducible.
    items_order = sorted(items)

    net = {r.rid: r.net() for r in usable}
    req = {r.rid: {ref for ref, n in net[r.rid].items() if n < 0} for r in usable}
    producers = defaultdict(list)            # item -> recipes that net-produce it
    for r in usable:
        for o in r.outputs:
            if net[r.rid].get(o.ref, 0) > 0:
                producers[o.ref].append(r)
    no_producer = {it for it in items if not producers.get(it)}

    # --- E: energy-producible items. Bootstrap = "free producers" (recipes whose net
    # has no negative item, i.e. self-sustaining from energy: growth chambers,
    # harvesters, generators), propagating through recipes whose net-negative inputs
    # are all in E. Used to force energy paths for E items (no raw shortcuts).
    E = set()
    changed = True
    while changed:
        changed = False
        for r in usable:
            if req[r.rid] <= E:
                for o in r.outputs:
                    if net[r.rid].get(o.ref, 0) > 0 and o.ref not in E:
                        E.add(o.ref)
                        changed = True

    def e_recipe(r):
        return req[r.rid] <= E

    # --- producibility with greedy cycle-breaking. Raw inputs = items with no producer
    # plus items released to break a no-bootstrap cycle (prefer mined/vanilla items, so
    # e.g. diamond becomes raw but diamond_block/pickaxe are still crafted).
    def producible_with(raw):
        avail = set(raw) | no_producer
        prod = set()
        ch = True
        while ch:
            ch = False
            for it in items_order:
                if it in prod or it in avail:
                    continue
                for r in producers.get(it, []):
                    if req[r.rid] <= (avail | prod):
                        prod.add(it)
                        ch = True
                        break
        return prod

    forced_raw = set()
    producible = producible_with(forced_raw)
    for _ in range(1000):
        if target in producible or target in no_producer:
            break
        stuck = [it for it in items_order
                 if it not in producible and it not in no_producer and it not in forced_raw]
        if not stuck:
            break
        stuck_set = set(stuck)
        dep = defaultdict(int)
        for it in stuck:
            for r in producers[it]:
                for ref in req[r.rid]:
                    if ref in stuck_set:
                        dep[ref] += 1
        pool = [it for it in stuck if graph.is_vanilla(it)] or stuck
        forced_raw.add(max(pool, key=lambda x: dep[x]))
        producible = producible_with(forced_raw)

    produced = producible
    raw_items = (no_producer | forced_raw | extra_leaves) - produced

    if target not in produced:
        return {"ok": False, "error": "target_is_raw",
                "message": f"{graph.display_name(target)} can't be produced from the "
                           f"available recipes (it's a raw input or all paths are banned)."}

    # --- which recipes may run: energy recipes (clean), plus recipes that make a
    # produced non-vanilla (SF) item. Excludes e.g. stick<-bamboo (non-energy recipe
    # producing only a vanilla item), forcing the energy path for vanilla items.
    def include(r):
        if not any(o.ref in produced for o in r.outputs):
            return False
        if e_recipe(r):
            return True
        # a non-energy (raw-consuming) recipe is kept only if it makes something that
        # ISN'T energy-producible (so it's a needed path); excludes e.g. stick<-bamboo
        # since sticks are energy-producible, but keeps pickaxe<-diamond+stick.
        return any(o.ref in produced and o.ref not in E for o in r.outputs)

    run = [r for r in usable if include(r)]
    # an E (vanilla) item must only be produced by energy recipes
    prod_recipes = defaultdict(list)
    cons_recipes = defaultdict(list)
    for r in run:
        for o in r.outputs:
            if o.ref in produced and (o.ref not in E or e_recipe(r)):
                prod_recipes[o.ref].append((r.rid, eff_amt(r, o)))
        for i in r.ingredients:
            cons_recipes[i.ref].append((r.rid, i.amount))

    prob = pulp.LpProblem("fewest_machines", pulp.LpMinimize)
    ops = {r.rid: pulp.LpVariable(f"ops_{r.rid}", lowBound=0) for r in run}
    # machines start CONTINUOUS: the single-sourcing loop below only needs the flow (ops) to see
    # which producers an item uses, and continuous LPs are instant. We flip to Integer for the
    # final solve, on the already single-sourced (much less degenerate) model — far faster than
    # solving the full integer MILP with all the tier/byproduct variants up front.
    mach = {r.rid: pulp.LpVariable(f"m_{r.rid}", lowBound=0) for r in run}
    for r in run:
        prob += mach[r.rid] >= ops[r.rid] / max(eff_ops(r), 1e-9)

    leaves = set()
    for item in items_order:
        if item not in produced:
            leaves.add(item)
            continue
        production = pulp.lpSum(ops[rid] * amt for rid, amt in prod_recipes.get(item, []))
        consumption = pulp.lpSum(ops[rid] * amt for rid, amt in cons_recipes.get(item, []))
        demand = rate_per_min if item == target else 0
        prob += production >= consumption + demand, f"bal_{item}"

    # Objective (lexicographic via tiny weights):
    #  1. fewest machines (primary)
    #  2. prefer FAST machines: a slowness cost (1/throughput) per machine, so on ties the
    #     solver uses one fast tier rather than mixing in a slow one (e.g. 2x Virtual Garden
    #     III, never 1x III + 1x I; one Excitation Chamber kind, not chamber + tech gen).
    #     The Network Auto Crafter (NTW_AUTO_CRAFTER) is preferred over every other equal-speed
    #     auto-crafter (Slimefun's + FluffyMachines') — same 120/min, easiest to set up.
    #  3. fewest operations (so utilization/byproducts aren't inflated).
    OTHER_CRAFTERS = {"VANILLA_AUTO_CRAFTER", "ENHANCED_AUTO_CRAFTER", "ARMOR_AUTO_CRAFTER",
                      "AUTO_CRAFTING_TABLE", "AUTO_ENHANCED_CRAFTING_TABLE",
                      "AUTO_MAGIC_WORKBENCH", "AUTO_ARMOR_FORGE", "AUTO_ANCIENT_ALTAR"}

    def slowness(r):
        s = 1.0 / max(eff_ops(r), 1e-9)
        if r.machine_id in OTHER_CRAFTERS:
            s += 0.05                       # prefer NTW_AUTO_CRAFTER among equal-speed crafters
        return s

    prob += (pulp.lpSum(mach[r.rid] for r in run)
             + 1e-4 * pulp.lpSum(mach[r.rid] * slowness(r) for r in run)
             + 1e-7 * pulp.lpSum(ops.values()))

    def _solve():
        # The machine count is an integer and dominates the objective; the tie-break weights are
        # ~1e-4. An ABSOLUTE gap < 1 therefore proves the machine count optimal while letting CBC
        # stop instead of grinding to prove the negligible tie-break (which makes degenerate
        # small-quantity queries hang for ~20s). timeLimit is a safety net — the incumbent still
        # has the optimal machine count and satisfies single-sourcing.
        try:
            st = prob.solve(pulp.PULP_CBC_CMD(msg=0, gapAbs=0.9, gapRel=1e-4, timeLimit=10))
        except TypeError:                       # older PuLP arg names
            st = prob.solve(pulp.PULP_CBC_CMD(msg=0, fracGap=1e-4, maxSeconds=10))
        return pulp.LpStatus[st]

    status = _solve()
    if status != "Optimal":
        return {"ok": False, "error": f"solver status: {status}"}

    # SINGLE-SOURCING: produce each item's PRIMARY output from ONE recipe/machine type — the user
    # wants e.g. all nether wart from Electric Ore Grinder III, never a mix of Grinder II + III +
    # Growth Chamber, even when a mix would shave a machine. Binaries make CBC explode, so do it
    # GREEDILY: for each item the current solution makes from >1 producer, keep the dominant one
    # (most output = the machine type the solver loaded most) and DISABLE the rest (cap ops at 0),
    # then re-solve, until none remain. Byproducts (an item as a SECONDARY output) are never
    # constrained, so chains like crimson-fungus -> nether-wart-block byproduct -> grinder work.
    prim_amt = defaultdict(dict)             # item -> {rid: primary output amount}
    for r in run:
        if r.output_id not in produced or r.rid not in ops:
            continue
        o = next((o for o in r.outputs if o.ref == r.output_id), None)
        prim_amt[r.output_id][r.rid] = eff_amt(r, o) if o else 1
    prim_of = {it: list(d) for it, d in prim_amt.items() if len(d) > 1}

    def _single_source():
        for _ in range(40):
            conflicts = [it for it, rids in prim_of.items()
                         if sum(1 for rid in rids if (ops[rid].value() or 0) > 1e-6) > 1]
            if not conflicts:
                break
            disabled_now = []
            for it in conflicts:
                rids = prim_of[it]
                keep = max(rids, key=lambda r: (ops[r].value() or 0) * prim_amt[it][r])
                for rid in rids:
                    if rid != keep and ops[rid].upBound != 0:
                        ops[rid].upBound = 0
                        disabled_now.append(rid)
            if not disabled_now:
                break
            if _solve() != "Optimal":         # over-constrained: undo this round and stop
                for rid in disabled_now:
                    ops[rid].upBound = None
                _solve()
                break

    # 1) single-source on the fast CONTINUOUS relaxation (disables minor producers cheaply),
    # 2) flip to INTEGER and solve the now much smaller model,
    # 3) re-run single-sourcing to clean up any split introduced by integer rounding.
    _single_source()
    for v in mach.values():
        v.cat = pulp.LpInteger
    if _solve() != "Optimal":
        return {"ok": False, "error": "no integer solution"}
    _single_source()

    # --- collect solution
    steps = []
    machine_totals = defaultdict(int)
    fixture_names: dict[str, str] = {}
    fixture_ids: set[str] = set()        # chickens / cards: listed, but NOT machines
    raw_inputs = defaultdict(float)
    val = {rid: (ops[rid].value() or 0) for rid in ops}
    rec_by_id = {r.rid: r for r in run}
    # every item consumed somewhere in the chosen solution — so a recipe's secondary outputs
    # (byproducts) can be flagged as actually USED elsewhere (e.g. a Nether Growth Chamber's
    # nether-wart-block byproduct feeding the grinder) vs incidental waste.
    consumed = set()
    for r in run:
        if val[r.rid] > 1e-6:
            consumed.update(i.ref for i in r.ingredients)

    def machine_display(mid):
        # use the machines.json display when it's a real override; otherwise, if the
        # machine id is itself an item (generators, chambers...), use the item's name —
        # unless that "name" is actually a description (e.g. DynaTech's Growth Chamber
        # name field is "Automatically grows plants."), in which case fall back to the id.
        md = graph.machines.get(mid, {})
        disp = md.get("display")
        auto = mid.replace("_", " ").title()
        if (not disp or disp == auto) and mid in graph.items:
            name = graph.display_name(mid)
            if not name.rstrip().endswith(".") and len(name.split()) <= 5:
                return name
        return disp or auto
    for r in run:
        n = val[r.rid]
        if n < 1e-6:
            continue
        m = int(round(mach[r.rid].value() or 0))
        machine_totals[r.machine_id] += m

        def fx_name(fid, fallback):
            # prefer a real item's display name (cards are items); else the label
            return graph.display_name(fid) if fid in graph.items else fallback

        # a fixture (chicken / resource card) is needed 1:1 with the machine, but it
        # is NOT itself a machine: a chamber + coal chicken is ONE machine, a tech gen
        # + 4 cloners + a damascus card is ONE machine. So fixtures are listed, never
        # counted toward total_machines.
        step_fixtures = []
        for fx in r.fixtures:
            nm = fx_name(fx["id"], fx.get("name", fx["id"]))
            machine_totals[fx["id"]] += m
            fixture_names[fx["id"]] = nm
            fixture_ids.add(fx["id"])
            step_fixtures.append({"id": fx["id"], "name": nm, "count": m})
        # tech generators also hold the chosen boost cards (global config)
        if is_tg(r):
            for fid, per_gen in tg_boost.items():
                nm = _boost_name(fid)
                machine_totals[fid] += m * per_gen
                fixture_ids.add(fid)
                fixture_names[fid] = nm
                step_fixtures.append({"id": fid, "name": nm, "count": m * per_gen})
        # headline the target when a multi-output machine (quarry/aquarium) yields it,
        # else the recipe's primary output
        prim = (next((o for o in r.outputs if o.ref == target), None)
                or next((o for o in r.outputs if o.ref == r.output_id), r.outputs[0]))
        step = {
            "output_id": prim.ref,
            "output_name": graph.display_name(prim.ref),
            "machine_id": r.machine_id,
            "machine_name": machine_display(r.machine_id),
            "machines": m,
            "ops_per_min": round(n, 3),
            "produced_per_min": round(n * eff_amt(r, prim), 2),
            "energy_only": r.energy_only,
            "fixtures": step_fixtures,
            "byproducts": [{"ref": o.ref, "name": graph.display_name(o.ref),
                            "per_min": round(n * eff_amt(r, o), 2),
                            "used": o.ref in consumed}
                           for o in r.outputs if o.ref != prim.ref],
            "ingredients": [{"ref": i.ref, "name": graph.display_name(i.ref),
                             "kind": i.kind, "per_op": i.amount,
                             "per_min": round(n * i.amount, 2)}
                            for i in r.ingredients],
        }
        if is_tg(r):
            step["energy_per_tick"] = int(tg_energy)
        steps.append(step)
        for i in r.ingredients:
            if i.ref in leaves:
                raw_inputs[i.ref] += n * i.amount

    steps.sort(key=lambda s: s["output_id"] != target)
    return {
        "ok": True,
        "target": target,
        "target_name": graph.display_name(target),
        "rate_per_min": rate_per_min,
        "total_machines": sum(v for k, v in machine_totals.items() if k not in fixture_ids),
        "machine_totals": [
            {"machine_id": k,
             "machine_name": fixture_names.get(k) or machine_display(k),
             "count": v, "is_fixture": k in fixture_ids}
            for k, v in sorted(machine_totals.items(),
                               key=lambda kv: (kv[0] in fixture_ids, -kv[1]))],
        "steps": steps,
        "raw_inputs": [
            {"ref": k, "name": graph.display_name(k), "per_min": round(v, 2)}
            for k, v in sorted(raw_inputs.items(), key=lambda kv: -kv[1])],
        "tech_gen": {
            "divisor": tg_D,
            "output_stacks": tg_stacks,
            "cycle_minutes": round(TG_BASE_CYCLE_MIN / tg_D, 3),
            "per_min_per_gen": round(tg_out_amount * tg_ops_per_min, 2),
            "energy_per_tick": int(tg_energy),
            "boost_cards": [{"name": _boost_name(k), "count": v}
                            for k, v in tg_boost.items()],
        },
    }
