from majiro_script import MjoTypeMask

class Opcode:
    def __init__(self, value, mnemonic, operator, encoding, transition, aliases=None):
        self.value = value
        self.mnemonic = mnemonic
        self.operator = operator
        self.encoding = encoding
        self.transition = transition
        self.aliases = aliases or []

    def __repr__(self):
        return f"<Opcode {self.mnemonic} ({self.value:04x})>"

OPCODES = {}
OPCODES_BY_MNEMONIC = {}

def _register(op):
    OPCODES[op.value] = op
    OPCODES_BY_MNEMONIC[op.mnemonic] = op
    for alias in op.aliases:
        OPCODES_BY_MNEMONIC[alias] = op

def define_opcode(value, mnemonic, op, encoding, transition, *aliases):
    _register(Opcode(value, mnemonic, op, encoding, transition, aliases))

def define_binary_operator(base_value, mnemonic, op, allowed_types, is_comparison, *aliases):
    if allowed_types & MjoTypeMask.Int:
        if allowed_types == MjoTypeMask.Int:
             transition = "ii.b" if is_comparison else "ii.i"
             new_aliases = []
             for a in aliases:
                 new_aliases.append(a)
                 new_aliases.append(a + ".i")
             new_aliases.insert(0, mnemonic + ".i")
             define_opcode(base_value, mnemonic, op, "", transition, *new_aliases)
        else:
             transition = "ii.b" if is_comparison else "ii.i"
             new_aliases = []
             new_aliases.append(mnemonic)
             for a in aliases:
                 new_aliases.append(a + ".i")
                 new_aliases.append(a)
             define_opcode(base_value, mnemonic + ".i", op, "", transition, *new_aliases)

    if allowed_types & MjoTypeMask.Float:
        transition = "nn.b" if is_comparison else "nn.f"
        new_aliases = [a + ".r" for a in aliases]
        define_opcode(base_value + 1, mnemonic + ".r", op, "", transition, *new_aliases)

    if allowed_types & MjoTypeMask.String:
        transition = "ss.b" if is_comparison else "ss.s"
        new_aliases = [a + ".s" for a in aliases]
        define_opcode(base_value + 2, mnemonic + ".s", op, "", transition, *new_aliases)

    if allowed_types & MjoTypeMask.IntArray:
        transition = "II.b" if is_comparison else "-"
        new_aliases = [a + ".iarr" for a in aliases]
        define_opcode(base_value + 3, mnemonic + ".iarr", op, "", transition, *new_aliases)

    if allowed_types & MjoTypeMask.FloatArray:
        transition = "FF.b" if is_comparison else "-"
        new_aliases = [a + ".rarr" for a in aliases]
        define_opcode(base_value + 4, mnemonic + ".rarr", op, "", transition, *new_aliases)

    if allowed_types & MjoTypeMask.StringArray:
        transition = "SS.b" if is_comparison else "-"
        new_aliases = [a + ".sarr" for a in aliases]
        define_opcode(base_value + 5, mnemonic + ".sarr", op, "", transition, *new_aliases)

def define_assignment_operator(base_value, mnemonic, op, allowed_types, pop, *aliases):
    encoding = "fho"
    
    if allowed_types & MjoTypeMask.Int:
        if allowed_types == MjoTypeMask.Int:
            transition = "i." if pop else "i.i"
            new_aliases = []
            for a in aliases:
                new_aliases.append(a)
                new_aliases.append(a + ".i")
            new_aliases.insert(0, mnemonic + ".i")
            define_opcode(base_value, mnemonic, op, encoding, transition, *new_aliases)
        else:
            transition = "i." if pop else "i.i"
            new_aliases = []
            new_aliases.append(mnemonic)
            for a in aliases:
                new_aliases.append(a + ".i")
                new_aliases.append(a)
            define_opcode(base_value, mnemonic + ".i", op, encoding, transition, *new_aliases)

    if allowed_types & MjoTypeMask.Float:
        transition = "n." if pop else "n.f"
        new_aliases = [a + ".r" for a in aliases]
        define_opcode(base_value + 1, mnemonic + ".r", op, encoding, transition, *new_aliases)

    if allowed_types & MjoTypeMask.String:
        transition = "s." if pop else "s.s"
        new_aliases = [a + ".s" for a in aliases]
        define_opcode(base_value + 2, mnemonic + ".s", op, encoding, transition, *new_aliases)

    if allowed_types & MjoTypeMask.IntArray:
        transition = "I." if pop else "I.I"
        new_aliases = [a + ".iarr" for a in aliases]
        define_opcode(base_value + 3, mnemonic + ".iarr", op, encoding, transition, *new_aliases)

    if allowed_types & MjoTypeMask.FloatArray:
        transition = "F." if pop else "F.F"
        new_aliases = [a + ".rarr" for a in aliases]
        define_opcode(base_value + 4, mnemonic + ".rarr", op, encoding, transition, *new_aliases)

    if allowed_types & MjoTypeMask.StringArray:
        transition = "S." if pop else "S.S"
        new_aliases = [a + ".sarr" for a in aliases]
        define_opcode(base_value + 5, mnemonic + ".sarr", op, encoding, transition, *new_aliases)

