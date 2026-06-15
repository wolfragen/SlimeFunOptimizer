"""Custom-generation machines the registration pass and addon recognizers miss.

These produce items via bespoke tick logic / non-standard recipe registration, so the
recipe-coverage auditor (lane B) flagged them. One focused recognizer each:

  * SeedPlucker (DynaTech)          crop -> seeds, via `recipes.add(new MachineRecipe(...))`
  * ProduceCollector (Slimefun)     bucket->milk, bowl->mushroom_stew, via `new AnimalProduce`
  * OilPump (Slimefun)              bucket -> oil bucket (single hardcoded recipe)
  * StoneworksFactory (Infinity)    cobble->stone->... chains, from the `Choice` enum arrays
  * ResourceSynthesizer (Infinity)  two inputs -> output, from the recipe triples it's built with

MaterialHive (DynaTech) is intentionally NOT modeled: it takes 64x of an ingot and meters
1 back out (a storage/hive), i.e. a net consumer, never a useful producer. SmartFactory
(FluffyMachines) is a generic auto-crafter handled in machines.py (added to the crafter list).
"""

from __future__ import annotations

import zipfile

from . import bytecode
from .classfile import parse
from .model import Ingredient, Recipe

DEF_SECONDS = 5.0


def _find(zf, simple):
    for n in zf.namelist():
        if n.endswith("/" + simple + ".class") or n.split("/")[-1] == simple + ".class":
            return parse(zf.read(n))
    return None


def _one_stack(ins, i, cp):
    """Parse a `new ItemStack(Material[,n])` / `new CustomItemStack` starting at index i
    (the `new` opcode). Returns ((kind, ref, amount), end_index) or (None, i)."""
    if ins[i].opcode != 0xbb or not cp.class_name(ins[i].u16()).endswith(("ItemStack", "CustomItemStack")):
        return None, i
    kind, ref, amt = None, None, 1
    j = i + 1
    while j < len(ins):
        y = ins[j]
        op = y.opcode
        if op == 0xb2:
            o, f, d = cp.field_ref(y.u16())
            if o.endswith("Material"):
                kind, ref = "vanilla", f
            elif d.endswith(("SlimefunItemStack;", "CustomItemStack;")):
                kind, ref = "slimefun", f
        elif op == 0x10:
            amt = y.s8()
        elif op == 0x11:
            amt = y.s16()
        elif 0x03 <= op <= 0x08:
            amt = op - 0x03
        elif op == 0xb7 and cp.method_ref(y.u16())[1] == "<init>":
            break
        elif op == 0xbb:           # a nested new -> stop (shouldn't happen for these)
            break
        j += 1
    return ((kind, ref, amt) if ref else None), j


def _stacks_between(ins, lo, hi, cp):
    """All ItemStack values (new ItemStack(...) or bare getstatic SF item) in [lo, hi)."""
    out, i = [], lo
    while i < hi:
        x = ins[i]
        if x.opcode == 0xbb and cp.class_name(x.u16()).endswith(("ItemStack", "CustomItemStack")):
            s, end = _one_stack(ins, i, cp)
            if s:
                out.append(s)
            i = max(end, i + 1)
            continue
        if x.opcode == 0xb2:
            o, f, d = cp.field_ref(x.u16())
            if d.endswith("SlimefunItemStack;"):
                out.append(("slimefun", f, 1))
        i += 1
    return out


def _mk(machine, ings, outs, seconds=DEF_SECONDS, src=""):
    return Recipe(kind="machine", output_id=outs[0][1], output_amount=outs[0][2],
                  recipe_type=None, machine=machine, time_seconds=seconds,
                  ingredients=[Ingredient(k, r, a) for k, r, a in ings],
                  outputs=[Ingredient(k, r, a) for k, r, a in outs],
                  ctor_class=machine, source_class=src)


