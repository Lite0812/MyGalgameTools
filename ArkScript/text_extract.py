# -*- coding: utf-8 -*-
"""
ArkScript — 文本提取工具
=========================
从 .bin 脚本文件提取可翻译文本, 输出 GalTransl 标准 JSON 格式.

用法:
  python text_extract.py <input.bin 或 目录> [-o <output.json>]

JSON 格式 (每条):
  {
    "id":     "seen01/0001",       # 唯一标识: 文件名/序号
    "name":   "比女",              # 角色名 (对话时有值, 选项时为空)
    "pre_jp": "比女\\n「台词」",    # 原始日文全文 (含角色名)
    "message":"「台词」"           # 台词部分 (翻译目标)
  }
"""

import sys
import os
import json
import struct
import argparse

# 内联 opcode 长度表 (不依赖 opcode.py, 可独立使用)
_OPERAND_SIZES = {
    0x00: 1, 0x01: 1, 0x10: 2, 0x11: 4, 0x12: 2,
    0x13: -1, 0x15: 2, 0x30: 4, 0x31: 6, 0x32: 7,
    0x41: 4, 0x42: 6, 0x43: 4, 0x44: 6, 0x45: 4, 0x46: 6,
    0x47: 4, 0x48: 6, 0x49: 4, 0x4A: 6,
    0x50: 4, 0x51: 6, 0x52: 4, 0x53: 6, 0x54: 4, 0x55: 6,
    0x56: 4, 0x57: 6, 0x58: 4, 0x59: 6, 0x5A: 4, 0x5B: 6,
    0x60: 4, 0x61: 6, 0x62: 4, 0x63: 6,
    0x70: 4, 0x71: 6, 0x80: 1, 0x90: 3, 0xA0: 4, 0xA2: 4,
}


def is_translatable(text):
    """判断字符串是否需要翻译 (含日文字符或中日韩字符)"""
    for ch in text:
        cp = ord(ch)
        # 平假名 / 片假名 / CJK统一汉字 / 全角标点
        if (0x3040 <= cp <= 0x30FF or   # 假名
            0x4E00 <= cp <= 0x9FFF or   # CJK汉字
            0x3000 <= cp <= 0x303F or   # CJK标点
            0xFF00 <= cp <= 0xFFEF):    # 全角ASCII
            return True
    return False


def extract_strings(bin_path, encoding='cp932'):
    """
    从 .bin 文件提取所有字符串.
    返回 [(offset, raw_text), ...]
    """
    data = open(bin_path, 'rb').read()
    if len(data) < 12:
        return []

    code = data[12:]
    pos = 0
    strings = []
    str_index = 0

    while pos < len(code):
        op = code[pos]
        pos += 1

        if op == 0x13:
            if pos + 4 > len(code):
                break
            str_len = struct.unpack('<I', code[pos:pos+4])[0]
            pos += 4
            if pos + str_len > len(code):
                break
            enc_bytes = code[pos:pos+str_len]
            dec_bytes = bytes(~b & 0xFF for b in enc_bytes)
            pos += str_len

            try:
                text = dec_bytes.decode(encoding)
            except Exception:
                text = None

            if text is not None:
                strings.append((str_index, text))
            str_index += 1

        elif op in _OPERAND_SIZES:
            pos += _OPERAND_SIZES[op]
        # else: NOP

    return strings


def parse_dialogue(text):
    """
    解析对话字符串, 返回 (name, message).

    格式1: "角色名\\n「台词」"  → name="角色名", message="「台词」"
    格式2: "无括号的文本"       → name="", message="无括号的文本"
    """
    if '「' in text and '\n' in text:
        # 第一个换行前是角色名
        idx = text.index('\n')
        name = text[:idx]
        message = text[idx+1:]
        return name, message
    elif '「' in text:
        # 有括号但没换行 (少见情况)
        return "", text
    else:
        # 选项或旁白 (无括号)
        return "", text


def extract_to_json(bin_path, encoding='cp932'):
    """提取并生成 GalTransl JSON 条目列表"""
    basename = os.path.splitext(os.path.basename(bin_path))[0]
    strings = extract_strings(bin_path, encoding)

    entries = []
    trans_index = 0

    for str_index, text in strings:
        if not is_translatable(text):
            continue

        trans_index += 1
        name, message = parse_dialogue(text)

        entry = {
            "id": f"{basename}/{trans_index:04d}",
            "name": name,
            "pre_jp": text,
            "message": message,
        }
        entries.append(entry)

    return entries


def main():
    parser = argparse.ArgumentParser(description='ArkScript 文本提取工具')
    parser.add_argument('input', help='输入 .bin 文件或包含 .bin 的目录')
    parser.add_argument('-o', '--output', help='输出 .json 文件 (目录模式时为输出目录)')
    parser.add_argument('--encoding', default='cp932', help='文本编码 (默认: cp932)')
    parser.add_argument('--all', action='store_true', help='处理目录下所有 .bin 文件')
    args = parser.parse_args()

    input_path = args.input

    # 单文件模式
    if os.path.isfile(input_path):
        entries = extract_to_json(input_path, args.encoding)
        basename = os.path.splitext(os.path.basename(input_path))[0]

        if args.output:
            out_path = args.output
        else:
            out_path = os.path.join(os.path.dirname(input_path), basename + '.json')

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

        print(f"[信息] {input_path} → {out_path}")
        print(f"[信息] 提取了 {len(entries)} 条可翻译文本")
        dialog = sum(1 for e in entries if e['name'])
        choice = sum(1 for e in entries if not e['name'])
        print(f"[信息] 对话: {dialog}, 选项/旁白: {choice}")

    # 目录模式
    elif os.path.isdir(input_path):
        out_dir = args.output or input_path
        os.makedirs(out_dir, exist_ok=True)

        total = 0
        for fname in sorted(os.listdir(input_path)):
            if not fname.endswith('.bin'):
                continue
            bin_path = os.path.join(input_path, fname)
            entries = extract_to_json(bin_path, args.encoding)

            if not entries:
                continue

            basename = os.path.splitext(fname)[0]
            out_path = os.path.join(out_dir, basename + '.json')

            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)

            print(f"  {fname:20s} → {len(entries):4d} 条")
            total += len(entries)

        print(f"\n[信息] 总计提取 {total} 条可翻译文本")

    else:
        print(f"[错误] 路径不存在: {input_path}")
        sys.exit(1)


if __name__ == '__main__':
    main()
