"""GeneticChickengineering-Reborn recognizer.

This addon builds items through a localization service:
    LocalizationService.getItem("POCKET_CHICKEN", texture)  ; putstatic FIELD
so display names live in `lang/en-US.yml` (under `items.<ID>.name`) rather than in
bytecode. Item ids equal the field names. Crafting recipes for its machines are
already captured by the generic recognizer; this module supplies the item
definitions (id + name + texture). Its chicken *products* are bred/gathered, so
they are correctly treated as raw inputs downstream.
"""

from __future__ import annotations

import re
import zipfile

from . import bytecode
from .classfile import parse
from .model import ItemDef, _is_texture

_LANG = "lang/en-US.yml"


def _parse_lang_names(text: str) -> dict[str, str]:
    """Tiny YAML reader for the `items.<ID>.name` entries (no PyYAML needed)."""
    names: dict[str, str] = {}
    lines = text.splitlines()
    in_items = False
    cur = None
    for line in lines:
        if re.match(r"^items:\s*$", line):
            in_items = True
            continue
        if in_items and re.match(r"^\S", line):   # dedented out of items:
            break
        if not in_items:
            continue
        m = re.match(r"^  ([A-Z0-9_]+):\s*$", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r'^    name:\s*"(.*)"\s*$', line)
        if m and cur:
            names[cur] = m.group(1)
    return names


def _parse_textures(zf) -> dict[str, str]:
    """Recover {id -> texture} from `ldc id; ldc texture; getItem; putstatic` sites."""
    textures: dict[str, str] = {}
    for name in zf.namelist():
        if not name.endswith(".class"):
            continue
        data = zf.read(name)
        if b"getItem" not in data:
            continue
        cf = parse(data)
        cp = cf.constant_pool
        for m in cf.methods:
            if not m.code:
                continue
            ins = list(bytecode.iter_instructions(m.code))
            for i, instr in enumerate(ins):
                if instr.opcode in (0xb6, 0xb9):
                    o, n, d = cp.method_ref(instr.u16())
                    if n != "getItem":
                        continue
                    strs = []
                    for k in range(i - 1, max(i - 8, -1), -1):
                        op = ins[k].opcode
                        if op in (0x12, 0x13):
                            idx = ins[k].u8() if op == 0x12 else ins[k].u16()
                            kind, val = cp.ldc_value(idx)
                            if kind == "string":
                                strs.append(val)
                    strs.reverse()
                    if strs:
                        item_id = strs[0]
                        tex = next((s for s in strs[1:] if _is_texture(s)), None)
                        if tex:
                            textures[item_id] = tex
    return textures


def extract(zf: zipfile.ZipFile):
    """Return (item_defs, recipes); recipes come from the generic pass."""
    try:
        names = _parse_lang_names(zf.read(_LANG).decode("utf-8", "replace"))
    except KeyError:
        return [], []
    textures = _parse_textures(zf)
    items = [ItemDef(item_id, name, 1, "gce", textures.get(item_id))
             for item_id, name in names.items()]
    return items, []
