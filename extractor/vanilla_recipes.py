"""Vanilla Minecraft 1.20.6 crafting recipes.

Reads the recipe JSON straight from the (bundled) vanilla server jar that Paper
already downloaded — no extra download needed:
    test-server-1.20.6/cache/mojang_1.20.6.jar
        -> META-INF/versions/1.20.6/server-1.20.6.jar (nested)
            -> data/minecraft/recipes/*.json  +  data/minecraft/tags/items/*.json

Per the user's choices:
  * only `crafting_shaped` + `crafting_shapeless` (not smelting/stonecutting/smithing)
  * ALL crafting recipes are kept, both directions (e.g. 9 diamond -> diamond_block AND
    diamond_block -> 9 diamond) — nothing is excluded or forced "raw". The solver
    decides what is producible vs. must be supplied (an item is only effectively "raw"
    if it has no production path, e.g. ores; energy-generated items like diamonds from
    chickens/tech generators are handled by their own recipes elsewhere).
  * tag ingredients (e.g. #planks) -> one representative concrete item
  * crafting machine left generic ("VANILLA_CRAFTING"); maps to the network
    auto-crafter once that plugin is added.

Result Recipe objects use recipe_type "VANILLA_CRAFTING" and all-vanilla ingredients.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections import Counter
from pathlib import Path

from .model import Recipe, Ingredient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUNDLER = ROOT.parent / "test-server-1.20.6" / "cache" / "mojang_1.20.6.jar"
NESTED = "META-INF/versions/1.20.6/server-1.20.6.jar"
RECIPE_DIR = "data/minecraft/recipes/"
# committed cache so a rebuild works WITHOUT the (un-redistributable) Mojang server jar:
# when the jar is present we extract fresh and refresh this; otherwise we load from it.
CACHE = ROOT / "data" / "vanilla_recipes.json"
TAG_DIR = "data/minecraft/tags/items/"
MACHINE = "VANILLA_CRAFTING"


def _mat(item_id: str) -> str:
    """minecraft:oak_planks -> OAK_PLANKS (matches how vanilla items are keyed)."""
    return item_id.split(":", 1)[-1].upper()


def _open_server(bundler_path: Path) -> zipfile.ZipFile:
    outer = zipfile.ZipFile(bundler_path)
    return zipfile.ZipFile(io.BytesIO(outer.read(NESTED)))


def _load_tags(zf) -> dict[str, list[str]]:
    raw = {}
    for n in zf.namelist():
        if n.startswith(TAG_DIR) and n.endswith(".json"):
            key = "minecraft:" + n[len(TAG_DIR):-5]
            raw[key] = json.loads(zf.read(n)).get("values", [])
    return raw


def _resolve_tag(tag: str, tags: dict, seen=None) -> str | None:
    """First concrete item of a tag (resolving nested tags) — the representative."""
    seen = seen or set()
    if tag in seen:
        return None
    seen.add(tag)
    for v in tags.get(tag, []):
        val = v["id"] if isinstance(v, dict) else v
        if val.startswith("#"):
            r = _resolve_tag(val[1:], tags, seen)
            if r:
                return r
        else:
            return val
    return None


def _ingredient_ref(obj, tags):
    """An ingredient entry -> a single material name (representative for tags)."""
    if isinstance(obj, list):              # list = "any of"; take the first
        obj = obj[0] if obj else None
    if not isinstance(obj, dict):
        return None
    if "item" in obj:
        return _mat(obj["item"])
    if "tag" in obj:
        r = _resolve_tag(obj["tag"], tags)
        return _mat(r) if r else None
    return None


def _parse(zf, tags):
    """Return list of (output, count, Counter(input_ref->n)) for shaped/shapeless."""
    out = []
    for n in zf.namelist():
        if not (n.startswith(RECIPE_DIR) and n.endswith(".json")):
            continue
        d = json.loads(zf.read(n))
        t = d.get("type", "").replace("minecraft:", "")
        if t not in ("crafting_shaped", "crafting_shapeless"):
            continue
        res = d.get("result", {})
        output = _mat(res["id"]) if "id" in res else None
        if not output:
            continue
        count = res.get("count", 1)
        inputs = Counter()
        ok = True
        if t == "crafting_shaped":
            keymap = d.get("key", {})
            for row in d.get("pattern", []):
                for ch in row:
                    if ch == " ":
                        continue
                    ref = _ingredient_ref(keymap.get(ch), tags)
                    if ref is None:
                        ok = False
                    else:
                        inputs[ref] += 1
        else:  # shapeless
            for ing in d.get("ingredients", []):
                ref = _ingredient_ref(ing, tags)
                if ref is None:
                    ok = False
                else:
                    inputs[ref] += 1
        if ok and inputs:
            out.append((output, count, inputs))
    return out


ELECTRIC_FURNACE = "ElectricFurnace"   # vanilla smelting is automated by Slimefun's Electric Furnace


def _parse_smelting(zf, tags):
    """Return (output, input_ref, seconds) for every vanilla minecraft:smelting recipe."""
    out = []
    for n in zf.namelist():
        if not (n.startswith(RECIPE_DIR) and n.endswith(".json")):
            continue
        d = json.loads(zf.read(n))
        if d.get("type", "").replace("minecraft:", "") != "smelting":
            continue
        res = d.get("result")
        output = _mat(res.get("id") or res.get("item")) if isinstance(res, dict) \
            else _mat(res) if isinstance(res, str) else None
        inp = _ingredient_ref(d.get("ingredient"), tags)
        if output and inp:
            out.append((output, inp, max(1, round(d.get("cookingtime", 200) / 20))))
    return out


def _mk_craft(output, count, inputs):
    return Recipe(
        kind="crafting", output_id=output, output_amount=count,
        recipe_type=MACHINE, machine=None, time_seconds=None,
        ingredients=[Ingredient("vanilla", ref, amt) for ref, amt in inputs.items()],
        outputs=[], ctor_class="", source_class="minecraft")


def _mk_smelt(output, inp, secs):
    return Recipe(
        kind="machine", output_id=output, output_amount=1,
        recipe_type=None, machine=ELECTRIC_FURNACE, time_seconds=secs,
        ingredients=[Ingredient("vanilla", inp, 1)],
        outputs=[], ctor_class="", source_class="minecraft")


def _from_cache(rows):
    out = []
    for r in rows:
        if r.get("machine") == ELECTRIC_FURNACE:
            out.append(_mk_smelt(r["output_id"], r["ingredients"][0]["ref"], r.get("time_seconds", 10)))
        else:
            out.append(_mk_craft(r["output_id"], r["output_amount"],
                                 {i["ref"]: i["amount"] for i in r["ingredients"]}))
    return out


def extract(bundler_path: Path | None = None):
    path = Path(bundler_path) if bundler_path else DEFAULT_BUNDLER
    if not path.exists():
        # no Mojang jar (e.g. a fresh clone rebuilding for its own addons): fall back to the
        # committed cache so vanilla crafts + smelts aren't lost.
        if CACHE.exists():
            return _from_cache(json.loads(CACHE.read_text(encoding="utf-8")))
        return []
    zf = _open_server(path)
    tags = _load_tags(zf)

    # Crafting (both directions, e.g. diamond_block <-> diamond) + smelting (mapped to the
    # Electric Furnace: sand->glass, raw ore->ingot, cobblestone->stone, food, charcoal...).
    # Nothing is forced "raw"; the solver decides what's producible vs. supplied.
    recipes = [_mk_craft(o, c, i) for o, c, i in _parse(zf, tags)]
    recipes += [_mk_smelt(o, inp, s) for o, inp, s in _parse_smelting(zf, tags)]
    try:                                    # refresh the committed cache for jar-less rebuilds
        rows = []
        for r in recipes:
            row = {"output_id": r.output_id, "output_amount": r.output_amount,
                   "ingredients": [{"ref": i.ref, "amount": i.amount} for i in r.ingredients]}
            if r.machine:
                row["machine"] = r.machine
                row["time_seconds"] = r.time_seconds
            rows.append(row)
        CACHE.write_text(json.dumps(rows, indent=0), encoding="utf-8")
    except OSError:
        pass
    return recipes


if __name__ == "__main__":
    rs = extract()
    print(f"vanilla crafting recipes: {len(rs)}")
    for r in rs:
        if r.output_id in ("DIAMOND_PICKAXE", "STICK", "OAK_PLANKS", "IRON_BLOCK", "DIAMOND"):
            print(f"  {r.output_id} x{r.output_amount} <- "
                  f"{[(i.ref, i.amount) for i in r.ingredients]}")