def _recipes_from_machinerecipe(zf, simple, machine, method="registerDefaultRecipes"):
    """Machines that do `recipes.add(new MachineRecipe(time, ItemStack[] in, ItemStack[] out))`."""
    cf = _find(zf, simple)
    if not cf:
        return []
    cp = cf.constant_pool
    m = cf.method(method)
    if not m or not m.code:
        return []
    ins = list(bytecode.iter_instructions(m.code))
    starts = [i for i, x in enumerate(ins)
              if x.opcode == 0xbb and cp.class_name(x.u16()).endswith("MachineRecipe")]
    starts.append(len(ins))
    out = []
    for a, b in zip(starts, starts[1:]):
        stacks = _stacks_between(ins, a, b, cp)
        if len(stacks) >= 2:                  # first = input, last = output (single in/out)
            out.append(_mk(machine, [stacks[0]], [stacks[-1]], src=simple))
    return out


def _produce_collector(zf):
    """Slimefun ProduceCollector: `new AnimalProduce(new ItemStack(in), new ItemStack(out), pred)`."""
    cf = _find(zf, "ProduceCollector")
    if not cf:
        return []
    cp = cf.constant_pool
    m = cf.method("registerDefaultRecipes")
    if not m or not m.code:
        return []
    ins = list(bytecode.iter_instructions(m.code))
    starts = [i for i, x in enumerate(ins)
              if x.opcode == 0xbb and cp.class_name(x.u16()).endswith("AnimalProduce")]
    starts.append(len(ins))
    out = []
    for a, b in zip(starts, starts[1:]):
        stacks = _stacks_between(ins, a, b, cp)
        if len(stacks) >= 2:
            out.append(_mk("PRODUCE_COLLECTOR", [stacks[0]], [stacks[1]], src="ProduceCollector"))
    return out


def _oil_pump(zf):
    """Slimefun Oil Pump: a bucket -> a bucket of oil (single recipe)."""
    if not _find(zf, "OilPump"):
        return []
    return [_mk("OIL_PUMP", [("vanilla", "BUCKET", 1)], [("slimefun", "OIL_BUCKET", 1)],
                src="OilPump")]


def _stoneworks(zf):
    """InfinityExpansion Stoneworks Factory: per `Choice`, parallel Material[] in/out arrays."""
    cf = _find(zf, "StoneworksFactory$Choice")
    if not cf:
        return []
    cp = cf.constant_pool
    m = cf.method("<clinit>")
    if not m or not m.code:
        return []
    ins = list(bytecode.iter_instructions(m.code))
    out, seen = [], set()
    # each Choice ctor: ... anewarray Material {getstatic Material.X via aastore} (inputs),
    # then anewarray Material {...} (outputs), then Choice.<init>. Collect the two arrays per ctor.
    arrays = []          # list of material-name lists, in source order
    cur = None
    for x in ins:
        if x.opcode == 0xbd and cp.class_name(x.u16()).endswith("Material"):
            cur = []
            arrays.append(cur)
        elif x.opcode == 0xb2 and cur is not None:
            o, f, d = cp.field_ref(x.u16())
            if o.endswith("Material"):
                cur.append(f)
        elif x.opcode == 0xb7 and cp.method_ref(x.u16())[1] == "<init>" \
                and cp.method_ref(x.u16())[0].endswith("Choice"):
            cur = None
    # arrays come in pairs (inputs, outputs) per non-empty Choice
    it = iter(arrays)
    for inp, outp in zip(it, it):
        for a, b in zip(inp, outp):
            if (a, b) in seen:
                continue
            seen.add((a, b))
            out.append(_mk("STONEWORKS_FACTORY", [("vanilla", a, 1)], [("vanilla", b, 1)],
                           src="StoneworksFactory"))
    return out


_MOBTECH_TIERS = ["MOB_TECH_COLLECTOR_MACHINE_I", "MOB_TECH_COLLECTOR_MACHINE_II",
                  "MOB_TECH_COLLECTOR_MACHINE_III"]
_MOBTECH_SECONDS = 15.0          # MobTechCollectorMachineRecipe time=15, machine speed 1.0


