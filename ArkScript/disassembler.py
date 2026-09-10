# -*- coding: utf-8 -*-
"""
ArkScript VM — 反汇编器
========================
用法:
  python disassembler.py <input.bin> [-o <output.asm.txt>] [--encoding cp932]
  或直接将 .bin 文件拖放到本脚本上
"""

import sys
import os
import struct
import argparse

from opcodelist import (
    OPCODES, NOP_OPCODES, HEADER_SIZE,
    TYPE_REF, VAR_REF, F32, U8, STR_DATA,
    string_encrypt, read_header, is_jump_target
)


def disassemble(data, encoding='cp932'):
    """
    反汇编字节码, 返回指令列表和跳转目标集合.
    
    每条指令: {
        'offset': int,        # 字节码内偏移 (从0开始, 不含文件头)
        'op': int,            # opcode字节
        'mnemonic': str,      # 助记符
        'operands': list,     # 解析后的操作数
        'raw_bytes': bytes,   # 原始字节 (含opcode)
        'comment': str,       # 注释
    }
    """
    version, reserved, code_start = read_header(data)
    code = data[code_start:]
    code_size = len(code)
    
    instructions = []
    jump_targets = set()
    pos = 0
    
    while pos < code_size:
        inst_start = pos
        op = code[pos]
        pos += 1
        
        operands = []
        comment = ""
        
        if op == 0x13:
            # 字符串指令: u32长度 + N字节取反加密数据
            if pos + 4 > code_size:
                raise ValueError(f"偏移 0x{inst_start:06X}: PUSH_STR 长度字段越界")
            str_len = struct.unpack('<I', code[pos:pos+4])[0]
            pos += 4
            if pos + str_len > code_size:
                raise ValueError(f"偏移 0x{inst_start:06X}: PUSH_STR 数据越界 (len={str_len})")
            enc_bytes = code[pos:pos+str_len]
            dec_bytes = string_encrypt(enc_bytes)
            pos += str_len
            
            try:
                text = dec_bytes.decode(encoding)
            except Exception:
                text = None
            
            operands.append(('str', str_len, enc_bytes, dec_bytes, text))
        
        elif op in OPCODES:
            mnemonic, op_types, desc = OPCODES[op]
            for ot in op_types:
                if ot == TYPE_REF:
                    val = code[pos]
                    operands.append(('type_ref', val))
                    pos += 1
                elif ot == VAR_REF:
                    scope = code[pos]
                    index = code[pos+1]
                    operands.append(('var_ref', scope, index))
                    pos += 2
                elif ot == F32:
                    raw = code[pos:pos+4]
                    fval = struct.unpack('<f', raw)[0]
                    operands.append(('f32', fval, raw))
                    # 收集跳转目标
                    if is_jump_target(op):
                        target = int(fval)
                        if 0 <= target < code_size:
                            jump_targets.add(target)
                    pos += 4
                elif ot == U8:
                    val = code[pos]
                    operands.append(('u8', val))
                    pos += 1
        else:
            # NOP (default分支) — 无操作数
            pass
        
        raw_bytes = code[inst_start:pos]
        
        if op in OPCODES:
            mnemonic = OPCODES[op][0]
        elif op in NOP_OPCODES:
            mnemonic = f"NOP_{op:02X}"
        else:
            mnemonic = f"NOP_{op:02X}"
        
        instructions.append({
            'offset': inst_start,
            'op': op,
            'mnemonic': mnemonic,
            'operands': operands,
            'raw_bytes': raw_bytes,
        })
    
    return instructions, jump_targets, version, reserved


def format_string_for_asm(dec_bytes, text, encoding):
    """
    将解密后的字符串格式化为asm.txt中的表示.
    不可打印字节用 {{XX}} 占位符.
    """
    if text is not None:
        # 尝试逐字符编码回去验证, 用 {{XX}} 替换无法显示的字节
        result = []
        i = 0
        raw = dec_bytes
        while i < len(raw):
            b = raw[i]
            # CP932 双字节字符
            if encoding == 'cp932' and ((0x81 <= b <= 0x9F) or (0xE0 <= b <= 0xFC)):
                if i + 1 < len(raw):
                    pair = raw[i:i+2]
                    try:
                        ch = pair.decode('cp932')
                        result.append(ch)
                        i += 2
                        continue
                    except Exception:
                        pass
                result.append(f"{{{{{b:02X}}}}}")
                i += 1
            elif 0x20 <= b <= 0x7E:
                # 可打印ASCII
                ch = chr(b)
                if ch == '"':
                    result.append('\\"')
                elif ch == '\\':
                    result.append('\\\\')
                else:
                    result.append(ch)
                i += 1
            elif b == 0x0A:
                result.append('\\n')
                i += 1
            elif b == 0x0D:
                result.append('\\r')
                i += 1
            elif b == 0x09:
                result.append('\\t')
                i += 1
            elif (0xA1 <= b <= 0xDF):
                # CP932半角片假名
                try:
                    ch = bytes([b]).decode('cp932')
                    result.append(ch)
                except Exception:
                    result.append(f"{{{{{b:02X}}}}}")
                i += 1
            else:
                result.append(f"{{{{{b:02X}}}}}")
                i += 1
        return ''.join(result)
    else:
        # 无法解码, 全部用占位符
        return ''.join(f"{{{{{b:02X}}}}}" for b in dec_bytes)


