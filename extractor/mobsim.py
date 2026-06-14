"""InfinityExpansion — Mob Simulation Chamber + Mob Data Cards.

A *mob data card* (e.g. ZOMBIE_DATA_CARD) placed in a Mob Simulation Chamber makes the
chamber simulate that mob and spit out its drops — exactly like a resource card in a Tech
Generator, but:
  * the drops are a WEIGHTED random set (RandomizedSet): each cycle outputs ONE drop chosen
    by weight, so a drop's rate is (weight / total_weight) * amount per cycle. NOTE addDrop
    stores `1/passed_weight`, so the number in MobData.setup is INVERSE rarity (higher =
    rarer): SHEEP passes wool=1, mutton=1, pink_wool=10000 -> real weights 1, 1, 0.0001 ->
    wool & mutton ~50% each, pink wool ~0%. We invert when extracting.
  * energy drain per tick = the chamber's BASE energy + the card tier's energy.

Mechanic (from MobSimulationChamber.tick / MobData.setup, verified against the jar):
  * one output every `ticks-per-output` Slimefun ticks (config default 20; 1 SF tick = 0.5 s)
    -> 1 pick / 10 s -> 6 cycles/min per card.
  * base chamber energy 150 J/t; card energy by tier (PASSIVE 75 ... BOSS 9000), read from
    MobDataTier so it stays correct if the addon changes.

Each card becomes ONE energy-only chamber recipe whose `outputs` are the drops at their
expected per-cycle amount (weight/total * amount); the card itself is a `fixture`
(category "card") — 1 per chamber, or up to 64 when the UI's "stackable cards" is on
(handled in the solver, not here). Drops that are vanilla materials are emitted as vanilla
outputs; Slimefun/addon drops (VOID_DUST, COMPRESSED_CARBON, ...) as slimefun.
"""

from __future__ import annotations

import re
import zipfile

from . import bytecode
from .classfile import parse
from .model import Ingredient, ItemDef, Recipe

PKG = "io/github/mooy1/infinityexpansion/items/mobdata/"
CHAMBER_ID = "MOB_SIMULATION_CHAMBER"
SF_TICK_SECONDS = 0.5          # 1 Slimefun tick = 0.5 s (project-wide calibration)
DEFAULT_INTERVAL = 20          # config.yml mob-simulation-options.ticks-per-output
BASE_ENERGY = 150              # chamber base J/tick (MobData.setup chamber ctor)
# addDrop stores 1/weight, so a big passed number = a RARE roll (PINK_WOOL 10000 -> ~0.005%,
# DRAGON_EGG 1e6 -> ~0%). Those rare signature drops aren't a realistic way to make the item,
# so only emit a drop as a production output if its real roll-share is at least this; rarer
# rolls stay in the card's `drops` info list but aren't offered as a producer.
MIN_DROP_SHARE = 0.01          # 1%

_FCONST = {0x0b: 0.0, 0x0c: 1.0, 0x0d: 2.0}


def _ldc(cp, x):
    try:
        return cp.ldc_value(x.u8() if x.opcode == 0x12 else x.u16())
    except Exception:
        return None


def _float(cp, x):
    if x.opcode in _FCONST:
        return _FCONST[x.opcode]
    v = _ldc(cp, x)                         # ldc of a numeric constant -> ('num', value)
    if isinstance(v, tuple) and v[0] == "num":
        return float(v[1])
    return None


def _int(x):
    op = x.opcode
    if op == 0x10:
        return x.s8()
    if op == 0x11:
        return x.s16()
    if 0x03 <= op <= 0x08:
        return op - 0x03
    return None


def _str(v):
    return v[1] if isinstance(v, tuple) and v[0] == "string" else None


def _strip(s):
    return re.sub(r"[&§][0-9a-fk-orA-FK-OR]", "", s).strip()


