import struct
import shlex
import re
from majiro_opcodes import OPCODES_BY_MNEMONIC
from majiro_script import MjoScript, FunctionIndexEntry, MjoType, MjoFlags, MjoScope, MjoModifier, MjoInvertMode, SYSCALL_SUFFIX, load_known_names
from majiro_crc import Crc

class BinaryWriter:
    def __init__(self):
        self.data = bytearray()
    
    def write_bytes(self, b):
        self.data.extend(b)
    
    def write_uint16(self, v):
        self.data.extend(struct.pack('<H', v))
        
    def write_int16(self, v):
        self.data.extend(struct.pack('<h', v))
        
    def write_uint32(self, v):
        self.data.extend(struct.pack('<I', v))
        
    def write_int32(self, v):
        self.data.extend(struct.pack('<i', v))
        
    def write_float(self, v):
        self.data.extend(struct.pack('<f', v))
        
    def write_byte(self, v):
        self.data.append(v)
        
    def position(self):
        return len(self.data)
        
    def patch_int32(self, offset, value):
        struct.pack_into('<i', self.data, offset, value)

class Assembler:
    def __init__(self, encoding='shift_jis', other_encoding=None, encoding_errors='replace', text_encoding=None):
        # 兼容旧参数：encoding 作为 text 默认编码；如果提供 text_encoding 则覆盖
        self.text_encoding = text_encoding or encoding
        self.other_encoding = other_encoding or encoding
        self.encoding_errors = encoding_errors
        self.labels = {} # label_name -> offset
        self.backpatches = [] # list of dict
        self.functions = [] # list of (hash, is_entry)
        self.enable_read_mark = False
        self.writer = BinaryWriter()
        self.function_entry_labels = {}
        self.current_function_hash = None
        load_known_names()

    def _strip_inline_comment(self, line):
        in_string = False
        escape = False
        for i, c in enumerate(line):
            if in_string:
                if escape:
                    escape = False
                elif c == '\\':
                    escape = True
                elif c == '"':
                    in_string = False
            else:
                if c == '"':
                    in_string = True
                elif c == ';':
                    return line[:i]
        return line

    def _normalize_label(self, label):
        if label == "entry" and self.current_function_hash is not None:
            return f"entry_{self.current_function_hash:08x}"
        return label

    def _parse_relative_jump(self, token):
        s = token
        if s.startswith('@'):
            s = s[1:]
        if not s.startswith('~'):
            raise Exception(f"Invalid relative jump: {token}")
        s = s[1:]
        if s.startswith('+'):
            return int(s[1:], 16)
        if s.startswith('-'):
            return -int(s[1:], 16)
        if s == "0":
            return 0
        return int(s, 16)

    def _parse_hash_token(self, token):
        if not token.startswith('$'):
            return Crc.hash32_str(token, encoding=self.other_encoding)
        name = token[1:]
        if re.fullmatch(r"[0-9a-fA-F]+", name or ""):
            return int(name, 16)
        if '@' not in name:
            return Crc.hash32_str("$" + name + SYSCALL_SUFFIX, encoding=self.other_encoding)
        if name.startswith('$'):
            full_name = name
        else:
            full_name = "$" + name
        return Crc.hash32_str(full_name, encoding=self.other_encoding)
        
    def assemble(self, text):
        lines = text.splitlines()
        
        in_index = False
        
        processed_lines = []
        for line in lines:
            line = self._strip_inline_comment(line).strip()
            if not line: continue
            processed_lines.append(line)
            
        line_idx = 0
        while line_idx < len(processed_lines):
            line = processed_lines[line_idx]
            line_idx += 1
            
            if line == 'index':
                in_index = True
                continue
            
            if line.startswith('index '):
                parts = line.split()
                if len(parts) >= 2 and parts[1].startswith('$'):
                    hash_val = int(parts[1][1:], 16)
                    is_entry = any('entry' in p for p in parts[2:])
                    if not any(h == hash_val for h, _ in self.functions):
                        self.functions.append((hash_val, is_entry))
                continue
            
            if line.startswith('readmark '):
                if 'enable' in line:
                    self.enable_read_mark = True
                elif 'disable' in line:
                    self.enable_read_mark = False
                continue
            
            if line.startswith('func '):
                m = re.match(r"^func\s+\$([0-9a-fA-F]+)", line)
                if m:
                    hash_val = int(m.group(1), 16)
                    is_entry = 'entrypoint' in line
                    if not any(h == hash_val for h, _ in self.functions):
                        self.functions.append((hash_val, is_entry))
                    self.current_function_hash = hash_val
                continue
            
            if line == "{":
                continue
            if line == "}":
                self.current_function_hash = None
                continue
                
            if in_index:
                if not line.startswith('$'):
                    in_index = False
                    line_idx -= 1 
                    continue
                else:
                    parts = line.replace(',', ' ').split()
                    hash_str = parts[0].strip()[1:]
                    hash_val = int(hash_str, 16)
                    is_entry = any('entry' in p for p in parts[1:])
                    self.functions.append((hash_val, is_entry))
                    continue

            if line.endswith(':'):
                label_name = line[:-1].strip()
                if label_name.startswith('@'):
                    label_name = label_name[1:]
                label_name = self._normalize_label(label_name)
                if self.current_function_hash is not None and label_name.startswith("entry_"):
                    self.function_entry_labels[self.current_function_hash] = label_name
                self.labels[label_name] = self.writer.position()
                continue
                
            # Instruction
            try:
                tokens = shlex.split(line)
            except ValueError:
                tokens = line.split()
                
            if not tokens: continue
            
            mnemonic = tokens.pop(0)
            if mnemonic not in OPCODES_BY_MNEMONIC:
                raise Exception(f"Unknown mnemonic: {mnemonic}")
                
            opcode = OPCODES_BY_MNEMONIC[mnemonic]
            self.writer.write_uint16(opcode.value)
            
            instruction_start = self.writer.position() - 2
            
            # Helper to track backpatches for this instruction
            current_instr_backpatches = []
            
            flags_val = 0 # To store flags for 'o' check
            
            for enc in opcode.encoding:
                if enc == '0':
                    self.writer.write_uint32(0)
                    continue
                
                if enc == 'f':
                    flags_val = self._parse_flags(tokens)
                    self.writer.write_uint16(flags_val)
                
                elif enc == 'h':
                    t = tokens.pop(0)
                    self.writer.write_uint32(self._parse_hash_token(t))
                    
                elif enc == 'o':
                    # Check implicit offset condition
                    scope = MjoFlags.scope(flags_val)
                    if scope != MjoScope.Local:
                        # If next token is a number, parse it, otherwise -1
                        val = -1
                        if tokens:
                            try:
                                val = int(tokens[0])
                                tokens.pop(0)
                            except ValueError:
                                pass # Not a number, assume implicit -1
                        self.writer.write_int16(val)
                    else:
                        # Local scope, offset mandatory
                        t = tokens.pop(0)
                        self.writer.write_int16(int(t))
                        
                elif enc == 'i':
                    t = tokens.pop(0)
                    self.writer.write_int32(int(t))
                    
                elif enc == 'r':
                    t = tokens.pop(0)
                    self.writer.write_float(float(t))
                    
                elif enc == 's':
                    t = tokens.pop(0)
                    val = t.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
                    # text 指令使用 text_encoding，其它字符串使用 other_encoding
                    use_enc = self.text_encoding if opcode.mnemonic == "text" else self.other_encoding
                    encoded = val.encode(use_enc, errors=self.encoding_errors)
                    self.writer.write_uint16(len(encoded) + 1)
                    self.writer.write_bytes(encoded)
                    self.writer.write_byte(0)
                    
                elif enc == 't':
                    # Type list [a, b]
                    types = self._parse_type_list(tokens)
                    self.writer.write_uint16(len(types))
                    self.writer.write_bytes(bytes(types))
                    
                elif enc == 'a':
                    t = tokens.pop(0)
                    if t.startswith('(') and t.endswith(')'):
                        val = int(t[1:-1])
                    else:
                        val = int(t)
                    self.writer.write_uint16(val)
                    
                elif enc == 'j':
                    t = tokens.pop(0)
                    if t.startswith('@~') or t.startswith('~'):
                        val = self._parse_relative_jump(t)
                        self.writer.write_int32(val)
                    elif t.startswith('@'):
                        label = self._normalize_label(t[1:])
                        patch_pos = self.writer.position()
                        self.writer.write_int32(0) # Placeholder
                        current_instr_backpatches.append({
                            'pos': patch_pos,
                            'label': label,
                            'type': 'j'
                        })
                    else:
                        raise Exception(f"Invalid jump target: {t}")
                        
                elif enc == 'l':
                    t = tokens.pop(0)
                    if t.startswith('#'):
                        val = int(t[1:])
                    else:
                        val = int(t)
                    self.writer.write_uint16(val)
                    
                elif enc == 'c':
                    rest = " ".join(tokens)
                    tokens = []
                    targets = [x.strip() for x in rest.split(',')]
                    
                    self.writer.write_uint16(len(targets))
                    for idx, t in enumerate(targets):
                        if t.startswith('@~') or t.startswith('~'):
                            val = self._parse_relative_jump(t)
                            self.writer.write_int32(val)
                        elif t.startswith('@'):
                            label = self._normalize_label(t[1:])
                            patch_pos = self.writer.position()
                            self.writer.write_int32(0)
                            current_instr_backpatches.append({
                                'pos': patch_pos,
                                'label': label,
                                'type': 'c',
                                'case_index': idx,
                                'instr_start': instruction_start
                            })
                        else:
                            self.writer.write_int32(0)

            # End of instruction
            instruction_end = self.writer.position()
            instruction_size = instruction_end - instruction_start
            
            # Update backpatches with instruction end
            for patch in current_instr_backpatches:
                patch['instr_end'] = instruction_end
                self.backpatches.append(patch)
                
        # Resolve backpatches
        for patch in self.backpatches:
            label = patch['label']
            if label not in self.labels:
                raise Exception(f"Undefined label: {label}")
            
            target_offset = self.labels[label]
            if patch['type'] == 'c':
                base_offset = patch['instr_start'] + 2 + 2 + 4 * (patch['case_index'] + 1)
            else:
                base_offset = patch['instr_end']
            rel_offset = target_offset - base_offset
            
            self.writer.patch_int32(patch['pos'], rel_offset)
            
        return self._build_script_object()

    def _parse_flags(self, tokens):
        scope = MjoScope.Local
        type_ = MjoType.Int
        invert = MjoInvertMode.None_
        modifier = MjoModifier.None_
        dimension = 0
        
        while tokens:
            t = tokens[0].lower()
            consumed = True
            
            if t == 'local': scope = MjoScope.Local
            elif t == 'thread': scope = MjoScope.Thread
            elif t == 'savefile' or t == 'save': scope = MjoScope.SaveFile
            elif t == 'persistent' or t == 'persist': scope = MjoScope.Persistent
            elif t == 'int': type_ = MjoType.Int
            elif t == 'float': type_ = MjoType.Float
            elif t == 'string': type_ = MjoType.String
            elif t == 'intarray': type_ = MjoType.IntArray
            elif t == 'floatarray': type_ = MjoType.FloatArray
            elif t == 'stringarray': type_ = MjoType.StringArray
            elif t.startswith('invert_'):
                mode_str = t.split('_')[1]
                if mode_str == 'numeric': invert = MjoInvertMode.Numeric
                elif mode_str == 'boolean': invert = MjoInvertMode.Boolean
                elif mode_str == 'bitwise': invert = MjoInvertMode.Bitwise
            elif t == 'neg': invert = MjoInvertMode.Numeric
            elif t == 'not': invert = MjoInvertMode.Bitwise
            elif t == 'notl': invert = MjoInvertMode.Boolean
            elif t == 'preinc': modifier = MjoModifier.PreIncrement
            elif t == 'predec': modifier = MjoModifier.PreDecrement
            elif t == 'postinc': modifier = MjoModifier.PostIncrement
            elif t == 'postdec': modifier = MjoModifier.PostDecrement
            elif t == 'dim1': dimension = 1
            elif t == 'dim2': dimension = 2
            elif t == 'dim3': dimension = 3
            else:
                consumed = False
            
            if consumed:
                tokens.pop(0)
            else:
                break
                
        return MjoFlags.build(type_, scope, modifier, invert, dimension)

    def _parse_type_list(self, tokens):
        # Expect [ ... ]
        # Since tokens are split by shlex, [ might be part of token or separate
        # But we assume standard formatting
        
        collected = []
        while tokens:
            t = tokens.pop(0)
            collected.append(t)
            if ']' in t:
                break
        
        full = "".join(collected)
        content = full.replace('[', '').replace(']', '')
        if not content.strip(): return []
        
        res = []
        for ts in content.split(','):
            ts = ts.strip().lower()
            if not ts: continue
            if ts == 'int': res.append(MjoType.Int)
            elif ts == 'float': res.append(MjoType.Float)
            elif ts == 'string': res.append(MjoType.String)
            elif ts == 'intarray': res.append(MjoType.IntArray)
            elif ts == 'floatarray': res.append(MjoType.FloatArray)
            elif ts == 'stringarray': res.append(MjoType.StringArray)
            
        return res

    def _build_script_object(self):
        script = MjoScript()
        script.is_encrypted = False
        script.enable_read_mark = self.enable_read_mark
        entry_point = 0
        
        script.function_index = []
        for hash_val, is_entry in self.functions:
            label = self.function_entry_labels.get(hash_val)
            if label and label in self.labels:
                offset = self.labels[label]
            elif f"func_{hash_val:08x}" in self.labels:
                offset = self.labels[f"func_{hash_val:08x}"]
            else:
                print(f"Warning: Function label {label} not found")
                offset = 0
            
            script.function_index.append(FunctionIndexEntry(hash_val, offset))
            if is_entry:
                entry_point = offset
                
        script.entry_point_offset = entry_point
        return script, self.writer.data

    def save(self, stream, script, bytecode, encrypt=False):
        writer = BinaryWriter()
        
        # Signature
        if encrypt:
            sig = b"MajiroObjX1.000\0"
        else:
            sig = b"MajiroObjV1.000\0"
        writer.write_bytes(sig)
        
        writer.write_uint32(script.entry_point_offset)
        writer.write_uint32(1 if script.enable_read_mark else 0) # size? or just flag?
        
        # Function index
        writer.write_int32(len(script.function_index))
        for func in script.function_index:
            writer.write_uint32(func.name_hash)
            writer.write_uint32(func.offset)
            
        # Bytecode
        writer.write_int32(len(bytecode))
        
        if encrypt:
            # We need to copy bytecode because crypt32 modifies in-place
            data_to_write = bytearray(bytecode)
            Crc.crypt32(data_to_write)
            writer.write_bytes(data_to_write)
        else:
            writer.write_bytes(bytecode)
            
        stream.write(writer.data)
