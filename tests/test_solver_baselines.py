"""Solver baseline (snapshot) tests.

These are NOT correctness/optimality assertions. Each baseline in tests/baselines/
is a snapshot of the solver's output captured at a known-good point in time. When the
extractor or solver changes, a baseline may legitimately drift — a different recipe path,
a better (fewer) or worse (more) machine count, etc.

So on divergence the test FAILS loudly with a human-readable diff, but the failure means
"the result changed — go look", not "the new result is wrong". The required workflow:

    1. Read the diff below. It shows total-machine delta (and whether it got better/worse),
       which machines changed, which recipe paths changed, and raw-input changes.
    2. Manually decide whether the new output is correct and better/worse than the snapshot.
    3. If the new output is the one you want, refresh the baseline:
           python tests/test_solver_baselines.py --update
       (or delete the baseline file and re-run the generator).

Run directly:  python tests/test_solver_baselines.py           (report + exit 1 on drift)
               python tests/test_solver_baselines.py --update   (rewrite baselines)
Or via pytest: pytest tests/test_solver_baselines.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE_DIR = Path(__file__).resolve().parent / "baselines"

from solver.graph import Graph          # noqa: E402
from solver.optimize import solve       # noqa: E402

_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = Graph.load()
    return _GRAPH


# Default tech-generator boost = 4x Cloning Card (T1), matching the UI/CLI/API default
# (server/app.py). A baseline can override via its query's "tech_gen" (None = unboosted).
DEFAULT_TECH_GEN = [{"category": "cloning", "tier": 1}] * 4


def _run(query: dict) -> dict:
    """Solve a baseline query and return the same normalized shape we snapshot."""
    tg = query["tech_gen"] if "tech_gen" in query else DEFAULT_TECH_GEN
    res = solve(_graph(), query["target"], query["rate_per_min"],
                banned=set(query.get("banned", [])),
                tech_gen_config=tg,
                stackable_cards=query.get("stackable_cards", False))
    machine_totals = {m["machine_id"]: m["count"] for m in res["machine_totals"]}
    chosen = {s["output_id"]: s.get("machine_id") for s in res["steps"]}
    raw = {(r.get("id") or r.get("ref")): r.get("per_min") for r in res["raw_inputs"]}
    return {
        "total_machines": res["total_machines"],
        "machine_totals": dict(sorted(machine_totals.items())),
        "chosen_machine_per_output": dict(sorted(chosen.items())),
        "raw_inputs": dict(sorted(raw.items())),
    }


def _diff(baseline: dict, live: dict) -> list[str]:
    """Human-readable divergence report. Empty list == identical."""
    out: list[str] = []

    b_tot, l_tot = baseline["total_machines"], live["total_machines"]
    if b_tot != l_tot:
        verdict = "BETTER (fewer machines)" if l_tot < b_tot else "WORSE (more machines)"
        out.append(f"total_machines: {b_tot} -> {l_tot}  (delta {l_tot - b_tot:+d}, {verdict})")

    def cmp_map(label, b, l, kind="count"):
        keys = sorted(set(b) | set(l))
        for k in keys:
            bv, lv = b.get(k), l.get(k)
            if bv == lv:
                continue
            if bv is None:
                out.append(f"  [{label}] + {k} = {lv}  (new)")
            elif lv is None:
                out.append(f"  [{label}] - {k}  (was {bv}, now gone)")
            else:
                out.append(f"  [{label}] ~ {k}: {bv} -> {lv}")

    if baseline["machine_totals"] != live["machine_totals"]:
        out.append("machine_totals changed:")
        cmp_map("machine", baseline["machine_totals"], live["machine_totals"])
    if baseline["chosen_machine_per_output"] != live["chosen_machine_per_output"]:
        out.append("recipe path (chosen machine per output) changed:")
        cmp_map("path", baseline["chosen_machine_per_output"], live["chosen_machine_per_output"])
    if baseline["raw_inputs"] != live["raw_inputs"]:
        out.append("raw_inputs changed:")
        cmp_map("raw", baseline["raw_inputs"], live["raw_inputs"])
    return out


def _baselines() -> list[Path]:
    return sorted(BASELINE_DIR.glob("*.json"))


def check(update: bool = False) -> int:
    failures = 0
    for path in _baselines():
        baseline = json.loads(path.read_text(encoding="utf-8"))
        query = baseline["query"]
        live = _run(query)
        diff = _diff(baseline, live)
        name = path.stem
        if not diff:
            print(f"[OK]   {name}: matches snapshot ({live['total_machines']} machines)")
            continue
        if update:
            baseline.update(live)
            path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
            print(f"[UPD]  {name}: baseline refreshed -> {live['total_machines']} machines")
            continue
        failures += 1
        print(f"\n[DRIFT] {name}: solver output diverged from snapshot — MANUAL REVIEW REQUIRED")
        print(f"        query: {query['target']} @ {query['rate_per_min']}/min "
              f"banned={query.get('banned') or 'none'}")
        for line in diff:
            print("        " + line)
        print("        -> verify the new result is correct and decide better/worse; "
              "if intended, rerun with --update.\n")
    return failures


# ---- pytest entry point -------------------------------------------------------
def test_solver_baselines():
    failures = check(update=False)
    assert failures == 0, (
        f"{failures} solver baseline(s) drifted — see the printed diff above. This is a "
        "snapshot, not an optimality assertion: manually check why it changed and whether "
        "it's better or worse, then refresh with `python tests/test_solver_baselines.py --update`."
    )


if __name__ == "__main__":
    update = "--update" in sys.argv
    rc = check(update=update)
    if update:
        sys.exit(0)
    if rc:
        print(f"{rc} baseline(s) drifted.")
    else:
        print("All baselines match.")
    sys.exit(1 if rc else 0)
