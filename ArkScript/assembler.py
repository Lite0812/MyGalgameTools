# -*- coding: utf-8 -*-
"""
ArkScript VM — 汇编器
======================
用法:
  python assembler.py <input.asm.txt> [-o <output.bin>] [--encoding cp932]
  或直接将 .asm.txt 文件拖放到本脚本上
"""

import sys
import os
import struct
import re
import argparse

from opcodelist import (
    OPCODES, NOP_OPCODES,
    TYPE_REF, VAR_REF, F32, U8, STR_DATA,
    string_encrypt, write_header, is_jump_target
)


def parse_asm(asm_text, encoding='cp932'):
    """
    解析 asm.txt, 返回 (指令列表, 元信息).
    两遍扫描: 第1遍收集标签位置, 第2遍生成字节码.
    """
    lines = asm_text.split('\n')
    
    # 提取头部元信息
    version = 2.2
    reserved = 0
    
    for line in lines:
        line = line.strip()
        if line.startswith('.header'):
            # 新格式: .header version=2.2, reserved=0, encoding="cp932"
            for key, value in parse_directive_args(line[len('.header'):]).items():
                if key == 'version':
                    try:
                        version = float(value)
                    except ValueError:
                        pass
                elif key == 'reserved':
                    try:
                        reserved = int(value, 0)
                    except ValueError:
                        pass
        elif line.startswith('; 版本:'):
            try:
                version = float(line.split(':')[1].strip())
            except ValueError:
                pass
        elif line.startswith('; 保留字段:'):
            try:
                reserved = int(line.split(':')[1].strip())
            except ValueError:
                pass
        elif line.startswith('; 编码:'):
            # 可从注释读取编码, 但优先使用命令行参数
            pass
    
    # 第一遍: 收集所有指令和标签, 计算偏移
    parsed_lines = []  # (类型, 数据)
    
    for line_no, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        
        # 跳过空行、注释和元信息指令
        if not line or line.startswith(';') or line.startswith('.'):
            continue
        
        # 标签定义
        if line.endswith(':') and not line.startswith(' ') and not line.startswith('\t'):
            label_name = line[:-1].strip()
            parsed_lines.append(('label', label_name, line_no))
            continue
        
        # 指令行
        parts = line.split(None, 1)
        if not parts:
            continue
        
        mnemonic = parts[0]
        operand_str = parts[1] if len(parts) > 1 else ""
        
        # 去掉行尾注释
        # 注意: 字符串中可能包含分号, 需要在引号外截断
        operand_clean = strip_comment(operand_str)
        
        parsed_lines.append(('inst', mnemonic, operand_clean, line_no))
    
    # 查找opcode: 助记符 → opcode值
    mnemonic_to_op = {}
    for op, (mn, _, _) in OPCODES.items():
        mnemonic_to_op[mn] = op
    # NOP类
    for op in NOP_OPCODES:
        mnemonic_to_op[f"NOP_{op:02X}"] = op
    # 其他可能出现的NOP (0x00-0xFF中未定义的)
    for op in range(256):
        nm = f"NOP_{op:02X}"
        if nm not in mnemonic_to_op and op not in OPCODES:
            mnemonic_to_op[nm] = op
    
    # === 第1遍: 计算每条指令的字节大小, 确定标签偏移 ===
    label_offsets = {}
    inst_list = []
    current_offset = 0
    
    for item in parsed_lines:
        if item[0] == 'label':
            label_name = item[1]
            label_offsets[label_name] = current_offset
        elif item[0] == 'inst':
            mnemonic, operand_str, line_no = item[1], item[2], item[3]
            
            if mnemonic not in mnemonic_to_op:
                raise ValueError(f"行 {line_no}: 未知助记符 '{mnemonic}'")
            
            op = mnemonic_to_op[mnemonic]
            
            # 计算指令大小
            if op == 0x13:
                # 字符串: 1(op) + 4(len) + N(data)
                str_text = parse_string_operand(operand_str)
                str_bytes = encode_string(str_text, encoding)
                inst_size = 1 + 4 + len(str_bytes)
            elif op in OPCODES:
                _, op_types, _ = OPCODES[op]
                inst_size = 1
                for ot in op_types:
                    if ot == TYPE_REF: inst_size += 1
                    elif ot == VAR_REF: inst_size += 2
                    elif ot == F32: inst_size += 4
                    elif ot == U8: inst_size += 1
            else:
                inst_size = 1  # NOP
            
            inst_list.append({
                'op': op,
                'mnemonic': mnemonic,
                'operand_str': operand_str,
                'line_no': line_no,
                'offset': current_offset,
                'size': inst_size,
            })
            current_offset += inst_size
    
    # === 第2遍: 生成字节码 ===
    bytecode = bytearray()
    
    for inst in inst_list:
        op = inst['op']
        operand_str = inst['operand_str']
        line_no = inst['line_no']
        
        bytecode.append(op)
        
        if op == 0x13:
            # 字符串指令
            str_text = parse_string_operand(operand_str)
            str_bytes = encode_string(str_text, encoding)
            enc_bytes = string_encrypt(str_bytes)
            bytecode.extend(struct.pack('<I', len(enc_bytes)))
            bytecode.extend(enc_bytes)
        
        elif op in OPCODES:
            _, op_types, _ = OPCODES[op]
            operands = parse_operands(operand_str, op_types, op, label_offsets, line_no)
            
            for ot, val in zip(op_types, operands):
                if ot == TYPE_REF:
                    bytecode.append(val)
                elif ot == VAR_REF:
                    bytecode.append(val[0])  # scope
                    bytecode.append(val[1])  # index
                elif ot == F32:
                    bytecode.extend(struct.pack('<f', val))
                elif ot == U8:
                    bytecode.append(val)
        
        # NOP: 只有opcode字节, 已经添加
    
    return bytes(bytecode), version, reserved


