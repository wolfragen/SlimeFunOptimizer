"""Recognizer for the Networks addon.

Networks does NOT use Slimefun's energy net (it has its own power system), and it builds every
item through a helper `Theme.themedSlimefunItemStack(String id, ItemStack, Theme, String name,
String[] lore)` rather than `new SlimefunItemStack("ID", ...)`. So the generic model pass and
the energy-net machine enumerator (electric_machines.py) both miss it entirely.

This module reads `NetworksSlimefunItemStacks.<clinit>` and recovers each item's real id (the
`NTW_*` string passed to the helper). It returns ItemDef objects so the catalog has names/icons
for Networks items — most importantly the Network Auto Crafter (`NTW_AUTO_CRAFTER`), which the
solver uses as an auto-crafter (wired in machines.py).
"""

from __future__ import annotations

import re

from . import bytecode, classfile
from .model import ItemDef

_ID_RE = re.compile(r"NTW_[A-Z0-9_]+")
_STACK_HOLDER = "NetworksSlimefunItemStacks"


def _name_from_id(item_id: str) -> str:
    """NTW_AUTO_CRAFTER -> 'Auto Crafter'; NTW_NETWORK_GRID -> 'Network Grid'."""
    return item_id[4:].replace("_", " ").title() if item_id.startswith("NTW_") else item_id


def extract(zf) -> list[ItemDef]:
    target = None
    for name in zf.namelist():
        if name.endswith(_STACK_HOLDER + ".class"):
            target = name
            break
    if not target:
        return []
    cf = classfile.parse(zf.read(target))
    cp = cf.constant_pool
    clinit = cf.method("<clinit>")
    if not clinit or not clinit.code:
        return []

    defs: list[ItemDef] = []
    last_id = None
    for x in bytecode.iter_instructions(clinit.code):
        if x.opcode in (0x12, 0x13):                      # ldc / ldc_w
            idx = x.u8() if x.opcode == 0x12 else x.u16()
            kind, v = cp.ldc_value(idx)
            if kind == "string" and _ID_RE.fullmatch(str(v)):
                last_id = v
        elif x.opcode == 0xb3:                            # putstatic <field>
            _, _, d = cp.field_ref(x.u16())
            if d.endswith("SlimefunItemStack;") and last_id:
                defs.append(ItemDef(id=last_id, name=_name_from_id(last_id),
                                    amount=1, source_class=cf.name))
                last_id = None
    return defs
