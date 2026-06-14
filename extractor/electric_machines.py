"""Enumerate EVERY electric machine + generator across all jars.

The machine catalog is otherwise built only from items that appear in recipes, so machines
with no item recipe (energy generators, and machines whose recipes we don't model) are
missing from the ban list. Every electric block on Slimefun's energy net implements
`EnergyNetComponent.getEnergyComponentType()` returning CONSUMER (a machine) or GENERATOR
(an energy generator) — inherited through the class hierarchy (AContainer -> CONSUMER,
AGenerator -> GENERATOR, InfinityExpansion AbstractMachineBlock, DynaTech AbstractElectricMachine,
FluffyMachines, ...). We find every class whose resolved type is CONSUMER/GENERATOR (generators
also via the EnergyNetProvider interface), then map it to the machine item it's built with.

Item-mapping handles every instantiation style we've seen:
  * Slimefun / Infinity:  new X(group, SlimefunItems.FOO, RecipeType, recipe[])   -> getstatic at call site
  * DynaTech:             new X(group, Items.FOO.stack(), ...)  where Items.FOO is an ItemWrapper
  * ExtraTools:           new X()  -- item passed to super() INSIDE X's own <init> (ETItems.FOO)
  * inline:               new X(group, new SlimefunItemStack("FOO", ...), ...)
The field NAME (or inline id string) is the Slimefun item id by convention.

NOTE: this only covers blocks on SLIMEFUN'S energy net. Addons with their own power system
(Networks) are invisible here and are handled separately (see networks_items.py).

Returns {item_id: "electric"|"generator"}.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from . import bytecode, classfile

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
DATA = ROOT / "data"
_ENERGY_TYPE = "EnergyNetComponentType"
# field descriptors that denote "this is the machine's item" (the ctor arg before RecipeType)
_ITEM_DESCS = ("SlimefunItemStack;", "CustomItemStack;", "ItemWrapper;")

# Energy machines whose per-variant item ids are GENERATED AT RUNTIME (string concat over a
# material list) and so cannot be enumerated statically. We register the machine TYPE under a
# representative id so it is still bannable. (DynaTech Mineralized Apiary: one block per ore.)
_RUNTIME_ID_MACHINES = {"MINERALIZED_APIARY": ("electric", "DynaTech")}


def _own_energy_type(cf):
    """The EnergyNetComponentType this class's getEnergyComponentType returns, or None."""
    m = cf.method("getEnergyComponentType")
    if not m or not m.code:
        return None
    cp = cf.constant_pool
    for x in bytecode.iter_instructions(m.code):
        if x.opcode == 0xb2:  # getstatic EnergyNetComponentType.X
            o, f, _ = cp.field_ref(x.u16())
            if o.endswith(_ENERGY_TYPE):
                return f
    return None


def _ldc_str(cp, instr):
    if instr.opcode in (0x12, 0x13):
        idx = instr.u8() if instr.opcode == 0x12 else instr.u16()
        kind, v = cp.ldc_value(idx)
        if kind == "string":
            return v
    return None


def _item_at_callsite(ins, start, cp):
    """Scan instructions from a `new X` for the item id passed to X's constructor.

    Returns the item id (a field name or an inline SlimefunItemStack string) or None.
    Stops at the RecipeType arg / the constructor call / a recipe array — so we never
    pick up a recipe ingredient.
    """
    for j in range(start, min(start + 30, len(ins))):
        y = ins[j]
        if y.opcode == 0xbb:                 # new X
            cls = cp.class_name(y.u16())
            if cls.endswith(("SlimefunItemStack", "CustomItemStack")):
                # inline `new SlimefunItemStack("ID", ...)` -> next string is the id
                for k in range(j + 1, min(j + 4, len(ins))):
                    s = _ldc_str(cp, ins[k])
                    if s:
                        return s
                return None
            return None                      # any other `new` ends the arg list
        if y.opcode in (0xb7, 0xbd):         # <init> / anewarray (recipe array) -> done
            return None
        if y.opcode == 0xb2:                 # getstatic
            o, f, d = cp.field_ref(y.u16())
            if o.endswith("RecipeType"):
                return None
            if d.endswith(_ITEM_DESCS):
                return f                     # field name == item id (by convention)
    return None


