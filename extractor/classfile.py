"""Pure-Python JVM .class file reader.

Parses just enough of the class-file format to drive recipe extraction:
the constant pool, fields, methods, and each method's Code attribute.

No third-party dependencies. Reads bytes straight from memory (e.g. a jar
entry); never needs a JVM, javap, or a decompiler.

Reference: JVM Spec, Chapter 4 (The class File Format).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional


# Constant-pool tags
CONSTANT_Utf8 = 1
CONSTANT_Integer = 3
CONSTANT_Float = 4
CONSTANT_Long = 5
CONSTANT_Double = 6
CONSTANT_Class = 7
CONSTANT_String = 8
CONSTANT_Fieldref = 9
CONSTANT_Methodref = 10
CONSTANT_InterfaceMethodref = 11
CONSTANT_NameAndType = 12
CONSTANT_MethodHandle = 15
CONSTANT_MethodType = 16
CONSTANT_Dynamic = 17
CONSTANT_InvokeDynamic = 18
CONSTANT_Module = 19
CONSTANT_Package = 20


class ConstantPool:
    """Indexable constant pool with convenience resolvers.

    Entries are stored as tuples tagged by their kind. Helper methods resolve
    the cross-references (e.g. a Fieldref -> "owner.name:descriptor").
    """

    def __init__(self) -> None:
        self.entries: dict[int, tuple] = {}

    def __getitem__(self, idx: int) -> tuple:
        return self.entries[idx]

    def get(self, idx: int) -> Optional[tuple]:
        return self.entries.get(idx)

    def tag(self, idx: int) -> int:
        return self.entries[idx][0]

    # --- resolvers -------------------------------------------------------
    def utf8(self, idx: int) -> str:
        return self.entries[idx][1]

    def class_name(self, idx: int) -> str:
        """Internal binary name of a CONSTANT_Class, e.g. 'org/bukkit/Material'."""
        return self.utf8(self.entries[idx][1])

    def string_value(self, idx: int) -> str:
        """The string literal of a CONSTANT_String."""
        return self.utf8(self.entries[idx][1])

    def name_and_type(self, idx: int) -> tuple[str, str]:
        _, name_i, desc_i = self.entries[idx]
        return self.utf8(name_i), self.utf8(desc_i)

    def field_ref(self, idx: int) -> tuple[str, str, str]:
        """(owner_class, field_name, descriptor) for a Fieldref."""
        _, cls_i, nt_i = self.entries[idx]
        name, desc = self.name_and_type(nt_i)
        return self.class_name(cls_i), name, desc

    def method_ref(self, idx: int) -> tuple[str, str, str]:
        """(owner_class, method_name, descriptor) for a Method/InterfaceMethod ref."""
        _, cls_i, nt_i = self.entries[idx]
        name, desc = self.name_and_type(nt_i)
        return self.class_name(cls_i), name, desc

    def integer(self, idx: int) -> int:
        return self.entries[idx][1]

    def ldc_value(self, idx: int):
        """Resolve an ldc operand to a Python value where meaningful.

        Returns a tagged tuple so callers can distinguish kinds:
        ('string', str), ('class', name), ('int', n), ('float', f), or
        ('other', None).
        """
        e = self.entries[idx]
        tag = e[0]
        if tag == CONSTANT_String:
            return ("string", self.string_value(idx))
        if tag == CONSTANT_Class:
            return ("class", self.class_name(idx))
        if tag in (CONSTANT_Integer,):
            return ("int", e[1])
        if tag in (CONSTANT_Float, CONSTANT_Long, CONSTANT_Double):
            return ("num", e[1])
        return ("other", None)


@dataclass
class Method:
    name: str
    descriptor: str
    access_flags: int
    code: Optional[bytes] = None       # raw bytecode of the Code attribute
    max_stack: int = 0
    max_locals: int = 0


@dataclass
class FieldInfo:
    name: str
    descriptor: str
    access_flags: int


@dataclass
class ClassFile:
    name: str                          # internal name, e.g. 'io/.../FluffyItemSetup'
    super_name: str
    interfaces: list[str]
    constant_pool: ConstantPool
    fields: list[FieldInfo]
    methods: list[Method]

    def method(self, name: str) -> Optional[Method]:
        for m in self.methods:
            if m.name == name:
                return m
        return None


def _read_constant_pool(data: bytes, pos: int) -> tuple[ConstantPool, int]:
    cp = ConstantPool()
    count = struct.unpack_from(">H", data, pos)[0]
    pos += 2
    n = 1
    while n < count:
        tag = data[pos]
        pos += 1
        if tag == CONSTANT_Utf8:
            (length,) = struct.unpack_from(">H", data, pos)
            pos += 2
            text = data[pos:pos + length].decode("utf-8", "replace")
            pos += length
            cp.entries[n] = (tag, text)
        elif tag == CONSTANT_Integer:
            (val,) = struct.unpack_from(">i", data, pos)
            pos += 4
            cp.entries[n] = (tag, val)
        elif tag == CONSTANT_Float:
            (val,) = struct.unpack_from(">f", data, pos)
            pos += 4
            cp.entries[n] = (tag, val)
        elif tag == CONSTANT_Long:
            (val,) = struct.unpack_from(">q", data, pos)
            pos += 8
            cp.entries[n] = (tag, val)
            n += 1  # long/double occupy two pool slots
        elif tag == CONSTANT_Double:
            (val,) = struct.unpack_from(">d", data, pos)
            pos += 8
            cp.entries[n] = (tag, val)
            n += 1
        elif tag == CONSTANT_Class:
            (name_i,) = struct.unpack_from(">H", data, pos)
            pos += 2
            cp.entries[n] = (tag, name_i)
        elif tag == CONSTANT_String:
            (utf_i,) = struct.unpack_from(">H", data, pos)
            pos += 2
            cp.entries[n] = (tag, utf_i)
        elif tag in (CONSTANT_Fieldref, CONSTANT_Methodref,
                     CONSTANT_InterfaceMethodref, CONSTANT_NameAndType,
                     CONSTANT_Dynamic, CONSTANT_InvokeDynamic):
            a, b = struct.unpack_from(">HH", data, pos)
            pos += 4
            cp.entries[n] = (tag, a, b)
        elif tag == CONSTANT_MethodHandle:
            kind = data[pos]
            (ref_i,) = struct.unpack_from(">H", data, pos + 1)
            pos += 3
            cp.entries[n] = (tag, kind, ref_i)
        elif tag in (CONSTANT_MethodType, CONSTANT_Module, CONSTANT_Package):
            (idx,) = struct.unpack_from(">H", data, pos)
            pos += 2
            cp.entries[n] = (tag, idx)
        else:
            raise ValueError(f"Unknown constant pool tag {tag} at slot {n}")
        n += 1
    return cp, pos


def _skip_attributes(data: bytes, pos: int) -> int:
    (count,) = struct.unpack_from(">H", data, pos)
    pos += 2
    for _ in range(count):
        (_name_i, length) = struct.unpack_from(">HI", data, pos)
        pos += 6 + length
    return pos


def _read_code_attribute(body: bytes) -> tuple[int, int, bytes]:
    """Return (max_stack, max_locals, bytecode) from a Code attribute body."""
    max_stack, max_locals, code_len = struct.unpack_from(">HHI", body, 0)
    bytecode = body[8:8 + code_len]
    return max_stack, max_locals, bytecode


def parse(data: bytes) -> ClassFile:
    """Parse raw class-file bytes into a ClassFile."""
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("Not a Java class file (bad magic)")
    pos = 8  # skip magic + minor/major version
    cp, pos = _read_constant_pool(data, pos)

    access_flags, this_class, super_class = struct.unpack_from(">HHH", data, pos)
    pos += 6
    this_name = cp.class_name(this_class)
    super_name = cp.class_name(super_class) if super_class else ""

    (iface_count,) = struct.unpack_from(">H", data, pos)
    pos += 2
    interfaces = []
    for _ in range(iface_count):
        (ci,) = struct.unpack_from(">H", data, pos)
        pos += 2
        interfaces.append(cp.class_name(ci))

    # fields
    fields: list[FieldInfo] = []
    (field_count,) = struct.unpack_from(">H", data, pos)
    pos += 2
    for _ in range(field_count):
        f_access, name_i, desc_i = struct.unpack_from(">HHH", data, pos)
        pos += 6
        fields.append(FieldInfo(cp.utf8(name_i), cp.utf8(desc_i), f_access))
        pos = _skip_attributes(data, pos)

    # methods
    methods: list[Method] = []
    (method_count,) = struct.unpack_from(">H", data, pos)
    pos += 2
    for _ in range(method_count):
        m_access, name_i, desc_i = struct.unpack_from(">HHH", data, pos)
        pos += 6
        method = Method(cp.utf8(name_i), cp.utf8(desc_i), m_access)
        (attr_count,) = struct.unpack_from(">H", data, pos)
        pos += 2
        for _ in range(attr_count):
            attr_name_i, attr_len = struct.unpack_from(">HI", data, pos)
            pos += 6
            attr_body = data[pos:pos + attr_len]
            pos += attr_len
            if cp.utf8(attr_name_i) == "Code":
                ms, ml, bc = _read_code_attribute(attr_body)
                method.max_stack, method.max_locals, method.code = ms, ml, bc
        methods.append(method)

    return ClassFile(
        name=this_name,
        super_name=super_name,
        interfaces=interfaces,
        constant_pool=cp,
        fields=fields,
        methods=methods,
    )
