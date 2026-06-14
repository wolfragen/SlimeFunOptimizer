"""Dev tool: readable JVM disassembly of a class method from any plugin jar.
Usage: python tools/disasm.py <class/path.class> [method ...]   (omit methods to list them)
Searches plugins/*.jar for the class. Resolves field/method/ldc refs and common opcodes.
"""
import sys, pathlib, zipfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from extractor import classfile, bytecode
N={0x60:'iadd',0x64:'isub',0x68:'imul',0x6c:'idiv',0x70:'irem',0x1a:'iload_0',0x1b:'iload_1',0x1c:'iload_2',0x1d:'iload_3',0x15:'iload',0x36:'istore',0x2a:'aload_0',0x2b:'aload_1',0x2c:'aload_2',0x2d:'aload_3',0x19:'aload',0x3a:'astore',0x99:'ifeq',0x9a:'ifne',0x9b:'iflt',0x9c:'ifge',0x9d:'ifgt',0x9e:'ifle',0x9f:'if_icmpeq',0xa0:'if_icmpne',0xa1:'if_icmplt',0xa2:'if_icmpge',0xa3:'if_icmpgt',0xa4:'if_icmple',0xa7:'goto',0xac:'ireturn',0x84:'iinc',0xc7:'ifnonnull',0xc6:'ifnull',0xb0:'areturn',0x57:'pop',0x59:'dup',0xb1:'return',0x4e:'astore_3',0x3b:'istore_0',0x3c:'istore_1',0x3d:'istore_2',0x3e:'istore_3'}
def find(cls):
    for jp in pathlib.Path('plugins').glob('*.jar'):
        zf=zipfile.ZipFile(jp)
        if cls in zf.namelist(): return classfile.parse(zf.read(cls))
    raise SystemExit('not found: '+cls)
cls=sys.argv[1]; cf=find(cls); cp=cf.constant_pool
methods=sys.argv[2:]
if not methods:
    for m in cf.methods: print(m.name+m.descriptor)
    raise SystemExit
for mn in methods:
    for m in cf.methods:
        if m.name==mn and m.code:
            print(f"=== {m.name}{m.descriptor} ===")
            for x in bytecode.iter_instructions(m.code):
                op=x.opcode; nm=bytecode.OPCODES.get(op) or N.get(op); nm=nm[0] if isinstance(nm,tuple) else (nm or hex(op)); e=''
                try:
                    if op in(0xb2,0xb3,0xb4,0xb5): o,f,d=cp.field_ref(x.u16()); e=f"{o.split('/')[-1]}.{f}"
                    elif op in(0xb6,0xb7,0xb8,0xb9): o,n,d=cp.method_ref(x.u16()); e=f"{o.split('/')[-1]}.{n}{d.split(')')[-1]}"
                    elif op in(0x12,0x13): idx=x.u8() if op==0x12 else x.u16(); k,v=cp.ldc_value(idx); e=repr(v)
                    elif op==0x10: e=str(x.s8())
                    elif op==0x11: e=str(x.s16())
                    elif op in bytecode.ICONST_VALUES: e=str(bytecode.ICONST_VALUES[op])
                    elif op in(0x15,0x36,0x19,0x3a): e=f"#{x.u8()}"
                except Exception as ex: e=f"<{ex}>"
                print(f"  {x.offset:4} {nm:<14}{e}")
            print()
