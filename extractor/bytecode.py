"""JVM bytecode decoding.

Turns a Code attribute's raw bytes into a clean stream of instructions.
Exact instruction lengths matter: a single wrong length desyncs the entire
decode, so the operand-length table is built explicitly and the three
variable-length opcodes (wide, tableswitch, lookupswitch) are handled by hand.

Reference: JVM Spec, Chapter 6 (The Java Virtual Machine Instruction Set).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


# Number of operand bytes following each opcode (fixed-length opcodes only).
# Variable-length opcodes (0xc4 wide, 0xaa tableswitch, 0xab lookupswitch)
# are handled specially in iter_instructions and excluded here.
_OPERAND_LEN: dict[int, int] = {}


def _fill_operand_lengths() -> None:
    t = _OPERAND_LEN
    # default: all opcodes 0 operand bytes unless overridden below
    for op in range(0x00, 0x100):
        t[op] = 0
    # 1-byte operands
    for op in (0x10,        # bipush
               0x12,        # ldc
               0x15, 0x16, 0x17, 0x18, 0x19,  # *load
               0x36, 0x37, 0x38, 0x39, 0x3a,  # *store
               0xbc,        # newarray
               0xa9):       # ret
        t[op] = 1
    # 2-byte operands
    for op in (0x11,        # sipush
               0x13, 0x14,  # ldc_w, ldc2_w
               0x84,        # iinc
               0xb2, 0xb3, 0xb4, 0xb5,  # get/put static/field
               0xb6, 0xb7, 0xb8,        # invokevirtual/special/static
               0xbb,        # new
               0xbd,        # anewarray
               0xc0, 0xc1,  # checkcast, instanceof
               0xbe,        # (arraylength has 0 -> overwritten below)
               ):
        t[op] = 2
    t[0xbe] = 0  # arraylength has no operands; correct the line above
    # branch instructions: 2-byte signed offset
    for op in range(0x99, 0xa8):   # ifeq..goto (0x99..0xa7)
        t[op] = 2
    t[0xa8] = 2                     # jsr
    t[0xc6] = 2                     # ifnull
    t[0xc7] = 2                     # ifnonnull
    # 3-byte operands
    t[0xc5] = 3                     # multianewarray
    # 4-byte operands
    for op in (0xb9,    # invokeinterface
               0xba,    # invokedynamic
               0xc8,    # goto_w
               0xc9):   # jsr_w
        t[op] = 4


_fill_operand_lengths()


# Mnemonics for the opcodes the extractor reasons about.
OPCODES = {
    0x01: "aconst_null",
    0x02: "iconst_m1", 0x03: "iconst_0", 0x04: "iconst_1", 0x05: "iconst_2",
    0x06: "iconst_3", 0x07: "iconst_4", 0x08: "iconst_5",
    0x10: "bipush", 0x11: "sipush",
    0x12: "ldc", 0x13: "ldc_w", 0x14: "ldc2_w",
    0x59: "dup",
    0x53: "aastore",
    0xb2: "getstatic", 0xb3: "putstatic", 0xb4: "getfield", 0xb5: "putfield",
    0xb6: "invokevirtual", 0xb7: "invokespecial", 0xb8: "invokestatic",
    0xb9: "invokeinterface", 0xba: "invokedynamic",
    0xbb: "new", 0xbc: "newarray", 0xbd: "anewarray",
    0xc0: "checkcast", 0xc1: "instanceof",
    0x57: "pop", 0x58: "pop2",
    0xb1: "return", 0xb0: "areturn",
}

# iconst_<n> immediate values
ICONST_VALUES = {
    0x02: -1, 0x03: 0, 0x04: 1, 0x05: 2, 0x06: 3, 0x07: 4, 0x08: 5,
}


@dataclass
class Instruction:
    offset: int
    opcode: int
    operands: bytes
    name: str

    def u16(self) -> int:
        """First 2 operand bytes as an unsigned short (constant-pool index)."""
        return struct.unpack_from(">H", self.operands, 0)[0]

    def s16(self) -> int:
        return struct.unpack_from(">h", self.operands, 0)[0]

    def u8(self) -> int:
        return self.operands[0]

    def s8(self) -> int:
        return struct.unpack_from(">b", self.operands, 0)[0]


def iter_instructions(code: bytes):
    """Yield Instruction objects in order from a Code attribute's bytecode."""
    i = 0
    n = len(code)
    while i < n:
        op = code[i]
        start = i
        i += 1
        if op == 0xc4:  # wide
            sub = code[i]
            if sub == 0x84:  # wide iinc: opcode + 2 + 2
                operands = code[i:i + 5]
                i += 5
            else:            # wide load/store: opcode + 2
                operands = code[i:i + 3]
                i += 3
            yield Instruction(start, op, operands, "wide")
            continue
        if op == 0xaa:  # tableswitch
            pad = (4 - (i % 4)) % 4
            i += pad
            default = struct.unpack_from(">i", code, i)[0]
            low = struct.unpack_from(">i", code, i + 4)[0]
            high = struct.unpack_from(">i", code, i + 8)[0]
            n_offsets = high - low + 1
            i += 12 + 4 * n_offsets
            yield Instruction(start, op, b"", "tableswitch")
            continue
        if op == 0xab:  # lookupswitch
            pad = (4 - (i % 4)) % 4
            i += pad
            npairs = struct.unpack_from(">i", code, i + 4)[0]
            i += 8 + 8 * npairs
            yield Instruction(start, op, b"", "lookupswitch")
            continue
        length = _OPERAND_LEN[op]
        operands = code[i:i + length]
        i += length
        yield Instruction(start, op, operands, OPCODES.get(op, f"op_{op:02x}"))
