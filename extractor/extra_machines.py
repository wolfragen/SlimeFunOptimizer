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

# Durable tools lose durability per op instead of being consumed 1:1 (Supreme setDamage(+2)
# pattern, same as the Virtual Aquarium). A tool is consumed at DMG_PER_OP / maxDurability
# per cycle, so it needs a steady supply. Non-tools are consumed 1:1.
_DMG_PER_OP = 2
_TOOL_DURABILITY = {"FISHING_ROD": 64, "TRIDENT": 250, "GOLDEN_HOE": 32, "SHEARS": 238,
                    "IRON_SWORD": 250, "DIAMOND_SWORD": 1561, "GOLDEN_SWORD": 32,
                    "WOODEN_SWORD": 59, "STONE_SWORD": 131, "NETHERITE_SWORD": 2031}


def _in_amount(ref):
    """How much of an input is consumed per op: 2/maxDurability for a tool, else 1."""
    d = _TOOL_DURABILITY.get(ref)
    return round(_DMG_PER_OP / d, 8) if d else 1


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


def _kind_of(owner):
    return "vanilla" if owner.endswith("Material") else "slimefun"


def _collector_pairs(cf, ctor_simple, method="registerDefaultRecipes"):
    """[(in_kind, in_ref, out_kind, out_ref)] for `new <ctor>(new ItemStack(IN), new
    ItemStack(OUT), predicate)` recipes — the last two getstatics before each ctor."""
    cp = cf.constant_pool
    m = cf.method(method)
    if not m or not m.code:
        return []
    ins = list(bytecode.iter_instructions(m.code))
    out = []
    for i, x in enumerate(ins):
        if x.opcode == 0xb7 and cp.method_ref(x.u16())[1] == "<init>" \
                and ctor_simple in cp.method_ref(x.u16())[0]:
            gs = [(cp.field_ref(y.u16())[0], cp.field_ref(y.u16())[1])
                  for y in ins[max(0, i - 16):i] if y.opcode == 0xb2]
            if len(gs) >= 2:
                (io, iref), (oo, oref) = gs[-2], gs[-1]
                out.append((_kind_of(io), iref, _kind_of(oo), oref))
    return out


def _mob_collector(zf):
    """Supreme Mob Collector: an item + a nearby mob -> that mob's drop (53 recipes). Like the
    MobTech Collector but the input is a real tool/consumable: GLASS_BOTTLE -> honey/ink/dragon
    breath, SHEARS -> wool/honeycomb, IRON_SWORD -> hostile drops, GOLD_INGOT -> bartering. The
    tool inputs (sword/shears) lose durability (consumed at 2/maxDurability); bottles/ingots 1:1.
    One recipe per (input,mob) pair; the mob predicate is ignored but the recipes stay distinct
    (a chamber makes one drop at a time).

    The machine is keyed by the CLASS name "MobCollector" (not a tier item id) so the graph's
    tier mechanism (machine_tiers.json) fans each recipe across the 3 tiers at their real
    processing speeds — I=1, II=5, III=15 — instead of running every tier at speed 1."""
    cf = _find(zf, "MobCollector")
    if not cf:
        return []
    pairs = _collector_pairs(cf, "MobCollectorMachineRecipe")
    return [_mk("MobCollector", [(ik, iref, _in_amount(iref))], [(ok, oref, 1)],
                seconds=15.0, src="MobCollector")
            for ik, iref, ok, oref in pairs]


_MOBTECH_SECONDS = 15.0          # MobTechCollectorMachineRecipe time=15, machine speed 1.0


def _mobtech_collector(zf):
    """Supreme MobTech Collector: one EMPTY_MOBTECH + a nearby mob -> that mob's data item.

    Each `new MobTechCollectorMachineRecipe(EMPTY_MOBTECH, <MobDTO>, predicate)` is a SEPARATE
    recipe (one machine collects ONE mob, decided by the mob nearby — which we ignore but keep
    the recipes distinct). The input EMPTY_MOBTECH is consumed 1:1 (consumeItem, NOT durability;
    that's the Virtual Aquarium). Output id = "SUPREME_" + the DTO field (SIMPLE_GOLEM ->
    SUPREME_SIMPLE_GOLEM, the 'Common' mob that feeds the cloner-golem chain).

    Keyed by the CLASS name "MobTechCollector" so the graph fans it across the 3 tiers
    (machine_tiers.json). Unlike the regular Mob Collector, these tiers all set
    processingSpeed=1 (only the mob-detection range grows I/II/III=3/6/9), so every tier
    genuinely runs at the same speed."""
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
    return [_mk("MobTechCollector", [("slimefun", "EMPTY_MOBTECH", 1)],
                [("slimefun", "SUPREME_" + f, 1)],
                seconds=_MOBTECH_SECONDS, src="MobTechCollector")
            for f in dict.fromkeys(mobs)]