def format_f32(fval, raw_bytes):
    """格式化float值: 整数值直接显示为整数, 否则显示float"""
    ival = int(fval)
    if fval == float(ival) and abs(ival) < 2**24:
        return str(ival)
    else:
        return f"{fval}"


def quote_asm_string(value):
    """将字符串内容包成 asm 字符串字面量。"""
    return f'"{value}"'


def generate_asm(instructions, jump_targets, version, reserved, encoding):
    """生成 asm.txt 内容"""
    lines = []

    # 参考 scd 反汇编输出: 用少量指令式元信息替代大段头部注释。
    lines.append(".file kind=ark_bin")
    lines.append(f".header version={version:g}, reserved={reserved}, encoding={quote_asm_string(encoding)}")
    lines.append(f"; instructions={len(instructions)}, labels={len(jump_targets)}")
    lines.append("")
    lines.append(".code")
    
    # 构建偏移→标签名映射 (只为落在指令边界上的目标生成标签)
    inst_offsets = {inst['offset'] for inst in instructions}
    label_map = {}
    for target in sorted(jump_targets):
        if target in inst_offsets:
            label_map[target] = f"loc_{target:06X}"
        # 不在边界上的目标在指令输出时用 @0x... 原始偏移格式
    
    # 输出指令
    prev_had_label = False
    for inst in instructions:
        offset = inst['offset']
        
        # 标签
        if offset in label_map:
            lines.append("")
            lines.append(f"{label_map[offset]}:")
            prev_had_label = True
        
        # 指令行
        mnemonic = inst['mnemonic']
        op = inst['op']
        operand_strs = []
        
        for operand in inst['operands']:
            if operand[0] == 'type_ref':
                operand_strs.append(f"0x{operand[1]:02X}")
            elif operand[0] == 'var_ref':
                scope, index = operand[1], operand[2]
                operand_strs.append(f"${scope:02X}:{index:02X}")
            elif operand[0] == 'f32':
                fval, raw = operand[1], operand[2]
                # 跳转目标用标签
                if is_jump_target(op):
                    target = int(fval)
                    if target in label_map:
                        operand_strs.append(f"@{label_map[target]}")
                    else:
                        operand_strs.append(f"@0x{target:06X}")
                else:
                    operand_strs.append(format_f32(fval, raw))
            elif operand[0] == 'u8':
                operand_strs.append(f"0x{operand[1]:02X}")
            elif operand[0] == 'str':
                str_len, enc_bytes, dec_bytes, text = operand[1], operand[2], operand[3], operand[4]
                formatted = format_string_for_asm(dec_bytes, text, encoding)
                operand_strs.append(f'"{formatted}"')
        
        if operand_strs:
            line = f"  {mnemonic:10s} {', '.join(operand_strs)}"
        else:
            line = f"  {mnemonic}"
        
        lines.append(line)
    
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description='ArkScript 反汇编器')
    parser.add_argument('input', help='输入 .bin 文件')
    parser.add_argument('-o', '--output', help='输出 .asm.txt 文件')
    parser.add_argument('--encoding', default='cp932', help='文本编码 (默认: cp932)')
    args = parser.parse_args()
    
    input_path = args.input
    if not os.path.isfile(input_path):
        print(f"[错误] 文件不存在: {input_path}")
        sys.exit(1)
    
    # 确定输出路径
    if args.output:
        output_path = args.output
    else:
        base = os.path.splitext(input_path)[0]
        output_path = base + '.asm.txt'
    
    # 读取文件
    with open(input_path, 'rb') as f:
        data = f.read()
    
    print(f"[信息] 读取: {input_path} ({len(data)} 字节)")
    
    # 反汇编
    try:
        instructions, jump_targets, version, reserved = disassemble(data, args.encoding)
    except Exception as e:
        print(f"[错误] 反汇编失败: {e}")
        sys.exit(1)
    
    print(f"[信息] 版本: {version}, 指令数: {len(instructions)}, 跳转标签: {len(jump_targets)}")
    
    # 生成输出
    asm_text = generate_asm(instructions, jump_targets, version, reserved, args.encoding)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(asm_text)
    
    print(f"[信息] 输出: {output_path}")
    
    # 统计
    str_count = sum(1 for inst in instructions if inst['op'] == 0x13)
    print(f"[信息] 字符串数: {str_count}")


if __name__ == '__main__':
    main()
