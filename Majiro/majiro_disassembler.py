import struct
import io
from majiro_script import MjoScript, Instruction, MjoType, MjoFlags, MjoScope, MjoModifier, MjoInvertMode, FunctionIndexEntry, load_known_names, KNOWN_SYSCALL_NAMES_BY_HASH, KNOWN_FUNCTION_NAMES_BY_HASH, KNOWN_VARIABLE_NAMES_BY_HASH
from majiro_opcodes import OPCODES, OPCODES_BY_MNEMONIC
from majiro_crc import Crc

class BinaryReader:
    def __init__(self, stream):
        self.stream = stream

    def read_byte(self):
        b = self.stream.read(1)
        if not b: raise EOFError()
        return b[0]

    def read_bytes(self, count):
        b = self.stream.read(count)
        if len(b) < count: raise EOFError()
        return b

    def read_uint16(self):
        return struct.unpack('<H', self.read_bytes(2))[0]

    def read_int16(self):
        return struct.unpack('<h', self.read_bytes(2))[0]

    def read_uint32(self):
        return struct.unpack('<I', self.read_bytes(4))[0]

    def read_int32(self):
        return struct.unpack('<i', self.read_bytes(4))[0]

    def read_float(self):
        return struct.unpack('<f', self.read_bytes(4))[0]

    def read_string_fixed(self, size):
        return self.read_bytes(size).decode('ascii').rstrip('\0')

    def position(self):
        return self.stream.tell()

