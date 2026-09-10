# -*- coding: utf-8 -*-
r"""
SS文本提取脚本 - 从.ss文件提取文本并保存为JSON/TXT格式
支持注音文本提取，按line合并分句，匹配name属性

注音格式: @r注音@被注音文本@
句子分隔符: @n（用于分隔同一行的多个分句）
原始换行符: \n（直接保留）

用法: python ssTextExtractor.py <Scene\> [Output\] [options]
选项:
  -v: 详细输出模式
  -p: 纯净JSON模式（只输出name和message字段）
  -d: 反汇编模式（输出字节码反汇编到.asm文件）
  -t: 全量TXT模式（导出所有字符串到TXT，UTF-16LE编码）
  -T: 翻译TXT模式（导出◇原文/◆译文格式，UTF-16LE编码）
  -r: 去除注音格式（将 @r注音@文本@ 转为 文本，支持JSON和-T模式）
"""

import sys
import os
import glob
import struct
import unicodedata
import json
import re
from collections import defaultdict


def Decrypt(string, l, k):
    key = 28807
    localKey = key * k % 65536
    newString = b''
    for n in range(0, l):
        newString += struct.pack('H', localKey ^ struct.unpack('H', string[n*2:n*2+2])[0])
    return newString

# ============================================================
# 指令码定义 (来自 tnm_code.h)
# ============================================================
CD_NONE = 0x00
CD_NL = 0x01              # 换行（行号）
CD_PUSH = 0x02            # 压栈
CD_POP = 0x03             # 出栈
CD_COPY = 0x04            # 复制栈顶
CD_PROPERTY = 0x05        # 属性展开
CD_COPY_ELM = 0x06        # 元素复制
CD_DEC_PROP = 0x07        # 属性定义
CD_ELM_POINT = 0x08       # 元素起点
CD_ARG = 0x09             # 参数展开

CD_GOTO = 0x10            # 跳转
CD_GOTO_TRUE = 0x11       # 条件为真跳转
CD_GOTO_FALSE = 0x12      # 条件为假跳转
CD_GOSUB = 0x13           # 子程序调用(返回int)
CD_GOSUBSTR = 0x14        # 子程序调用(返回str)
CD_RETURN = 0x15          # 返回
CD_EOF = 0x16             # 文件结束

CD_ASSIGN = 0x20          # 赋值
CD_OPERATE_1 = 0x21       # 一元运算符
CD_OPERATE_2 = 0x22       # 二元运算符

CD_COMMAND = 0x30         # 命令
CD_TEXT = 0x31            # 文本显示
CD_NAME = 0x32            # 角色名称
CD_SEL_BLOCK_START = 0x33 # 选择块开始
CD_SEL_BLOCK_END = 0x34   # 选择块结束

# 数据类型码
FM_VOID = 0x00
FM_INT = 0x0a
FM_STR = 0x14
FM_INTLIST = 0x1e
FM_STRLIST = 0x28
FM_INTREF = 0x32
FM_STRREF = 0x3c
FM_INTLISTREF = 0x46
FM_STRLISTREF = 0x50

# 运算符码
OP_NONE = 0x00
OP_PLUS = 0x01
OP_MINUS = 0x02
OP_MULTIPLE = 0x03
OP_DIVIDE = 0x04
OP_AMARI = 0x05
OP_EQUAL = 0x10
OP_NOT_EQUAL = 0x11
OP_GREATER = 0x12
OP_GREATER_EQUAL = 0x13
OP_LESS = 0x14
OP_LESS_EQUAL = 0x15
OP_LOGICAL_AND = 0x20
OP_LOGICAL_OR = 0x21
OP_TILDE = 0x30
OP_AND = 0x31
OP_OR = 0x32
OP_HAT = 0x33
OP_SL = 0x34
OP_SR = 0x35
OP_SR3 = 0x36

# 指令名称映射
INSTR_NAMES = {
    CD_NONE: "NONE",
    CD_NL: "NL",
    CD_PUSH: "PUSH",
    CD_POP: "POP",
    CD_COPY: "COPY",
    CD_PROPERTY: "PROPERTY",
    CD_COPY_ELM: "COPY_ELM",
    CD_DEC_PROP: "DEC_PROP",
    CD_ELM_POINT: "ELM_POINT",
    CD_ARG: "ARG",
    CD_GOTO: "GOTO",
    CD_GOTO_TRUE: "GOTO_TRUE",
    CD_GOTO_FALSE: "GOTO_FALSE",
    CD_GOSUB: "GOSUB",
    CD_GOSUBSTR: "GOSUBSTR",
    CD_RETURN: "RETURN",
    CD_EOF: "EOF",
    CD_ASSIGN: "ASSIGN",
    CD_OPERATE_1: "OPERATE_1",
    CD_OPERATE_2: "OPERATE_2",
    CD_COMMAND: "COMMAND",
    CD_TEXT: "TEXT",
    CD_NAME: "NAME",
    CD_SEL_BLOCK_START: "SEL_START",
    CD_SEL_BLOCK_END: "SEL_END",
}

# 数据类型名称映射
FORM_NAMES = {
    FM_VOID: "void",
    FM_INT: "int",
    FM_STR: "str",
    FM_INTLIST: "intlist",
    FM_STRLIST: "strlist",
    FM_INTREF: "intref",
    FM_STRREF: "strref",
    FM_INTLISTREF: "intlistref",
    FM_STRLISTREF: "strlistref",
}

# 运算符名称映射
OP_NAMES = {
    OP_NONE: "=",
    OP_PLUS: "+",
    OP_MINUS: "-",
    OP_MULTIPLE: "*",
    OP_DIVIDE: "/",
    OP_AMARI: "%",
    OP_EQUAL: "==",
    OP_NOT_EQUAL: "!=",
    OP_GREATER: ">",
    OP_GREATER_EQUAL: ">=",
    OP_LESS: "<",
    OP_LESS_EQUAL: "<=",
    OP_LOGICAL_AND: "&&",
    OP_LOGICAL_OR: "||",
    OP_TILDE: "~",
    OP_AND: "&",
    OP_OR: "|",
    OP_HAT: "^",
    OP_SL: "<<",
    OP_SR: ">>",
    OP_SR3: ">>>",
}

# 注音功能ID (PUSH int 61 表示注音相关命令)
RUBY_FUNCTION_ID = 61

# 特效文本函数ID (通过COMMAND显示的演出文本)
# 函数ID 2113929255 用于显示过场字幕等特效文本
EFFECT_TEXT_FUNCTION_ID = 2113929255

# 选择支函数ID (PUSH int 76 + PUSH str[] 多个选项 + COMMAND)
SELECTION_FUNCTION_ID = 76

# 注释文本函数ID (开发者备注/场景说明)
# 函数ID 2113929497 用于显示注释性文本
COMMENT_TEXT_FUNCTION_ID = 2113929497

# 术语/词条函数ID (用于显示游戏中的专有名词)
# 2113929817 - 术语检查/获取
TERM_CHECK_FUNCTION_ID = 2113929817
# 2113929820 - 术语设置/显示
TERM_SET_FUNCTION_ID = 2113929820
# 2113929821 - 系统提示/获得物品
SYSTEM_MSG_FUNCTION_ID = 2113929821


