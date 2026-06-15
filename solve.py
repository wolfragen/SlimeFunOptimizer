"""CLI: solve an automation target.

    python solve.py "Cloner Robotic Golem" 64 1
    python solve.py SUPREME_CLONING_GOLEM 64 1 --ban ANCIENT_ALTAR
"""

from __future__ import annotations

import argparse

from solver import config as cfg
from solver.graph import Graph
from solver.optimize import solve


def resolve_item(graph: Graph, query: str) -> str | None:
    if query in graph.items or query in graph.by_output:
        return query
    hits = graph.search(query, limit=5)
    producible = [h for h in hits if h["producible"]]
    if producible:
        return producible[0]["id"]
    return hits[0]["id"] if hits else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("item")
    ap.add_argument("quantity", type=float)
    ap.add_argument("minutes", type=float, nargs="?", default=1.0)
    ap.add_argument("--ban", nargs="*", default=None)
    ap.add_argument("--techgen", nargs="*", default=None,
                    help="boost slots, e.g. cloning:1 cloning:1 cloning:1 acceleration:1; "
                         "omit for the default 4x cloning T1, pass with no args for no boost")
    ap.add_argument("--config", default=None,
                    help="load a saved config (configs/<name>.json) for bans + tech-gen + "
                         "stackable; --ban/--techgen/--stackable override it")
    ap.add_argument("--stackable", action="store_true", default=None,
                    help="Mob Simulation Chamber: stack up to 64 data cards per chamber")
    args = ap.parse_args()

    # a saved config supplies the defaults; explicit flags override it.
    saved = cfg.load(args.config) if args.config else {}

    if args.ban is not None:
        banned = set(args.ban)
    else:
        banned = set(saved.get("banned", []))

    if args.techgen is not None:
        tg_config = []
        for slot in args.techgen:
            cat, _, tier = slot.partition(":")
            tg_config.append({"category": cat, "tier": int(tier or 1)})
    elif saved.get("tech_gen") is not None and args.config:
        tg_config = saved["tech_gen"]
    else:
        # default tech-gen fill for tests: 4 stacks of cloner T1 in every generator
        tg_config = [{"category": "cloning", "tier": 1} for _ in range(4)]

    stackable = args.stackable if args.stackable is not None \
        else bool(saved.get("stackable_cards", False))

    g = Graph.load()
    item_id = resolve_item(g, args.item)
    if not item_id:
        print(f"No item matches '{args.item}'")
        return
    rate = args.quantity / args.minutes
    print(f"Target: {g.display_name(item_id)} ({item_id})  @ {rate:g}/min\n")
    res = solve(g, item_id, rate, banned=banned, tech_gen_config=tg_config,
                stackable_cards=stackable)
    if not res["ok"]:
        print("FAILED:", res.get("message") or res.get("error"))
        return
    tg = res.get("tech_gen")
    if tg and tg["divisor"] > 1:
        print(f"Tech gen config: {tg['output_stacks']} stack(s) every "
              f"{tg['cycle_minutes']} min = {tg['per_min_per_gen']}/min/gen, "
              f"{tg['energy_per_tick']} J/t"
              + (f"  [{', '.join(f'{c['count']}x {c['name']}' for c in tg['boost_cards'])}]"
                 if tg["boost_cards"] else "") + "\n")
    print(f"TOTAL MACHINES: {res['total_machines']}")
    print("By machine type:")
    for m in res["machine_totals"]:
        if not m.get("is_fixture"):
            print(f"   {m['count']:>4} x {m['machine_name']}")
    fixtures = [m for m in res["machine_totals"] if m.get("is_fixture")]
    if fixtures:
        print("Cards / chickens needed (not counted as machines):")
        for m in fixtures:
            print(f"   {m['count']:>4} x {m['machine_name']}")
    print("\nSteps (per minute):")
    for s in res["steps"]:
        print(f"   {s['machines']:>3} x {s['machine_name']:<26} -> "
              f"{s['produced_per_min']:>8}/min {s['output_name']}")
    print("\nRaw inputs needed (per minute):")
    for ri in res["raw_inputs"]:
        print(f"   {ri['per_min']:>10}  {ri['name']}")


if __name__ == "__main__":
    main()
