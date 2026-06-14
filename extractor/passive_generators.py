"""Passive item generators — machines that emit an item from pure energy.

Some machines aren't `registerRecipe` driven: they override the tick/process loop to
push a fixed item every tick. InfinityExpansion's MaterialGenerator is the clearest
case — the cobblestone / obsidian generators:
    new MaterialGenerator(group, ITEM, type, recipe).material(M).speed(N).energyPerTick(E)
`process()` pushes `new ItemStack(material, speed)` every machine tick, so the rate is
`speed` items per tick. With 1 Slimefun tick = 0.5s that is `speed * 120` items/min —
e.g. Infinity Cobble Generator (speed 64) makes 64 cobblestone/tick = 7680/min, far
faster than a cobblestone chicken. We model each generator as its own machine (the
generator item id) producing `speed` of its material per 0.5s, from energy alone.

(VoidHarvester is already covered by infinity_machines; the cobble/obsidian generators
were the gap the generic recognizer missed.)
"""

from __future__ import annotations

import zipfile

from . import bytecode
from .classfile import parse
from .model import Recipe, Ingredient

_MACHINES_CLASS = "io/github/mooy1/infinityexpansion/items/machines/Machines.class"
TICK_SECONDS = 0.5   # 1 Slimefun tick; process() runs once per tick


def _int_before(ins, j, cp):
    x = ins[j - 1]
    op = x.opcode
    if op == 0x10:
        return x.s8()
    if op == 0x11:
        return x.s16()
    if op in bytecode.ICONST_VALUES:
        return bytecode.ICONST_VALUES[op]
    if op in (0x12, 0x13):
        idx = x.u8() if op == 0x12 else x.u16()
        k, v = cp.ldc_value(idx)
        return v if k == "int" else None
    return None


def extract(zf: zipfile.ZipFile):
    """Return (item_defs, recipes) for InfinityExpansion MaterialGenerators."""
    try:
        cf = parse(zf.read(_MACHINES_CLASS))
    except KeyError:
        return [], []
    cp = cf.constant_pool
    m = cf.method("setup")
    if not m or not m.code:
        return [], []
    ins = list(bytecode.iter_instructions(m.code))
    recipes: list[Recipe] = []
    for i, x in enumerate(ins):
        if x.opcode != 0xbb:  # new
            continue
        if not cp.class_name(x.u16()).endswith("MaterialGenerator"):
            continue
        # the machine item is the first Machines.* field after `new` (the 2nd ctor arg)
        item = material = speed = None
        j = i + 1
        while j < len(ins):
            y = ins[j]
            op = y.opcode
            if op == 0xb2:  # getstatic
                o, f, d = cp.field_ref(y.u16())
                if item is None and o.endswith("Machines"):
                    item = f
                elif o.endswith("Material") and material is None:
                    # only the material set via .material() matters; capture the one
                    # immediately preceding the .material() call below
                    pass
            elif op == 0xb6:  # invokevirtual on the builder
                _, n, _ = cp.method_ref(y.u16())
                if n == "material":
                    mo = ins[j - 1]
                    if mo.opcode == 0xb2:
                        material = cp.field_ref(mo.u16())[1]
                elif n == "speed":
                    speed = _int_before(ins, j, cp)
                elif n == "register":
                    break
            elif op == 0xbb and cp.class_name(y.u16()).endswith("MaterialGenerator"):
                break  # next generator started
            j += 1
        if item and material and speed:
            recipes.append(Recipe(
                kind="machine",
                output_id=material,
                output_amount=speed,
                recipe_type=None,
                machine=item,                      # each generator tier = its own machine
                time_seconds=TICK_SECONDS,         # speed items every tick
                ingredients=[],                    # pure energy -> free producer
                outputs=[Ingredient("vanilla", material, speed)],
                ctor_class="MaterialGenerator",
                source_class="Machines",
            ))
    return [], recipes