class SSHeader:
    """SS文件头部解析器"""
    def __init__(self, f):
        f.seek(0)
        self.header_size = struct.unpack('I', f.read(4))[0]
        header_data = f.read(128)
        hdr = struct.unpack('32I', header_data)
        
        # 字节码段
        self.scn_ofs = hdr[0]
        self.scn_size = hdr[1]
        
        # 字符串段
        self.str_idx_ofs = hdr[2]
        self.str_idx_cnt = hdr[3]
        self.str_list_ofs = hdr[4]
        self.str_cnt = hdr[5]
        
        # 标签段
        self.label_ofs = hdr[6]
        self.label_cnt = hdr[7]
        self.z_label_ofs = hdr[8]
        self.z_label_cnt = hdr[9]
        
        # 命令标签段
        self.cmd_label_ofs = hdr[10]
        self.cmd_label_cnt = hdr[11]
        
        # 场景属性段
        self.scn_prop_ofs = hdr[12]
        self.scn_prop_cnt = hdr[13]
        self.scn_prop_idx_ofs = hdr[14]
        self.scn_prop_idx_cnt = hdr[15]
        self.scn_prop_name_ofs = hdr[16]
        self.scn_prop_name_cnt = hdr[17]
        
        # 场景命令段
        self.scn_cmd_ofs = hdr[18]
        self.scn_cmd_cnt = hdr[19]
        self.scn_cmd_idx_ofs = hdr[20]
        self.scn_cmd_idx_cnt = hdr[21]
        self.scn_cmd_name_ofs = hdr[22]
        self.scn_cmd_name_cnt = hdr[23]
        
        # 调用属性段
        self.call_prop_idx_ofs = hdr[24]
        self.call_prop_idx_cnt = hdr[25]
        self.call_prop_name_ofs = hdr[26]
        self.call_prop_name_cnt = hdr[27]
        
        # 名称段
        self.namae_ofs = hdr[28]
        self.namae_cnt = hdr[29]
        
        # 已读标志段
        self.read_flag_ofs = hdr[30]
        self.read_flag_cnt = hdr[31]


class Instruction:
    """表示一条字节码指令"""
    def __init__(self, offset, opcode, operands=None, description=""):
        self.offset = offset
        self.opcode = opcode
        self.operands = operands or []
        self.description = description


class TextEntry:
    """表示一个文本条目"""
    def __init__(self, str_id, text, line, read_flag, ruby_text=None, ruby_str_id=None):
        self.str_id = str_id
        self.text = text
        self.line = line
        self.read_flag = read_flag
        self.ruby_text = ruby_text      # 注音文本
        self.ruby_str_id = ruby_str_id  # 注音字符串的str_id（用于封回）


