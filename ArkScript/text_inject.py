# -*- coding: utf-8 -*-
"""
ArkScript — 文本注入工具
=========================
将翻译后的 JSON 文本注入回 .bin 脚本文件.
内部流程: 反汇编 → 替换字符串 → 重新汇编 (自动重算所有偏移)

用法:
  python text_inject.py <input.bin> <translated.json> [-o <output.bin>]

JSON 格式 (每条):
  {
    "id":     "seen01/0001",
    "name":   "比女",              # 可选: 翻译后的角色名
    "pre_jp": "比女\\n「...」",     # 原始日文 (用于校验)
    "message":"「翻译后的台词」"    # 翻译后的台词
  }
"""

import sys
import os
import json
import struct
import argparse

# 导入反汇编/汇编工具
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from disassembler import disassemble, format_string_for_asm, generate_asm
from assembler import parse_asm
from opcode import string_encrypt, write_header


def is_translatable(text):
    """同 text_extract.py 中的判断"""
    for ch in text:
        cp = ord(ch)
        if (0x3040 <= cp <= 0x30FF or 0x4E00 <= cp <= 0x9FFF or
            0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF):
            return True
    return False


def rebuild_full_text(entry, original_text):
    """
    根据翻译JSON条目重建完整的脚本字符串.

    规则:
    - 如果原文是 "角色名\\n「台词」" 格式:
      → 用 entry['name'] + \\n + entry['message'] 重建
    - 如果原文没有换行+括号 (选项/旁白):
      → 直接用 entry['message']
    """
    translated_msg = entry.get('message', '')
    translated_name = entry.get('name', '')

    # 判断原文格式
    has_dialog_format = '「' in original_text and '\n' in original_text

    if has_dialog_format and translated_name:
        # 对话格式: 名字 + 换行 + 台词
        return translated_name + '\n' + translated_msg
    elif has_dialog_format and not translated_name:
        # 对话但名字为空 → 保持原来的名字
        idx = original_text.index('\n')
        orig_name = original_text[:idx]
        return orig_name + '\n' + translated_msg
    else:
        # 选项/旁白
        return translated_msg


def inject(bin_path, json_path, output_path, encoding='cp932'):
    """
    主注入流程:
    1. 反汇编 .bin → 内存中的指令列表
    2. 匹配翻译JSON, 替换字符串
    3. 生成修改后的 asm.txt
    4. 重新汇编 → 新 .bin
    """
    # 读取原始 .bin
    with open(bin_path, 'rb') as f:
        data = f.read()

    # 读取翻译 JSON
    with open(json_path, 'r', encoding='utf-8') as f:
        translations = json.load(f)

    # 建立 pre_jp → translation 的映射
    # 使用 pre_jp 作为匹配键 (最可靠)
    trans_map = {}
    for entry in translations:
        pre_jp = entry.get('pre_jp', '')
        if pre_jp and entry.get('message', '') != '':
            trans_map[pre_jp] = entry

    # 反汇编
    instructions, jump_targets, version, reserved = disassemble(data, encoding)

    # 遍历指令, 替换字符串
    replaced = 0
    skipped = 0

    for inst in instructions:
        if inst['op'] != 0x13:
            continue
        if not inst['operands']:
            continue

        operand = inst['operands'][0]
        if operand[0] != 'str':
            continue

        str_len, enc_bytes, dec_bytes, text = operand[1], operand[2], operand[3], operand[4]

        if text is None or not is_translatable(text):
            continue

        # 查找翻译
        if text in trans_map:
            entry = trans_map[text]
            new_text = rebuild_full_text(entry, text)

            # 编码新文本
            try:
                new_bytes = new_text.encode(encoding)
            except UnicodeEncodeError:
                # 如果CP932编码失败, 跳过
                print(f"[警告] 编码失败, 跳过: {new_text[:30]}...")
                skipped += 1
                continue

            # 加密
            new_enc = string_encrypt(new_bytes)

            # 替换操作数
            inst['operands'][0] = ('str', len(new_enc), new_enc, new_bytes, new_text)
            replaced += 1
        else:
            skipped += 1

    # 生成修改后的 asm.txt
    asm_text = generate_asm(instructions, jump_targets, version, reserved, encoding)

    # 重新汇编
    bytecode, ver, res = parse_asm(asm_text, encoding)

    # 写入输出文件
    header = write_header(ver, res)
    output_data = header + bytecode

    with open(output_path, 'wb') as f:
        f.write(output_data)

    return replaced, skipped, len(output_data)


def main():
    parser = argparse.ArgumentParser(description='ArkScript 文本注入工具')
    parser.add_argument('bin_input', help='原始 .bin 文件或包含 .bin 的目录')
    parser.add_argument('json_input', help='翻译后的 .json 文件或包含 .json 的目录')
    parser.add_argument('-o', '--output', help='输出 .bin 文件 (目录模式时为输出目录)')
    parser.add_argument('--encoding', default='cp932', help='文本编码 (默认: cp932)')
    args = parser.parse_args()

    # 单文件模式
    if os.path.isfile(args.bin_input) and os.path.isfile(args.json_input):
        if args.output:
            out_path = args.output
        else:
            base = os.path.splitext(args.bin_input)[0]
            out_path = base + '.new.bin'

        print(f"[信息] 原始: {args.bin_input}")
        print(f"[信息] 翻译: {args.json_input}")

        replaced, skipped, size = inject(
            args.bin_input, args.json_input, out_path, args.encoding
        )

        print(f"[信息] 替换: {replaced} 条, 未匹配: {skipped} 条")
        print(f"[信息] 输出: {out_path} ({size} 字节)")

    # 目录模式
    elif os.path.isdir(args.bin_input) and os.path.isdir(args.json_input):
        out_dir = args.output or os.path.join(args.bin_input, 'translated')
        os.makedirs(out_dir, exist_ok=True)

        total_replaced = 0
        for fname in sorted(os.listdir(args.bin_input)):
            if not fname.endswith('.bin'):
                continue
            basename = os.path.splitext(fname)[0]
            json_path = os.path.join(args.json_input, basename + '.json')

            if not os.path.isfile(json_path):
                continue

            bin_path = os.path.join(args.bin_input, fname)
            out_path = os.path.join(out_dir, fname)

            replaced, skipped, size = inject(
                bin_path, json_path, out_path, args.encoding
            )
            print(f"  {fname:20s} → 替换{replaced:4d}条  {size:8d}字节")
            total_replaced += replaced

        print(f"\n[信息] 总计替换 {total_replaced} 条")
        print(f"[信息] 输出目录: {out_dir}")

    else:
        print("[错误] bin_input 和 json_input 必须同时是文件或同时是目录")
        sys.exit(1)


if __name__ == '__main__':
    main()
