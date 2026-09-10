# -*- coding: utf-8 -*-
"""
ArkScript VM — Opcode 定义模块
================================
目标引擎: ArkScript (月の守 / tkm.exe)
文件格式: .bin = 12字节头 + 字节码
字符串加密: 逐字节取反 (~byte / XOR 0xFF)
编码: CP932 (Shift-JIS)

验证: 28/28 .bin 文件全量逐字节解析通过
"""

import struct

# ============================================================
# 文件头
# ============================================================
HEADER_SIZE = 12          # 8字节 double(版本) + 4字节 保留
HEADER_VERSION = 2.2      # LE double

# ============================================================
# 操作数类型
# ============================================================
# type_ref : 1字节类型ID
# var_ref  : 2字节变量引用 (scope_byte, index_byte)
# f32      : 4字节 LE float32 (数值或绝对跳转偏移)
# u8       : 1字节无符号整数
# u32_len  : 4字节 LE u32 (字符串长度, 后跟 N 字节取反加密数据)

TYPE_REF = "type_ref"   # 1B
VAR_REF  = "var_ref"    # 2B
F32      = "f32"        # 4B
U8       = "u8"         # 1B
STR_DATA = "str_data"   # 变长: 4B(len) + N字节

# ============================================================
# 完整 Opcode 表
# ============================================================
# 格式: opcode -> (助记符, [操作数类型列表], 语义说明)
#
# 操作数读取顺序严格遵循伪C中handler的读取顺序:
#   sub_40A840 → 读1字节 type_ref
#   sub_40A920 → 读2字节 var_ref (scope + index)
#   sub_4058D0(ctx, 4) → 读4字节 f32
#   sub_4058D0(ctx, 1) → 读1字节 u8
#   0x13特殊: 读4字节长度 + N字节取反加密字符串

OPCODES = {
    # --- 基础栈操作 ---
    0x00: ("PUSH_TYPE0",  [TYPE_REF],           "创建类型对象压栈(mode=0)"),
    0x01: ("PUSH_TYPE1",  [TYPE_REF],           "创建类型对象压栈(mode=1)"),
    0x11: ("PUSH_NUM",    [F32],                "压入float数值字面量"),
    0x12: ("PUSH_VAR",    [VAR_REF],            "压入变量值"),
    0x13: ("PUSH_STR",    [STR_DATA],           "压入NOT加密的CP932字符串"),
    0x20: ("POP",         [],                   "弹出栈顶"),
    0x21: ("DUP",         [],                   "复制栈顶 (sub_406CF0)"),
    0x22: ("DUP2",        [],                   "复制栈顶 (同0x21 handler)"),
    0x80: ("PUSH_BYTE",   [U8],                 "压入1字节值"),

    # --- 流程控制 ---
    0x10: ("CALL",        [VAR_REF],            "调用/跳转(通过变量)"),
    0x15: ("STORE",       [VAR_REF],            "存储/赋值"),
    0x70: ("JMP",         [F32],                "无条件跳转(绝对偏移)"),
    0x71: ("JMP_IF0",     [VAR_REF, F32],       "条件跳转: var==0时跳转"),
    0xA0: ("CALL_SUB",    [F32],                "调用子过程(绝对偏移)"),
    0xA2: ("CALL_EXT",    [F32],                "调用外部过程(绝对偏移)"),
    0xB0: ("END",         [],                   "脚本结束标记"),
    0xB1: ("RETURN",      [],                   "返回/结束段"),

    # --- 算术/比较 ---
    0x30: ("ARITH",       [VAR_REF, VAR_REF],   "算术运算(两变量)"),
    0x31: ("ARITH_LIT",   [F32, VAR_REF],       "算术运算(字面量+变量)"),
    0x32: ("COMPARE",     [F32, U8, VAR_REF],   "比较运算"),
    0x90: ("SET_PROP",    [U8, VAR_REF],        "设置属性(字节+变量)"),

    # --- 双操作数组: 0x4x (ADD/SUB/MUL/DIV/MOD 系列) ---
    # 奇数 = 2×var_ref (两变量操作)
    # 偶数 = var_ref + f32 (变量+字面量操作)
    0x41: ("ADD_VV",      [VAR_REF, VAR_REF],   "加法: var += var"),
    0x42: ("ADD_VF",      [VAR_REF, F32],       "加法: var += float"),
    0x43: ("SUB_VV",      [VAR_REF, VAR_REF],   "减法: var -= var"),
    0x44: ("SUB_VF",      [VAR_REF, F32],       "减法: var -= float"),
    0x45: ("MUL_VV",      [VAR_REF, VAR_REF],   "乘法: var *= var"),
    0x46: ("MUL_VF",      [VAR_REF, F32],       "乘法: var *= float"),
    0x47: ("DIV_VV",      [VAR_REF, VAR_REF],   "除法: var /= var"),
    0x48: ("DIV_VF",      [VAR_REF, F32],       "除法: var /= float"),
    0x49: ("MOD_VV",      [VAR_REF, VAR_REF],   "取模: var %= var"),
    0x4A: ("MOD_VF",      [VAR_REF, F32],       "取模: var %= float"),

    # --- 双操作数组: 0x5x (比较系列) ---
    0x50: ("EQ_VV",       [VAR_REF, VAR_REF],   "等于: var == var"),
    0x51: ("EQ_VF",       [VAR_REF, F32],       "等于: var == float"),
    0x52: ("NE_VV",       [VAR_REF, VAR_REF],   "不等: var != var"),
    0x53: ("NE_VF",       [VAR_REF, F32],       "不等: var != float"),
    0x54: ("GT_VV",       [VAR_REF, VAR_REF],   "大于: var > var"),
    0x55: ("GT_VF",       [VAR_REF, F32],       "大于: var > float"),
    0x56: ("GE_VV",       [VAR_REF, VAR_REF],   "大于等于: var >= var"),
    0x57: ("GE_VF",       [VAR_REF, F32],       "大于等于: var >= float"),
    0x58: ("LT_VV",       [VAR_REF, VAR_REF],   "小于: var < var"),
    0x59: ("LT_VF",       [VAR_REF, F32],       "小于: var < float"),
    0x5A: ("LE_VV",       [VAR_REF, VAR_REF],   "小于等于: var <= var"),
    0x5B: ("LE_VF",       [VAR_REF, F32],       "小于等于: var <= float"),

    # --- 双操作数组: 0x6x (逻辑运算系列) ---
    0x60: ("AND_VV",      [VAR_REF, VAR_REF],   "逻辑与"),
    0x61: ("AND_VF",      [VAR_REF, F32],       "逻辑与(变量+字面量)"),
    0x62: ("OR_VV",       [VAR_REF, VAR_REF],   "逻辑或"),
    0x63: ("OR_VF",       [VAR_REF, F32],       "逻辑或(变量+字面量)"),
}

