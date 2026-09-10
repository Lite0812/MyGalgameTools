import struct
import os
import re
from enum import IntEnum, IntFlag
from majiro_crc import Crc

class MjoType(IntEnum):
    Int = 0
    Float = 1
    String = 2
    IntArray = 3
    FloatArray = 4
    StringArray = 5
    Unknown = 255

class MjoTypeMask(IntFlag):
    Int = 1 << MjoType.Int
    Float = 1 << MjoType.Float
    String = 1 << MjoType.String
    IntArray = 1 << MjoType.IntArray
    FloatArray = 1 << MjoType.FloatArray
    StringArray = 1 << MjoType.StringArray

    Numeric = Int | Float
    Primitive = Int | Float | String
    Array = IntArray | FloatArray | StringArray
    All = Primitive | Array
    None_ = 0

class MjoScope(IntEnum):
    Persistent = 0
    SaveFile = 1
    Thread = 2
    Local = 3

class MjoInvertMode(IntEnum):
    None_ = 0
    Numeric = 1
    Boolean = 2
    Bitwise = 3

class MjoModifier(IntEnum):
    None_ = 0
    PreIncrement = 1
    PreDecrement = 2
    PostIncrement = 3
    PostDecrement = 4

class MjoFlagMask(IntFlag):
    Dim      = 0b00011000_00000000
    Type     = 0b00000111_00000000
    Scope    = 0b00000000_11100000
    Invert   = 0b00000000_00011000
    Modifier = 0b00000000_00000111

class MjoFlags:
    @staticmethod
    def build(type_: MjoType, scope: MjoScope, modifier: MjoModifier, invert_mode: MjoInvertMode, dimension: int) -> int:
        return (dimension << 11) | (type_ << 8) | (scope << 5) | (invert_mode << 3) | modifier

    @staticmethod
    def dimension(flags: int) -> int:
        return (flags & MjoFlagMask.Dim) >> 11

    @staticmethod
    def type_(flags: int) -> MjoType:
        return MjoType((flags & MjoFlagMask.Type) >> 8)

    @staticmethod
    def scope(flags: int) -> MjoScope:
        return MjoScope((flags & MjoFlagMask.Scope) >> 5)

    @staticmethod
    def invert_mode(flags: int) -> MjoInvertMode:
        return MjoInvertMode((flags & MjoFlagMask.Invert) >> 3)

    @staticmethod
    def modifier(flags: int) -> MjoModifier:
        return MjoModifier(flags & MjoFlagMask.Modifier)

class Instruction:
    def __init__(self, opcode, offset=None):
        self.opcode = opcode
        self.offset = offset
        self.size = 0
        
        # Operands
        self.flags = 0 # ushort
        self.hash = 0 # uint
        self.var_offset = 0 # short
        self.type_list = [] # List[MjoType]
        self.string = None # str
        self.external_key = None # str
        self.int_value = 0 # int
        self.float_value = 0.0 # float
        self.argument_count = 0 # ushort
        self.line_number = 0 # ushort
        self.jump_offset = 0 # int (relative)
        self.switch_offsets = [] # List[int] (relative)

    def __repr__(self):
        return f"<Instruction {self.opcode.mnemonic} @ {self.offset}>"

class FunctionIndexEntry:
    def __init__(self, name_hash, offset):
        self.name_hash = name_hash
        self.offset = offset

class MjoScript:
    def __init__(self):
        self.signature = "MajiroObjX1.000\0"
        self.is_encrypted = True
        self.entry_point_offset = 0
        self.enable_read_mark = True
        self.function_index = [] # List[FunctionIndexEntry]
        self.instructions = [] # List[Instruction]
        self.externalized_strings = {} # Dict[str, str]

    def sanity_check(self):
        pass # TODO: Implement sanity check if needed

SYSCALL_SUFFIX = "@MAJIRO_INTER"
KNOWN_SYSCALL_NAMES_BY_HASH = {}
KNOWN_FUNCTION_NAMES_BY_HASH = {}
KNOWN_VARIABLE_NAMES_BY_HASH = {}
KNOWN_SYSCALL_NAMES = set()
_KNOWN_NAMES_LOADED = False

def _extract_string_list(text, name):
    pattern = re.compile(rf"{name}\s*=\s*new List<string>\s*{{(.*?)}};", re.S)
    match = pattern.search(text)
    if not match:
        return []
    body = match.group(1)
    return re.findall(r'"([^"]*)"', body)

def load_known_names():
    global _KNOWN_NAMES_LOADED
    if _KNOWN_NAMES_LOADED:
        return
    _KNOWN_NAMES_LOADED = True
    data_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "MajiroLib", "Data.cs"))
    if not os.path.exists(data_path):
        return
    with open(data_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    syscalls = _extract_string_list(text, "KnownSyscallNames")
    groups = _extract_string_list(text, "KnownGroupNames")
    functions = _extract_string_list(text, "KnownFunctionNames")
    variables = _extract_string_list(text, "KnownVariableNames")
    for name in syscalls:
        KNOWN_SYSCALL_NAMES.add(name)
        h = Crc.hash32_str("$" + name + SYSCALL_SUFFIX)
        KNOWN_SYSCALL_NAMES_BY_HASH[h] = name
    for group in groups:
        name = "$main@" + group
        h = Crc.hash32_str(name)
        KNOWN_FUNCTION_NAMES_BY_HASH[h] = name
    for name in functions:
        h = Crc.hash32_str(name)
        KNOWN_FUNCTION_NAMES_BY_HASH[h] = name
    for name in variables:
        h = Crc.hash32_str(name)
        KNOWN_VARIABLE_NAMES_BY_HASH[h] = name