class Disassembler:
    def __init__(self, encoding='shift_jis'):
        self.encoding = encoding
        load_known_names()
    
    def _format_float32(self, value):
        packed = struct.pack('<f', value)
        target_value = struct.unpack('<f', packed)[0]
        for precision in range(1, 10):
            s = format(target_value, f".{precision}g")
            if struct.pack('<f', float(s)) == packed:
                if 'e' in s or 'E' in s:
                    abs_val = abs(target_value)
                    if abs_val >= 1e-4 and abs_val < 1e7:
                        fixed = format(target_value, ".9f").rstrip('0').rstrip('.')
                        return "0" if fixed == "-0" else fixed
                return s
        s = format(target_value, ".9g")
        if 'e' in s or 'E' in s:
            abs_val = abs(target_value)
            if abs_val >= 1e-4 and abs_val < 1e7:
                fixed = format(target_value, ".9f").rstrip('0').rstrip('.')
                return "0" if fixed == "-0" else fixed
        return s

    def disassemble_script(self, stream):
        reader = BinaryReader(stream)
        
        signature = reader.read_bytes(16)
        # Check signature
        if signature == b"MajiroObjX1.000\0":
            is_encrypted = True
        elif signature == b"MajiroObjV1.000\0":
            is_encrypted = False
        else:
            raise Exception(f"Invalid signature: {signature}")

        entry_point_offset = reader.read_uint32()
        read_mark_size = reader.read_uint32()
        function_count = reader.read_int32()
        
        function_index = []
        for _ in range(function_count):
            name_hash = reader.read_uint32()
            offset = reader.read_uint32()
            function_index.append(FunctionIndexEntry(name_hash, offset))

        byte_code_size = reader.read_int32()
        byte_code = bytearray(reader.read_bytes(byte_code_size))
        
        if is_encrypted:
            Crc.crypt32(byte_code)
            
        script = MjoScript()
        script.signature = signature.decode('ascii').rstrip('\0')
        script.is_encrypted = is_encrypted
        script.entry_point_offset = entry_point_offset
        script.enable_read_mark = read_mark_size != 0
        script.function_index = function_index
        
        # Disassemble bytecode
        bc_stream = io.BytesIO(byte_code)
        self._disassemble_bytecode(bc_stream, script)
        
        return script

    def _disassemble_bytecode(self, stream, script):
        reader = BinaryReader(stream)
        end_pos = stream.getbuffer().nbytes
        
        while stream.tell() < end_pos:
            offset = stream.tell()
            instruction = self._read_instruction(reader, offset)
            script.instructions.append(instruction)

    def _read_instruction(self, reader, offset):
        opcode_value = reader.read_uint16()
        if opcode_value not in OPCODES:
            raise Exception(f"Invalid opcode at offset 0x{offset:08X}: 0x{opcode_value:04X}")
        
        opcode = OPCODES[opcode_value]
        instruction = Instruction(opcode, offset)
        
        for operand in opcode.encoding:
            if operand == 't':
                count = reader.read_uint16()
                instruction.type_list = [MjoType(b) for b in reader.read_bytes(count)]
            elif operand == 's':
                size = reader.read_uint16()
                string_bytes = reader.read_bytes(size - 1)
                null_terminator = reader.read_byte()
                if null_terminator != 0:
                    raise Exception("String not null-terminated")
                instruction.string = string_bytes.decode(self.encoding, errors='replace')
            elif operand == 'f':
                instruction.flags = reader.read_uint16()
            elif operand == 'h':
                instruction.hash = reader.read_uint32()
            elif operand == 'o':
                instruction.var_offset = reader.read_int16()
            elif operand == '0':
                placeholder = reader.read_uint32()
                if placeholder != 0:
                    pass # Warning?
            elif operand == 'i':
                instruction.int_value = reader.read_int32()
            elif operand == 'r':
                instruction.float_value = reader.read_float()
            elif operand == 'a':
                instruction.argument_count = reader.read_uint16()
            elif operand == 'j':
                instruction.jump_offset = reader.read_int32()
            elif operand == 'l':
                instruction.line_number = reader.read_uint16()
            elif operand == 'c':
                count = reader.read_uint16()
                instruction.switch_offsets = [reader.read_int32() for _ in range(count)]
            else:
                raise Exception(f"Unrecognized encoding specifier: {operand}")
        
        instruction.size = reader.position() - offset
        return instruction

    def print_script(self, script, writer):
        def format_relative_jump(offset):
            if offset < 0:
                return f"@~-{-offset:04x}"
            if offset > 0:
                return f"@~+{offset:04x}"
            return "@~0"

        def switch_target_offset(instr, index, rel_offset):
            return instr.offset + 2 + 2 + 4 * (index + 1) + rel_offset

        def compute_labels(instrs, func_start):
            labels = {func_start: "entry"}
            for i, instr in enumerate(instrs):
                if 'j' in instr.opcode.encoding:
                    target = instr.offset + instr.size + instr.jump_offset
                    labels.setdefault(target, f"block_{target:04x}")
                if 'c' in instr.opcode.encoding:
                    for idx, rel_offset in enumerate(instr.switch_offsets):
                        target = switch_target_offset(instr, idx, rel_offset)
                        labels.setdefault(target, f"block_{target:04x}")
                if 'j' in instr.opcode.encoding or 'c' in instr.opcode.encoding:
                    if i + 1 < len(instrs):
                        next_offset = instrs[i + 1].offset
                        labels.setdefault(next_offset, f"block_{next_offset:04x}")
            return labels

        if script.enable_read_mark:
            writer.write("readmark enable\n")
        else:
            writer.write("readmark disable\n")
            
        writer.write("\n")
        
        # Sort functions by offset
        sorted_functions = sorted(script.function_index, key=lambda x: x.offset)
        
        # Function ranges
        func_ranges = []
        for i in range(len(sorted_functions)):
            start = sorted_functions[i].offset
            end = sorted_functions[i+1].offset if i + 1 < len(sorted_functions) else None
            func_ranges.append((start, end, sorted_functions[i]))
            
        # Group instructions by function
        instr_by_func = {}
        
        # Assign instructions to functions
        # Instructions are sorted by offset
        func_idx = 0
        current_func_start = -1
        current_func_end = -1
        
        if sorted_functions:
            current_func_start = sorted_functions[0].offset
            current_func_end = sorted_functions[1].offset if len(sorted_functions) > 1 else float('inf')
        
        for instr in script.instructions:
            # Check if we moved to next function
            while func_idx + 1 < len(sorted_functions) and instr.offset >= sorted_functions[func_idx+1].offset:
                func_idx += 1
                current_func_start = sorted_functions[func_idx].offset
                current_func_end = sorted_functions[func_idx+1].offset if func_idx + 1 < len(sorted_functions) else float('inf')
            
            if sorted_functions and instr.offset >= current_func_start:
                if current_func_start not in instr_by_func: instr_by_func[current_func_start] = []
                instr_by_func[current_func_start].append(instr)

        def print_operands(instr, labels):
            for operand in instr.opcode.encoding:
                if operand == '0': continue
                
                writer.write(" ")
                
                if operand == 'o' and MjoFlags.scope(instr.flags) != MjoScope.Local and instr.var_offset == -1:
                    continue

                if operand == 't':
                    writer.write("[")
                    writer.write(", ".join(t.name.lower() for t in instr.type_list))
                    writer.write("]")
                elif operand == 's':
                    escaped = instr.string.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                    writer.write(f'"{escaped}"')
                elif operand == 'f':
                    flags = instr.flags
                    parts = []
                    parts.append(MjoFlags.scope(flags).name.lower())
                    parts.append(MjoFlags.type_(flags).name.lower())
                    
                    inv = MjoFlags.invert_mode(flags)
                    if inv == MjoInvertMode.Numeric: parts.append("invert_numeric")
                    elif inv == MjoInvertMode.Boolean: parts.append("invert_boolean")
                    elif inv == MjoInvertMode.Bitwise: parts.append("invert_bitwise")
                    
                    mod = MjoFlags.modifier(flags)
                    if mod == MjoModifier.PreIncrement: parts.append("preinc")
                    elif mod == MjoModifier.PreDecrement: parts.append("predec")
                    elif mod == MjoModifier.PostIncrement: parts.append("postinc")
                    elif mod == MjoModifier.PostDecrement: parts.append("postdec")
                    
                    dim = MjoFlags.dimension(flags)
                    if dim == 1: parts.append("dim1")
                    elif dim == 2: parts.append("dim2")
                    elif dim == 3: parts.append("dim3")
                    
                    writer.write(" ".join(parts))
                elif operand == 'h':
                    is_syscall = instr.opcode.mnemonic in ("syscall", "syscallp")
                    is_call = instr.opcode.mnemonic in ("call", "callp")
                    if is_syscall and instr.hash in KNOWN_SYSCALL_NAMES_BY_HASH:
                        writer.write(f"${KNOWN_SYSCALL_NAMES_BY_HASH[instr.hash]}")
                    elif is_call and instr.hash in KNOWN_FUNCTION_NAMES_BY_HASH:
                        writer.write(f"${KNOWN_FUNCTION_NAMES_BY_HASH[instr.hash]}")
                    else:
                        writer.write(f"${instr.hash:08x}")
                elif operand == 'o':
                    writer.write(str(instr.var_offset))
                elif operand == 'i':
                    writer.write(str(instr.int_value))
                elif operand == 'r':
                    writer.write(self._format_float32(instr.float_value))
                elif operand == 'a':
                    writer.write(f"({instr.argument_count})")
                elif operand == 'j':
                    target = instr.offset + instr.size + instr.jump_offset
                    if target in labels:
                        writer.write(f"@{labels[target]}")
                    else:
                        writer.write(format_relative_jump(instr.jump_offset))
                elif operand == 'l':
                    writer.write(f"#{instr.line_number}")
                elif operand == 'c':
                    targets = []
                    for idx, rel_offset in enumerate(instr.switch_offsets):
                        target = switch_target_offset(instr, idx, rel_offset)
                        if target in labels:
                            targets.append(f"@{labels[target]}")
                        else:
                            targets.append(format_relative_jump(rel_offset))
                    writer.write(", ".join(targets))

        # Print functions
        for func in sorted_functions:
            start = func.offset
            instrs = instr_by_func.get(start, [])
            labels = compute_labels(instrs, start)
            
            # Find signature from argcheck
            sig_types = []
            for instr in instrs:
                if instr.opcode.mnemonic == "argcheck":
                    sig_types = instr.type_list
                    break
            
            sig_str = ", ".join(t.name.lower() for t in sig_types)
            is_entrypoint = script.entry_point_offset == func.offset
            header = f"func ${func.name_hash:08x}({sig_str}) "
            if is_entrypoint:
                header += "entrypoint "
            header += "{"
            if func.name_hash in KNOWN_FUNCTION_NAMES_BY_HASH:
                header += f" ; {KNOWN_FUNCTION_NAMES_BY_HASH[func.name_hash]}"
            writer.write(header + "\n")
            
            writer.write(" entry:\n")
            
            for instr in instrs:
                if instr.offset in labels:
                    label = labels[instr.offset]
                    if label != "entry":
                        writer.write(f" {label}:\n")
                
                writer.write(f"  {instr.opcode.mnemonic:<13}")
                print_operands(instr, labels)
                is_call = instr.opcode.mnemonic in ("call", "callp")
                is_load = instr.opcode.mnemonic.startswith("ld")
                is_store = instr.opcode.mnemonic.startswith("st")
                if is_call and instr.hash in KNOWN_FUNCTION_NAMES_BY_HASH:
                    writer.write(f" ; {KNOWN_FUNCTION_NAMES_BY_HASH[instr.hash]}")
                elif (is_load or is_store) and instr.hash in KNOWN_VARIABLE_NAMES_BY_HASH:
                    writer.write(f" ; {KNOWN_VARIABLE_NAMES_BY_HASH[instr.hash]}")
                writer.write("\n")
                
            writer.write("}\n\n")