def _machine_items_in_init(ins, cp):
    """All machine-item ids constructed in a <init> body (handles per-tier branches).

    The machine item is the SlimefunItemStack/ItemWrapper getstatic that is immediately
    followed (as the next constructor arg) by a RecipeType getstatic — recipe ingredients
    are instead stored into the recipe array (aastore), so this never grabs an ingredient.
    """
    out = []
    for i, x in enumerate(ins):
        if x.opcode == 0xbb:                 # inline new SlimefunItemStack("ID", ...)
            if cp.class_name(x.u16()).endswith(("SlimefunItemStack", "CustomItemStack")):
                for k in range(i + 1, min(i + 4, len(ins))):
                    s = _ldc_str(cp, ins[k])
                    if s:
                        out.append(s)
                        break
            continue
        if x.opcode != 0xb2:
            continue
        o, f, d = cp.field_ref(x.u16())
        if not d.endswith(_ITEM_DESCS):
            continue
        for j in range(i + 1, min(i + 8, len(ins))):   # is the NEXT arg a RecipeType?
            y = ins[j]
            if y.opcode == 0xb2:
                o2, _, _ = cp.field_ref(y.u16())
                if o2.endswith("RecipeType"):
                    out.append(f)
                    break
            if y.opcode in (0x53, 0xbb, 0xbd):         # aastore / new / anewarray -> ingredient
                break
    return out


class _Scan:
    """Shared single pass over all jars: class hierarchy, energy types, instantiations."""

    def __init__(self):
        self.supers: dict[str, str] = {}
        self.ifaces: dict[str, list] = {}
        self.own_type: dict[str, str] = {}
        self.init_items: dict[str, list] = {}   # class -> item ids built in its OWN <init>
        self.inst: dict[str, set] = {}          # class -> {item ids at `new` call sites}
        self.new_targets: set = set()
        self.jar_of: dict[str, str] = {}

        for jar in PLUGINS.glob("*.jar"):
            addon = jar.name.split(" - ")[0].split(".jar")[0]
            with zipfile.ZipFile(jar) as zf:
                for name in zf.namelist():
                    if not name.endswith(".class"):
                        continue
                    try:
                        cf = classfile.parse(zf.read(name))
                    except Exception:  # noqa: BLE001
                        continue
                    cp = cf.constant_pool
                    self.supers[cf.name] = cf.super_name or ""
                    self.ifaces[cf.name] = cf.interfaces or []
                    self.jar_of[cf.name] = addon
                    t = _own_energy_type(cf)
                    if t:
                        self.own_type[cf.name] = t
                    ctor = cf.method("<init>")
                    if ctor and ctor.code:
                        items = _machine_items_in_init(
                            list(bytecode.iter_instructions(ctor.code)), cp)
                        if items:
                            self.init_items[cf.name] = items
                    for m in cf.methods:
                        if not m.code:
                            continue
                        ins = list(bytecode.iter_instructions(m.code))
                        for i, x in enumerate(ins):
                            if x.opcode != 0xbb:  # new
                                continue
                            cls = cp.class_name(x.u16())
                            self.new_targets.add(cls)
                            it = _item_at_callsite(ins, i + 1, cp)
                            if it:
                                self.inst.setdefault(cls, set()).add(it)

    def init_chain(self, c):
        seen, out = set(), []
        while c and c not in seen:
            seen.add(c)
            out += self.init_items.get(c, [])
            c = self.supers.get(c, "")
        return out

    def is_generator(self, c):
        seen = set()
        while c and c not in seen:
            seen.add(c)
            if any(i.endswith("EnergyNetProvider") for i in self.ifaces.get(c, [])):
                return True
            c = self.supers.get(c, "")
        return False

    def type_of(self, c):
        seen = set()
        while c and c not in seen:
            if c in self.own_type:
                return self.own_type[c]
            seen.add(c)
            c = self.supers.get(c, "")
        return None

    def items_of(self, c):
        return set(self.inst.get(c, set())) | set(self.init_chain(c))

    def energy_classes(self):
        return {c for c in self.supers
                if self.is_generator(c) or self.type_of(c) in ("CONSUMER", "GENERATOR")}


