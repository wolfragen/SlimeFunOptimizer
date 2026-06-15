"""Named solver configs saved to disk (`configs/<name>.json`).

A *config* is the reusable, item-independent part of a solve: the banned-machine
list, the Tech Generator boost slots, and the stackable-cards toggle. The web UI
keeps these in the browser's localStorage (per-user, can't be read off-disk), so
this module lets the UI ALSO persist the exact same settings to a file in the repo.

That on-disk copy is what test baselines (and the CLI) read, so a baseline can be
pinned to the precise in-game setup the user is running without re-typing the ban
list into every baseline query. One shape, used by server, CLI, and tests.

Shape (`configs/<name>.json`):
    {
      "name": "my_setup",
      "banned": ["ANCIENT_ALTAR", ...],
      "tech_gen": [{"category": "cloning", "tier": 1}, ... up to 4, null = empty slot],
      "stackable_cards": false
    }
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = ROOT / "configs"


def _safe_name(name: str) -> str:
    """Filesystem-safe slug for a config name (keeps it readable, no path tricks)."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip()).strip("_.")
    if not slug:
        raise ValueError("config name is empty")
    return slug


def _path(name: str) -> Path:
    return CONFIGS_DIR / f"{_safe_name(name)}.json"


def normalize(banned=None, tech_gen=None, stackable_cards: bool = False) -> dict:
    """Coerce loose inputs into the canonical stored shape (sorted bans, 4 slots)."""
    bans = sorted({str(b) for b in (banned or [])})
    slots: list[dict | None] = []
    for s in (tech_gen or []):
        if s is None or not s.get("category"):
            slots.append(None)
        else:
            slots.append({"category": str(s["category"]), "tier": int(s.get("tier", 1))})
    return {
        "banned": bans,
        "tech_gen": slots,
        "stackable_cards": bool(stackable_cards),
    }


def save(name: str, banned=None, tech_gen=None, stackable_cards: bool = False) -> Path:
    """Write a named config to `configs/<name>.json`, returning its path."""
    CONFIGS_DIR.mkdir(exist_ok=True)
    data = {"name": _safe_name(name), **normalize(banned, tech_gen, stackable_cards)}
    path = _path(name)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load(name: str) -> dict:
    """Load a named config; raises FileNotFoundError if it doesn't exist."""
    path = _path(name)
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"name": data.get("name", _safe_name(name)),
            **normalize(data.get("banned"), data.get("tech_gen"),
                        data.get("stackable_cards", False))}


def exists(name: str) -> bool:
    return _path(name).exists()


def list_names() -> list[str]:
    """Saved config names, sorted (the file stems under configs/)."""
    if not CONFIGS_DIR.exists():
        return []
    return sorted(p.stem for p in CONFIGS_DIR.glob("*.json"))


def delete(name: str) -> bool:
    path = _path(name)
    if path.exists():
        path.unlink()
        return True
    return False