class SSDisassembler:
    """完整的字节码反汇编器"""
    
    def __init__(self, bytecode, strings, header):
        self.bytecode = bytecode
        self.strings = strings
        self.header = header
        self.pos = 0
        self.instructions = []
        self.text_entries = []     # (idx, str_id, text, line, read_flag)
        self.name_entries = []     # (idx, str_id, name, line)
        self.command_entries = []  # (offset, arg_list_id, arg_cnt, elm_cnt, read_flag, line)
        self.current_line = 0
    
    def read_byte(self):
        if self.pos >= len(self.bytecode):
            return None
        b = self.bytecode[self.pos]
        self.pos += 1
        return b
    
    def read_int(self):
        if self.pos + 4 > len(self.bytecode):
            return None
        val = struct.unpack_from('i', self.bytecode, self.pos)[0]
        self.pos += 4
        return val
    
    def get_string(self, idx):
        if 0 <= idx < len(self.strings):
            return self.strings[idx]
        return f"<string_{idx}>"
    
    def disassemble(self):
        """反汇编所有字节码指令"""
        while self.pos < len(self.bytecode):
            offset = self.pos
            opcode = self.read_byte()
            if opcode is None:
                break
            
            instr = self.parse_instruction(offset, opcode)
            if instr:
                self.instructions.append(instr)
        
        return self.instructions
    
    def parse_instruction(self, offset, opcode):
        """解析单条指令并生成反汇编描述"""
        operands = []
        desc = INSTR_NAMES.get(opcode, f"UNKNOWN_{opcode:02X}")
        
        if opcode == CD_NL:
            line_no = self.read_int()
            self.current_line = line_no
            operands = [line_no]
            desc = f"NL line={line_no}"
            
        elif opcode == CD_PUSH:
            form = self.read_int()
            form_name = FORM_NAMES.get(form, f"form_{form}")
            if form == FM_INT:
                val = self.read_int()
                operands = [form, val]
                desc = f"PUSH {form_name} {val}"
            elif form == FM_STR:
                str_id = self.read_int()
                operands = [form, str_id]
                text = self.get_string(str_id)
                desc = f"PUSH {form_name} [{str_id}] = '{text[:30]}...'"
            else:
                val = self.read_int()
                operands = [form, val]
                desc = f"PUSH {form_name} {val}"
                
        elif opcode == CD_POP:
            form = self.read_int()
            form_name = FORM_NAMES.get(form, f"form_{form}")
            operands = [form]
            desc = f"POP {form_name}"
            
        elif opcode == CD_COPY:
            form = self.read_int()
            form_name = FORM_NAMES.get(form, f"form_{form}")
            operands = [form]
            desc = f"COPY {form_name}"
            
        elif opcode == CD_PROPERTY:
            desc = "PROPERTY"
            
        elif opcode == CD_COPY_ELM:
            desc = "COPY_ELM"
            
        elif opcode == CD_DEC_PROP:
            form = self.read_int()
            prop_id = self.read_int()
            operands = [form, prop_id]
            form_name = FORM_NAMES.get(form, f"form_{form}")
            desc = f"DEC_PROP {form_name} id={prop_id}"
            
        elif opcode == CD_ELM_POINT:
            desc = "ELM_POINT"
            
        elif opcode == CD_ARG:
            desc = "ARG"
            
        elif opcode == CD_GOTO:
            label = self.read_int()
            operands = [label]
            desc = f"GOTO label_{label}"
            
        elif opcode == CD_GOTO_TRUE:
            label = self.read_int()
            operands = [label]
            desc = f"GOTO_TRUE label_{label}"
            
        elif opcode == CD_GOTO_FALSE:
            label = self.read_int()
            operands = [label]
            desc = f"GOTO_FALSE label_{label}"
            
        elif opcode == CD_GOSUB:
            label = self.read_int()
            arg_cnt = self.read_int()
            operands = [label, arg_cnt]
            # 检测异常arg_cnt（正常值应很小，超过100视为异常）
            if arg_cnt is not None and 0 <= arg_cnt <= 100:
                desc = f"GOSUB label_{label} args={arg_cnt}"
                for _ in range(arg_cnt):
                    arg_form = self.read_int()
                    operands.append(arg_form)
            else:
                desc = f"GOSUB_INVALID label_{label} args={arg_cnt}"
            
        elif opcode == CD_GOSUBSTR:
            label = self.read_int()
            arg_cnt = self.read_int()
            operands = [label, arg_cnt]
            if arg_cnt is not None and 0 <= arg_cnt <= 100:
                desc = f"GOSUBSTR label_{label} args={arg_cnt}"
                for _ in range(arg_cnt):
                    arg_form = self.read_int()
                    operands.append(arg_form)
            else:
                desc = f"GOSUBSTR_INVALID label_{label} args={arg_cnt}"
            
        elif opcode == CD_RETURN:
            arg_cnt = self.read_int()
            operands = [arg_cnt]
            if arg_cnt is not None and 0 <= arg_cnt <= 100:
                desc = f"RETURN args={arg_cnt}"
                if arg_cnt > 0:
                    ret_form = self.read_int()
                    operands.append(ret_form)
                    form_name = FORM_NAMES.get(ret_form, f"form_{ret_form}")
                    desc = f"RETURN {form_name}"
            else:
                desc = f"RETURN_INVALID args={arg_cnt}"
            
        elif opcode == CD_EOF:
            desc = "EOF"
            
        elif opcode == CD_ASSIGN:
            form_l = self.read_int()
            form_r = self.read_int()
            al_id = self.read_int()
            operands = [form_l, form_r, al_id]
            desc = f"ASSIGN {FORM_NAMES.get(form_l,'?')} = {FORM_NAMES.get(form_r,'?')}"
            
        elif opcode == CD_OPERATE_1:
            form = self.read_int()
            op = self.read_byte()
            operands = [form, op]
            op_name = OP_NAMES.get(op, f"op_{op}")
            desc = f"OPERATE_1 {op_name} {FORM_NAMES.get(form,'?')}"
            
        elif opcode == CD_OPERATE_2:
            form_l = self.read_int()
            form_r = self.read_int()
            op = self.read_byte()
            operands = [form_l, form_r, op]
            op_name = OP_NAMES.get(op, f"op_{op}")
            desc = f"OPERATE_2 {FORM_NAMES.get(form_l,'?')} {op_name} {FORM_NAMES.get(form_r,'?')}"
            
        elif opcode == CD_COMMAND:
            arg_list_id = self.read_int()
            arg_cnt = self.read_int()
            operands = [arg_list_id, arg_cnt]
            # 检测异常arg_cnt（正常值应很小，超过100视为异常）
            if arg_cnt is not None and 0 <= arg_cnt <= 100:
                for _ in range(arg_cnt):
                    arg_form = self.read_int()
                    operands.append(arg_form)
                elm_cnt = self.read_int()
                operands.append(elm_cnt)
                read_flag = self.read_int()
                operands.append(read_flag)
                self.command_entries.append((offset, arg_list_id, arg_cnt, elm_cnt, read_flag, self.current_line))
                desc = f"COMMAND arg_list={arg_list_id} args={arg_cnt} elms={elm_cnt}"
            else:
                desc = f"COMMAND_INVALID arg_list={arg_list_id} args={arg_cnt}"
            
        elif opcode == CD_TEXT:
            read_flag = self.read_int()
            operands = [read_flag]
            for i in range(len(self.instructions)-1, -1, -1):
                prev = self.instructions[i]
                if prev.opcode == CD_PUSH and len(prev.operands) >= 2 and prev.operands[0] == FM_STR:
                    str_id = prev.operands[1]
                    text = self.get_string(str_id)
                    self.text_entries.append((len(self.text_entries), str_id, text, self.current_line, read_flag))
                    desc = f"TEXT flag={read_flag} [{str_id}] = '{text[:40]}...'"
                    break
            else:
                desc = f"TEXT flag={read_flag}"
            
        elif opcode == CD_NAME:
            read_flag = self.read_int()
            operands = [read_flag]
            for i in range(len(self.instructions)-1, -1, -1):
                prev = self.instructions[i]
                if prev.opcode == CD_PUSH and len(prev.operands) >= 2 and prev.operands[0] == FM_STR:
                    str_id = prev.operands[1]
                    name = self.get_string(str_id)
                    self.name_entries.append((len(self.name_entries), str_id, name, self.current_line))
                    desc = f"NAME [{str_id}] = '{name}'"
                    break
            else:
                desc = f"NAME flag={read_flag}"
            
        elif opcode == CD_SEL_BLOCK_START:
            desc = "SEL_BLOCK_START"
            
        elif opcode == CD_SEL_BLOCK_END:
            desc = "SEL_BLOCK_END"
        
        return Instruction(offset, opcode, operands, desc)