def define_array_assignment_operator(base_value, mnemonic, op, allowed_types, pop, *aliases):
    encoding = "fho"
    
    if allowed_types & MjoTypeMask.Int:
        if allowed_types == MjoTypeMask.Int:
            transition = "i[i#d]." if pop else "i[i#d].i"
            new_aliases = []
            for a in aliases:
                new_aliases.append(a)
                new_aliases.append(a + ".i")
            new_aliases.insert(0, mnemonic + ".i")
            define_opcode(base_value, mnemonic, op, encoding, transition, *new_aliases)
        else:
            transition = "i[i#d]." if pop else "i[i#d].i"
            new_aliases = []
            new_aliases.append(mnemonic)
            for a in aliases:
                new_aliases.append(a + ".i")
                new_aliases.append(a)
            define_opcode(base_value, mnemonic + ".i", op, encoding, transition, *new_aliases)

    if allowed_types & MjoTypeMask.Float:
        transition = "n[i#d]." if pop else "n[i#d].f"
        new_aliases = [a + ".r" for a in aliases]
        define_opcode(base_value + 1, mnemonic + ".r", op, encoding, transition, *new_aliases)

    if allowed_types & MjoTypeMask.String:
        transition = "s[i#d]." if pop else "s[i#d].s"
        new_aliases = [a + ".s" for a in aliases]
        define_opcode(base_value + 2, mnemonic + ".s", op, encoding, transition, *new_aliases)


# --- Definition Block ---
# binary operators
define_binary_operator(0x100, "mul",  "*",  MjoTypeMask.Numeric, False)
define_binary_operator(0x108, "div",  "/",  MjoTypeMask.Numeric, False)
define_binary_operator(0x110, "rem",  "%",  MjoTypeMask.Int, False, "mod")
define_binary_operator(0x118, "add",  "+",  MjoTypeMask.Primitive, False)
define_binary_operator(0x120, "sub",  "-",  MjoTypeMask.Numeric, False)
define_binary_operator(0x128, "shr",  ">>", MjoTypeMask.Int, False)
define_binary_operator(0x130, "shl",  "<<", MjoTypeMask.Int, False)
define_binary_operator(0x138, "cle",  "<=", MjoTypeMask.Primitive, True)
define_binary_operator(0x140, "clt",  "<",  MjoTypeMask.Primitive, True)
define_binary_operator(0x148, "cge",  ">=", MjoTypeMask.Primitive, True)
define_binary_operator(0x150, "cgt",  ">",  MjoTypeMask.Primitive, True)
define_binary_operator(0x158, "ceq",  "==", MjoTypeMask.All, True)
define_binary_operator(0x160, "cne",  "!=", MjoTypeMask.All, True)
define_binary_operator(0x168, "xor",  "^",  MjoTypeMask.Int, False)
define_binary_operator(0x170, "andl", "&&", MjoTypeMask.Int, False)
define_binary_operator(0x178, "orl",  "||", MjoTypeMask.Int, False)
define_binary_operator(0x180, "and",  "&",  MjoTypeMask.Int, False)
define_binary_operator(0x188, "or",   "|",  MjoTypeMask.Int, False)