def parse_directive_args(s):
    """解析 .directive 后的 key=value 列表。"""
    result = {}
    for part in split_operands(s):
        part = part.strip()
        if not part or '=' not in part:
            continue
        key, value = part.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        result[key.strip()] = value
    return result


def strip_comment(s):
    """去掉字符串外的注释 (以;开头)"""
    in_str = False
    escape = False
    for i, c in enumerate(s):
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"':
            in_str = not in_str
        elif c == ';' and not in_str:
            return s[:i].rstrip()
    return s.rstrip()


def parse_string_operand(operand_str):
    """从操作数字符串中提取引号内的文本"""
    operand_str = operand_str.strip()
    if not operand_str.startswith('"'):
        raise ValueError(f"字符串操作数格式错误: {operand_str[:50]}")
    
    # 找到匹配的结束引号
    i = 1
    result = []
    while i < len(operand_str):
        c = operand_str[i]
        if c == '\\' and i + 1 < len(operand_str):
            nc = operand_str[i+1]
            if nc == 'n':
                result.append('\n')
                i += 2
            elif nc == 'r':
                result.append('\r')
                i += 2
            elif nc == 't':
                result.append('\t')
                i += 2
            elif nc == '"':
                result.append('"')
                i += 2
            elif nc == '\\':
                result.append('\\')
                i += 2
            else:
                result.append(c)
                i += 1
        elif c == '"':
            break
        elif c == '{' and i + 1 < len(operand_str) and operand_str[i+1] == '{':
            # {{XX}} 占位符
            end = operand_str.index('}}', i)
            hex_str = operand_str[i+2:end]
            result.append(('raw_byte', int(hex_str, 16)))
            i = end + 2
        else:
            result.append(c)
            i += 1
    
    return result


