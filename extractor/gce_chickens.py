"""GeneticChickengineering-Reborn — excitation chamber chicken "recipes".

An excitation chamber turns a *chicken* into a steady stream of its resource from
pure energy: drop a cobblestone chicken in a chamber and it emits cobblestone, no
item input. Each chicken type is therefore an energy-only recipe of the chamber,
and to run it you need BOTH the chamber AND the chicken — so the chicken is emitted
as a `fixture` (a machine-like held item the solver lists 1:1 with the chambers).

Resource table (`ChickenTypes.<clinit>` -> `TYPES.put(typing, ChickenProduct(...))`):
the integer key is a 6-bit *typing*; the number of set bits is the chicken's tier
(its count of dominant gene pairs). We assume **perfect** chickens (all homozygous),
so for a perfect chicken `resourceTier == DNAStrength == popcount(typing)`. The
chamber's `findNextRecipe` computes
    time_ticks = (BASE(14) + resourceTier - 2*DNAStrength) / processingSpeed
which for a perfect chicken collapses to
    progress_ticks = (14 - popcount(typing)) / speed     (integer division, may be 0)
There is NO floor of 1 in the bytecode. On top of the progress ticks, Slimefun's
`AContainer.tick()` burns two non-productive Slimefun ticks per craft: the tick that
finishes the operation only pushes output + calls `endOperation` (it does NOT start
the next recipe), and the following tick starts a fresh operation without adding any
progress. So the steady-state period is `progress_ticks + 2` ticks. A Slimefun tick
is 0.5s, so
    seconds = (max(0, (14 - tier) // speed) + 2) * 0.5  ->  rate = 60 / seconds per min.
Netherite (typing 0, tier 0) is slowest; feather (typing 63, tier 6) fastest, and
cobblestone (typing 47 = 0b101111, tier 5) matches the user's "5 dominant pairs".
"""

from __future__ import annotations

import zipfile

from . import bytecode
from .classfile import parse
from .model import Recipe, Ingredient

_CLASS = "net/guizhanss/gcereborn/items/chicken/ChickenTypes.class"
_BASE_TIME = 14

# (chamber id [already UPPER_SNAKE], processing speed) from Items setup
# (setProcessingSpeed): tier I = 1, tier II = 2, tier III = 10.
CHAMBERS = [
    ("EXCITATION_CHAMBER", 1),
    ("EXCITATION_CHAMBER_2", 2),
    ("EXCITATION_CHAMBER_3", 10),
]


def _read_types(zf: zipfile.ZipFile) -> list[tuple[int, str, str]]:
    """[(typing, kind, product_ref)] from ChickenTypes.TYPES.put(typing, product)."""
    try:
        data = zf.read(_CLASS)
    except KeyError:
        return []
    cf = parse(data)
    cp = cf.constant_pool
    m = cf.method("<clinit>")
    if not m or not m.code:
        return []
    ins = list(bytecode.iter_instructions(m.code))
    out: list[tuple[int, str, str]] = []
    key = None
    mat = None  # (kind, ref)
    for x in ins:
        op = x.opcode
        v = bytecode.ICONST_VALUES.get(
            op, x.s8() if op == 0x10 else (x.s16() if op == 0x11 else None))
        if v is not None:
            key = v
        elif op == 0xb2:  # getstatic: a Material or a SlimefunItemStack field
            owner, f, d = cp.field_ref(x.u16())
            if owner.endswith("Material"):
                mat = ("vanilla", f)
            elif d.endswith("SlimefunItemStack;"):
                mat = ("slimefun", f)
        elif op in (0xb6, 0xb8, 0xb9):
            if cp.method_ref(x.u16())[1] == "put":
                if key is not None and mat is not None:
                    out.append((key, mat[0], mat[1]))
                key = None
                mat = None
    return out


def extract(zf: zipfile.ZipFile):
    """Return (item_defs, recipes). One energy-only recipe per chicken per chamber."""
    types = _read_types(zf)
    recipes: list[Recipe] = []
    for typing, kind, product in types:
        tier = bin(typing).count("1")
        fixture = {
            "id": f"{product}_CHICKEN",
            "name": product.replace("_", " ").title() + " Chicken",
            "product": product,
        }
        for chamber_id, speed in CHAMBERS:
            # progress ticks (no floor of 1 in the bytecode) + the 2 non-productive
            # AContainer ticks per craft (finish/output tick + restart tick).
            progress_ticks = max(0, (_BASE_TIME - tier) // speed)
            recipes.append(Recipe(
                kind="machine",
                output_id=product,
                output_amount=1,
                recipe_type=None,
                machine=chamber_id,
                time_seconds=(progress_ticks + 2) * 0.5,
                ingredients=[],            # pure energy -> free producer
                outputs=[Ingredient(kind, product, 1)],
                ctor_class="",
                source_class="ChickenTypes",
                fixtures=[fixture],
            ))
    return [], recipes