# unary operators / nops
define_opcode(0x190, "notl",  "!", "", "i.i", "notl.i")
define_opcode(0x198, "not",   "~", "", "i.i", "not.i")
define_opcode(0x1a0, "neg.i", "-", "", "i.i")
define_opcode(0x1a1, "neg.r", "-", "", "f.f")

define_opcode(0x191, "nop.191", None, "", "")
define_opcode(0x1a8, "nop.1a8", None, "", "")
define_opcode(0x1a9, "nop.1a9", None, "", "")

# assignment operators
define_assignment_operator(0x1b0, "st",     "=",   MjoTypeMask.All, False)
define_assignment_operator(0x1b8, "st.mul", "*=",  MjoTypeMask.Numeric, False)
define_assignment_operator(0x1c0, "st.div", "/=",  MjoTypeMask.Numeric, False)
define_assignment_operator(0x1c8, "st.rem", "%=",  MjoTypeMask.Int, False, "st.mod")
define_assignment_operator(0x1d0, "st.add", "+=",  MjoTypeMask.Primitive, False)
define_assignment_operator(0x1d8, "st.sub", "-=",  MjoTypeMask.Numeric, False)
define_assignment_operator(0x1e0, "st.shl", "<<=", MjoTypeMask.Int, False)
define_assignment_operator(0x1e8, "st.shr", ">>=", MjoTypeMask.Int, False)
define_assignment_operator(0x1f0, "st.and", "&=",  MjoTypeMask.Int, False)
define_assignment_operator(0x1f8, "st.xor", "^=",  MjoTypeMask.Int, False)
define_assignment_operator(0x200, "st.or",  "|=",  MjoTypeMask.Int, False)

define_assignment_operator(0x210, "stp",     "=",   MjoTypeMask.All, True)
define_assignment_operator(0x218, "stp.mul", "*=",  MjoTypeMask.Numeric, True)
define_assignment_operator(0x220, "stp.div", "/=",  MjoTypeMask.Numeric, True)
define_assignment_operator(0x228, "stp.rem", "%=",  MjoTypeMask.Int, True, "stp.mod")
define_assignment_operator(0x230, "stp.add", "+=",  MjoTypeMask.Primitive, True)
define_assignment_operator(0x238, "stp.sub", "-=",  MjoTypeMask.Numeric, True)
define_assignment_operator(0x240, "stp.shl", "<<=", MjoTypeMask.Int, True)
define_assignment_operator(0x248, "stp.shr", ">>=", MjoTypeMask.Int, True)
define_assignment_operator(0x250, "stp.and", "&=",  MjoTypeMask.Int, True)
define_assignment_operator(0x258, "stp.xor", "^=",  MjoTypeMask.Int, True)
define_assignment_operator(0x260, "stp.or",  "|=",  MjoTypeMask.Int, True)

# array assignment operators
define_array_assignment_operator(0x270, "stelem",     "=",   MjoTypeMask.Primitive, False)
define_array_assignment_operator(0x278, "stelem.mul", "*=",  MjoTypeMask.Numeric, False)
define_array_assignment_operator(0x280, "stelem.div", "/=",  MjoTypeMask.Numeric, False)
define_array_assignment_operator(0x288, "stelem.rem", "%=",  MjoTypeMask.Int, False, "stelem.mod")
define_array_assignment_operator(0x290, "stelem.add", "+=",  MjoTypeMask.Primitive, False)
define_array_assignment_operator(0x298, "stelem.sub", "-=",  MjoTypeMask.Numeric, False)
define_array_assignment_operator(0x2a0, "stelem.shl", "<<=", MjoTypeMask.Int, False)
define_array_assignment_operator(0x2a8, "stelem.shr", ">>=", MjoTypeMask.Int, False)
define_array_assignment_operator(0x2b0, "stelem.and", "&=",  MjoTypeMask.Int, False)
define_array_assignment_operator(0x2b8, "stelem.xor", "^=",  MjoTypeMask.Int, False)
define_array_assignment_operator(0x2c0, "stelem.or",  "|=",  MjoTypeMask.Int, False)