def extract() -> dict[str, dict]:
    """{item_id: {"category": "electric"|"generator", "addon": <jar>}} for every energy machine."""
    s = _Scan()
    result: dict[str, dict] = {}
    for c in s.energy_classes():
        if c not in s.new_targets:
            continue                          # abstract base, never instantiated
        gen = s.is_generator(c) or s.type_of(c) == "GENERATOR"
        cat = "generator" if gen else "electric"
        addon = s.jar_of.get(c, "Other")
        for item in s.items_of(c):
            if "CAPACITOR" in item:           # energy storage, not a machine
                continue
            cur = result.get(item)
            if cur is None or (cat == "generator" and cur["category"] != "generator"):
                result[item] = {"category": cat, "addon": addon}   # generator role wins
    for mid, (cat, addon) in _RUNTIME_ID_MACHINES.items():
        result.setdefault(mid, {"category": cat, "addon": addon})
    return result


def class_items() -> dict[str, str]:
    """{simple class name -> item id} for energy classes that map to exactly ONE item.

    Lets the catalog/solver key a machine recipe by its real item id (so the icon resolves
    and there's no GROWTH_CHAMBER_MK2_END vs GrowthChamberEndMK2 duplicate). Multi-item /
    tiered classes are intentionally excluded — machine_tiers.json handles those per tier.
    """
    s = _Scan()
    out: dict[str, str] = {}
    for c in s.energy_classes():
        if c not in s.new_targets:
            continue
        items = {i for i in s.items_of(c) if "CAPACITOR" not in i}
        if len(items) == 1:
            out[c.split("/")[-1]] = next(iter(items))
    return out


def audit() -> dict:
    """Diagnostics for trust: which energy classes mapped to an item and which didn't."""
    s = _Scan()
    energy = s.energy_classes()
    instd = [c for c in energy if c in s.new_targets]
    mapped = [c for c in instd if s.items_of(c)]
    gaps = [c for c in instd if not s.items_of(c)]
    items = set()
    ipath = DATA / "items.json"
    if ipath.exists():
        items = {i["id"] for i in json.loads(ipath.read_text(encoding="utf-8"))}
    mapped_ids = sorted({i for c in mapped for i in s.items_of(c)})
    unknown_ids = [i for i in mapped_ids if items and i not in items and "CAPACITOR" not in i]
    return {
        "energy_classes": len(energy),
        "instantiated": len(instd),
        "mapped_classes": len(mapped),
        "gap_classes": sorted(f"{s.jar_of.get(c, '?')}:{c.split('/')[-1]}" for c in gaps),
        "mapped_ids": mapped_ids,
        "ids_not_in_items_json": unknown_ids,
    }


if __name__ == "__main__":
    from collections import Counter
    r = extract()
    print(Counter(v["category"] for v in r.values()), "total", len(r))
    a = audit()
    print(f"\naudit: {a['energy_classes']} energy classes, {a['instantiated']} instantiated, "
          f"{a['mapped_classes']} mapped, {len(a['gap_classes'])} GAPS")
    if a["gap_classes"]:
        for g in a["gap_classes"]:
            print("   GAP", g)
    if a["ids_not_in_items_json"]:
        print("   ids not in items.json:", a["ids_not_in_items_json"])
