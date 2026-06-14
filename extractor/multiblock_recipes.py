"""Multiblock machine processing recipes.

Slimefun multiblock machines (Ore Crusher, Grind Stone, Compressor, Ore Washer,
Smeltery, Juicer, ...) register their *processing* recipes in
`registerDefaultRecipes(List)` as a flat list of alternating input/output
ItemStacks — e.g. Ore Crusher CARBON -> COAL x8, Compressor CHARCOAL -> COAL.
These are separate from the `new SlimefunItem(...)` guide recipes and frequently
have VANILLA outputs, so the item-driven recognizer missed them. The machine /
RecipeType is the class name in UPPER_SNAKE (GrindStone -> GRIND_STONE).
"""

from __future__ import annotations

import zipfile

from . import bytecode
from .classfile import parse
from .model import Recipe, Ingredient, _to_upper_snake, BUKKIT_MATERIAL


def _items_added(ins, cp):
    """Ordered list of (kind, ref, amount) ItemStacks added to the recipe list."""
    items = []
    pending = None   # current item value being built: [kind, ref, amount]
    for x in ins:
        op = x.opcode
        if op == 0xbb:  # new ... -> start a fresh ItemStack value
            name = cp.class_name(x.u16()).split("/")[-1]
            if name.endswith("ItemStack"):
                pending = ["vanilla", None, 1]
        elif op == 0xb2:  # getstatic: a Material or an item field
            owner, f, d = cp.field_ref(x.u16())
            if owner.endswith(BUKKIT_MATERIAL):
                if pending is not None:
                    pending[0], pending[1] = "vanilla", f
                else:
                    pending = ["vanilla", f, 1]  # rare: bare material
            elif d.endswith("SlimefunItemStack;") or owner.endswith("SlimefunItems"):
                # a Slimefun item added directly (not wrapped in new ItemStack)
                if pending is None:
                    items.append(("slimefun", f, 1))
                else:
                    pending[0], pending[1] = "slimefun", f
        elif op in bytecode.ICONST_VALUES or op == 0x10:
            v = bytecode.ICONST_VALUES.get(op, x.s8() if op == 0x10 else None)
            if v is not None and pending is not None and pending[1] is not None:
                pending[2] = v
        elif op in (0xb6, 0xb9):  # invoke add() -> commit the pending value
            _, n, _ = cp.method_ref(x.u16())
            if n in ("add", "addAll") and pending is not None and pending[1]:
                items.append(tuple(pending))
                pending = None
    return items


def extract(zf: zipfile.ZipFile):
    recipes: list[Recipe] = []
    for name in zf.namelist():
        if not name.endswith(".class") or "$" in name.split("/")[-1]:
            continue
        data = zf.read(name)
        if b"registerDefaultRecipes" not in data:
            continue
        cf = parse(data)
        m = cf.method("registerDefaultRecipes")
        if not m or not m.code or not m.descriptor.startswith("(Ljava/util/List;)"):
            continue
        cp = cf.constant_pool
        items = _items_added(list(bytecode.iter_instructions(m.code)), cp)
        if len(items) < 2:
            continue
        machine = _to_upper_snake(cf.name.split("/")[-1])
        # alternating input, output pairs
        for k in range(0, len(items) - 1, 2):
            in_kind, in_ref, _ = items[k]
            out_kind, out_ref, out_amt = items[k + 1]
            recipes.append(Recipe(
                kind="crafting", output_id=out_ref, output_amount=out_amt,
                recipe_type=machine, machine=None, time_seconds=None,
                ingredients=[Ingredient(in_kind, in_ref, 1)],
                outputs=[], ctor_class="", source_class=cf.name))
    return recipes