define_array_assignment_operator(0x2d0, "stelemp",     "=",   MjoTypeMask.Primitive, True)
define_array_assignment_operator(0x2d8, "stelemp.mul", "*=",  MjoTypeMask.Numeric, True)
define_array_assignment_operator(0x2e0, "stelemp.div", "/=",  MjoTypeMask.Numeric, True)
define_array_assignment_operator(0x2e8, "stelemp.rem", "%=",  MjoTypeMask.Int, True, "stelemp.mod")
define_array_assignment_operator(0x2f0, "stelemp.add", "+=",  MjoTypeMask.Primitive, True)
define_array_assignment_operator(0x2f8, "stelemp.sub", "-=",  MjoTypeMask.Numeric, True)
define_array_assignment_operator(0x300, "stelemp.shl", "<<=", MjoTypeMask.Int, True)
define_array_assignment_operator(0x308, "stelemp.shr", ">>=", MjoTypeMask.Int, True)
define_array_assignment_operator(0x310, "stelemp.and", "&=",  MjoTypeMask.Int, True)
define_array_assignment_operator(0x318, "stelemp.xor", "^=",  MjoTypeMask.Int, True)
define_array_assignment_operator(0x320, "stelemp.or",  "|=",  MjoTypeMask.Int, True)

# 0800 range opcodes
define_opcode(0x800, "ldc.i", None, "i", ".i")
define_opcode(0x801, "ldstr", None, "s", ".s", "ldc.s")
define_opcode(0x802, "ld", None, "fho", ".#t", "ldvar")
define_opcode(0x803, "ldc.r", None, "r", ".f")

define_opcode(0x80f, "call",  None, "h0a", "[*#a].*")
define_opcode(0x810, "callp", None, "h0a", "[*#a].")

define_opcode(0x829, "alloca", None, "t", ".[#t]")
define_opcode(0x82b, "ret", None, "", "[*].", "return")

define_opcode(0x82c, "br", None, "j", ".", "jmp")
define_opcode(0x82d, "brtrue", None, "j", "p.", "brinst", "jnz", "jne")
define_opcode(0x82e, "brfalse", None, "j", "p.", "brnull", "brzero", "jz", "je")

define_opcode(0x82f, "pop", None, "", "*.")

define_opcode(0x830, "br.case", None, "j", "p.", "br.v", "jmp.v")
define_opcode(0x831, "bne.case", None, "j", "p.", "bne.v", "jne.v")
define_opcode(0x832, "bge.case", None, "j", "p.", "bge.v", "jge.v")
define_opcode(0x833, "ble.case", None, "j", "p.", "ble.v", "jle.v")
define_opcode(0x838, "blt.case", None, "j", "p.", "blt.v", "jlt.v")
define_opcode(0x839, "bgt.case", None, "j", "p.", "bgt.v", "jgt.v")

define_opcode(0x834, "syscall",  None, "ha", "[*#a].*")
define_opcode(0x835, "syscallp", None, "ha", "[*#a].")

define_opcode(0x836, "argcheck", None, "t", ".[#t]", "sigchk")

define_opcode(0x837, "ldelem", None, "fho", "[i#d].#t")

define_opcode(0x83a, "line", None, "l", ".")

define_opcode(0x83b, "bsel.1", None, "j", ".")
define_opcode(0x83c, "bsel.3", None, "j", ".")
define_opcode(0x83d, "bsel.2", None, "j", ".")

define_opcode(0x83e, "conv.i", None, "", "f.i")
define_opcode(0x83f, "conv.r", None, "", "i.f")

define_opcode(0x840, "text", None, "s", ".")
define_opcode(0x841, "proc", None, "", ".")
define_opcode(0x842, "ctrl", None, "s", "[#s].")
define_opcode(0x843, "bsel.x", None, "j", ".")
define_opcode(0x844, "bsel.clr", None, "", ".")
define_opcode(0x845, "bsel.4", None, "j", ".")
define_opcode(0x846, "bsel.jmp.4", None, "", ".")
define_opcode(0x847, "bsel.5", None, "j", ".")

define_opcode(0x850, "switch", None, "c", "i.")
