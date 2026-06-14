"""Supreme MobTech recognizer (the user's acceptance example lives here).

Supreme's "tech" mobs (golems / bees / zombies) are custom `MobTechGeneric`
objects, not SlimefunItemStacks, so the generic recognizer can't see them. Each
family (IronGolemTech, BeeTech, ZombieTech) defines 7 variants in its <clinit>:
  new MobTechGeneric(id, name, texture, MobTechType) ; putstatic FAMILY.FIELD

Recipes are produced parametrically by MobTech.getRoboticStartRecipe /
getMutationStartRecipe — a per-MobTechType array. The "simple" variant of the
family fills one slot (loaded from a local), and is the only non-static slot.

This module pairs each variant's MobTechType with its recipe array, substitutes
the family's SIMPLE item into the local-loaded slot, and emits crafting recipes
(RecipeType ENHANCED_CRAFTING_TABLE) keyed by the registry id (e.g.
SUPREME_CLONING_GOLEM = "Cloner Robotic Golem").
"""

from __future__ import annotations

import zipfile

from . import bytecode
from .classfile import parse
from .model import RecipeExtractor, Ingredient, Recipe, ItemDef, _is_texture

MOBTECH_CLASS = "com/github/relativobr/supreme/resource/mobtech/MobTech.class"
MOBTECHGENERIC = "MobTechGeneric"
ENHANCED = "ENHANCED_CRAFTING_TABLE"


def _mob_drop(item_id: str) -> str | None:
    """The mob's vanilla signature drop used in its robotic recipe slot
    (MobTech.getItemStackMobTechSimpleRobotic): id contains golem/bee/zombie."""
    u = item_id.upper()
    if "BEE" in u:
        return "HONEYCOMB"
    if "ZOMBIE" in u:
        return "ROTTEN_FLESH"
    if "GOLEM" in u:
        return "POPPY"
    return None


def _start_recipes(cf) -> dict[str, list[Ingredient]]:
    """{MobTechType field name -> recipe ingredients} from the two builder methods."""
    cp = cf.constant_pool
    result: dict[str, list[Ingredient]] = {}
    helper = RecipeExtractor(cf)
    for mname in ("getRoboticStartRecipe", "getMutationStartRecipe"):
        m = cf.method(mname)
        if not m or not m.code:
            continue
        ins = list(bytecode.iter_instructions(m.code))
        regions = helper._find_array_regions(ins)
        for i, instr in enumerate(ins):
            if instr.opcode == 0xb2:
                owner, fname, _ = cp.field_ref(instr.u16())
                if owner.endswith("MobTechType"):
                    nxt = min((r for r in regions if r.start > i),
                              key=lambda r: r.start, default=None)
                    if nxt is not None and fname not in result:
                        result[fname] = nxt.ingredients
    return result


def _gene_recipes(cf) -> dict[str, str]:
    """{MUTATION MobTechType -> Gene item field} from getMutationStartRecipe.

    Mutation mobs are made in the Tech Mutation machine from the family's SIMPLE
    mob + a Gene; getMutationStartRecipe is a switch returning the gene per type:
        getstatic MobTechType.MUTATION_BERSERK ; getstatic SupremeComponents.GENE_BERSERK
    """
    cp = cf.constant_pool
    m = cf.method("getMutationStartRecipe")
    result: dict[str, str] = {}
    if not m or not m.code:
        return result
    ins = list(bytecode.iter_instructions(m.code))
    pending = None
    for instr in ins:
        if instr.opcode == 0xb2:
            owner, fname, _ = cp.field_ref(instr.u16())
            if owner.endswith("MobTechType"):
                pending = fname
            elif pending and "GENE" in fname:
                result[pending] = fname
                pending = None
    return result


