"""Supreme Virtual Garden & Virtual Aquarium — a determining item makes loot from energy.

Both hold a determining item (a fixture, NOT consumed, like a tech-gen card or chicken)
and emit its loot from pure energy:

* Virtual GARDEN: deterministic 1:1. A plant -> its crop/dye. Pairs from
  `VirtualGardenMachineRecipe.<clinit>` (`new AbstractItemRecipe(inMaterial, outMaterial)`),
  e.g. LILY_OF_THE_VALLEY -> WHITE_DYE (43 of them).
* Virtual AQUARIUM: weighted. The determining tool (FISHING_ROD / TRIDENT / GOLDEN_HOE)
  selects a LOOT POOL; each output has a % chance (in the GUI lore "&fGive &bSponge &f2%")
  and the pool sums to 100%. We model the EXPECTED yield: one multi-output recipe per pool,
  each output amount = chance/100 (fractional) per cycle, fixture = the tool.

Rate (shared GenericMachine timing): `cycle_seconds = baseTime / processingSpeed`
(Slimefun MachineRecipe ticks = seconds*2, 1 tick = 0.5s, 1 item/cycle). baseTime from
`config.yml` (garden/aquarium = 15). Tiers I/II/III have processingSpeed 1/5/15 ->
4 / 20 / 60 cycles/min.
"""

from __future__ import annotations

import re
import zipfile

from . import bytecode
from .classfile import parse
from .model import Recipe, Ingredient

_GARDEN_RECIPE = "com/github/relativobr/supreme/machine/recipe/VirtualGardenMachineRecipe.class"
_AQUARIUM_RECIPE = "com/github/relativobr/supreme/machine/recipe/VirtualAquariumMachineRecipe.class"
_SETUP = "com/github/relativobr/supreme/setup/SetupMachines.class"
_DEFAULT_BASE_TIME = 15


def _fkind(cp, idx):
    o, f, d = cp.field_ref(idx)
    if o.endswith("Material"):
        return ("vanilla", f)
    if o.endswith("SlimefunItems") or d.endswith("SlimefunItemStack;"):
        return ("slimefun", f)
    return ("other", f)


def _int_before(ins, i):
    x = ins[i - 1]
    op = x.opcode
    if op == 0x10:
        return x.s8()
    if op == 0x11:
        return x.s16()
    if op in bytecode.ICONST_VALUES:
        return bytecode.ICONST_VALUES[op]
    return None


def _read_garden_pairs(zf):
    cf = parse(zf.read(_GARDEN_RECIPE))
    cp = cf.constant_pool
    pairs, recent = [], []
    for x in bytecode.iter_instructions(cf.method("<clinit>").code):
        if x.opcode == 0xb2:
            recent.append(_fkind(cp, x.u16()))
        elif x.opcode == 0xb7:
            o, n, _ = cp.method_ref(x.u16())
            if n == "<init>" and o.endswith("AbstractItemRecipe") and len(recent) >= 2:
                pairs.append((recent[-2], recent[-1]))
                recent = []
    return pairs


def _read_aquarium_pools(zf):
    """{determining_item: [(out_kind, out_ref, chance%)]} from the GUI loot list."""
    cf = parse(zf.read(_AQUARIUM_RECIPE))
    cp = cf.constant_pool
    items = []          # (ctor, material, lore)
    mat = lore = None
    for x in bytecode.iter_instructions(cf.method("getAllRecipe").code):
        op = x.opcode
        if op == 0xbb:
            mat = lore = None
        elif op == 0xb2:
            o, f, d = cp.field_ref(x.u16())
            if o.endswith("Material"):
                mat = f
        elif op in (0x12, 0x13):
            idx = x.u8() if op == 0x12 else x.u16()
            k, v = cp.ldc_value(idx)
            if k == "string":
                lore = v
        elif op == 0xb7:
            o, n, _ = cp.method_ref(x.u16())
            if n == "<init>" and o.split("/")[-1] in ("CustomItemStack", "ItemStack"):
                items.append((o.split("/")[-1], mat, lore))
                mat = lore = None
    pools: dict[str, list] = {}
    i = 0
    while i < len(items) - 1:
        det, out = items[i], items[i + 1]
        if det[0] == "CustomItemStack" and out[0] == "ItemStack" and det[1] and out[1]:
            m = re.search(r"(\d+)%", det[2] or "")
            chance = int(m.group(1)) if m else None
            pools.setdefault(det[1], []).append(("vanilla", out[1], chance))
            i += 2
        else:
            i += 1
    return pools


