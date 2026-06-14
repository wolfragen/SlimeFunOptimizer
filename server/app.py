"""FastAPI backend for the Slimefun automation calculator.

Serves the static web UI plus a small JSON API:
  GET  /api/items?q=...      item search (autocomplete)
  GET  /api/machines         machine list (for the ban panel)
  POST /api/solve            {item, quantity, minutes, banned[], leaves[]} -> plan
  GET  /icons/<file>         extracted resource-pack icons
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from solver.graph import Graph
from solver.optimize import solve

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
ICONS = ROOT / "data" / "icons"

app = FastAPI(title="Slimefun Automation Calculator")
GRAPH = Graph.load()


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
    stackable_cards: bool = False    # Mob Simulation Chamber: stack up to 64 data cards/machine


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
                stackable_cards=req.stackable_cards)
    return JSONResponse(res)


@app.get("/icons/{name}")
def icon(name: str):
    p = ICONS / name
    if p.exists() and p.suffix == ".png":
        return FileResponse(p)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/icon_map")
def icon_map():
    import json
    p = ROOT / "data" / "icon_map.json"
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})


app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
