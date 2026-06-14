"""Supreme machine-card recognizer.

Cards (CARD_COAL, CARD_STONE, ...) are crafted in the Enhanced Crafting Table from
two materials + a center card, set up parametrically:
    TechGenerator.preSetup(plugin, CARD_X, materialA, materialB)
in the Setup{Simple,Advanced,Ultimate}Card classes. The center card tier comes from
the setup class. The exact per-slot counts are obscured by a 4->6 arg delegation
chain, so ingredient *materials* are exact and counts are a faithful approximation
(materialA x4, materialB x4, center x1) — tune in data/recipes_manual.json if needed.
"""

from __future__ import annotations

import zipfile

from . import bytecode
from .classfile import parse
from .model import Recipe, Ingredient, BUKKIT_MATERIAL, _is_item_ref_desc

CENTER_BY_CLASS = {
    "SetupSimpleCard": "CENTER_CARD_SIMPLE",
    "SetupAdvancedCard": "CENTER_CARD_ADVANCED",
    "SetupUltimateCard": "CENTER_CARD_ULTIMATE",
}

# Resource cores: new CustomCoreRecipe(output, mainMat[, second[, last]]) builds a
# 9-slot recipe (3x main + 3x second + 3x last, each a full 64 stack). Single-material
# cores set main=second=last -> 9 stacks (576) of the base; the 2-material ctor sets
# last=main. The cores are made in the (Electric) Core Fabricator.
STACK = 64
SLOTS_EACH = 3
CORE_RECIPE_CLASS = "CustomCoreRecipe"
CORE_MACHINE = "ElectricCoreFabricator"


def extract_cores(zf: zipfile.ZipFile):
    recipes: list[Recipe] = []
    for name in zf.namelist():
        if not name.endswith(".class"):
            continue
        data = zf.read(name)
        if CORE_RECIPE_CLASS.encode() not in data:
            continue
        cf = parse(data)
        cp = cf.constant_pool
        for m in cf.methods:
            if not m.code:
                continue
            ins = list(bytecode.iter_instructions(m.code))
            for i, instr in enumerate(ins):
                if not (instr.opcode == 0xb7
                        and cp.method_ref(instr.u16())[0].endswith(CORE_RECIPE_CLASS)
                        and cp.method_ref(instr.u16())[1] == "<init>"):
                    continue
                # collect getstatics between NEW CustomCoreRecipe and this <init>
                output = None
                mats = []
                for k in range(i - 1, max(i - 14, -1), -1):
                    op = ins[k].opcode
                    if op == 0xb2:
                        o, f, d = cp.field_ref(ins[k].u16())
                        if o.endswith(BUKKIT_MATERIAL):
                            mats.append(f)
                        elif _is_item_ref_desc(d):
                            output = f
                    elif op == 0xbb and cp.class_name(ins[k].u16()).endswith(CORE_RECIPE_CLASS):
                        break
                mats.reverse()
                if not output or not mats:
                    continue
                main = mats[0]
                second = mats[1] if len(mats) >= 2 else mats[0]
                last = mats[2] if len(mats) >= 3 else mats[0]
                totals: dict[str, int] = {}
                for mat in (main, second, last):
                    totals[mat] = totals.get(mat, 0) + SLOTS_EACH * STACK
                recipes.append(Recipe(
                    kind="machine", output_id=output, output_amount=1,
                    recipe_type=None, machine=CORE_MACHINE, time_seconds=None,
                    ingredients=[Ingredient("vanilla", mat, amt)
                                 for mat, amt in totals.items()],
                    outputs=[Ingredient("slimefun", output, 1)],
                    ctor_class=CORE_RECIPE_CLASS, source_class=cf.name))
    return recipes


def extract(zf: zipfile.ZipFile):
    recipes: list[Recipe] = []
    for name in zf.namelist():
        sn = name.split("/")[-1].replace(".class", "")
        if not (sn.startswith("Setup") and "Card" in sn and name.endswith(".class")):
            continue
        center = CENTER_BY_CLASS.get(sn, "CENTER_CARD_SIMPLE")
        cf = parse(zf.read(name))
        cp = cf.constant_pool
        m = cf.method("setup")
        if not m or not m.code:
            continue
        ins = list(bytecode.iter_instructions(m.code))
        for i, instr in enumerate(ins):
            if instr.opcode not in (0xb8,):
                continue
            owner, mname, _ = cp.method_ref(instr.u16())
            if mname != "preSetup":
                continue
            # the 3 getstatics before the call: card, materialA, materialB
            refs = []  # (kind, ref)
            for k in range(i - 1, max(i - 10, -1), -1):
                op = ins[k].opcode
                if op == 0xb2:
                    o, f, d = cp.field_ref(ins[k].u16())
                    if o.endswith(BUKKIT_MATERIAL):
                        refs.append(("vanilla", f))
                    elif _is_item_ref_desc(d):
                        refs.append(("slimefun", f))
                elif op == 0xb8 and cp.method_ref(ins[k].u16())[1] == "preSetup":
                    break
            refs.reverse()
            if len(refs) < 3:
                continue
            card_id = refs[0][1]
            mats = refs[1:3]
            ings = [Ingredient(k, r, 4) for k, r in mats]
            ings.append(Ingredient("slimefun", center, 1))
            recipes.append(Recipe(
                kind="crafting", output_id=card_id, output_amount=1,
                recipe_type="ENHANCED_CRAFTING_TABLE", machine=None,
                time_seconds=None, ingredients=ings, outputs=[],
                ctor_class="", source_class=cf.name))
    return recipes
