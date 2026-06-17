"""FastAPI backend for the Slimefun automation calculator.

Serves the static web UI plus a small JSON API:
  GET  /api/items?q=...           item search (autocomplete)
  GET  /api/machines             machine list (for the ban panel)
  POST /api/solve                {item, quantity, minutes, banned[], leaves[]} -> plan
  GET  /api/recipes_for?item=    every recipe that directly produces an item (reverse search)
  GET  /api/recipes_by_machine?machine=   every recipe a machine runs (search)
  GET  /api/recipe_machines      machines that have at least one recipe (search picker)
  GET  /icons/<file>             extracted resource-pack icons
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from solver import config as cfg
from solver.graph import Graph
from solver.optimize import (
    solve, tech_gen_params, TECH_GENERATOR, TG_BASE_CYCLE_MIN, TG_STACK,
)

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
ICONS = ROOT / "data" / "icons"

app = FastAPI(title="Slimefun Automation Calculator")
GRAPH = Graph.load()


@app.middleware("http")
async def no_store_static(request, call_next):
    """Make the browser always revalidate (ETag) so edited JS/CSS never serves stale.

    Local dev tool — avoids the "I changed app.js but the page runs the old one" trap.
    """
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


class TechGenSlot(BaseModel):
    category: str          # "cloning" | "acceleration" | "efficiency"
    tier: int = 1


class SolveRequest(BaseModel):
    item: str
    quantity: float = 64
    minutes: float = 1
    banned: list[str] = []
    leaves: list[str] = []
    tech_gen: list[TechGenSlot | None] = []
    stackable_cards: bool = True     # Mob Simulation Chamber: stack up to 64 data cards/machine
    data_card_weight: float = 0.1    # per-card "machine cost" in the optimizer objective


@app.get("/api/items")
def api_items(q: str = ""):
    if not q:
        return {"items": []}
    return {"items": GRAPH.search(q, limit=40)}


@app.get("/api/machines")
def api_machines():
    rows = []
    for m in GRAPH.machines.values():
        rows.append({
            "id": m["id"], "name": m["display"],
            "category": m.get("category", ""),
            "addon": m.get("addon", "Other"),
        })
    rows.sort(key=lambda r: (r["addon"], r["name"]))
    return {"machines": rows}


def _machine_name(mid: str) -> str:
    m = GRAPH.machines.get(mid)
    if m and m.get("display"):
        return m["display"]
    return GRAPH.display_name(mid)


def _resolve_tech_gen(tech_gen):
    """Normalize a caller's tech-gen slots into solver boost params.

    Mirrors /api/solve: an explicit list (incl. all-null = deliberately emptied)
    is honored as-is; an omitted/empty config defaults to 4x Cloning Card (T1).
    """
    if tech_gen:
        cfg = [None if s is None else {"category": s.get("category"), "tier": int(s.get("tier", 1))}
               for s in tech_gen]
    else:
        cfg = [{"category": "cloning", "tier": 1}] * 4
    return tech_gen_params(cfg)


def _parse_tech_gen_query(tech_gen: str):
    """Parse the JSON `tech_gen` query string used by the reverse-search/search pages."""
    cfg = None
    if tech_gen:
        try:
            cfg = json.loads(tech_gen)
        except (ValueError, TypeError):
            cfg = None
    return _resolve_tech_gen(cfg)


def _serialize_recipe(rec, tg=None) -> dict:
    """Shape a Recipe for the reverse-search / search pages.

    `tg` is the tech-gen boost params (from `tech_gen_params`); when given, Tech
    Generator recipes report the boosted output amount and cycle rate so these
    pages match the calculator's numbers instead of the unboosted base recipe.
    """
    def ing(i):
        return {"ref": i.ref, "amount": i.amount, "name": GRAPH.display_name(i.ref)}
    ops = rec.ops_per_min_per_machine          # exact, for derived seconds/rate
    outputs = [ing(o) for o in rec.outputs]
    primary_amt = next((o["amount"] for o in outputs if o["ref"] == rec.output_id),
                       rec.output_amount)
    if tg and rec.machine_id == TECH_GENERATOR:
        tg_D, tg_stacks, _tg_energy, _tg_boost = tg
        ops = tg_D / TG_BASE_CYCLE_MIN
        primary_amt = tg_stacks * TG_STACK
        for o in outputs:
            if o["ref"] == rec.output_id:
                o["amount"] = primary_amt      # keep the per-operation amount (e.g. x320)
    return {
        "rid": rec.rid,
        "machine_id": rec.machine_id,
        "machine_name": _machine_name(rec.machine_id),
        "output_id": rec.output_id,
        "output_name": GRAPH.display_name(rec.output_id),
        "output_amount": rec.output_amount,
        "ingredients": [ing(i) for i in rec.ingredients],
        "outputs": outputs,
        "addon": rec.addon,
        "ops_per_min": round(ops, 4),
        "op_seconds": round(60.0 / ops, 2) if ops else None,   # duration of one operation
        "output_per_min": round(ops * primary_amt, 2),         # items produced per minute
        "energy_only": rec.energy_only,
        "fixtures": rec.fixtures,
    }


@app.get("/api/recipes_for")
def api_recipes_for(item: str = "", tech_gen: str = ""):
    """Reverse search: every recipe that directly produces `item` (incl. as a byproduct)."""
    recs = GRAPH.by_output.get(item, [])
    tg = _parse_tech_gen_query(tech_gen)
    return {
        "item": item,
        "item_name": GRAPH.display_name(item) if item else "",
        "recipes": [_serialize_recipe(r, tg) for r in recs],
    }


@app.get("/api/recipes_by_machine")
def api_recipes_by_machine(machine: str = "", tech_gen: str = ""):
    """Search: every recipe the given machine can run."""
    recs = [r for r in GRAPH.recipes if r.machine_id == machine]
    tg = _parse_tech_gen_query(tech_gen)
    return {
        "machine": machine,
        "machine_name": _machine_name(machine) if machine else "",
        "recipes": [_serialize_recipe(r, tg) for r in recs],
    }


@app.get("/api/recipe_machines")
def api_recipe_machines():
    """Machines that have at least one recipe, with a recipe count (for the search picker)."""
    counts: dict[str, int] = {}
    for r in GRAPH.recipes:
        counts[r.machine_id] = counts.get(r.machine_id, 0) + 1
    rows = []
    for mid, n in counts.items():
        m = GRAPH.machines.get(mid, {})
        rows.append({
            "id": mid, "name": _machine_name(mid),
            "addon": m.get("addon", "Other"), "count": n,
        })
    rows.sort(key=lambda r: (r["addon"], r["name"]))
    return {"machines": rows}


class SaveConfigRequest(BaseModel):
    name: str
    banned: list[str] = []
    tech_gen: list[TechGenSlot | None] = []
    stackable_cards: bool = True
    data_card_weight: float = 0.1


@app.get("/api/configs")
def api_configs():
    """List the named configs saved to disk (for the load dropdown)."""
    return {"configs": cfg.list_names()}


@app.get("/api/configs/{name}")
def api_config_get(name: str):
    """Load one named config (banned + tech_gen + stackable_cards)."""
    try:
        return cfg.load(name)
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": f"No config named '{name}'"},
                            status_code=404)


@app.post("/api/configs")
def api_config_save(req: SaveConfigRequest):
    """Persist the current UI settings as a named config under configs/<name>.json."""
    tg = [None if s is None else {"category": s.category, "tier": s.tier}
          for s in req.tech_gen]
    try:
        path = cfg.save(req.name, banned=req.banned, tech_gen=tg,
                        stackable_cards=req.stackable_cards,
                        data_card_weight=req.data_card_weight)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "name": path.stem, "configs": cfg.list_names()}


@app.delete("/api/configs/{name}")
def api_config_delete(name: str):
    deleted = cfg.delete(name)
    return {"ok": deleted, "configs": cfg.list_names()}


@app.post("/api/solve")
def api_solve(req: SolveRequest):
    item_id = req.item
    if item_id not in GRAPH.items and item_id not in GRAPH.by_output:
        hits = GRAPH.search(item_id, limit=5)
        producible = [h for h in hits if h["producible"]] or hits
        if not producible:
            return JSONResponse({"ok": False, "error": f"No item matches '{item_id}'"})
        item_id = producible[0]["id"]
    minutes = req.minutes or 1
    rate = req.quantity / minutes
    # default tech-gen fill = 4x Cloning Card (T1) when the caller omits it (matches the UI/CLI);
    # an explicitly-sent list (incl. all-null = deliberately emptied) is honored as-is.
    if req.tech_gen:
        tg = [None if s is None else {"category": s.category, "tier": s.tier}
              for s in req.tech_gen]
    else:
        tg = [{"category": "cloning", "tier": 1}] * 4
    res = solve(GRAPH, item_id, rate, banned=set(req.banned),
                extra_leaves=set(req.leaves), tech_gen_config=tg,
                stackable_cards=req.stackable_cards,
                data_card_weight=req.data_card_weight)
    return JSONResponse(res)


@app.get("/icons/{name}")
def icon(name: str):
    p = ICONS / name
    if p.exists() and p.suffix == ".png":
        return FileResponse(p)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/icon_map")
def icon_map():
    p = ROOT / "data" / "icon_map.json"
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})


app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