def _tier_energies(zf) -> dict[str, int]:
    """{TIER -> energy J/tick} from MobDataTier(<name>, ord, xp, energy, material)."""
    cf = parse(zf.read(PKG + "MobDataTier.class"))
    cp = cf.constant_pool
    ins = list(bytecode.iter_instructions(cf.method("<clinit>").code))
    out, ints = {}, []
    for i, x in enumerate(ins):
        v = _int(x)
        if v is not None:
            ints.append(v)
        elif x.opcode == 0xbb and cp.class_name(x.u16()).endswith("MobDataTier"):
            ints = []
        elif x.opcode == 0xb3:                      # putstatic <TIER>
            _, nm, d = cp.field_ref(x.u16())
            if d.endswith("MobDataTier;") and len(ints) >= 2:
                # pushed ints after `new`: ordinal, xp, energy  -> energy is the last
                out[nm] = ints[-1]
            ints = []
    return out


def _config_interval(zf) -> int:
    for n in zf.namelist():
        if n.endswith("config.yml"):
            txt = zf.read(n).decode("utf-8", "replace")
            m = re.search(r"ticks-per-output:\s*(\d+)", txt)
            if m:
                return int(m.group(1))
    return DEFAULT_INTERVAL


def _card_ids(zf) -> dict[str, tuple[str, str]]:
    """MobData field -> (card_item_id, display_name) from MobDataCard.create(name, tier)."""
    cf = parse(zf.read(PKG + "MobData.class"))
    cp = cf.constant_pool
    ins = list(bytecode.iter_instructions(cf.method("<clinit>").code))
    out = {}
    for i, x in enumerate(ins):
        if x.opcode == 0xb8 and cp.method_ref(x.u16())[1] == "create":
            name = None
            for y in ins[max(0, i - 6):i]:
                if y.opcode in (0x12, 0x13):
                    name = _str(_ldc(cp, y)) or name
            fld = None
            for y in ins[i + 1:i + 3]:
                if y.opcode == 0xb3:
                    fld = cp.field_ref(y.u16())[1]
                    break
            if fld and name:
                cid = name.upper().replace(" ", "_") + "_DATA_CARD"
                out[fld] = (cid, "&b" + name + " Data Card")
    return out


def _base_items(zf) -> list[ItemDef]:
    """Chamber / infuser / empty-card item defs (id + display name) from MobData.<clinit>."""
    cf = parse(zf.read(PKG + "MobData.class"))
    cp = cf.constant_pool
    ins = list(bytecode.iter_instructions(cf.method("<clinit>").code))
    defs = []
    for i, x in enumerate(ins):
        if x.opcode == 0xbb and cp.class_name(x.u16()).endswith("SlimefunItemStack"):
            strs = [s for s in (_str(_ldc(cp, y)) for y in ins[i + 1:i + 12]) if s]
            if len(strs) >= 2:
                defs.append(ItemDef(id=strs[0], name=strs[1], amount=1, source_class="MobData"))
    return defs