class SSTextExtractor:
    """SS文件文本提取器，支持注音识别"""
    
    def __init__(self, bytecode, strings, header):
        self.bytecode = bytecode
        self.strings = strings
        self.header = header
        self.pos = 0
        self.instructions = []
        self.text_entries = []      # TextEntry对象列表
        self.name_entries = []      # (str_id, name, line)
        self.current_line = 0
        self.pending_ruby = None        # 待处理的注音文本
        self.pending_ruby_str_id = None  # 待处理的注音str_id
    
    def read_byte(self):
        if self.pos >= len(self.bytecode):
            return None
        b = self.bytecode[self.pos]
        self.pos += 1
        return b
    
    def read_int(self):
        if self.pos + 4 > len(self.bytecode):
            return None
        val = struct.unpack_from('i', self.bytecode, self.pos)[0]
        self.pos += 4
        return val
    
    def get_string(self, idx):
        if 0 <= idx < len(self.strings):
            return self.strings[idx]
        return ""
    
    def extract(self):
        """提取所有文本和注音"""
        while self.pos < len(self.bytecode):
            offset = self.pos
            opcode = self.read_byte()
            if opcode is None:
                break
            
            self.parse_instruction(offset, opcode)
        
        return self.text_entries, self.name_entries
    
    def parse_instruction(self, offset, opcode):
        """解析单条指令，识别注音模式"""
        operands = []
        
        if opcode == CD_NL:
            line_no = self.read_int()
            self.current_line = line_no
            operands = [line_no]
            
        elif opcode == CD_PUSH:
            form = self.read_int()
            if form == FM_INT:
                val = self.read_int()
                operands = [form, val]
            elif form == FM_STR:
                str_id = self.read_int()
                operands = [form, str_id]
            else:
                val = self.read_int()
                operands = [form, val]
                
        elif opcode == CD_POP:
            form = self.read_int()
            operands = [form]
            
        elif opcode == CD_COPY:
            form = self.read_int()
            operands = [form]
            
        elif opcode == CD_DEC_PROP:
            form = self.read_int()
            prop_id = self.read_int()
            operands = [form, prop_id]
            
        elif opcode == CD_GOTO:
            label = self.read_int()
            operands = [label]
            
        elif opcode == CD_GOTO_TRUE:
            label = self.read_int()
            operands = [label]
            
        elif opcode == CD_GOTO_FALSE:
            label = self.read_int()
            operands = [label]
            
        elif opcode == CD_GOSUB:
            label = self.read_int()
            arg_cnt = self.read_int()
            operands = [label, arg_cnt]
            # 检测异常arg_cnt
            if arg_cnt is not None and 0 <= arg_cnt <= 100:
                for _ in range(arg_cnt):
                    arg_form = self.read_int()
                    operands.append(arg_form)
            
        elif opcode == CD_GOSUBSTR:
            label = self.read_int()
            arg_cnt = self.read_int()
            operands = [label, arg_cnt]
            if arg_cnt is not None and 0 <= arg_cnt <= 100:
                for _ in range(arg_cnt):
                    arg_form = self.read_int()
                    operands.append(arg_form)
            
        elif opcode == CD_RETURN:
            arg_cnt = self.read_int()
            operands = [arg_cnt]
            if arg_cnt is not None and 0 <= arg_cnt <= 100 and arg_cnt > 0:
                ret_form = self.read_int()
                operands.append(ret_form)
            
        elif opcode == CD_ASSIGN:
            form_l = self.read_int()
            form_r = self.read_int()
            al_id = self.read_int()
            operands = [form_l, form_r, al_id]
            
        elif opcode == CD_OPERATE_1:
            form = self.read_int()
            op = self.read_byte()
            operands = [form, op]
            
        elif opcode == CD_OPERATE_2:
            form_l = self.read_int()
            form_r = self.read_int()
            op = self.read_byte()
            operands = [form_l, form_r, op]
            
            # 检测字符串比较操作 (str == str 或 str != str)
            # 这通常用于判断文本是否被修改，第一个字符串是原始文本
            if form_l == FM_STR and form_r == FM_STR and op in (OP_EQUAL, OP_NOT_EQUAL):
                # 查找前面两个PUSH str
                str_ids_found = []
                for i in range(len(self.instructions)-1, max(-1, len(self.instructions)-10), -1):
                    prev = self.instructions[i]
                    if prev.opcode == CD_PUSH and len(prev.operands) >= 2 and prev.operands[0] == FM_STR:
                        str_ids_found.append(prev.operands[1])
                        if len(str_ids_found) >= 2:
                            break
                    elif prev.opcode == CD_NL:
                        break
                
                # 提取含有东亚字符的字符串（第一个通常是原始文本）
                if len(str_ids_found) >= 2:
                    # str_ids_found[0] 是后一个PUSH（对比文本），str_ids_found[1] 是前一个PUSH（原始文本）
                    orig_str_id = str_ids_found[1]  # 第一个PUSH的字符串
                    orig_text = self.get_string(orig_str_id)
                    
                    # 只提取含有东亚字符的文本
                    if orig_text and has_east_asian_char(orig_text):
                        # 检查是否已经提取过这个str_id
                        already_exists = any(e.str_id == orig_str_id for e in self.text_entries)
                        if not already_exists:
                            entry = TextEntry(orig_str_id, orig_text, self.current_line, -2, None, None)
                            self.text_entries.append(entry)
            
        elif opcode == CD_COMMAND:
            arg_list_id = self.read_int()
            arg_cnt = self.read_int()
            operands = [arg_list_id, arg_cnt]
            
            # 检测异常arg_cnt（正常值应很小，超过100视为异常）
            if arg_cnt is None or arg_cnt < 0 or arg_cnt > 100:
                # 异常情况，不读取参数，直接返回
                instr = Instruction(offset, opcode, operands)
                self.instructions.append(instr)
                return
            
            for _ in range(arg_cnt):
                arg_form = self.read_int()
                operands.append(arg_form)
            elm_cnt = self.read_int()
            operands.append(elm_cnt)
            read_flag = self.read_int()
            operands.append(read_flag)
            
            # 检查是否为注音设置命令
            # 正确模式: PUSH int 61 -> PUSH str [注音] -> COMMAND arg_list=1 args=1
            # 必须同时满足: 1) PUSH str在COMMAND前面  2) PUSH int 61在PUSH str前面
            if arg_list_id == 1 and arg_cnt == 1:
                # 查找前面的指令序列，确认是注音模式
                found_push_str = False
                found_push_61 = False
                ruby_str_id = None
                
                for i in range(len(self.instructions)-1, max(-1, len(self.instructions)-10), -1):
                    prev = self.instructions[i]
                    if prev.opcode == CD_PUSH and len(prev.operands) >= 2:
                        if prev.operands[0] == FM_STR and not found_push_str:
                            # 找到PUSH str，记录但还需要确认前面有PUSH int 61
                            ruby_str_id = prev.operands[1]
                            found_push_str = True
                        elif prev.operands[0] == FM_INT and prev.operands[1] == RUBY_FUNCTION_ID:
                            # 找到PUSH int 61
                            found_push_61 = True
                            break
                        elif prev.operands[0] == FM_INT:
                            # 遇到其他PUSH int，继续往前找
                            pass
                        else:
                            # 遇到其他类型，停止搜索
                            break
                    elif prev.opcode == CD_TEXT or prev.opcode == CD_NAME or prev.opcode == CD_NL:
                        # 遇到这些指令，说明跨越了语句边界，停止搜索
                        break
                
                # 只有同时找到PUSH str和PUSH int 61才设置注音
                if found_push_str and found_push_61 and ruby_str_id is not None:
                    self.pending_ruby = self.get_string(ruby_str_id)
                    self.pending_ruby_str_id = ruby_str_id
            # 检查是否为清除注音命令 (PUSH int 61 -> COMMAND arg_list=0 args=0)
            elif arg_list_id == 0 and arg_cnt == 0:
                # 检查前面是否有PUSH int 61
                for i in range(len(self.instructions)-1, max(-1, len(self.instructions)-10), -1):
                    prev = self.instructions[i]
                    if prev.opcode == CD_PUSH and len(prev.operands) >= 2:
                        if prev.operands[0] == FM_INT and prev.operands[1] == RUBY_FUNCTION_ID:
                            self.pending_ruby = None
                            self.pending_ruby_str_id = None
                            break
                        elif prev.operands[0] == FM_STR:
                            break
            
            # 检查是否为特效文本命令 (PUSH int 2113929255 -> ... -> PUSH str [文本] -> COMMAND)
            # 特效文本通常有多个参数，最后一个非空字符串是实际文本
            if arg_cnt >= 2:  # 特效文本通常有4个参数
                found_effect_func = False
                effect_text_str_id = None
                
                # 查找前面的指令，确认是否为特效文本模式
                for i in range(len(self.instructions)-1, max(-1, len(self.instructions)-20), -1):
                    prev = self.instructions[i]
                    if prev.opcode == CD_PUSH and len(prev.operands) >= 2:
                        if prev.operands[0] == FM_INT and prev.operands[1] == EFFECT_TEXT_FUNCTION_ID:
                            found_effect_func = True
                            break
                        elif prev.operands[0] == FM_STR:
                            # 记录最后一个字符串参数（还需要确认是否有特效函数ID）
                            candidate_str_id = prev.operands[1]
                            candidate_text = self.get_string(candidate_str_id)
                            # 只记录非空且含有东亚字符的文本
                            if candidate_text and candidate_text.strip() and effect_text_str_id is None:
                                # 检查是否含有东亚字符（过滤掉路径、文件名等）
                                if has_east_asian_char(candidate_text):
                                    effect_text_str_id = candidate_str_id
                    elif prev.opcode == CD_NL:
                        # 跨越行边界，停止
                        break
                
                # 如果找到特效文本函数且有有效文本
                if found_effect_func and effect_text_str_id is not None:
                    effect_text = self.get_string(effect_text_str_id)
                    # 创建文本条目（特效文本没有注音，read_flag设为-1标识）
                    entry = TextEntry(effect_text_str_id, effect_text, self.current_line, -1, None, None)
                    self.text_entries.append(entry)
            
            # 检查是否为选择支命令 (PUSH int 76 -> PUSH str[] 多个选项 -> COMMAND)
            # 选择支通常有多个字符串参数
            if arg_cnt >= 2:
                found_selection_func = False
                selection_str_ids = []
                
                # 查找前面的指令，确认是否为选择支模式
                for i in range(len(self.instructions)-1, max(-1, len(self.instructions)-30), -1):
                    prev = self.instructions[i]
                    if prev.opcode == CD_PUSH and len(prev.operands) >= 2:
                        if prev.operands[0] == FM_INT and prev.operands[1] == SELECTION_FUNCTION_ID:
                            found_selection_func = True
                            break
                        elif prev.operands[0] == FM_STR:
                            # 收集字符串参数
                            selection_str_ids.append(prev.operands[1])
                    elif prev.opcode == CD_NL:
                        # 跨越行边界，停止
                        break
                
                # 如果找到选择支函数且有字符串参数
                if found_selection_func and selection_str_ids:
                    # 选择支字符串是按逆序收集的，需要反转
                    selection_str_ids.reverse()
                    for sel_str_id in selection_str_ids:
                        sel_text = self.get_string(sel_str_id)
                        # 只提取含有东亚字符的文本（过滤内部标识符）
                        if sel_text and not sel_text.startswith('_'):
                            # 创建文本条目（选择支文本，read_flag设为-3标识）
                            entry = TextEntry(sel_str_id, sel_text, self.current_line, -3, None, None)
                            self.text_entries.append(entry)
            
            # 检查是否为注释文本命令 (PUSH int 2113929497 -> ... -> PUSH str [文本] -> COMMAND)
            # 注释文本通常是开发者备注/场景说明
            if arg_cnt >= 1:
                found_comment_func = False
                comment_text_str_id = None
                
                # 查找前面的指令，确认是否为注释文本模式
                for i in range(len(self.instructions)-1, max(-1, len(self.instructions)-20), -1):
                    prev = self.instructions[i]
                    if prev.opcode == CD_PUSH and len(prev.operands) >= 2:
                        if prev.operands[0] == FM_INT and prev.operands[1] == COMMENT_TEXT_FUNCTION_ID:
                            found_comment_func = True
                            break
                        elif prev.operands[0] == FM_STR:
                            # 记录最后一个字符串参数
                            candidate_str_id = prev.operands[1]
                            candidate_text = self.get_string(candidate_str_id)
                            # 只记录非空且含有东亚字符的文本
                            if candidate_text and candidate_text.strip() and comment_text_str_id is None:
                                if has_east_asian_char(candidate_text):
                                    comment_text_str_id = candidate_str_id
                    elif prev.opcode == CD_NL:
                        # 跨越行边界，停止
                        break
                
                # 如果找到注释文本函数且有有效文本
                if found_comment_func and comment_text_str_id is not None:
                    comment_text = self.get_string(comment_text_str_id)
                    # 创建文本条目（注释文本，read_flag设为-4标识）
                    entry = TextEntry(comment_text_str_id, comment_text, self.current_line, -4, None, None)
                    self.text_entries.append(entry)
            
            # 检查是否为术语文本命令 (PUSH int 2113929817 -> PUSH str [文本] -> COMMAND)
            # 术语文本用于显示游戏中的专有名词
            if arg_cnt >= 1:
                found_term_func = False
                term_text_str_id = None
                
                # 查找前面的指令，确认是否为术语文本模式
                for i in range(len(self.instructions)-1, max(-1, len(self.instructions)-15), -1):
                    prev = self.instructions[i]
                    if prev.opcode == CD_PUSH and len(prev.operands) >= 2:
                        if prev.operands[0] == FM_INT and prev.operands[1] in (TERM_CHECK_FUNCTION_ID, TERM_SET_FUNCTION_ID, SYSTEM_MSG_FUNCTION_ID):
                            found_term_func = True
                            break
                        elif prev.operands[0] == FM_STR:
                            # 记录最后一个字符串参数
                            candidate_str_id = prev.operands[1]
                            candidate_text = self.get_string(candidate_str_id)
                            # 只记录非空且含有东亚字符的文本
                            if candidate_text and candidate_text.strip() and term_text_str_id is None:
                                if has_east_asian_char(candidate_text):
                                    term_text_str_id = candidate_str_id
                    elif prev.opcode == CD_NL:
                        # 跨越行边界，停止
                        break
                
                # 如果找到术语文本函数且有有效文本
                if found_term_func and term_text_str_id is not None:
                    term_text = self.get_string(term_text_str_id)
                    # 创建文本条目（术语文本，read_flag设为-5标识）
                    entry = TextEntry(term_text_str_id, term_text, self.current_line, -5, None, None)
                    self.text_entries.append(entry)
            
        elif opcode == CD_TEXT:
            read_flag = self.read_int()
            operands = [read_flag]
            
            # 过滤异常的flag值（语音ID标识等）
            # 正常的read_flag通常在较小范围内，异常大的值（如50331651=0x03000003）是特殊标识
            if read_flag > 0x10000:  # 65536以上视为异常
                # 不是正常的文本条目，跳过
                pass
            else:
                # 查找前面的PUSH str获取文本
                for i in range(len(self.instructions)-1, -1, -1):
                    prev = self.instructions[i]
                    if prev.opcode == CD_PUSH and len(prev.operands) >= 2 and prev.operands[0] == FM_STR:
                        str_id = prev.operands[1]
                        text = self.get_string(str_id)
                        
                        # 创建文本条目，附带注音信息
                        entry = TextEntry(str_id, text, self.current_line, read_flag, 
                                         self.pending_ruby, self.pending_ruby_str_id)
                        self.text_entries.append(entry)
                        
                        # 使用后清除注音（注音只应用于紧随的TEXT）
                        self.pending_ruby = None
                        self.pending_ruby_str_id = None
                        break
            
        elif opcode == CD_NAME:
            read_flag = self.read_int()
            operands = [read_flag]
            
            # 查找前面的PUSH str获取名称
            for i in range(len(self.instructions)-1, -1, -1):
                prev = self.instructions[i]
                if prev.opcode == CD_PUSH and len(prev.operands) >= 2 and prev.operands[0] == FM_STR:
                    str_id = prev.operands[1]
                    name = self.get_string(str_id)
                    self.name_entries.append((str_id, name, self.current_line))
                    break
        
        # 记录指令
        instr = Instruction(offset, opcode, operands)
        self.instructions.append(instr)


