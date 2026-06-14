"""Machine tiers.

Slimefun/addon machines come in tiers: the SAME AContainer class is instantiated several
times with a different machine item and `setProcessingSpeed(N)` (e.g. Electric Press =
ELECTRIC_PRESS speed 1 + ELECTRIC_PRESS_2 speed 3; Carbon Press I/II/III). Each tier runs
the SAME recipes, faster. We extract, per machine class, the list of (item_id, speed) so
the solver can offer every tier as its own bannable machine and pick the fewest (fastest).

Detection: a builder chain `new <Class>(group, ITEM, ...) ... .setProcessingSpeed(N) ...
.register(...)`. The machine item is the first SlimefunItemStack arg; speed defaults to 1
if not set. Result: {class_simple_name -> [(item_id, speed), ...]}.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from . import bytecode, classfile

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
DATA = ROOT / "data"

_SFIS = "SlimefunItemStack;"
# the machine item arg can be a SlimefunItemStack, a CustomItemStack, or (DynaTech) an
# ItemWrapper field that is then .stack()'d — match all three.
_ITEM_DESCS = ("SlimefunItemStack;", "CustomItemStack;", "ItemWrapper;")


def _intval(x):
    op = x.opcode
    if op == 0x10:
        return x.s8()
    if op == 0x11:
        return x.s16()
    return bytecode.ICONST_VALUES.get(op)


def extract() -> dict[str, list]:
    """{machine class simple name -> [(item_id, speed), ...]} across all jars."""
    families: dict[str, dict[str, int]] = {}
    for jar in PLUGINS.glob("*.jar"):
        with zipfile.ZipFile(jar) as zf:
            for name in zf.namelist():
                if not name.endswith(".class"):
                    continue
                data = zf.read(name)
                if b"setProcessingSpeed" not in data:
                    continue
                try:
                    cf = classfile.parse(data)
                except Exception:  # noqa: BLE001
                    continue
                cp = cf.constant_pool
                for m in cf.methods:
                    if not m.code:
                        continue
                    _scan(list(bytecode.iter_instructions(m.code)), cp, families)
    return {c: sorted(d.items(), key=lambda kv: kv[1]) for c, d in families.items()}


def _scan(ins, cp, families):
    for i, x in enumerate(ins):
        if x.opcode != 0xbb:  # new
            continue
        cls = cp.class_name(x.u16()).split("/")[-1]
        # find the matching <init> for this class; the item is the first SlimefunItemStack
        # getstatic between `new` and <init>; speed is setProcessingSpeed after <init>.
        item = None
        init_idx = None
        j = i + 1
        depth = 0
        after_rt = False   # the machine item is the ctor arg BEFORE the RecipeType
        while j < len(ins) and j < i + 80:
            op = ins[j].opcode
            if op == 0xbb and cp.class_name(ins[j].u16()).split("/")[-1] == cls:
                depth += 1
            elif op == 0xb2 and item is None and not after_rt:
                o, f, d = cp.field_ref(ins[j].u16())
                if o.endswith("RecipeType"):
                    after_rt = True          # stop here — don't grab recipe ingredients
                elif d.endswith(_ITEM_DESCS):
                    item = f                 # field name == item id (incl. DynaTech ItemWrapper)
            elif op == 0xb7:
                o, n, _ = cp.method_ref(ins[j].u16())
                if n == "<init>" and o.split("/")[-1] == cls:
                    if depth == 0:
                        init_idx = j
                        break
                    depth -= 1
            j += 1
        if init_idx is None or item is None:
            continue
        # only a real machine sets its OWN processing speed; stop at register or the next
        # `new` so we never borrow a neighbouring machine's speed (items like AlloyIngot
        # call register() with no setProcessingSpeed and must be excluded).
        speed = None
        k = init_idx + 1
        while k < len(ins) and k < init_idx + 60:
            op = ins[k].opcode
            if op == 0xbb:
                break
            if op in (0xb6, 0xb7, 0xb8, 0xb9):
                o, n, _ = cp.method_ref(ins[k].u16())
                if n == "setProcessingSpeed":
                    speed = _intval(ins[k - 1])
                    break
                if n == "register":
                    break
            k += 1
        if speed:
            families.setdefault(cls, {})[item] = speed


if __name__ == "__main__":
    fam = extract()
    multi = {c: t for c, t in fam.items() if len(t) > 1}
    print(f"{len(fam)} machine classes, {len(multi)} with multiple tiers")
    for c, t in sorted(multi.items()):
        print(f"  {c:<26} {t}")
    (DATA / "machine_tiers.json").write_text(json.dumps(fam, indent=1), encoding="utf-8")