def _mobtech_collector(zf):
    """Supreme MobTech Collector: one EMPTY_MOBTECH + a nearby mob -> that mob's data item.

    Each `new MobTechCollectorMachineRecipe(EMPTY_MOBTECH, <MobDTO>, predicate)` is a SEPARATE
    recipe (one machine collects ONE mob, decided by the mob nearby — which we ignore but keep
    the recipes distinct). The input EMPTY_MOBTECH is consumed 1:1 (consumeItem, NOT durability;
    that's the Virtual Aquarium). Output id = "SUPREME_" + the DTO field (SIMPLE_GOLEM ->
    SUPREME_SIMPLE_GOLEM, the 'Common' mob that feeds the cloner-golem chain). 3 same-speed tiers.
    """
    cf = _find(zf, "MobTechCollector")
    if not cf:
        return []
    cp = cf.constant_pool
    m = cf.method("registerDefaultRecipes")
    if not m or not m.code:
        return []
    ins = list(bytecode.iter_instructions(m.code))
    mobs = []
    for i, x in enumerate(ins):
        if x.opcode == 0xb7 and cp.method_ref(x.u16())[1] == "<init>" \
                and "MobTechCollectorMachineRecipe" in cp.method_ref(x.u16())[0]:
            for y in ins[max(0, i - 9):i]:    # the SIMPLE_* DTO getstatic before this ctor
                if y.opcode == 0xb2:
                    o, f, d = cp.field_ref(y.u16())
                    if f.startswith("SIMPLE_") and o.endswith("Tech"):
                        mobs.append(f)
                        break
    out = []
    for tier in _MOBTECH_TIERS:
        for f in dict.fromkeys(mobs):
            out.append(_mk(tier, [("slimefun", "EMPTY_MOBTECH", 1)],
                           [("slimefun", "SUPREME_" + f, 1)],
                           seconds=_MOBTECH_SECONDS, src="MobTechCollector"))
    return out


def _resource_synthesizer(zf):
    """InfinityExpansion Resource Synthesizer: built with a SlimefunItemStack[] of (in1,in2,out)
    triples (process() reads recipes[i], [i+1], [i+2]). The array is passed to `.recipes(...)`
    in Machines.setup."""
    cf = _find(zf, "Machines")
    if not cf:
        return []
    cp = cf.constant_pool
    m = cf.method("setup")
    if not m or not m.code:
        return []
    ins = list(bytecode.iter_instructions(m.code))
    # find the call to ResourceSynthesizer.recipes(...) and the anewarray feeding it
    call = next((i for i, x in enumerate(ins)
                 if x.opcode in (0xb6, 0xb9) and cp.method_ref(x.u16())[1] == "recipes"
                 and cp.method_ref(x.u16())[0].endswith("ResourceSynthesizer")), None)
    if call is None:
        return []
    arr = next((i for i in range(call - 1, -1, -1)
                if ins[i].opcode == 0xbd and cp.class_name(ins[i].u16()).endswith("SlimefunItemStack")),
               None)
    if arr is None:
        return []
    items = [cp.field_ref(x.u16())[1] for x in ins[arr:call]
             if x.opcode == 0xb2 and cp.field_ref(x.u16())[2].endswith("SlimefunItemStack;")]
    out = []
    for k in range(0, len(items) - 2, 3):     # (in1, in2, out)
        in1, in2, res = items[k], items[k + 1], items[k + 2]
        ings = [("slimefun", in1, 1)] + ([("slimefun", in2, 1)] if in2 != in1 else [])
        out.append(_mk("RESOURCE_SYNTHESIZER", ings, [("slimefun", res, 1)],
                       seconds=10.0, src="ResourceSynthesizer"))
    return out


def extract(zf: zipfile.ZipFile, addon: str):
    """Return recipes for the extra custom machines present in this jar."""
    a = addon.lower()
    out = []
    if "dynatech" in a:
        out += _recipes_from_machinerecipe(zf, "SeedPlucker", "SEED_PLUCKER")
    if "slimefun" in a:
        out += _produce_collector(zf)
        out += _oil_pump(zf)
    if "infinityexpansion" in a.replace(" ", ""):
        out += _stoneworks(zf)
        out += _resource_synthesizer(zf)
    if "supreme" in a:
        out += _mobtech_collector(zf)
    return out
