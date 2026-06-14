"""InfinityExpansion energy-only generator machines.

Machines like the Void Harvester produce a Slimefun item from energy alone (no item
input). Their output is advertised in getDisplayRecipes(). These are emitted as
"energy-only" recipes (empty ingredient list) so the solver can plan "place N
machines" instead of treating the item as a manual raw input. Vanilla outputs
(quarries -> cobblestone/ores) are skipped — they're already raw materials.
"""

from __future__ import annotations

import zipfile

from . import bytecode
from .classfile import parse
from .model import Recipe, Ingredient

MACHINE_SUPER = "AbstractMachineBlock"


def extract(zf: zipfile.ZipFile):
    recipes: list[Recipe] = []
    for name in zf.namelist():
        if not name.endswith(".class") or "$" in name.split("/")[-1]:
            continue
        data = zf.read(name)
        if b"getDisplayRecipes" not in data:
            continue
        cf = parse(data)
        if cf.super_name.split("/")[-1] != MACHINE_SUPER:
            continue
        m = cf.method("getDisplayRecipes")
        if not m or not m.code:
            continue
        cp = cf.constant_pool
        machine = cf.name.split("/")[-1]
        # Slimefun-item outputs (getstatic of a SlimefunItemStack field). Vanilla
        # Material.* outputs are intentionally ignored.
        outputs = []
        for instr in bytecode.iter_instructions(m.code):
            if instr.opcode == 0xb2:
                owner, fname, desc = cp.field_ref(instr.u16())
                if desc.endswith("SlimefunItemStack;"):
                    outputs.append(fname)
        for out in dict.fromkeys(outputs):   # dedup, keep order
            recipes.append(Recipe(
                kind="machine", output_id=out, output_amount=1,
                recipe_type=None, machine=machine, time_seconds=None,
                ingredients=[],          # empty = energy-only
                outputs=[Ingredient("slimefun", out, 1)],
                ctor_class="", source_class=cf.name))
    return recipes
