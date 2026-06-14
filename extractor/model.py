"""Recipe & item recognizers.

Extraction is *call-driven*: a recipe is emitted only when a recognized
registration call is seen, and the ingredient array(s) are matched to that call.
This avoids false positives (e.g. research unlock lists, GUI background arrays)
and correctly pairs the two arrays of an electric-machine recipe.

Two recipe kinds are produced:

* "crafting" — a `new SlimefunItem-subclass(group, output, RecipeType, ItemStack[])`
  constructor. The item is made in the workbench/multiblock named by RecipeType.
  Bytecode shape (verified against the live jars):
      getstatic <output SlimefunItemStack>
      getstatic RecipeType.XXX
      <push len>; anewarray ItemStack; (dup;<idx>;<value>;aastore)*
      invokespecial <Subclass>.<init>( ... RecipeType, ItemStack[] ... )

* "machine" — `this.registerRecipe(int seconds, ItemStack[] input, ItemStack[] output)`
  (or the single-ItemStack convenience overload) inside an electric machine class
  (an `AContainer` subclass). The machine is the enclosing source class.

A per-slot "value expr" is one of: getstatic <Items>.FIELD (a Slimefun/addon item),
new ItemStack(Material.X[, amount]) (vanilla), or aconst_null (empty).

Item definitions come from `new SlimefunItemStack(String id, icon, ...)`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_HEX_HASH = re.compile(r"[0-9a-fA-F]{38,}$")
_BASE64 = re.compile(r"[A-Za-z0-9+/=]{60,}$")


def _is_texture(s: str) -> bool:
    """True if a string looks like a player-head texture (hash or base64), not a name."""
    if not s or " " in s:
        return False
    return bool(_HEX_HASH.match(s) or _BASE64.match(s))


def _to_upper_snake(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).upper()


def _is_item_ref_desc(desc: str) -> bool:
    """A field/return descriptor that holds an item the graph cares about."""
    s = _suffix(desc)
    return s.endswith("ItemStack") or s.endswith("ItemWrapper")


def _is_item_ctor(owner: str, desc: str) -> bool:
    """True for an id-bearing item-stack constructor (SlimefunItemStack & subclasses
    such as SupremeItemStack) — recognized by `<init>(String id, ...)`. Excludes
    CustomItemStack / bukkit ItemStack, whose first arg is a Material/ItemStack."""
    return (owner.split("/")[-1].endswith("ItemStack")
            and desc.startswith("(Ljava/lang/String;"))

from . import bytecode
from .classfile import ClassFile
from .bytecode import Instruction


# Item classes whose ctor is (ItemGroup, SlimefunItemStack, ItemStack[]) with NO
# RecipeType arg — the machine is implied by the class. Matched by simple name.
NO_RECIPETYPE_CLASSES = {
    "AlloyIngot": "SMELTERY",
    "Jetpack": "ENHANCED_CRAFTING_TABLE",
    "JetBoots": "ENHANCED_CRAFTING_TABLE",
    "EnhancedFurnace": "ENHANCED_CRAFTING_TABLE",
    "ExplosiveBow": "ENHANCED_CRAFTING_TABLE",
    "IcyBow": "ENHANCED_CRAFTING_TABLE",
    "ElementalRune": "ANCIENT_ALTAR",
    "Talisman": "MAGIC_WORKBENCH",
    "MagicianTalisman": "MAGIC_WORKBENCH",
    "InfinityTool": "INFINITY_WORKBENCH",
    "InfinityArmor": "INFINITY_WORKBENCH",
    "StorageUnit": "ENHANCED_CRAFTING_TABLE",
    "Strainer": "ENHANCED_CRAFTING_TABLE",
}

# Static helper methods that register a recipe `(output, ItemStack[])` with an
# implied RecipeType (e.g. InfinityExpansion's Materials.registerEnhanced/Smeltery).
RECIPE_HELPER_METHODS = {
    "registerEnhanced": "ENHANCED_CRAFTING_TABLE",
    "registerSmeltery": "SMELTERY",
}

SLIMEFUN_ITEMSTACK = "slimefun4/api/items/SlimefunItemStack"
RECIPE_TYPE = "slimefun4/api/recipes/RecipeType"
BUKKIT_MATERIAL = "org/bukkit/Material"
BUKKIT_ITEMSTACK = "org/bukkit/inventory/ItemStack"
ITEMSTACK_ARRAY_DESC = "[Lorg/bukkit/inventory/ItemStack;"
ITEMSTACK_DESC = "Lorg/bukkit/inventory/ItemStack;"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Ingredient:
    kind: str            # "slimefun" | "vanilla" | "empty" | "unknown"
    ref: Optional[str]   # item id (slimefun) or Material name (vanilla)
    amount: int = 1


@dataclass
class Recipe:
    kind: str                          # "crafting" | "machine"
    output_id: Optional[str]
    output_amount: int
    recipe_type: Optional[str]         # RecipeType field name (crafting)
    machine: Optional[str]             # enclosing machine class (machine recipes)
    time_seconds: Optional[float]
    ingredients: list[Ingredient]      # inputs
    outputs: list[Ingredient]          # full output array (machine recipes)
    ctor_class: str
    source_class: str
    fixtures: list = field(default_factory=list)  # held machine-like items (e.g. a chicken)


@dataclass
class ItemDef:
    id: str
    name: Optional[str]
    amount: int
    source_class: str
    texture: Optional[str] = None


# ---------------------------------------------------------------------------
# Descriptor parsing
# ---------------------------------------------------------------------------
def parse_descriptor(desc: str) -> tuple[list[str], str]:
    assert desc.startswith("(")
    i = 1
    params: list[str] = []
    while desc[i] != ")":
        t, i = _read_type(desc, i)
        params.append(t)
    return params, desc[i + 1:]


def _read_type(desc: str, i: int) -> tuple[str, int]:
    start = i
    while desc[i] == "[":
        i += 1
    if desc[i] == "L":
        end = desc.index(";", i)
        return desc[start:end + 1], end + 1
    return desc[start:i + 1], i + 1


def _suffix(s: str) -> str:
    s = s.lstrip("[")
    if s.startswith("L") and s.endswith(";"):
        s = s[1:-1]
    return s


# ---------------------------------------------------------------------------
# Instruction helpers
# ---------------------------------------------------------------------------
def _int_value(cp, ins: Instruction) -> Optional[int]:
    op = ins.opcode
    if op in bytecode.ICONST_VALUES:
        return bytecode.ICONST_VALUES[op]
    if op == 0x10:
        return ins.s8()
    if op == 0x11:
        return ins.s16()
    if op in (0x12, 0x13):
        idx = ins.u8() if op == 0x12 else ins.u16()
        kind, val = cp.ldc_value(idx)
        if kind == "int":
            return val
    return None


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------
@dataclass
class _Region:
    """A parsed `new ItemStack[]{...}` array and the instruction span it covers."""
    start: int        # index of the anewarray instruction
    end: int          # index just past the last aastore
    size: int
    ingredients: list[Ingredient]


class RecipeExtractor:
    def __init__(self, cf: ClassFile):
        self.cf = cf
        self.cp = cf.constant_pool
        self.recipes: list[Recipe] = []
        self.item_defs: list[ItemDef] = []
        self.aliases: dict[str, str] = {}   # field_name -> referenced field_name

    def run(self) -> None:
        for m in self.cf.methods:
            if m.code:
                self._scan_method(m.code)

    # -- per method -------------------------------------------------------
    def _scan_method(self, code: bytes) -> None:
        ins = list(bytecode.iter_instructions(code))
        regions = self._find_array_regions(ins)
        if "/supreme/" in self.cf.name:
            self._scan_paired_recipe_fields(ins, regions)
        for idx, instr in enumerate(ins):
            op = instr.opcode
            if op in (0xb6, 0xb7, 0xb8, 0xb9):
                owner, name, desc = self.cp.method_ref(instr.u16())
                if (name in ("<init>", "register") and ITEMSTACK_ARRAY_DESC in desc
                        and ("RecipeType" in desc   # std RecipeType or a custom *RecipeType
                             or owner.split("/")[-1] in NO_RECIPETYPE_CLASSES)):
                    self._emit_crafting(ins, idx, regions, owner)
                elif name in ("registerRecipe", "addRecipe") and (
                        ITEMSTACK_ARRAY_DESC in desc or ITEMSTACK_DESC in desc):
                    self._emit_machine(ins, idx, regions, desc)
                elif name in RECIPE_HELPER_METHODS and ITEMSTACK_ARRAY_DESC in desc:
                    self._emit_crafting(ins, idx, regions, owner,
                                        forced_type=RECIPE_HELPER_METHODS[name])
                elif name == "<init>" and owner.endswith("/Singularity"):
                    self._emit_singularity(ins, idx)
            elif op == 0xb3:  # putstatic — item or recipe stored to a static field
                self._parse_field_store(ins, idx, regions)

    def _is_sfis_init(self, instr: Instruction) -> bool:
        owner, name, _ = self.cp.method_ref(instr.u16())
        return name == "<init>" and owner.endswith(SLIMEFUN_ITEMSTACK)

    def _scan_paired_recipe_fields(self, ins, regions) -> None:
        """Supreme stores an item and its recipe array in consecutive static fields:
            putstatic Components.SYNTHETIC_RUBY        (the item)
            ...new ItemStack[]{...}...
            putstatic Components.SYNTHETIC_RUBY_RECIPE (its recipe grid)
        Emit a crafting recipe (output = the item field) for each such pair.
        """
        cp = self.cp
        last_item = None  # (field, idx)
        for idx, instr in enumerate(ins):
            if instr.opcode != 0xb3:
                continue
            owner, fname, desc = cp.field_ref(instr.u16())
            is_array = desc.startswith("[")
            if is_array and _suffix(desc).endswith("ItemStack"):
                # recipe array — the output item is named by the field (RECIPE_X -> X)
                output_id = fname[len("RECIPE_"):] if fname.startswith("RECIPE_") else None
                if not output_id and last_item and 0 < idx - last_item[1] < 90:
                    output_id = last_item[0]
                if not output_id:
                    continue
                region = self._closest_region(regions, idx)
                if region and any(g.kind in ("slimefun", "vanilla") for g in region.ingredients):
                    self.recipes.append(Recipe(
                        kind="crafting",
                        output_id=output_id,
                        output_amount=1,
                        recipe_type="ENHANCED_CRAFTING_TABLE",
                        machine=None,
                        time_seconds=None,
                        ingredients=region.ingredients,
                        outputs=[],
                        ctor_class="",
                        source_class=self.cf.name,
                    ))
                last_item = None
            elif _is_item_ref_desc(desc):
                last_item = (fname, idx)

    # -- find every ItemStack[] region -----------------------------------
    def _find_array_regions(self, ins) -> list[_Region]:
        regions = []
        for i, instr in enumerate(ins):
            if instr.opcode == 0xbd and self.cp.class_name(instr.u16()).endswith(BUKKIT_ITEMSTACK):
                region = self._parse_array(ins, i)
                if region is not None:
                    regions.append(region)
        return regions

    def _parse_array(self, ins, arr_idx) -> Optional[_Region]:
        cp = self.cp
        size = None
        for k in range(arr_idx - 1, max(arr_idx - 4, -1), -1):
            v = _int_value(cp, ins[k])
            if v is not None:
                size = v
                break
        if size is None or size <= 0 or size > 81:
            return None
        ingredients = [Ingredient("empty", None) for _ in range(size)]
        j = arr_idx + 1
        filled = 0
        last_end = arr_idx + 1
        while j < len(ins) and filled < size:
            if ins[j].opcode != 0x59:  # dup starts each store
                break
            k = j + 1
            idx_val = _int_value(cp, ins[k]) if k < len(ins) else None
            if idx_val is None:
                break
            k += 1
            val_start = k
            while k < len(ins) and ins[k].opcode != 0x53:  # until aastore
                k += 1
            if k >= len(ins):
                break
            if 0 <= idx_val < size:
                ingredients[idx_val] = self._eval_value(ins, val_start, k)
            filled += 1
            j = k + 1
            last_end = j
        return _Region(arr_idx, last_end, size, ingredients)

    def _eval_value(self, ins, start, end) -> Ingredient:
        cp = self.cp
        has_null = False
        material = None
        slimefun = None
        amount = 1
        amount_seen = False
        for k in range(start, end):
            op = ins[k].opcode
            if op == 0x01:
                has_null = True
            elif op == 0xb2:  # getstatic
                owner, fname, desc = cp.field_ref(ins[k].u16())
                if owner.endswith(BUKKIT_MATERIAL):
                    material = fname
                elif _is_item_ref_desc(desc):
                    slimefun = fname
                elif desc.startswith("L") and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", fname):
                    # a SlimefunItem-subclass field used as an ingredient via
                    # .getItem()/.stack() (e.g. Networks UnplaceableBlock); within a
                    # recipe slot this getstatic is the item, keyed by field name.
                    slimefun = fname
            else:
                iv = _int_value(cp, ins[k])
                # stack count of a `new ItemStack(X, n)` / `new SlimefunItemStack(X, n)`
                # — applies to slimefun items too (e.g. ElectricPress 4x MAGIC_LUMP_1).
                if (iv is not None and not amount_seen
                        and (material is not None or slimefun is not None)):
                    amount = iv
                    amount_seen = True
        if slimefun is not None:
            return Ingredient("slimefun", slimefun, amount if amount_seen else 1)
        if material is not None:
            return Ingredient("vanilla", material, amount if amount_seen else 1)
        if has_null:
            return Ingredient("empty", None)
        return Ingredient("unknown", None)

    def _closest_region(self, regions, before_idx, not_after=None):
        """The region whose fill ends at/just before before_idx."""
        best = None
        for r in regions:
            if r.end <= before_idx and (not_after is None or r.start < not_after):
                if best is None or r.start > best.start:
                    best = r
        return best

    # -- crafting recipe (constructor with RecipeType + ItemStack[]) ------
    def _emit_crafting(self, ins, call_idx, regions, owner, forced_type=None) -> None:
        region = self._closest_region(regions, call_idx)
        if region is None:
            return
        # Guard: if the ItemStack[] argument is produced by a method call between the
        # array and the constructor (e.g. DynaTech `Recipes.X.getInput()`), then this
        # region is NOT the recipe array — skip (handled by the builder recognizer).
        for k in range(region.end, call_idx):
            op = ins[k].opcode
            if op in (0xb6, 0xb7, 0xb8, 0xb9):
                _, _, d = self.cp.method_ref(ins[k].u16())
                if d.endswith(")" + ITEMSTACK_ARRAY_DESC):
                    return
        output_id, output_amount, recipe_type = self._scan_back_output_and_type(ins, region.start)
        if forced_type:
            recipe_type = forced_type
        if recipe_type is None:
            recipe_type = NO_RECIPETYPE_CLASSES.get(owner.split("/")[-1])
        if output_id is None:
            # wider fallback: some ctors put another array (e.g. PotionEffect[]) between
            # the output and the recipe array, which blocks the bounded scan above.
            output_id = self._scan_back_output_wide(ins, region.start)
        # A 5th `recipeOutput` arg AFTER the recipe array overrides the produced item/amount:
        # SlimefunItem(group, item, RecipeType, recipe[], new SlimefunItemStack(ITEM, n)).
        # e.g. Grind Stone: 1 nether wart -> new SlimefunItemStack(MAGIC_LUMP_1, 2).
        ro_id, ro_amt = self._recipe_output_after(ins, region.end, call_idx)
        if ro_id:
            output_id, output_amount = ro_id, ro_amt
        if output_id is None and recipe_type is None:
            return
        self.recipes.append(Recipe(
            kind="crafting",
            output_id=output_id,
            output_amount=output_amount,
            recipe_type=recipe_type,
            machine=None,
            time_seconds=None,
            ingredients=region.ingredients,
            outputs=[],
            ctor_class=owner,
            source_class=self.cf.name,
        ))

    def _recipe_output_after(self, ins, start, end):
        """Find a `recipeOutput` arg built between the recipe array and the constructor call:
        `new SlimefunItemStack(ITEM, n)` (or bare ITEM). Returns (item_id, amount) or (None, 1).
        """
        cp = self.cp
        for k in range(start, end):
            if ins[k].opcode != 0xbb:
                continue
            if not cp.class_name(ins[k].u16()).endswith("ItemStack"):
                continue
            item = None
            amt = 1
            for j in range(k + 1, min(k + 7, end)):
                op = ins[j].opcode
                if op == 0xb2:
                    o, f, d = cp.field_ref(ins[j].u16())
                    if item is None and _is_item_ref_desc(d):
                        item = f
                elif op == 0xb7:
                    break
                else:
                    iv = _int_value(cp, ins[j])
                    if iv is not None and iv > 1:
                        amt = iv
            if item:
                return item, amt
        return None, 1

    def _sfis_stack_amount(self, ins, k):
        """If the item getstatic at `k` is wrapped in `new SlimefunItemStack(ITEM, n)`
        (the output is a STACK of n), return n; else None.

        Pattern: `new SFIS; dup; getstatic ITEM(k); <int n>(k+1); invokespecial <init>(.,I)V`.
        """
        cp = self.cp
        if k + 2 < len(ins):
            n = _int_value(cp, ins[k + 1])
            if n is not None and n > 1 and ins[k + 2].opcode == 0xb7:
                o, nm, d = cp.method_ref(ins[k + 2].u16())
                if nm == "<init>" and o.endswith("ItemStack") and d.endswith("I)V"):
                    return n
        return None

    def _scan_back_output_and_type(self, ins, arr_idx):
        cp = self.cp
        output_id = None
        output_amount = 1
        recipe_type = None
        for k in range(arr_idx - 1, max(arr_idx - 12, -1), -1):
            op = ins[k].opcode
            if op == 0xb2:
                owner, fname, desc = cp.field_ref(ins[k].u16())
                if recipe_type is None and _suffix(desc).endswith("RecipeType"):
                    # standard RecipeType.X, or a machine's own RecipeType field
                    # (e.g. InfinityWorkbench.TYPE -> INFINITY_WORKBENCH).
                    recipe_type = (fname if owner.endswith(RECIPE_TYPE)
                                   else _to_upper_snake(owner.split("/")[-1]))
                elif output_id is None and _is_item_ref_desc(desc):
                    output_id = fname
                    # the output may be registered as `new SlimefunItemStack(ITEM, n)`
                    # (a stack of n) — e.g. Grind Stone: 1 nether wart -> 2 Magical Lump I.
                    amt = self._sfis_stack_amount(ins, k)
                    if amt:
                        output_amount = amt
            elif op in (0x53, 0xb6, 0xb9, 0xbd):
                if output_id is not None or recipe_type is not None:
                    break
        return output_id, output_amount, recipe_type

    def _scan_back_output_wide(self, ins, arr_idx):
        """Find the output item getstatic further back, past intervening arrays,
        bounded by the previous statement (putstatic / register call)."""
        cp = self.cp
        for k in range(arr_idx - 1, max(arr_idx - 60, -1), -1):
            op = ins[k].opcode
            if op == 0xb2:
                owner, fname, desc = cp.field_ref(ins[k].u16())
                if _is_item_ref_desc(desc):
                    return fname
            elif op == 0xb3:  # previous statement boundary
                break
            elif op in (0xb6, 0xb9):  # invokevirtual/interface (e.g. previous .register())
                _, n, _ = cp.method_ref(ins[k].u16())
                if n in ("register", "addItems"):
                    break
        return None

    # -- machine recipe (registerRecipe) ---------------------------------
    def _emit_machine(self, ins, call_idx, regions, desc) -> None:
        params, _ = parse_descriptor(desc)
        machine = self.cf.name.split("/")[-1]
        time_seconds = None
        if params and params[0] == "I":
            # seconds is the first int pushed before the recipe args
            pass  # found via region/back scan below

        array_params = [p for p in params if p == ITEMSTACK_ARRAY_DESC]
        single_then_array = (
            len(array_params) == 1 and len(params) >= 2
            and params[0] == ITEMSTACK_DESC and params[-1] == ITEMSTACK_ARRAY_DESC)
        if single_then_array:
            # MachineBlock.addRecipe(ItemStack output, ItemStack[] inputs): the OUTPUT
            # is the single ItemStack pushed first, the INPUTS are the trailing array
            # (e.g. InfinityExpansion decompaction: 1 COAL_BLOCK -> new ItemStack(COAL,9)).
            in_region = self._closest_region(regions, call_idx)
            if in_region is None:
                return
            out_ing = self._parse_single_itemstack_before(ins, in_region.start)
            if out_ing is None:
                return
            inputs = in_region.ingredients
            outputs = [out_ing]
            time_seconds = None
        elif len(array_params) >= 2:
            out_region = self._closest_region(regions, call_idx)
            if out_region is None:
                return
            in_region = self._closest_region(regions, out_region.start)
            if in_region is None:
                return
            # registerRecipe(int time, ItemStack[] in, ItemStack[] out): the time is pushed
            # BEFORE the input array. The int right before `anewarray` is the array's SIZE, so
            # skip it (start the scan one earlier) or we'd read the input count as the time —
            # this is what made the crimson-fungus recipe come out as 1s instead of 30s.
            time_seconds = (self._scan_back_int(ins, in_region.start - 1)
                            if params and params[0] == "I" else None)
            outputs = out_region.ingredients
            inputs = in_region.ingredients
        else:
            # single-ItemStack overload: parse the value exprs before the call
            inputs, outputs, time_seconds = self._parse_single_machine(ins, call_idx, params)
            if inputs is None:
                return
        output_id, output_amount = self._first_item(outputs)
        self.recipes.append(Recipe(
            kind="machine",
            output_id=output_id,
            output_amount=output_amount,
            recipe_type=None,
            machine=machine,
            time_seconds=time_seconds,
            ingredients=inputs,
            outputs=outputs,
            ctor_class="",
            source_class=self.cf.name,
        ))

    def _emit_singularity(self, ins, call_idx) -> None:
        """InfinityExpansion `new Singularity(group, output, input, amount)` ->
        amount x input -> output in the Singularity Constructor."""
        cp = self.cp
        items_seen = []   # (kind, ref) in reverse bytecode order: [input, output]
        amount = None
        for k in range(call_idx - 1, max(call_idx - 25, -1), -1):
            op = ins[k].opcode
            if op == 0xb2:
                owner, fname, desc = cp.field_ref(ins[k].u16())
                if owner.endswith(BUKKIT_MATERIAL):
                    items_seen.append(("vanilla", fname))
                elif _is_item_ref_desc(desc):
                    items_seen.append(("slimefun", fname))
            elif amount is None:
                iv = _int_value(cp, ins[k])
                if iv is not None and iv > 1:
                    amount = iv
            if len(items_seen) >= 2:
                break
        if len(items_seen) < 2:
            return
        in_kind, in_ref = items_seen[0]
        out_kind, out_ref = items_seen[1]
        self.recipes.append(Recipe(
            kind="machine", output_id=out_ref, output_amount=1,
            recipe_type=None, machine="SingularityConstructor",
            time_seconds=None,
            ingredients=[Ingredient(in_kind, in_ref, amount or 1)],
            outputs=[Ingredient(out_kind, out_ref, 1)],
            ctor_class="Singularity", source_class=self.cf.name,
        ))

    def _parse_single_itemstack_before(self, ins, before_idx) -> Optional[Ingredient]:
        """Parse the single ItemStack value whose `<init>` (or bare item field) ends
        just before before_idx — the leading output arg of MachineBlock.addRecipe."""
        cp = self.cp
        init_idx = None
        for k in range(before_idx - 1, max(before_idx - 30, -1), -1):
            op = ins[k].opcode
            if op == 0xb7:  # invokespecial
                owner, name, _ = cp.method_ref(ins[k].u16())
                if name == "<init>" and owner.endswith(BUKKIT_ITEMSTACK):
                    init_idx = k
                    break
            elif op == 0xb2:  # a bare item field used directly (no ItemStack wrapper)
                owner, fname, dsc = cp.field_ref(ins[k].u16())
                if _is_item_ref_desc(dsc):
                    return Ingredient("slimefun", fname, 1)
                if owner.endswith(BUKKIT_MATERIAL):
                    return Ingredient("vanilla", fname, 1)
        if init_idx is None:
            return None
        start = init_idx
        for k in range(init_idx - 1, max(init_idx - 12, -1), -1):
            if ins[k].opcode == 0xbb:  # matching `new`
                start = k
                break
        return self._eval_value(ins, start, init_idx)

    def _scan_back_int(self, ins, before_idx) -> Optional[int]:
        cp = self.cp
        for k in range(before_idx - 1, max(before_idx - 6, -1), -1):
            v = _int_value(cp, ins[k])
            if v is not None:
                return v
        return None

    def _parse_single_machine(self, ins, call_idx, params):
        """Handle registerRecipe(int, ItemStack, ItemStack) — no arrays.

        Each ItemStack value is parsed WITH its stack count (via _eval_value over the
        `new ItemStack(...)` expression), so e.g. ElectricPress's 9 COAL -> COAL_BLOCK
        keeps the 9. seconds = the int pushed before the first value (when present).
        """
        cp = self.cp
        values: list[Ingredient] = []
        seconds = None
        start = max(call_idx - 60, 0)
        k = start
        new_idx = None  # open `new *ItemStack` awaiting its <init>
        while k < call_idx:
            op = ins[k].opcode
            # ItemStack / SlimefunItemStack / CustomItemStack all carry a stack count
            if op == 0xbb and cp.class_name(ins[k].u16()).endswith("ItemStack"):
                new_idx = k
            elif op == 0xb7 and new_idx is not None:
                owner, name, _ = cp.method_ref(ins[k].u16())
                if name == "<init>" and owner.endswith("ItemStack"):
                    values.append(self._eval_value(ins, new_idx, k))
                    new_idx = None
            elif op == 0xb2 and new_idx is None:
                # a slimefun item field passed directly (no ItemStack wrapper)
                owner, fname, dsc = cp.field_ref(ins[k].u16())
                if _is_item_ref_desc(dsc):
                    values.append(Ingredient("slimefun", fname, 1))
            elif new_idx is None and not values:
                iv = _int_value(cp, ins[k])
                if iv is not None and seconds is None:
                    seconds = iv
            k += 1
        if len(values) < 2:
            return None, None, None
        # last two values = input, output (in source order)
        inputs = [values[-2]]
        outputs = [values[-1]]
        return inputs, outputs, seconds

    def _first_item(self, ings) -> tuple[Optional[str], int]:
        for ing in ings:
            if ing.kind in ("slimefun", "vanilla") and ing.ref:
                return ing.ref, ing.amount
        return None, 1

    # -- item definitions (keyed by the static field they are stored into) --
    def _parse_field_store(self, ins, ps_idx, regions) -> None:
        """Handle `putstatic Owner.FIELD` for item- or recipe-typed fields.

        The recipe graph references items by field name (getstatic), so items are
        keyed by field name. Cases:
          * alias:        FIELD = SomeOther.OTHER_FIELD   -> record an alias
          * construction: FIELD = new SlimefunItemStack(...) (possibly wrapped)
          * recipe build: FIELD = Recipe.init()...setInput(..).setOutput(..)  (fluent)
        """
        cp = self.cp
        owner, fname, desc = cp.field_ref(ins[ps_idx].u16())
        if _suffix(desc).endswith("Recipe"):
            self._parse_recipe_builder(ins, ps_idx, fname, regions)
            return
        item_typed = _is_item_ref_desc(desc)
        # alias: value pushed is another item field (re-export)
        prev = ins[ps_idx - 1] if ps_idx > 0 else None
        if item_typed and prev is not None and prev.opcode == 0xb2:
            o2, f2, d2 = cp.field_ref(prev.u16())
            if _is_item_ref_desc(d2) and f2 != fname:
                self.aliases[fname] = f2
                return
        # construction: nearest preceding id-bearing item-stack <init> within this
        # statement. Works for SlimefunItemStack, subclasses (SupremeItemStack), and
        # factory-wrapped builds (DynaTech `ItemWrapper.create(key, new SFIS(...))`).
        init_idx = None
        for k in range(ps_idx - 1, max(ps_idx - 120, -1), -1):
            op = ins[k].opcode
            if op == 0xb7:
                o, n, d = cp.method_ref(ins[k].u16())
                if n == "<init>" and _is_item_ctor(o, d):
                    init_idx = k
                    break
            if op == 0xb3:  # previous statement's store -> boundary
                break
        if init_idx is None:
            return
        _id, display, texture, amount = self._read_sfis(ins, init_idx)
        self.item_defs.append(ItemDef(fname, display, amount, self.cf.name, texture))

    def _parse_recipe_builder(self, ins, ps_idx, fname, regions) -> None:
        """Parse a fluent recipe builder stored to a Recipe-typed field.

        Shape: `Recipe.init().setRecipeType(RT).setInput(ItemStack[]).setOutput(...).build()`
        The field name is taken as the output item id (registries keep matching item /
        recipe field names, e.g. Items.MACHINE_SCRAP <-> Recipes.MACHINE_SCRAP).
        """
        cp = self.cp
        # statement window: back to the previous putstatic
        start = 0
        for k in range(ps_idx - 1, -1, -1):
            if ins[k].opcode == 0xb3:
                start = k + 1
                break
        recipe_type = None
        in_region = None
        out_region = None
        out_amount = 1
        for k in range(start, ps_idx):
            instr = ins[k]
            op = instr.opcode
            if op == 0xb2:  # getstatic RecipeType
                o, f, d = cp.field_ref(instr.u16())
                if o.endswith(RECIPE_TYPE):
                    recipe_type = f
            elif op in (0xb6, 0xb7, 0xb8, 0xb9):
                o, n, d = cp.method_ref(instr.u16())
                if n == "setInput" and ITEMSTACK_ARRAY_DESC in d:
                    in_region = self._closest_region(regions, k)
                elif n == "setOutput":
                    if ITEMSTACK_ARRAY_DESC in d:
                        out_region = self._closest_region(regions, k)
                    else:
                        amt = self._scan_back_int(ins, k)
                        if amt:
                            out_amount = amt
        if in_region is None:
            return
        output_id = fname
        if out_region is not None:
            oid, oamt = self._first_item(out_region.ingredients)
            if oid:
                output_id = oid
                out_amount = oamt
        self.recipes.append(Recipe(
            kind="crafting",
            output_id=output_id,
            output_amount=out_amount,
            recipe_type=recipe_type,
            machine=None,
            time_seconds=None,
            ingredients=in_region.ingredients,
            outputs=out_region.ingredients if out_region else [],
            ctor_class="",
            source_class=self.cf.name,
        ))

    def _read_sfis(self, ins, init_idx):
        """Read (id_string, display_name, texture, amount) from a SlimefunItemStack ctor.

        Args are collected in source order: [id, (texture?), name, lore...]. Rather
        than relying on fixed positions (which vary by overload), the first string
        is the id, the texture is the first head-hash/base64 string, and the display
        name is the first remaining human-readable string.
        """
        cp = self.cp
        _, _, desc = cp.method_ref(ins[init_idx].u16())
        params, _ = parse_descriptor(desc)
        strings: list[str] = []
        ints: list[int] = []
        for k in range(init_idx - 1, max(init_idx - 80, -1), -1):
            op = ins[k].opcode
            if op in (0x12, 0x13):
                idx = ins[k].u8() if op == 0x12 else ins[k].u16()
                kind, val = cp.ldc_value(idx)
                if kind == "string":
                    strings.append(val)
            else:
                iv = _int_value(cp, ins[k])
                if iv is not None:
                    ints.append(iv)
                if op == 0xbb and cp.class_name(ins[k].u16()).split("/")[-1].endswith("ItemStack"):
                    break
        strings.reverse()
        id_str = strings[0] if strings else None
        rest = strings[1:]
        texture = next((s for s in rest if _is_texture(s)), None)
        display = next((s for s in rest if s and not _is_texture(s)), None)
        amount = 1
        if "I" in params and ints:
            cand = [v for v in ints if 1 <= v <= 64]
            if cand:
                amount = cand[0]
        return id_str, display, texture, amount


def extract(cf: ClassFile) -> RecipeExtractor:
    ex = RecipeExtractor(cf)
    ex.run()
    return ex