def extract(zf: zipfile.ZipFile):
    """Return (item_defs, recipes) for the Mob Simulation Chamber and its data cards."""
    try:
        tiers = _tier_energies(zf)
        card_ids = _card_ids(zf)
    except KeyError:
        return [], []

    interval = _config_interval(zf)
    cycle_seconds = interval * SF_TICK_SECONDS          # default 20 * 0.5 = 10 s

    cf = parse(zf.read(PKG + "MobData.class"))
    cp = cf.constant_pool
    ins = list(bytecode.iter_instructions(cf.method("setup").code))

    item_defs = _base_items(zf)               # chamber / infuser / empty card
    for cid, cname in card_ids.values():      # the mob data cards themselves
        item_defs.append(ItemDef(id=cid, name=cname, amount=1, source_class="MobDataCard"))
    recipes: list[Recipe] = []

    cur = None                 # current card field ("?" until its MobData.<MOB> getstatic seen)
    tier = None                # current MobDataTier name
    drops: list = []           # (kind, ref, amount, weight)
    pending: list = []         # operands pushed for the NEXT addDrop call
    in_drops = False           # True once MobDataCard.<init> ran (past the crafting-recipe array)

    def flush():
        nonlocal cur, tier, drops, in_drops
        if cur and drops and cur in card_ids:
            cid, cname = card_ids[cur]
            total = sum(w for _, _, _, w in drops) or 1.0
            # expected items of this drop per cycle = (weight/total) * amount, computed against
            # the FULL weight total (faithful in-game rate). Only roll-shares >= MIN_DROP_SHARE
            # are emitted as production outputs; negligible side-rolls are kept in the card's
            # `drops` info list but not offered as a way to make that item.
            outs = [Ingredient(kind, ref, round(w / total * amt, 8))
                    for kind, ref, amt, w in drops if w / total >= MIN_DROP_SHARE]
            head = max(outs, key=lambda o: o.amount)
            tier_energy = tiers.get(tier, 0)
            recipes.append(Recipe(
                kind="machine", output_id=head.ref, output_amount=1,
                recipe_type=None, machine=CHAMBER_ID, time_seconds=cycle_seconds,
                ingredients=[],                 # energy-only
                outputs=outs,
                ctor_class="MobSimulationChamber", source_class="MobData",
                # energy per tick = base_energy (per chamber) + tier_energy (per card)
                fixtures=[{"id": cid, "name": _strip(cname), "category": "card",
                           "tier": tier, "tier_energy": tier_energy,
                           "base_energy": BASE_ENERGY, "energy": BASE_ENERGY + tier_energy,
                           "drops": [{"ref": r, "kind": k, "amount": amt,
                                      "pct": round(w / total * 100, 1)}
                                     for k, r, amt, w in drops]}],
            ))
        cur, tier, drops, in_drops = None, None, [], False

    for x in ins:
        op = x.opcode
        if op == 0xbb and cp.class_name(x.u16()).endswith("MobDataCard"):
            flush()
            cur = "?"
            continue
        if not cur:
            continue
        if op in (0xb6, 0xb7, 0xb9):           # invoke*
            owner, nm, d = cp.method_ref(x.u16())
            if nm == "<init>" and owner.endswith("MobDataCard"):
                in_drops = True                # past the crafting-recipe array; drops start now
                pending = []
            elif nm == "addDrop" and in_drops:
                items = [p for p in pending if p[0] == "item"]
                ints = [p[1] for p in pending if p[0] == "int"]
                floats = [p[1] for p in pending if p[0] == "float"]
                if items:
                    _, ref, owner2 = items[-1]
                    is_vanilla = d.startswith("(Lorg/bukkit/Material;")
                    # descriptor (...;IF) carries an int amount before the float weight
                    amt = ints[-1] if (ints and _wants_amount(d)) else 1
                    passed = floats[-1] if floats else 1.0
                    # addDrop(item, w) stores `1/w` in the RandomizedSet, so the passed number
                    # is INVERSE rarity: higher = RARER (PINK_WOOL 10000 -> real weight 0.0001;
                    # mutton/wool at w=1 -> 1.0 are the common rolls). Store the real pick-weight.
                    real_w = 1.0 / passed if passed else 1.0
                    drops.append(("vanilla" if is_vanilla else "slimefun", ref, amt, real_w))
                pending = []
            elif nm == "register" and owner.endswith("MobDataCard"):
                flush()
                pending = []
            continue
        if op == 0xb2:                         # getstatic
            owner, nm, d = cp.field_ref(x.u16())
            if not in_drops and d.endswith("SlimefunItemStack;") and owner.endswith("MobData") \
                    and cur == "?":
                cur = nm                       # the card field (MobData.<MOB>)
            elif not in_drops and d.endswith("MobDataTier;"):
                tier = nm
            elif in_drops:                     # a drop item (Material / SF item getstatic)
                pending.append(("item", nm, owner))
            continue
        if in_drops:
            iv = _int(x)
            if iv is not None:
                pending.append(("int", iv))
                continue
            fv = _float(cp, x)
            if fv is not None:
                pending.append(("float", fv))
    flush()
    return item_defs, recipes


def _wants_amount(desc: str) -> bool:
    """addDrop descriptor has an int amount before the float: (X;IF) not (X;F)."""
    args = desc.split(")")[0]
    return args.endswith("IF")