_FOUNDRY_TIERS = ["FOUNDRY_MACHINE", "FOUNDRY_MACHINE_II", "FOUNDRY_MACHINE_III"]


def _foundry(zf):
    """Supreme Foundry: 1x resource-core A + 3x resource-core B -> an alloy (8 recipes).
    getAllRecipe() lists the real recipe fields (RECIPE_*, excluding the machine-item ones);
    each RECIPE_* is built `new AbstractItemRecipe([coreA, coreB, coreB, coreB], output)`."""
    cf = _find(zf, "Foundry")
    if not cf:
        return []
    cp = cf.constant_pool
    ga = cf.method("getAllRecipe")
    if not ga or not ga.code:
        return []
    wanted = [cp.field_ref(x.u16())[1] for x in bytecode.iter_instructions(ga.code)
              if x.opcode == 0xb2]                # the 8 RECIPE_* the machine actually runs
    defs = {}
    for src in ("<clinit>", "setup", "registerDefaultRecipes"):
        m = cf.method(src)
        if not m or not m.code:
            continue
        ins = list(bytecode.iter_instructions(m.code))
        for i, x in enumerate(ins):
            if x.opcode == 0xb3 and cp.field_ref(x.u16())[1] in wanted:
                gs = [(cp.field_ref(y.u16())[0], cp.field_ref(y.u16())[1])
                      for y in ins[max(0, i - 24):i] if y.opcode == 0xb2]
                if len(gs) >= 5:
                    defs[cp.field_ref(x.u16())[1]] = gs[-5:]   # [A, B, B, B, OUTPUT]
    out = []
    for tier in _FOUNDRY_TIERS:
        for rname, gs in defs.items():
            cores, (oo, oref) = gs[:4], gs[4]
            counts = {}
            for o, f in cores:
                counts[(_kind_of(o), f)] = counts.get((_kind_of(o), f), 0) + 1
            ings = [(k, r, n) for (k, r), n in counts.items()]
            out.append(_mk(tier, ings, [(_kind_of(oo), oref, 1)], seconds=10.0, src="Foundry"))
    return out


_GOLD_PAN_TIERS = ["ELECTRIC_GOLD_PAN", "ELECTRIC_GOLD_PAN_2", "ELECTRIC_GOLD_PAN_3"]


def _electric_gold_pan(zf):
    """Slimefun Electric Gold Pan: GRAVEL -> a weighted drop (FLINT 40 / CLAY_BALL 20 /
    SIFTED_ORE 35 / IRON_NUGGET 5). GoldPan.getGoldPanDrops adds (weight, output) pairs; we
    model the expected yield (amount = weight/total per cycle), one multi-output recipe/tier."""
    cf = _find(zf, "GoldPan")
    if not cf:
        return []
    cp = cf.constant_pool
    m = cf.method("getGoldPanDrops")
    if not m or not m.code:
        return []
    drops, w = [], None
    for x in bytecode.iter_instructions(m.code):
        op = x.opcode
        if op == 0x10:
            w = x.s8()
        elif 0x03 <= op <= 0x08:
            w = op - 0x03
        elif op == 0xb2:
            o, f, d = cp.field_ref(x.u16())
            if w is not None:
                drops.append((_kind_of(o), f, w))
                w = None
    total = sum(c for _, _, c in drops) or 1
    outs = [(k, r, round(c / total, 6)) for k, r, c in drops]
    if not outs:
        return []
    return [_mk(t, [("vanilla", "GRAVEL", 1)], outs, seconds=DEF_SECONDS, src="ElectricGoldPan")
            for t in _GOLD_PAN_TIERS]


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