def load_strings(file, header):
    """加载并解密所有字符串"""
    strings = []
    
    # 读取字符串索引
    file.seek(header.str_idx_ofs)
    str_index = []
    for n in range(header.str_idx_cnt):
        offset = struct.unpack('I', file.read(4))[0]
        length = struct.unpack('I', file.read(4))[0]
        str_index.append((offset, length))
    
    # 读取并解密字符串
    for i, (offset, length) in enumerate(str_index):
        if length == 0:
            strings.append("")
            continue
        file.seek(header.str_list_ofs + offset * 2)
        data = file.read(length * 2)
        decrypted = Decrypt(data, length, i)
        try:
            text = decrypted.decode('UTF-16')
        except:
            text = ""
        strings.append(text)
    
    return strings


def has_east_asian_char(text):
    """检查是否包含东亚字符（用于过滤纯ASCII文本）"""
    for char in text:
        if unicodedata.east_asian_width(char) != 'Na':
            return True
    return False


def is_valid_ruby(ruby_text, base_text):
    """
    判断是否为有效的注音（读音标注）
    过滤掉：
    1. 音效标识 (se_xxx)
    2. 过长的"注音"（超过30字符，可能是特效文本）
    3. 包含标点符号的"注音"（真正的注音不会有这些）
    """
    if not ruby_text:
        return False
    
    # 过滤音效标识 (se_xxx, SE_xxx)
    if ruby_text.lower().startswith('se_'):
        return False
    
    # 过滤过长的"注音" - 外来语假名注音可能较长，放宽到30字符
    if len(ruby_text) > 30:
        return False
    
    # 过滤包含空格、句号、感叹号等的"注音" - 真正的读音不会有这些
    invalid_chars = [' ', '　', '。', '！', '？', '、', '\n', '，', '「', '」']
    for char in invalid_chars:
        if char in ruby_text:
            return False
    
    return True


