"""Supreme gear / fabricator recognizer (Thornium & magical gear).

Tiered gear is registered in *Fabricator classes' getAllRecipe() as:
    new AbstractItemRecipe(ItemTier.getMagicRecipe(BASE), OUTPUT)
where ItemTier.get<Tier>Recipe(base) is a 9-slot template of static tier materials
plus the base item in a couple of slots. The fabricator class is the machine.
"""

from __future__ import annotations

import zipfile

from . import bytecode
from .classfile import parse
from .model import Recipe, Ingredient, BUKKIT_MATERIAL, _is_item_ref_desc, _int_value

ITEMTIER = "util/ItemTier"
ABSTRACT_RECIPE = "AbstractItemRecipe"
# aload_0..3 and aload <idx> all denote the base-item parameter of a get*Recipe template
_ALOAD = {0x2a, 0x2b, 0x2c, 0x2d, 0x19}


def _tier_templates(zf):
    """{get*Recipe method name -> [slot,...]} where slot is ('base',) or ('item',kind,ref)."""
    try:
        cf = parse(zf.read("com/github/relativobr/supreme/util/ItemTier.class"))
    except KeyError:
        return {}
    cp = cf.constant_pool
    out = {}
    for m in cf.methods:
        if not (m.name.endswith("Recipe") and m.code):
            continue
        ins = list(bytecode.iter_instructions(m.code))
        # walk the array fill: dup; <index>; <value>; aastore
        slots = {}
        j = 0
        while j < len(ins) and ins[j].opcode != 0xbd:
            j += 1
        idx = None
        k = j + 1
        while k < len(ins):
            op = ins[k].opcode
            if op == 0x59:
                idx = None
            elif idx is None and (_int_value(cp, ins[k]) is not None):
                idx = _int_value(cp, ins[k])
            elif op in _ALOAD and idx is not None:
                slots[idx] = ("base",)
            elif op == 0xb2 and idx is not None:
                owner, f, d = cp.field_ref(ins[k].u16())
                kind = "vanilla" if owner.endswith(BUKKIT_MATERIAL) else "slimefun"
                slots[idx] = ("item", kind, f)
            elif op == 0xb1 or (op == 0xb0):
                break
            k += 1
        out[m.name] = [slots[i] for i in sorted(slots)]
    return out


def extract(zf: zipfile.ZipFile):
    recipes: list[Recipe] = []
    templates = _tier_templates(zf)
    for name in zf.namelist():
        if not name.endswith("Fabricator.class"):
            continue
        cf = parse(zf.read(name))
        cp = cf.constant_pool
        m = cf.method("getAllRecipe")
        if not m or not m.code:
            continue
        machine = cf.name.split("/")[-1]
        ins = list(bytecode.iter_instructions(m.code))
        for i, instr in enumerate(ins):
            if not (instr.opcode == 0xb7
                    and cp.method_ref(instr.u16())[0].endswith(ABSTRACT_RECIPE)
                    and cp.method_ref(instr.u16())[1] == "<init>"):
                continue
            # scan back: OUTPUT getstatic (closest), tier method, base getstatic
            output = tier = base = None
            for k in range(i - 1, max(i - 14, -1), -1):
                op = ins[k].opcode
                if op == 0xb2:
                    owner, f, d = cp.field_ref(ins[k].u16())
                    if _is_item_ref_desc(d):
                        if output is None:
                            output = f
                        elif base is None and tier is not None:
                            base = f
                elif op == 0xb8:
                    o, n, _ = cp.method_ref(ins[k].u16())
                    if o.endswith(ITEMTIER) and n.endswith("Recipe"):
                        tier = n
                elif op == 0xbb and cp.class_name(ins[k].u16()).endswith(ABSTRACT_RECIPE):
                    break
            if not output or not tier or tier not in templates or not base:
                continue
            ings = []
            for slot in templates[tier]:
                if slot[0] == "base":
                    ings.append(Ingredient("slimefun", base, 1))
                else:
                    ings.append(Ingredient(slot[1], slot[2], 1))
            recipes.append(Recipe(
                kind="machine", output_id=output, output_amount=1,
                recipe_type=None, machine=machine, time_seconds=None,
                ingredients=ings, outputs=[Ingredient("slimefun", output, 1)],
                ctor_class=ABSTRACT_RECIPE, source_class=cf.name))
    return recipes