def _read_tiers(zf, prefix):
    """[(machine_item_id, processingSpeed)] for tiers whose field starts with prefix."""
    cf = parse(zf.read(_SETUP))
    cp = cf.constant_pool
    ins = list(bytecode.iter_instructions(cf.method("setup").code))
    tiers, cur = [], None
    for i, x in enumerate(ins):
        if x.opcode == 0xb2:
            f = cp.field_ref(x.u16())[1]
            if f.startswith(prefix):
                cur = f
        elif x.opcode == 0xb6:
            if cp.method_ref(x.u16())[1] == "setProcessingSpeed" and cur:
                spd = _int_before(ins, i)
                if spd:
                    tiers.append((cur, spd))
                    cur = None
    return tiers


def _base_time(zf, key):
    try:
        txt = zf.read("config.yml").decode("utf-8", "replace")
    except KeyError:
        return _DEFAULT_BASE_TIME
    m = re.search(rf"{key}:\s*(\d+)", txt)
    return int(m.group(1)) if m else _DEFAULT_BASE_TIME


def extract(zf: zipfile.ZipFile):
    """Return (item_defs, recipes) for both Virtual Garden and Virtual Aquarium."""
    recipes: list[Recipe] = []
    try:
        garden_pairs = _read_garden_pairs(zf)
        garden_tiers = _read_tiers(zf, "VIRTUAL_GARDEN_MACHINE")
        garden_base = _base_time(zf, "base-time-virtual-garden")
    except KeyError:
        garden_pairs = garden_tiers = []
        garden_base = _DEFAULT_BASE_TIME

    for item_id, speed in garden_tiers:
        cycle = garden_base / speed
        for (ik, iref), (ok, oref) in garden_pairs:
            recipes.append(Recipe(
                kind="machine", output_id=oref, output_amount=1, recipe_type=None,
                machine=item_id, time_seconds=cycle, ingredients=[],
                outputs=[Ingredient(ok, oref, 1)],
                ctor_class="VirtualGarden", source_class="VirtualGardenMachineRecipe",
                fixtures=[{"id": iref, "name": iref.replace("_", " ").title(),
                           "product": oref, "category": "plant"}]))

    try:
        pools = _read_aquarium_pools(zf)
        aq_tiers = _read_tiers(zf, "VIRTUAL_AQUARIUM_MACHINE")
        aq_base = _base_time(zf, "base-time-virtual-aquarium")
    except KeyError:
        pools, aq_tiers = {}, []
        aq_base = _DEFAULT_BASE_TIME

    for item_id, speed in aq_tiers:
        cycle = aq_base / speed
        for det, outs in pools.items():
            # expected yield: one cycle makes a random pool item -> amount = chance/100
            out_ings = [Ingredient(k, ref, (ch or 0) / 100.0) for k, ref, ch in outs if ch]
            if not out_ings:
                continue
            primary = max(out_ings, key=lambda o: o.amount)
            recipes.append(Recipe(
                kind="machine", output_id=primary.ref, output_amount=1, recipe_type=None,
                machine=item_id, time_seconds=cycle, ingredients=[],
                outputs=out_ings,
                ctor_class="VirtualAquarium", source_class="VirtualAquariumMachineRecipe",
                fixtures=[{"id": det, "name": det.replace("_", " ").title(),
                           "product": primary.ref, "category": "tool"}]))
    return [], recipes