_GROWER_TIERS = ["BASIC_GROWER", "ADVANCED_GROWER", "INFINITY_GROWER"]
_TREE_TIERS = ["BASIC_TREE", "ADVANCED_TREE", "INFINITY_TREE"]


def _growing_machine(zf):
    """InfinityExpansion Virtual Farms (GROWER) + Tree Growers (TREE): a held plant (fixture,
    INPUT_PLANT, not consumed) generates its crop/wood from energy, like the Virtual Garden.
    Built in Machines.setup as `EnumMap<Material, ItemStack[]>` via put(KEY_plant, [outputs]);
    the GROWER map (seeds->crops) feeds the 3 GROWER tiers, the TREE map (sapling->leaves+logs)
    the 3 TREE tiers. Each map is built once and reused, so the first map of each kind wins."""
    cf = _find(zf, "Machines")
    if not cf:
        return []
    cp = cf.constant_pool
    m = cf.method("setup")
    if not m or not m.code:
        return []
    ins = list(bytecode.iter_instructions(m.code))

    def parse_put(put_i):
        """(key_material, [(kind,ref,amount)...outputs]) for the put() call at index put_i."""
        arr = None
        for k in range(put_i - 1, max(0, put_i - 60), -1):
            if ins[k].opcode == 0xbd and cp.class_name(ins[k].u16()).endswith("ItemStack"):
                arr = k
                break
            if ins[k].opcode in (0xb6, 0xb9) and cp.method_ref(ins[k].u16())[1] == "put":
                break
        if arr is None:
            return None
        key = None
        for k in range(arr - 1, max(0, arr - 4), -1):
            if ins[k].opcode == 0xb2:
                key = cp.field_ref(ins[k].u16())[1]
                break
        outs = _stacks_between(ins, arr, put_i, cp)
        return (key, outs) if key and outs else None

    # collect every EnumMap put across setup, then split by KEY content: saplings/fungi feed the
    # Tree Growers, seeds/crops feed the Virtual Farms (robust vs. which ctor consumes which map).
    crop_map, tree_map = [], []
    for i, x in enumerate(ins):
        if x.opcode in (0xb6, 0xb9) and cp.method_ref(x.u16())[1] == "put":
            pr = parse_put(i)                     # (key_material, [outputs])
            if not pr:
                continue
            key = pr[0]
            (tree_map if key.endswith(("_SAPLING", "_FUNGUS")) else crop_map).append(pr)
    out = []
    for tiers, mp in ((_GROWER_TIERS, crop_map), (_TREE_TIERS, tree_map)):
        for tier in tiers:
            for key, outs in mp:
                out.append(Recipe(
                    kind="machine", output_id=outs[0][1], output_amount=outs[0][2],
                    recipe_type=None, machine=tier, time_seconds=DEF_SECONDS,
                    ingredients=[],                  # energy-only; the plant is a fixture
                    outputs=[Ingredient(k, r, a) for k, r, a in outs],
                    ctor_class="GrowingMachine", source_class="GrowingMachine",
                    fixtures=[{"id": key, "name": key.replace("_", " ").title(),
                               "product": outs[0][1], "category": "plant"}]))
    return out


def _tech_robotic(zf):
    """Supreme Tech Robotic: no statically-extractable recipes -- TechRobotic.addRecipe is
    never called anywhere in the jar (the robotic-mob progression is built from MobTech DTOs
    at runtime), so there's nothing to emit. Annotated in recipe_coverage.KNOWN_OK_CUSTOM."""
    return []


def extract(zf: zipfile.ZipFile, addon: str):
    """Return recipes for the extra custom machines present in this jar."""
    a = addon.lower()
    out = []
    if "dynatech" in a:
        out += _recipes_from_machinerecipe(zf, "SeedPlucker", "SEED_PLUCKER")
    if "slimefun" in a:
        out += _produce_collector(zf)
        out += _oil_pump(zf)
        out += _electric_gold_pan(zf)
    if "infinityexpansion" in a.replace(" ", ""):
        out += _stoneworks(zf)
        out += _resource_synthesizer(zf)
        out += _growing_machine(zf)
    if "supreme" in a:
        out += _mobtech_collector(zf)
        out += _mob_collector(zf)
        out += _foundry(zf)
        out += _tech_robotic(zf)
    return out
