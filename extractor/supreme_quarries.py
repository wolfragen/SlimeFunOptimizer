"""Supreme Quarries — mine a weighted-random item from pure energy.

Seven tiers (STONE/COAL/IRON/GOLD/DIAMOND/THORNIUM/SUPREME_NUGGETS_QUARRY) are
registered in `SetupSupremeQuarry.setup` as `AbstractQuarry`s; each gets its loot
table from `ItemUtil.getOutputQuarry(item)`, which reads the `quarry-custom-output`
section of the bundled `config.yml` keyed by the quarry's id (lower-cased). The
generic recognizer caught the *crafting* recipes (AbstractQuarry is a SlimefunItem,
ENHANCED_CRAFTING_TABLE) but NOT the production: the energy->item output lives in
config, not in any registration call.

Mechanics (decoded from AbstractQuarry.tick / ItemUtil.getItemQuarry / getRandomInt):
- The block ticks on the Slimefun ticker but only produces every
  `custom-ticker-delay * delaySpeed + 1` Slimefun ticks (delaySpeed defaults to 1
  and `setDelaySpeed` is never called; the +1 is the threshold counter resetting
  from 0). config default `custom-ticker-delay: 2` -> one item every 3 ticks.
- Each production fires ONE item, picked by a cumulative weighted draw over the
  loot table: `r = floor(random*100)+1` in [1,100], the entry whose cumulative
  chance range contains r is emitted (so the per-entry `chance` is its percent and
  the entries sum to 100). The amount is always 1 — `chance` is a probability, not
  a count. (`limit-production-quarry` defaults to false, so chances aren't doubled.)

Per the InfinityExpansion-quarry convention ([[quarries]]) we model the EXPECTED
yield: one multi-output recipe per tier, each output amount = chance/100 per cycle,
at events/min = TICKS_PER_MIN / (custom-ticker-delay * delaySpeed + 1).
"""

from __future__ import annotations

import re
import zipfile

from .classfile import parse
from . import bytecode
from .model import Recipe, Ingredient

_CLINIT = "com/github/relativobr/supreme/machine/SupremeQuarry.class"
_DEFAULT_TICKER_DELAY = 2
_DELAY_SPEED = 1                          # AbstractQuarry default; setDelaySpeed unused
TICKS_PER_MIN = 120                       # 1 Slimefun tick = 0.5s (matches [[quarries]])

# config `quarry-custom-output` gives Slimefun item IDs; the graph is keyed by the
# static FIELD NAME (see [[data-extraction-approach]]). These few diverge from their id
# (Supreme strips its "SUPREME_" id prefix for the field; OIL_BUCKET is a base-SF rename).
# Verified against data/items.json; ids not listed here already equal their field (SULFATE).
_SF_ID_TO_FIELD = {
    "SUPREME_THORNIUM_BIT": "THORNIUM_BIT",
    "SUPREME_SUPREME_NUGGET": "SUPREME_NUGGET",
    "BUCKET_OF_OIL": "OIL_BUCKET",
}


def _read_quarry_ids(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(field_name, item_id)] for each quarry item, in declaration order.

    SupremeQuarry.<clinit> does `new SupremeItemStack(<id>, ...)` then `putstatic` to
    the tier's field; the config section is keyed by `item_id.toLowerCase()`.
    """
    cf = parse(zf.read(_CLINIT))
    cp = cf.constant_pool
    out: list[tuple[str, str]] = []
    capturing = False
    item_id: str | None = None
    for x in bytecode.iter_instructions(cf.method("<clinit>").code):
        op = x.opcode
        if op == 0xbb:                                   # new
            if cp.class_name(x.u16()).endswith("SupremeItemStack"):
                capturing, item_id = True, None
        elif op in (0x12, 0x13) and capturing and item_id is None:   # first ldc string = id
            idx = x.u8() if op == 0x12 else x.u16()
            k, v = cp.ldc_value(idx)
            if k == "string":
                item_id = v
        elif op == 0xb3:                                 # putstatic
            o, f, d = cp.field_ref(x.u16())
            if capturing and item_id and d.endswith("SlimefunItemStack;"):
                out.append((f, item_id))
                capturing, item_id = False, None
    return out


def _read_loot(zf: zipfile.ZipFile) -> dict[str, list[tuple[str, int, bool]]]:
    """{config_key: [(item, chance, is_slimefun)]} from config.yml quarry-custom-output."""
    try:
        txt = zf.read("config.yml").decode("utf-8", "replace")
    except KeyError:
        return {}
    sec = re.search(r"^quarry-custom-output:\s*$.*?(?=^\S)", txt, re.S | re.M)
    if not sec:
        return {}
    loot: dict[str, list[tuple[str, int, bool]]] = {}
    cur_key = None
    item = chance = is_sf = None

    def flush():
        if cur_key is not None and item is not None and chance is not None:
            loot.setdefault(cur_key, []).append((item, chance, bool(is_sf)))

    for line in sec.group(0).splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        s = line.strip()
        if indent == 2 and s.endswith(":"):              # a quarry id block
            flush(); item = chance = is_sf = None
            cur_key = s[:-1]
        elif indent == 4 and s.endswith(":"):            # a numbered entry
            flush(); item = chance = is_sf = None
        elif indent >= 6:
            if s.startswith("item:"):
                item = s.split(":", 1)[1].strip().strip('"')
            elif s.startswith("chance:"):
                chance = int(s.split(":", 1)[1].strip())
            elif s.startswith("is-slimefun:"):
                is_sf = s.split(":", 1)[1].strip() == "true"
    flush()
    return loot


def _ticker_delay(zf: zipfile.ZipFile) -> int:
    try:
        txt = zf.read("config.yml").decode("utf-8", "replace")
    except KeyError:
        return _DEFAULT_TICKER_DELAY
    m = re.search(r"^\s*custom-ticker-delay:\s*(\d+)", txt, re.M)
    return int(m.group(1)) if m else _DEFAULT_TICKER_DELAY


def _ingredient(item: str, is_sf: bool) -> Ingredient:
    if is_sf:
        return Ingredient("slimefun", _SF_ID_TO_FIELD.get(item, item), 0)
    return Ingredient("vanilla", item, 0)


def extract(zf: zipfile.ZipFile):
    """Return (item_defs, recipes): one weighted multi-output recipe per quarry tier."""
    try:
        quarries = _read_quarry_ids(zf)
    except KeyError:
        return [], []
    loot = _read_loot(zf)
    period = _ticker_delay(zf) * _DELAY_SPEED + 1
    events_per_min = TICKS_PER_MIN / period
    recipes: list[Recipe] = []
    for field, item_id in quarries:
        entries = loot.get(item_id.lower())
        if not entries:
            continue
        outs = [Ingredient(ing.kind, ing.ref, chance / 100.0)
                for item, chance, is_sf in entries
                if (ing := _ingredient(item, is_sf)).ref and chance > 0]
        if not outs:
            continue
        primary = max(outs, key=lambda o: o.amount)
        recipes.append(Recipe(
            kind="machine", output_id=primary.ref, output_amount=1, recipe_type=None,
            machine=field, time_seconds=60.0 / events_per_min, ingredients=[],
            outputs=outs, ctor_class="AbstractQuarry", source_class="SetupSupremeQuarry"))
    return [], recipes