def format_text_with_ruby(text, ruby_text):
    """将文本和注音格式化为 @r注音@被注音文本@ 格式"""
    if ruby_text:
        return f"@r{ruby_text}@{text}@"
    return text


def process_entries(text_entries, name_entries, export_all=False):
    """
    处理提取的条目：
    1. 普通文本(read_flag>=0)按line合并分句（用@n分隔）
    2. 特殊文本(read_flag<0)独立输出，不合并
    3. 匹配对应的name
    4. 处理注音格式
    5. 句子分隔符使用 @n，原始换行符保留为 \n
    
    read_flag 标识:
    - >=0: 普通TEXT指令文本
    - -1: 特效文本 (effect)
    - -2: 字符串比较文本 (compare)
    - -3: 选择支文本 (selection)
    - -4: 注释文本 (comment)
    - -5: 术语文本 (term)
    """
    # 构建line到name的映射
    # 注意：同一行可能有多个NAME（如先清空再设置），应使用最后一个（离TEXT最近的）
    line_to_name = {}
    for str_id, name, line in name_entries:
        line_to_name[line] = (str_id, name)  # 始终覆盖，保留最后一个NAME
    
    # 按line分组文本条目
    line_groups = defaultdict(list)
    for entry in text_entries:
        line_groups[entry.line].append(entry)
    
    # 分离普通文本和特殊文本
    normal_groups = defaultdict(list)  # read_flag >= 0
    special_entries = []               # read_flag < 0
    
    for entry in text_entries:
        if entry.read_flag < 0:
            special_entries.append(entry)
        else:
            normal_groups[entry.line].append(entry)
    
    result = []
    
    # 处理普通文本（合并同一行的分句）
    for line in sorted(normal_groups.keys()):
        entries = normal_groups[line]
        name_info = line_to_name.get(line, (None, ""))
        name_str_id, name = name_info if isinstance(name_info, tuple) else (None, name_info)
        
        # 构建合并后的文本
        text_parts = []
        str_ids = []  # 记录原始str_id以便封回
        
        for entry in entries:
            # 过滤空白分句（除非导出全部）
            if not export_all and entry.text.strip() == '':
                continue
            
            # 检查注音是否与角色名称相同（这是语音标识，不是真正的注音）
            actual_ruby = entry.ruby_text
            actual_ruby_str_id = entry.ruby_str_id
            if actual_ruby and actual_ruby == name:
                # 角色名称被当作注音，这是语音标识，不是真正的读音注音
                actual_ruby = None
                actual_ruby_str_id = None
            
            # 验证是否为有效的注音（过滤音效、过长文本等）
            if actual_ruby and not is_valid_ruby(actual_ruby, entry.text):
                actual_ruby = None
                actual_ruby_str_id = None
            
            formatted = format_text_with_ruby(entry.text, actual_ruby)
            text_parts.append(formatted)
            str_ids.append({
                "str_id": entry.str_id,
                "original": entry.text,
                "ruby": actual_ruby,
                "ruby_str_id": actual_ruby_str_id,
                "read_flag": entry.read_flag
            })
        
        # 如果过滤后没有内容，跳过
        if not text_parts:
            continue
        
        # 使用 @n 作为句子分隔符（区别于原始换行符 \\n）
        merged_text = "@n".join(text_parts)
        
        # 过滤纯ASCII文本（除非指定导出全部）
        if not export_all and not has_east_asian_char(merged_text):
            continue
        
        result.append({
            "line": line,
            "name": name,
            "name_str_id": name_str_id,  # 保存name的str_id用于封回
            "text": merged_text,
            "parts": str_ids  # 保存原始分句信息用于封回
        })
    
    # 处理特殊文本（每个独立输出，不合并）
    for entry in special_entries:
        # 过滤空白文本（除非导出全部）
        if not export_all and entry.text.strip() == '':
            continue
        
        # 过滤纯ASCII文本（除非指定导出全部）
        if not export_all and not has_east_asian_char(entry.text):
            continue
        
        name_info = line_to_name.get(entry.line, (None, ""))
        name_str_id, name = name_info if isinstance(name_info, tuple) else (None, name_info)
        
        # 确定文本类型
        if entry.read_flag == -1:
            text_type = "effect"  # 特效文本
        elif entry.read_flag == -2:
            text_type = "compare"  # 字符串比较文本
        elif entry.read_flag == -3:
            text_type = "selection"  # 选择支文本
        elif entry.read_flag == -4:
            text_type = "comment"  # 注释文本
        elif entry.read_flag == -5:
            text_type = "term"  # 术语文本
        else:
            text_type = "special"
        
        result.append({
            "line": entry.line,
            "name": name,
            "name_str_id": name_str_id,  # 保存name的str_id用于封回
            "text": entry.text,
            "type": text_type,
            "parts": [{
                "str_id": entry.str_id,
                "original": entry.text,
                "ruby": None,
                "ruby_str_id": None,
                "read_flag": entry.read_flag
            }]
        })
    
    # 按line排序
    result.sort(key=lambda x: x['line'])
    
    return result


