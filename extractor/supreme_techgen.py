"""Supreme Tech Generator — card -> resource recipes.

A Tech Generator turns a *resource card* into a steady stream of its resource from
pure energy (like a chicken in an excitation chamber): a damascus card produces
damascus steel, a sand card produces sand, etc. The card stays in the machine, so
it is emitted as a `fixture` (1 card per generator).

Card -> product pairs come from `SetupSimpleCard.setup` / `SetupAdvancedCard.setup`,
which call `TechGenerator.preSetup(supreme, [tier,] CARD_X, <ingredient(s)>, product)`.
The leading material(s) only build the card's *crafting grid* (e.g. CARD_REDSTONE is
crafted from 8x REDSTONE_BLOCK); the resource the card actually GENERATES is the LAST
material — the single ItemStack handed to `addRecipesToProcess(card, product)` deep in
the preSetup chain. So we emit exactly one recipe, for that last material:
CARD_REDSTONE -> REDSTONE (not REDSTONE_BLOCK), CARD_IRON -> IRON_INGOT (not IRON_BLOCK),
CARD_STONE -> COBBLESTONE, CARD_DIORITE -> DIORITE. (CARD_NETHERITE has two preSetup
calls, so it legitimately yields both NETHERITE_SCRAP and NETHERITE_INGOT.)

Production rate depends on what fills the 4 boost slots (cloning / acceleration /
efficiency cards), which is a per-query choice — so these recipes are flagged
`tech_gen` and carry only the base cycle (1 stack / 30 min, base energy 2000 J/t);
the solver computes the actual rate from the chosen boost config (see optimize.py).
"""

from __future__ import annotations

import zipfile

from . import bytecode
from .classfile import parse
from .model import Recipe, Ingredient

_SETUPS = [
    "com/github/relativobr/supreme/setup/SetupSimpleCard.class",
    "com/github/relativobr/supreme/setup/SetupAdvancedCard.class",
]

# base cycle: 1 stack (64) every 30 min (1800 s) at boost-divisor D=1, base energy
# 2000 J/tick. (Confirmed against the user's in-game calibration.)
STACK = 64
BASE_CYCLE_SECONDS = 1800
BASE_ENERGY = 2000


def _field(cp, idx):
    owner, name, desc = cp.field_ref(idx)
    # card fields are typed SlimefunItemStack too, so key on the owner class first
    if owner.split("/")[-1] in ("SimpleCard", "AdvancedCard"):
        return ("card", name)
    if owner.endswith("Material"):
        return ("vanilla", name)
    if owner.endswith("SlimefunItems") or desc.endswith("SlimefunItemStack;"):
        return ("slimefun", name)
    return ("other", name)


def extract(zf: zipfile.ZipFile):
    """Return (item_defs, recipes). One tech_gen recipe per resource card."""
    recipes: list[Recipe] = []
    seen = set()
    for cls in _SETUPS:
        try:
            cf = parse(zf.read(cls))
        except KeyError:
            continue
        cp = cf.constant_pool
        m = cf.method("setup")
        if not m or not m.code:
            continue
        ins = list(bytecode.iter_instructions(m.code))
        recent: list[tuple[str, str]] = []   # rolling getstatic field refs
        for x in ins:
            op = x.opcode
            if op == 0xb2:  # getstatic
                recent.append(_field(cp, x.u16()))
            elif op == 0xb8:  # invokestatic
                owner, name, _ = cp.method_ref(x.u16())
                if name == "preSetup" and owner.endswith("TechGenerator"):
                    # getstatics for this call, in order: card, then the material(s). The
                    # LAST material is the generated product (-> addRecipesToProcess); any
                    # earlier materials are only the card's crafting ingredients.
                    card = next((r for r in recent if r[0] == "card"), None)
                    mats = [(k, ref) for (k, ref) in recent if k in ("vanilla", "slimefun")]
                    if card and mats:
                        prod_kind, prod_ref = mats[-1]
                        if (card[1], prod_ref) not in seen:
                            seen.add((card[1], prod_ref))
                            recipes.append(Recipe(
                                kind="machine",
                                output_id=prod_ref,
                                output_amount=STACK,
                                recipe_type=None,
                                machine="TECH_GENERATOR",
                                time_seconds=BASE_CYCLE_SECONDS,
                                ingredients=[],          # pure energy -> free producer
                                outputs=[Ingredient(prod_kind, prod_ref, STACK)],
                                ctor_class="TechGenerator",
                                source_class=cls.split("/")[-1],
                                fixtures=[{"id": card[1],
                                           "name": card[1].replace("_", " ").title(),
                                           "product": prod_ref, "category": "card"}],
                            ))
                recent = []
    return [], recipes
