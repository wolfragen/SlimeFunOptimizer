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
from .model import Recipe, Ingredient, _int_value

MACHINE_SUPER = "AbstractMachineBlock"


def machineblock_owners(zf: zipfile.ZipFile) -> dict:
    """Map each generic-`Machines` recipe to the MachineBlock that actually owns it.

    InfinityExpansion registers many machines through one shared `Machines.setup()` using a
    generic `MachineBlock`, so the model pass attributes them all to a useless "Machines" id.
    Here we re-derive the real owner: each `new MachineBlock(group, ITEM, ...)` starts a chain
    of `addRecipe(ItemStack output, ItemStack[] input)` calls, so the owning machine of a recipe
    is the nearest preceding `new MachineBlock`'s ITEM. The single ItemStack (before the input
    array) is the OUTPUT; the array element is the INPUT.

    Returns {(output_id, input_ref, input_amount): machine_item_id}.
    """
    target = next((n for n in zf.namelist() if n.endswith("items/machines/Machines.class")), None)
    if not target:
        return {}
    cf = parse(zf.read(target))
    cp = cf.constant_pool
    m = cf.method("setup")
    if not m or not m.code:
        return {}
    ins = list(bytecode.iter_instructions(m.code))
    machines = []        # (index, machine_item) for each `new MachineBlock`
    for i, x in enumerate(ins):
        if x.opcode == 0xbb and cp.class_name(x.u16()).endswith("MachineBlock"):
            it = None
            for j in range(i + 1, min(i + 8, len(ins))):
                if ins[j].opcode == 0xb2 and cp.field_ref(ins[j].u16())[0].endswith("Machines"):
                    it = cp.field_ref(ins[j].u16())[1]
                    break
            machines.append((i, it))

    def owner(idx):
        best = None
        for i, it in machines:
            if i < idx:
                best = it
            else:
                break
        return best

    out = {}
    for i, x in enumerate(ins):
        if x.opcode not in (0xb6, 0xb9) or cp.method_ref(x.u16())[1] != "addRecipe":
            continue
        arr = None
        for k in range(i - 1, max(0, i - 25), -1):
            if ins[k].opcode == 0xbd:      # anewarray (the input array)
                arr = k
                break
            if ins[k].opcode in (0xb6, 0xb9) and cp.method_ref(ins[k].u16())[1] == "addRecipe":
                break
        if arr is None:
            continue
        o_ref = None                       # output = single ItemStack before the array
        for k in range(arr - 1, max(0, arr - 8), -1):
            if ins[k].opcode == 0xb2:
                o_ref = cp.field_ref(ins[k].u16())[1]
                break
        i_ref = i_amt = None               # input = element of the array
        for k in range(arr + 1, i):
            if ins[k].opcode == 0xb2 and i_ref is None:
                i_ref = cp.field_ref(ins[k].u16())[1]
            elif i_ref is not None:
                v = _int_value(cp, ins[k])
                if v is not None:
                    i_amt = v
                    break
        if o_ref and i_ref:
            out[(o_ref, i_ref, i_amt or 1)] = owner(i)
    return out


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
