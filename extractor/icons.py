"""Icon resolver.

Builds `data/icons/` (extracted from the Slimefun guide resource pack) and
`data/icon_map.json` mapping each item id to an icon source:
  * {"type": "pack", "file": "<id>.png"}     base Slimefun textures (local PNG)
  * {"type": "head", "tex": "<hash>"}         addon player-head items (rendered
                                              from minotar/mc-heads by the frontend)
  * {"type": "material", "material": "X"}     vanilla material fallback
  * absent                                    frontend draws a lettered placeholder
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PACK_DIR = ROOT / "resource-pack"
ICON_DIR = DATA / "icons"

PACK_PREFIX = "assets/slimefun/textures/item/"


def _find_pack() -> Path | None:
    if not PACK_DIR.exists():
        return None
    for p in PACK_DIR.iterdir():
        if p.is_file():
            with open(p, "rb") as f:
                if f.read(2) == b"PK":
                    return p
    return None


def build():
    items = json.loads((DATA / "items.json").read_text(encoding="utf-8"))
    ICON_DIR.mkdir(parents=True, exist_ok=True)

    pack_files: dict[str, str] = {}   # lowercase id -> png filename
    pack = _find_pack()
    if pack:
        with zipfile.ZipFile(pack) as zf:
            for name in zf.namelist():
                if name.startswith(PACK_PREFIX) and name.endswith(".png"):
                    base = name[len(PACK_PREFIX):]
                    key = base.rsplit("/", 1)[-1][:-4].lower()  # strip dir + .png
                    out = ICON_DIR / (key + ".png")
                    out.write_bytes(zf.read(name))
                    pack_files[key] = key + ".png"

    # word-set index of unmatched pack textures, so a reordered id still matches
    # (e.g. item TALISMAN_ANGEL <-> texture angel_talisman, or ENDER_LUMP_2 <->
    # ender_lump2). Tokenize on letter/digit boundaries; keep only unambiguous ones.
    def words(s):
        return frozenset(re.findall(r"[a-z]+|\d+", s.lower()))

    id_keys = {it["id"].lower() for it in items}
    wordset: dict[frozenset, list[str]] = {}
    for key in pack_files:
        if key not in id_keys:
            wordset.setdefault(words(key), []).append(key)

    icon_map = {}
    pack_hits = head_hits = vanilla_hits = 0
    for it in items:
        iid = it["id"]
        key = iid.lower()
        if key in pack_files:
            icon_map[iid] = {"type": "pack", "file": pack_files[key]}
            pack_hits += 1
        elif it.get("texture"):
            icon_map[iid] = {"type": "head", "tex": it["texture"]}
            head_hits += 1
        elif (cand := wordset.get(words(key))) and len(cand) == 1:
            icon_map[iid] = {"type": "pack", "file": pack_files[cand[0]]}
            pack_hits += 1
        elif it.get("vanilla"):
            # vanilla items have no local texture (pack is Slimefun-only, bundled MC jar
            # is server-side) -> frontend renders from the versioned vanilla-asset CDN,
            # trying item/<name> then block/<name>.
            icon_map[iid] = {"type": "vanilla", "name": key}
            vanilla_hits += 1
    (DATA / "icon_map.json").write_text(json.dumps(icon_map), encoding="utf-8")
    print(f"icons: {len(pack_files)} pack textures extracted; map covers "
          f"pack={pack_hits} head={head_hits} vanilla={vanilla_hits} of {len(items)} items")


if __name__ == "__main__":
    build()