# 无操作数的NOP类opcode (default分支, 只消耗opcode字节本身)
NOP_OPCODES = {0x16, 0x22, 0x40}
# 注: 0x20(POP), 0x21(DUP), 0xB0(END), 0xB1(RETURN) 已在OPCODES中定义

# ============================================================
# 辅助函数
# ============================================================

def operand_size(op_types):
    """计算操作数列表的固定字节大小 (不含STR_DATA的可变部分)"""
    size = 0
    for t in op_types:
        if t == TYPE_REF: size += 1
        elif t == VAR_REF: size += 2
        elif t == F32:     size += 4
        elif t == U8:      size += 1
        elif t == STR_DATA: size += 4  # 只算长度字段,数据部分可变
    return size


def inst_fixed_size(op):
    """返回指令的固定部分字节数 (opcode + 固定操作数, 不含字符串数据)"""
    if op in OPCODES:
        return 1 + operand_size(OPCODES[op][1])
    else:
        return 1  # NOP


def is_jump_target(op):
    """判断opcode的操作数中是否包含跳转目标地址"""
    return op in (0x70, 0x71, 0xA0, 0xA2)


def string_encrypt(data_bytes):
    """字符串加密/解密 (对称操作: 逐字节取反)"""
    return bytes(~b & 0xFF for b in data_bytes)


def read_header(data):
    """读取文件头, 返回 (version, reserved, code_start_offset)"""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"文件太小: {len(data)} < {HEADER_SIZE}")
    version = struct.unpack('<d', data[0:8])[0]
    reserved = struct.unpack('<I', data[8:12])[0]
    return version, reserved, HEADER_SIZE


def write_header(version=HEADER_VERSION, reserved=0):
    """生成文件头字节"""
    return struct.pack('<d', version) + struct.pack('<I', reserved)
