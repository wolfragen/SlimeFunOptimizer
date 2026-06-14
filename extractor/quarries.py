"""InfinityExpansion Quarries — mine a weighted-random resource from pure energy.

`Quarries.setup` builds a cumulative `List<Material>` (duplicates = weights, e.g. COAL and
COPPER appear twice) and passes a snapshot to each tier's
`Quarry(group, item, type, recipe[], int speed, int chance, Material[] outputs)`.
`process()` runs every `quarry-options.ticks-per-output` ticks (config, default shown 10);
with probability `1/chance` it mines, picking a UNIFORM-random entry of `outputs[]` and
emitting `speed` of it. Nether-only picks (QUARTZ/NETHERITE_INGOT/NETHERRACK) become
COBBLESTONE unless `output-nether-materials-in-overworld` (default false) — we assume the
overworld. An optional oscillator can boost one material; we model the base (no oscillator).

Per the user's choice we model the EXPECTED (weighted) yield: one multi-output recipe per
tier, output amount = speed * count(material)/N (fractional), at the mining-event rate
events/min = (ticks_per_min / interval) / chance, ticks_per_min = 120 (0.5s/tick).
So Infinity Quarry (speed 64, chance 1, N=16) ≈ 48 diamonds/min + lots of cobble, etc.
"""

from __future__ import annotations

import re
import zipfile
from collections import defaultdict

from . import bytecode
from .classfile import parse
from .model import Recipe, Ingredient

_SETUP = "io/github/mooy1/infinityexpansion/items/quarries/Quarries.class"
_DEFAULT_INTERVAL = 100
TICKS_PER_MIN = 120                      # 1 Slimefun tick = 0.5s
_NETHER = {"QUARTZ", "NETHERITE_INGOT", "NETHERRACK"}


def _iv(x):
    op = x.opcode
    if op == 0x10:
        return x.s8()
    if op == 0x11:
        return x.s16()
    if op in bytecode.ICONST_VALUES:
        return bytecode.ICONST_VALUES[op]
    return None


def _config(zf):
    try:
        txt = zf.read("config.yml").decode("utf-8", "replace")
    except KeyError:
        return _DEFAULT_INTERVAL, False
    # scope to the quarry-options: section (other sections also have ticks-per-output)
    sec = re.search(r"^quarry-options:.*?(?=^\S)", txt, re.S | re.M)
    block = sec.group(0) if sec else txt
    m = re.search(r"ticks-per-output:\s*(\d+)", block)
    interval = int(m.group(1)) if m else _DEFAULT_INTERVAL
    nether = bool(re.search(r"output-nether-materials-in-overworld:\s*true", block))
    return interval, nether


def _read_quarries(cf):
    """[(item_id, speed, chance, [materials])] in tier order (cumulative list snapshots)."""
    cp = cf.constant_pool
    ins = list(bytecode.iter_instructions(cf.method("setup").code))
    quarries, resources = [], []
    pending_new = False
    item = last_mat = None
    for i, x in enumerate(ins):
        op = x.opcode
        if op == 0xbb:                                   # new
            if cp.class_name(x.u16()).endswith("Quarry"):
                pending_new, item = True, None
        elif op == 0xb2:                                 # getstatic
            o, f, d = cp.field_ref(x.u16())
            if o.endswith("Material"):
                last_mat = f
            elif pending_new and item is None and o.endswith("Quarries"):
                item = f
        elif op == 0xb9 and cp.method_ref(x.u16())[1] == "add" and last_mat:
            resources.append(last_mat)
            last_mat = None
        elif op == 0xb7:                                 # invokespecial
            o, n, _ = cp.method_ref(x.u16())
            if n == "<init>" and o.endswith("Quarry"):
                ints = [v for k in range(max(i - 8, 0), i)
                        if (v := _iv(ins[k])) is not None and v != 0]
                if item and len(ints) >= 2:
                    speed, chance = ints[-2], ints[-1]
                    quarries.append((item, speed, chance, list(resources)))
                pending_new = False
    return quarries


def extract(zf: zipfile.ZipFile):
    """Return (item_defs, recipes): one weighted multi-output recipe per quarry tier."""
    try:
        cf = parse(zf.read(_SETUP))
    except KeyError:
        return [], []
    interval, nether_ok = _config(zf)
    recipes: list[Recipe] = []
    for item_id, speed, chance, mats in _read_quarries(cf):
        if not mats or chance <= 0:
            continue
        n = len(mats)
        counts: dict[str, int] = defaultdict(int)
        for mat in mats:
            mat = mat if (nether_ok or mat not in _NETHER) else "COBBLESTONE"
            counts[mat] += 1
        outs = [Ingredient("vanilla", mat, speed * c / n) for mat, c in counts.items()]
        events_per_min = (TICKS_PER_MIN / interval) / chance
        primary = max(outs, key=lambda o: o.amount)
        recipes.append(Recipe(
            kind="machine", output_id=primary.ref, output_amount=1, recipe_type=None,
            machine=item_id, time_seconds=60.0 / events_per_min, ingredients=[],
            outputs=outs, ctor_class="Quarry", source_class="Quarries"))
    return [], recipes