def extract_ss_file(ss_path, export_all=False, verbose=False):
    """从单个SS文件提取文本"""
    try:
        with open(ss_path, 'rb') as f:
            header = SSHeader(f)
            
            if verbose:
                print(f"  字节码: offset={header.scn_ofs}, size={header.scn_size}")
                print(f"  字符串数量: {header.str_idx_cnt}")
            
            # 加载字符串
            strings = load_strings(f, header)
            
            # 读取字节码
            f.seek(header.scn_ofs)
            bytecode = f.read(header.scn_size)
            
            # 提取文本
            extractor = SSTextExtractor(bytecode, strings, header)
            text_entries, name_entries = extractor.extract()
            
            if verbose:
                print(f"  文本条目: {len(text_entries)}")
                print(f"  名称条目: {len(name_entries)}")
                ruby_count = sum(1 for e in text_entries if e.ruby_text)
                print(f"  注音条目: {ruby_count}")
            
            # 处理并合并条目
            processed = process_entries(text_entries, name_entries, export_all)
            
            return {
                "file": os.path.basename(ss_path),
                "string_count": header.str_idx_cnt,
                "entries": processed
            }
            
    except Exception as e:
        print(f"  错误: {str(e)}")
        return None


def remove_ruby(text):
    """
    去除文本中的注音格式：
    - 将 @r注音@文本@ 替换为 文本
    - 处理前后可能的@n
    """
    # 匹配各种注音格式，包括前后可能的@n
    patterns = [
        r'@n\s*@r([^@]*)@([^@]*)@\s*@n',  # 前后都有@n
        r'@n\s*@r([^@]*)@([^@]*)@',          # 前面有@n
        r'@r([^@]*)@([^@]*)@\s*@n',          # 后面有@n
        r'@r([^@]*)@([^@]*)@'                  # 基本格式
    ]
    
    # 依次应用所有模式，多次替换直到没有匹配项
    for pattern in patterns:
        while True:
            new_text = re.sub(pattern, r'\2', text)
            if new_text == text:
                break
            text = new_text
    
    return text


def convert_to_pure_format(data, strip_ruby=False):
    """
    将完整格式转换为纯净格式
    纯净格式: [{"name": "角色名", "message": "对话文本"}, ...]
    如果没有name，则只保留message字段
    strip_ruby: 是否去除注音格式
    """
    pure_entries = []
    for entry in data.get('entries', []):
        pure_entry = {}
        if entry.get('name'):
            pure_entry['name'] = entry['name']
        message = entry.get('text', '')
        if strip_ruby:
            message = remove_ruby(message)
        pure_entry['message'] = message
        pure_entries.append(pure_entry)
    return pure_entries


def strip_ruby_from_full_format(data):
    """
    从完整格式JSON中去除注音
    处理:
    1. entry['text'] - 主文本字段
    2. entry['parts'][*]['original'] - 各分句的原始文本
    """
    import copy
    result = copy.deepcopy(data)
    
    for entry in result.get('entries', []):
        # 去除主文本中的注音
        if 'text' in entry:
            entry['text'] = remove_ruby(entry['text'])
        
        # 去除各分句中的注音
        if 'parts' in entry:
            for part in entry['parts']:
                if 'original' in part:
                    part['original'] = remove_ruby(part['original'])
    
    return result