def _families(zf) -> list[dict]:
    """Parse every *Tech family's <clinit> into a list of MobTechGeneric variants."""
    variants = []
    for name in zf.namelist():
        if not name.endswith(".class"):
            continue
        data = zf.read(name)
        if MOBTECHGENERIC.encode() not in data:
            continue
        cf = parse(data)
        if not any(f.descriptor.endswith("MobTechGeneric;") for f in cf.fields):
            continue
        cp = cf.constant_pool
        m = cf.method("<clinit>")
        if not m or not m.code:
            continue
        ins = list(bytecode.iter_instructions(m.code))
        family = cf.name.split("/")[-1].replace(".class", "")
        for i, instr in enumerate(ins):
            if instr.opcode != 0xb7:
                continue
            o, n, _ = cp.method_ref(instr.u16())
            if not (n == "<init>" and o.endswith(MOBTECHGENERIC)):
                continue
            # collect the 3 strings + MobTechType before the <init>, putstatic after
            strings, mob_type = [], None
            for k in range(i - 1, max(i - 20, -1), -1):
                op = ins[k].opcode
                if op in (0x12, 0x13):
                    idx = ins[k].u8() if op == 0x12 else ins[k].u16()
                    kind, val = cp.ldc_value(idx)
                    if kind == "string":
                        strings.append(val)
                elif op == 0xb2:
                    owner, fn, _ = cp.field_ref(ins[k].u16())
                    if owner.endswith("MobTechType"):
                        mob_type = fn
                elif op == 0xbb:
                    break
            strings.reverse()
            field = None
            for k in range(i + 1, min(i + 4, len(ins))):
                if ins[k].opcode == 0xb3:
                    field = cp.field_ref(ins[k].u16())[1]
                    break
            if not strings or mob_type is None:
                continue
            reg_id = strings[0]
            texture = next((s for s in strings[1:] if _is_texture(s)), None)
            display = next((s for s in strings[1:] if s and not _is_texture(s)), None)
            variants.append({
                "family": family, "field": field, "id": reg_id,
                "name": display, "texture": texture, "type": mob_type,
                "source": cf.name,
            })
    return variants


def extract(zf: zipfile.ZipFile):
    """Return (item_defs, recipes) for all Supreme MobTech variants."""
    items: list[ItemDef] = []
    recipes: list[Recipe] = []
    try:
        mobtech_cf = parse(zf.read(MOBTECH_CLASS))
    except KeyError:
        return items, recipes
    type_recipe = _start_recipes(mobtech_cf)        # ROBOTIC_* -> ItemStack[] (enhanced table)
    gene_recipe = _gene_recipes(mobtech_cf)         # MUTATION_* -> Gene item (tech mutation)
    variants = _families(zf)

    # family -> SIMPLE variant id (fills the local-loaded slot / mutation input)
    simple_of: dict[str, str] = {}
    for v in variants:
        if v["type"] == "SIMPLE":
            simple_of[v["family"]] = v["id"]

    for v in variants:
        items.append(ItemDef(v["id"], v["name"], 1, v["source"], v["texture"]))
        simple_id = simple_of.get(v["family"])
        if v["type"] in type_recipe:
            # ROBOTIC: a 9-slot Enhanced Crafting Table recipe whose one local-loaded slot
            # (getItemStackMobTechSimpleRobotic) is the mob's VANILLA DROP — golem->POPPY,
            # bee->HONEYCOMB, zombie->ROTTEN_FLESH (NOT the Common Golem; that's mutation-only).
            rec = type_recipe[v["type"]]
            drop = _mob_drop(v["id"])
            ings = []
            filled = False
            for g in rec:
                if g.kind in ("empty", "unknown") and drop and not filled:
                    ings.append(Ingredient("vanilla", drop, 1)); filled = True
                else:
                    ings.append(Ingredient(g.kind, g.ref, g.amount))
            recipes.append(Recipe(
                kind="crafting", output_id=v["id"], output_amount=1,
                recipe_type=ENHANCED, machine=None, time_seconds=None,
                ingredients=ings, outputs=[], ctor_class="", source_class=v["source"]))
        elif v["type"] in gene_recipe and simple_id:
            # MUTATION: SIMPLE mob + Gene -> mutant, in the Tech Mutation machine
            recipes.append(Recipe(
                kind="machine", output_id=v["id"], output_amount=1,
                recipe_type=None, machine="TechMutation", time_seconds=None,
                ingredients=[Ingredient("slimefun", simple_id, 1),
                             Ingredient("slimefun", gene_recipe[v["type"]], 1)],
                outputs=[Ingredient("slimefun", v["id"], 1)],
                ctor_class="", source_class=v["source"]))
        # SIMPLE variants are mob-collected -> raw input (no recipe)
    return items, recipes