def encode_string(parsed_chars, encoding):
    """将解析后的字符列表编码为字节"""
    result = bytearray()
    for item in parsed_chars:
        if isinstance(item, tuple) and item[0] == 'raw_byte':
            result.append(item[1])
        elif isinstance(item, str):
            result.extend(item.encode(encoding))
    return bytes(result)


def parse_operands(operand_str, op_types, op, label_offsets, line_no):
    """解析操作数字符串, 返回值列表"""
    operand_str = operand_str.strip()
    if not operand_str and not op_types:
        return []
    
    # 分割操作数 (按逗号, 但注意可能有标签名中的特殊字符)
    parts = split_operands(operand_str)
    
    if len(parts) != len(op_types):
        raise ValueError(
            f"行 {line_no}: 操作数数量不匹配, 期望 {len(op_types)} 个, 得到 {len(parts)} 个: '{operand_str}'"
        )
    
    values = []
    for ot, part in zip(op_types, parts):
        part = part.strip()
        if ot == TYPE_REF:
            values.append(parse_int(part))
        elif ot == VAR_REF:
            # 格式: $XX:XX
            if not part.startswith('$'):
                raise ValueError(f"行 {line_no}: var_ref 格式错误: '{part}'")
            scope_str, index_str = part[1:].split(':')
            values.append((int(scope_str, 16), int(index_str, 16)))
        elif ot == F32:
            if is_jump_target(op) and part.startswith('@'):
                # 标签引用
                label_name = part[1:]
                if label_name.startswith('0x'):
                    # 直接偏移
                    values.append(float(int(label_name, 16)))
                elif label_name in label_offsets:
                    values.append(float(label_offsets[label_name]))
                else:
                    raise ValueError(f"行 {line_no}: 未定义的标签 '{label_name}'")
            else:
                # 数值
                values.append(parse_float(part))
        elif ot == U8:
            values.append(parse_int(part))
    
    return values


def split_operands(s):
    """按逗号分割操作数, 但不在引号内分割"""
    parts = []
    current = []
    in_str = False
    depth = 0
    
    for c in s:
        if c == '"':
            in_str = not in_str
        if c == ',' and not in_str and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(c)
    
    if current:
        parts.append(''.join(current))
    
    return parts


def parse_int(s):
    """解析整数: 支持 0x 前缀和十进制"""
    s = s.strip()
    if s.startswith('0x') or s.startswith('0X'):
        return int(s, 16)
    return int(s)


def parse_float(s):
    """解析浮点数: 支持整数和小数"""
    s = s.strip()
    if '.' in s or 'e' in s.lower() or 'E' in s:
        return float(s)
    # 整数也转为float
    if s.startswith('0x') or s.startswith('0X'):
        return float(int(s, 16))
    if s.startswith('-'):
        return float(int(s))
    return float(int(s))


def main():
    parser = argparse.ArgumentParser(description='ArkScript 汇编器')
    parser.add_argument('input', help='输入 .asm.txt 文件')
    parser.add_argument('-o', '--output', help='输出 .bin 文件')
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
        base = input_path
        if base.endswith('.asm.txt'):
            base = base[:-8]
        elif base.endswith('.txt'):
            base = base[:-4]
        output_path = base + '.rebuild'
    
    # 读取 asm 文件
    with open(input_path, 'r', encoding='utf-8') as f:
        asm_text = f.read()
    
    print(f"[信息] 读取: {input_path}")
    
    # 汇编
    try:
        bytecode, version, reserved = parse_asm(asm_text, args.encoding)
    except Exception as e:
        print(f"[错误] 汇编失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # 生成输出文件: 头部 + 字节码
    header = write_header(version, reserved)
    output_data = header + bytecode
    
    with open(output_path, 'wb') as f:
        f.write(output_data)
    
    print(f"[信息] 输出: {output_path} ({len(output_data)} 字节)")
    print(f"[信息] 头部: {len(header)} 字节, 字节码: {len(bytecode)} 字节")


if __name__ == '__main__':
    main()