def disassemble_ss_file(ss_path, out_folder, verbose=False):
    """反汇编单个SS文件并输出到.asm文件"""
    try:
        with open(ss_path, 'rb') as f:
            header = SSHeader(f)
            
            if verbose:
                print(f"  头部大小: {header.header_size}")
                print(f"  字节码: offset={header.scn_ofs}, size={header.scn_size}")
                print(f"  字符串数量: {header.str_idx_cnt}")
                print(f"  标签数量: {header.label_cnt}")
                print(f"  Z标签数量: {header.z_label_cnt}")
            
            # 加载字符串
            strings = load_strings(f, header)
            
            # 读取字节码
            f.seek(header.scn_ofs)
            bytecode = f.read(header.scn_size)
            
            # 反汇编
            disasm = SSDisassembler(bytecode, strings, header)
            instructions = disasm.disassemble()
            
            # 输出.asm文件
            asm_name = os.path.basename(ss_path) + '.asm'
            asm_path = out_folder + asm_name
            
            with open(asm_path, 'w', encoding='utf-8') as af:
                af.write(f"; SS File Disassembly\n")
                af.write(f"; File: {ss_path}\n")
                af.write(f"; Bytecode size: {header.scn_size}\n")
                af.write(f"; String count: {len(strings)}\n")
                af.write(f";\n")
                af.write(f"; Text entries: {len(disasm.text_entries)}\n")
                af.write(f"; Name entries: {len(disasm.name_entries)}\n")
                af.write(f"; Command entries: {len(disasm.command_entries)}\n")
                af.write(f"\n")
                
                for instr in instructions:
                    af.write(f"{instr.offset:08X}: {instr.description}\n")
                
                # 添加完整字符串表（用于封回）
                af.write(f"\n")
                af.write(f"; ================================\n")
                af.write(f"; STRING TABLE (for packing)\n")
                af.write(f"; Format: @STR[index]=content\n")
                af.write(f"; ================================\n")
                for i, s in enumerate(strings):
                    if s:  # 只输出非空字符串
                        # 转义换行符
                        escaped = s.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')
                        af.write(f"@STR[{i}]={escaped}\n")
            
            print(f"  反汇编: {asm_path}")
            print(f"    指令数: {len(instructions)}")
            print(f"    文本条目: {len(disasm.text_entries)}")
            print(f"    名称条目: {len(disasm.name_entries)}")
            print(f"    命令条目: {len(disasm.command_entries)}")
            
            return True
            
    except Exception as e:
        print(f"  错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def export_ss_to_txt_raw(ss_path, out_folder, verbose=False):
    """
    全量文本模式：从SS文件提取所有字符串到TXT文件
    按索引顺序输出，每行一个字符串，不跳过空行
    TXT使用UTF-16LE编码
    """
    try:
        with open(ss_path, 'rb') as f:
            header = SSHeader(f)
            strings = load_strings(f, header)
        
        txt_name = os.path.basename(ss_path) + '.txt'
        txt_path = os.path.join(out_folder, txt_name)
        
        with open(txt_path, 'w', encoding='utf-16-le') as tf:
            # 写入BOM
            tf.write('\ufeff')
            for s in strings:
                # 转义换行符为可见形式
                escaped = s.replace('\r\n', '\\r\\n').replace('\n', '\\n').replace('\r', '\\r')
                tf.write(escaped + '\n')
        
        if verbose:
            print(f"  导出全量TXT: {txt_path} ({len(strings)} 条字符串)")
        return True
        
    except Exception as e:
        print(f"  错误: {str(e)}")
        return False


def export_json_to_txt_trans(json_path, out_folder, verbose=False):
    """
    翻译文本模式：从纯净JSON格式转换为翻译TXT
    格式: 
      ◇编号◇name（原文）
      ◆编号◆name（译文占位）
      空行
      ◇编号◇message（原文）  
      ◆编号◆message（译文占位）
    TXT使用UTF-16LE编码
    """
    try:
        with open(json_path, 'r', encoding='utf-8') as jf:
            data = json.load(jf)
        
        # 确保是纯净格式（列表）
        if not isinstance(data, list):
            if isinstance(data, dict) and 'entries' in data:
                data = convert_to_pure_format(data)
            else:
                print(f"  错误: 不支持的JSON格式")
                return False
        
        txt_name = os.path.basename(json_path).replace('.json', '.txt')
        txt_path = os.path.join(out_folder, txt_name)
        
        with open(txt_path, 'w', encoding='utf-16-le') as tf:
            # 写入BOM
            tf.write('\ufeff')
            idx = 1
            for entry in data:
                name = entry.get('name', '')
                message = entry.get('message', '')
                
                # 输出name（如果有）
                if name:
                    tf.write(f"◇{idx:08d}◇{name}\n")
                    tf.write(f"◆{idx:08d}◆{name}\n")
                    tf.write("\n")
                    idx += 1
                
                # 输出message
                if message:
                    tf.write(f"◇{idx:08d}◇{message}\n")
                    tf.write(f"◆{idx:08d}◆{message}\n")
                    tf.write("\n")
                    idx += 1
        
        if verbose:
            print(f"  导出翻译TXT: {txt_path} ({idx-1} 条文本)")
        return True
        
    except Exception as e:
        print(f"  错误: {str(e)}")
        return False


def export_ss_to_txt_trans(ss_path, out_folder, verbose=False, strip_ruby=False):
    """
    翻译文本模式：直接从SS文件提取并输出为翻译TXT格式
    先提取为纯净JSON格式，再转换为翻译TXT
    strip_ruby: 是否去除注音格式
    """
    try:
        result = extract_ss_file(ss_path, export_all=False, verbose=False)
        if not result:
            return False
        
        pure_data = convert_to_pure_format(result, strip_ruby=strip_ruby)
        
        txt_name = os.path.basename(ss_path) + '.txt'
        txt_path = os.path.join(out_folder, txt_name)
        
        with open(txt_path, 'w', encoding='utf-16-le') as tf:
            # 写入BOM
            tf.write('\ufeff')
            idx = 1
            for entry in pure_data:
                name = entry.get('name', '')
                message = entry.get('message', '')
                
                # 输出name（如果有）
                if name:
                    tf.write(f"◇{idx:08d}◇{name}\n")
                    tf.write(f"◆{idx:08d}◆{name}\n")
                    tf.write("\n")
                    idx += 1
                
                # 输出message
                if message:
                    tf.write(f"◇{idx:08d}◇{message}\n")
                    tf.write(f"◆{idx:08d}◆{message}\n")
                    tf.write("\n")
                    idx += 1
        
        if verbose:
            print(f"  导出翻译TXT: {txt_path} ({idx-1} 条文本)")
        return True
        
    except Exception as e:
        print(f"  错误: {str(e)}")
        return False


def main(argv):
    # 解析选项
    verbose = '-v' in argv
    pure_mode = '-p' in argv
    disasm_mode = '-d' in argv
    txt_raw_mode = '-t' in argv
    txt_trans_mode = '-T' in argv
    remove_ruby_mode = '-r' in argv
    
    if verbose:
        argv.remove('-v')
    if pure_mode:
        argv.remove('-p')
    if disasm_mode:
        argv.remove('-d')
    if txt_raw_mode:
        argv.remove('-t')
    if txt_trans_mode:
        argv.remove('-T')
    if remove_ruby_mode:
        argv.remove('-r')
    
    if len(argv) < 2 or argv[1] == '':
        print("SS文本提取脚本 - 从.ss文件提取文本到JSON/TXT")
        print("")
        print("用法: " + os.path.basename(argv[0]) + " <Scene\\> [Output\\] [options]")
        print("")
        print("选项:")
        print("  -v: 详细输出模式")
        print("  -p: 纯净JSON模式（只输出name和message字段）")
        print("  -r: 去除注音格式（将 @r注音@文本@ 转为 文本，支持JSON和-T模式）")
        print("  -d: 反汇编模式（输出字节码反汇编到.asm文件）")
        print("  -t: 全量TXT模式（导出所有字符串到TXT，UTF-16LE编码）")
        print("  -T: 翻译TXT模式（导出◇原文/◆译文格式，UTF-16LE编码）")
        print("")
        print("输出格式:")
        print("  完整JSON: {file, string_count, entries: [{line, name, text, parts}]}")
        print("  纯净JSON(-p): [{name, message}, ...]")
        print("  反汇编(-d): 输出.asm文件")
        print("  全量TXT(-t): 每行一个字符串，按索引顺序")
        print("  翻译TXT(-T): ◇编号◇原文 / ◆编号◆译文")
        print("")
        print("注音格式: @r注音@被注音文本@")
        print("句子分隔符使用 @n，原始换行符保留为 \\n")
        return False
    
    # 设置路径
    in_folder = argv[1].rstrip('\\') + '\\'
    
    if len(argv) >= 3 and argv[2] != '':
        out_folder = argv[2].rstrip('\\') + '\\'
    else:
        if disasm_mode:
            out_folder = in_folder.rstrip('\\') + '_asm\\'
        elif txt_raw_mode:
            out_folder = in_folder.rstrip('\\') + '_txt_raw\\'
        elif txt_trans_mode:
            out_folder = in_folder.rstrip('\\') + '_txt_trans\\'
        else:
            out_folder = in_folder.rstrip('\\') + '_json\\'
    
    if not os.path.exists(out_folder):
        os.makedirs(out_folder)
    
    # 处理所有SS文件
    # 使用 glob.escape() 转义路径中的特殊字符（如 [] 等）
    escaped_folder = glob.escape(in_folder.rstrip('\\')) + '\\'
    ss_files = glob.glob(escaped_folder + "*.ss")
    
    if not ss_files:
        print(f"错误: 在 {in_folder} 中未找到.ss文件")
        return False
    
    print(f"找到 {len(ss_files)} 个SS文件")
    
    for ss_path in ss_files:
        print(f"处理: {ss_path}")
        
        if disasm_mode:
            # 反汇编模式
            disassemble_ss_file(ss_path, out_folder, verbose)
        elif txt_raw_mode:
            # 全量TXT模式
            export_ss_to_txt_raw(ss_path, out_folder, verbose)
        elif txt_trans_mode:
            # 翻译TXT模式
            export_ss_to_txt_trans(ss_path, out_folder, verbose, strip_ruby=remove_ruby_mode)
        else:
            # JSON文本提取模式
            result = extract_ss_file(ss_path, False, verbose)
            
            if result:
                # 保存JSON
                json_name = os.path.basename(ss_path) + '.json'
                json_path = out_folder + json_name
                
                with open(json_path, 'w', encoding='utf-8') as jf:
                    if pure_mode:
                        # 纯净模式: 只输出 name 和 message
                        pure_data = convert_to_pure_format(result, strip_ruby=remove_ruby_mode)
                        json.dump(pure_data, jf, ensure_ascii=False, indent=2)
                    else:
                        # 完整模式
                        if remove_ruby_mode:
                            output_data = strip_ruby_from_full_format(result)
                        else:
                            output_data = result
                        json.dump(output_data, jf, ensure_ascii=False, indent=2)
                
                mode_str = "纯净模式" if pure_mode else "完整模式"
                if remove_ruby_mode:
                    mode_str += "+去除注音"
                print(f"  导出({mode_str}): {json_path} ({len(result['entries'])} 条目)")
    
    print("完成！")
    return True


if __name__ == "__main__":
    main(sys.argv)
