# -*- coding: utf-8 -*-
"""
Siglus Gameexe.dat 和 Scene.pck 解密/解包/打包工具
基于 SiglusExtract-master 实现

支持功能：
- Gameexe.dat 解密和解压缩
- Scene.pck 解密和解包为单独的 .ss 文件
- 16字节加密密钥来自 siglus_key.txt
"""

import os
import sys
import struct
import time
import argparse
import multiprocessing
import threading
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from collections import OrderedDict
from numba import njit, prange
import numpy as np

# 在Windows上需要此行以支持多进程
if sys.platform.startswith('win'):
    multiprocessing.freeze_support()

try:
    # 尝试使用进程池以获得真正的并行处理
    from concurrent.futures import ProcessPoolExecutor as Executor
    using_processes = True
except ImportError:
    # 回退到线程池
    from concurrent.futures import ThreadPoolExecutor as Executor
    using_processes = False

# ============================================================================
# 静态XOR密钥（来自SiglusExtract-master）
# ============================================================================

# GameExeKey (256字节) - 用于Gameexe.dat解密
GAMEEXE_KEY = bytes([
    0xD8, 0x29, 0xB9, 0x16, 0x3D, 0x1A, 0x76, 0xD0, 0x87, 0x9B, 0x2D, 0x0C, 0x7B, 0xD1, 0xA9, 0x19,
    0x22, 0x9F, 0x91, 0x73, 0x6A, 0x35, 0xB1, 0x7E, 0xD1, 0xB5, 0xE7, 0xE6, 0xD5, 0xF5, 0x06, 0xD6,
    0xBA, 0xBF, 0xF3, 0x45, 0x3F, 0xF1, 0x61, 0xDD, 0x4C, 0x67, 0x6A, 0x6F, 0x74, 0xEC, 0x7A, 0x6F,
    0x26, 0x74, 0x0E, 0xDB, 0x27, 0x4C, 0xA5, 0xF1, 0x0E, 0x2D, 0x70, 0xC4, 0x40, 0x5D, 0x4F, 0xDA,
    0x9E, 0xC5, 0x49, 0x7B, 0xBD, 0xE8, 0xDF, 0xEE, 0xCA, 0xF4, 0x92, 0xDE, 0xE4, 0x76, 0x10, 0xDD,
    0x2A, 0x52, 0xDC, 0x73, 0x4E, 0x54, 0x8C, 0x30, 0x3D, 0x9A, 0xB2, 0x9B, 0xB8, 0x93, 0x29, 0x55,
    0xFA, 0x7A, 0xC9, 0xDA, 0x10, 0x97, 0xE5, 0xB6, 0x23, 0x02, 0xDD, 0x38, 0x4C, 0x9B, 0x1F, 0x9A,
    0xD5, 0x49, 0xE9, 0x34, 0x0F, 0x28, 0x2D, 0x1B, 0x52, 0x39, 0x5C, 0x36, 0x89, 0x56, 0xA7, 0x96,
    0x14, 0xBE, 0x2E, 0xC5, 0x3E, 0x08, 0x5F, 0x47, 0xA9, 0xDF, 0x88, 0x9F, 0xD4, 0xCC, 0x69, 0x1F,
    0x30, 0x9F, 0xE7, 0xCD, 0x80, 0x45, 0xF3, 0xE7, 0x2A, 0x1D, 0x16, 0xB2, 0xF1, 0x54, 0xC8, 0x6C,
    0x2B, 0x0D, 0xD4, 0x65, 0xF7, 0xE3, 0x36, 0xD4, 0xA5, 0x3B, 0xD1, 0x79, 0x4C, 0x54, 0xF0, 0x2A,
    0xB4, 0xB2, 0x56, 0x45, 0x2E, 0xAB, 0x7B, 0x88, 0xC5, 0xFA, 0x74, 0xAD, 0x03, 0xB8, 0x9E, 0xD5,
    0xF5, 0x6F, 0xDC, 0xFA, 0x44, 0x49, 0x31, 0xF6, 0x83, 0x32, 0xFF, 0xC2, 0xB1, 0xE9, 0xE1, 0x98,
    0x3D, 0x6F, 0x31, 0x0D, 0xAC, 0xB1, 0x08, 0x83, 0x9D, 0x0D, 0x10, 0xD1, 0x41, 0xF9, 0x00, 0xBA,
    0x1A, 0xCF, 0x13, 0x71, 0xE4, 0x86, 0x21, 0x2F, 0x23, 0x65, 0xC3, 0x45, 0xA0, 0xC3, 0x92, 0x48,
    0x9D, 0xEA, 0xDD, 0x31, 0x2C, 0xE9, 0xE2, 0x10, 0x22, 0xAA, 0xE1, 0xAD, 0x2C, 0xC4, 0x2D, 0x7F
])

# Scene密钥 (256字节) - 用于Scene.pck解密 (decrypt_2)
SCENE_KEY = bytes([
    0x70, 0xF8, 0xA6, 0xB0, 0xA1, 0xA5, 0x28, 0x4F, 0xB5, 0x2F, 0x48, 0xFA, 0xE1, 0xE9, 0x4B, 0xDE,
    0xB7, 0x4F, 0x62, 0x95, 0x8B, 0xE0, 0x03, 0x80, 0xE7, 0xCF, 0x0F, 0x6B, 0x92, 0x01, 0xEB, 0xF8,
    0xA2, 0x88, 0xCE, 0x63, 0x04, 0x38, 0xD2, 0x6D, 0x8C, 0xD2, 0x88, 0x76, 0xA7, 0x92, 0x71, 0x8F,
    0x4E, 0xB6, 0x8D, 0x01, 0x79, 0x88, 0x83, 0x0A, 0xF9, 0xE9, 0x2C, 0xDB, 0x67, 0xDB, 0x91, 0x14,
    0xD5, 0x9A, 0x4E, 0x79, 0x17, 0x23, 0x08, 0x96, 0x0E, 0x1D, 0x15, 0xF9, 0xA5, 0xA0, 0x6F, 0x58,
    0x17, 0xC8, 0xA9, 0x46, 0xDA, 0x22, 0xFF, 0xFD, 0x87, 0x12, 0x42, 0xFB, 0xA9, 0xB8, 0x67, 0x6C,
    0x91, 0x67, 0x64, 0xF9, 0xD1, 0x1E, 0xE4, 0x50, 0x64, 0x6F, 0xF2, 0x0B, 0xDE, 0x40, 0xE7, 0x47,
    0xF1, 0x03, 0xCC, 0x2A, 0xAD, 0x7F, 0x34, 0x21, 0xA0, 0x64, 0x26, 0x98, 0x6C, 0xED, 0x69, 0xF4,
    0xB5, 0x23, 0x08, 0x6E, 0x7D, 0x92, 0xF6, 0xEB, 0x93, 0xF0, 0x7A, 0x89, 0x5E, 0xF9, 0xF8, 0x7A,
    0xAF, 0xE8, 0xA9, 0x48, 0xC2, 0xAC, 0x11, 0x6B, 0x2B, 0x33, 0xA7, 0x40, 0x0D, 0xDC, 0x7D, 0xA7,
    0x5B, 0xCF, 0xC8, 0x31, 0xD1, 0x77, 0x52, 0x8D, 0x82, 0xAC, 0x41, 0xB8, 0x73, 0xA5, 0x4F, 0x26,
    0x7C, 0x0F, 0x39, 0xDA, 0x5B, 0x37, 0x4A, 0xDE, 0xA4, 0x49, 0x0B, 0x7C, 0x17, 0xA3, 0x43, 0xAE,
    0x77, 0x06, 0x64, 0x73, 0xC0, 0x43, 0xA3, 0x18, 0x5A, 0x0F, 0x9F, 0x02, 0x4C, 0x7E, 0x8B, 0x01,
    0x9F, 0x2D, 0xAE, 0x72, 0x54, 0x13, 0xFF, 0x96, 0xAE, 0x0B, 0x34, 0x58, 0xCF, 0xE3, 0x00, 0x78,
    0xBE, 0xE3, 0xF5, 0x61, 0xE4, 0x87, 0x7C, 0xFC, 0x80, 0xAF, 0xC4, 0x8D, 0x46, 0x3A, 0x5D, 0xD0,
    0x36, 0xBC, 0xE5, 0x60, 0x77, 0x68, 0x08, 0x4F, 0xBB, 0xAB, 0xE2, 0x78, 0x07, 0xE8, 0x73, 0xBF,
    # 扩展部分（与GAMEEXE_KEY相同）
    0xD8, 0x29, 0xB9, 0x16, 0x3D, 0x1A, 0x76, 0xD0, 0x87, 0x9B, 0x2D, 0x0C, 0x7B, 0xD1, 0xA9, 0x19,
    0x22, 0x9F, 0x91, 0x73, 0x6A, 0x35, 0xB1, 0x7E, 0xD1, 0xB5, 0xE7, 0xE6, 0xD5, 0xF5, 0x06, 0xD6,
    0xBA, 0xBF, 0xF3, 0x45, 0x3F, 0xF1, 0x61, 0xDD, 0x4C, 0x67, 0x6A, 0x6F, 0x74, 0xEC, 0x7A, 0x6F,
    0x26, 0x74, 0x0E, 0xDB, 0x27, 0x4C, 0xA5, 0xF1, 0x0E, 0x2D, 0x70, 0xC4, 0x40, 0x5D, 0x4F, 0xDA
])


# ============================================================================
# Numba JIT 加速函数
# ============================================================================

@njit
def xor_with_key_numba(data: np.ndarray, key: np.ndarray, mask: int) -> None:
    """使用密钥对数据进行XOR操作（带索引掩码）- Numba JIT 加速版本"""
    key_len = len(key)
    for i in range(len(data)):
        data[i] ^= key[i & mask]


@njit
def xor_with_key_256_numba(data: np.ndarray, key: np.ndarray) -> None:
    """使用256字节密钥对数据进行XOR操作 - Numba JIT 加速版本"""
    for i in range(len(data)):
        data[i] ^= key[i & 0xFF]


@njit
def decompress_data_numba(compressed: np.ndarray, decompressed_size: int) -> np.ndarray:
    """
    使用SiglusExtract的LZSS变体算法解压缩数据 - Numba JIT 加速版本
    
    算法处理流程：
    读取一个控制字节，后跟8个数据单元。
    控制字节中的每一位表示：
    - 1: 下一个字节是字面量
    - 0: 接下来的2个字节是回溯引用（offset << 4 | length，复制length+2个字节）
    """
    output = np.zeros(decompressed_size, dtype=np.uint8)
    in_pos = 0
    out_pos = 0
    compressed_len = len(compressed)
    
    while out_pos < decompressed_size and in_pos < compressed_len:
        control = compressed[in_pos]
        in_pos += 1
        
        for bit in range(8):
            if out_pos >= decompressed_size:
                break
                
            if control & 1:
                if in_pos >= compressed_len:
                    break
                output[out_pos] = compressed[in_pos]
                out_pos += 1
                in_pos += 1
            else:
                if in_pos + 1 >= compressed_len:
                    break
                    
                ref_data = (compressed[in_pos + 1] << 8) | compressed[in_pos]
                in_pos += 2
                
                copy_length = (ref_data & 0x0F) + 2
                copy_offset = ref_data >> 4
                
                for _ in range(copy_length):
                    if out_pos >= decompressed_size:
                        break
                    if copy_offset > out_pos:
                        output[out_pos] = 0
                    else:
                        output[out_pos] = output[out_pos - copy_offset]
                    out_pos += 1
            
            control >>= 1
    
    return output


@njit
def compress_data_numba(data: np.ndarray, level: int) -> np.ndarray:
    """
    使用SiglusExtract的LZSS变体算法压缩数据 - Numba JIT 加速版本
    
    算法来自Compression.cpp：
    - 回查窗口大小: 4095 (0xFFF) - 最大偏移量适合12位
    - 匹配长度: 2到(level)字节，level范围: 2-17
    - 控制字节: 每个单元1位，1=字面量，0=回溯引用
    - 回溯引用格式: (offset << 4) | (length - 2)，作为16位值
    
    参数:
        data: 输入数据
        level: 压缩级别 2-17（越高=压缩越好，越慢）
    """
    MAX_OFFSET = 4095
    MIN_MATCH = 2
    MAX_MATCH = level
    
    output = np.zeros(len(data) * 2, dtype=np.uint8)
    output_len = 0
    data_len = len(data)
    pos = 0
    
    while pos < data_len:
        control_byte = 0
        control_pos = output_len
        output[output_len] = 0
        output_len += 1
        
        for bit in range(8):
            if pos >= data_len:
                control_byte |= (1 << bit)
                output[output_len] = 0
                output_len += 1
                continue
            
            best_offset = 0
            best_length = 0
            
            search_start = max(0, pos - MAX_OFFSET)
            max_possible = min(MAX_MATCH, data_len - pos)
            
            for search_pos in range(search_start, pos):
                match_len = 0
                
                while match_len < max_possible:
                    if data[search_pos + match_len] == data[pos + match_len]:
                        match_len += 1
                    else:
                        break
                
                if match_len >= MIN_MATCH and match_len > best_length:
                    best_length = match_len
                    best_offset = pos - search_pos
                    if best_length == MAX_MATCH:
                        break
            
            if best_length >= MIN_MATCH and best_offset <= MAX_OFFSET:
                ref_data = (best_offset << 4) | (best_length - 2)
                output[output_len] = ref_data & 0xFF
                output[output_len + 1] = (ref_data >> 8) & 0xFF
                output_len += 2
                pos += best_length
            else:
                control_byte |= (1 << bit)
                output[output_len] = data[pos]
                output_len += 1
                pos += 1
        
        output[control_pos] = control_byte
    
    comp_size = output_len + 8
    decomp_size = data_len
    
    result = np.zeros(comp_size, dtype=np.uint8)
    result[0] = comp_size & 0xFF
    result[1] = (comp_size >> 8) & 0xFF
    result[2] = (comp_size >> 16) & 0xFF
    result[3] = (comp_size >> 24) & 0xFF
    result[4] = decomp_size & 0xFF
    result[5] = (decomp_size >> 8) & 0xFF
    result[6] = (decomp_size >> 16) & 0xFF
    result[7] = (decomp_size >> 24) & 0xFF
    
    for i in range(output_len):
        result[8 + i] = output[i]
    
    return result


@njit
def fake_compress_numba(data: np.ndarray) -> np.ndarray:
    """
    假压缩 - 将数据存储为字面量，不进行实际压缩 - Numba JIT 加速版本
    速度快得多，但生成的文件更大。
    
    格式: 每8个字节，输出1个控制字节（0xFF = 全是字面量）+ 8个数据字节
    """
    data_len = len(data)
    output_len = ((data_len + 7) // 8) * 9
    output = np.zeros(output_len, dtype=np.uint8)
    pos = 0
    out_idx = 0
    
    while pos < data_len:
        output[out_idx] = 0xFF
        out_idx += 1
        
        for _ in range(8):
            if pos < data_len:
                output[out_idx] = data[pos]
                pos += 1
            else:
                output[out_idx] = 0
            out_idx += 1
    
    comp_size = out_idx + 8
    decomp_size = data_len
    
    result = np.zeros(comp_size, dtype=np.uint8)
    result[0] = comp_size & 0xFF
    result[1] = (comp_size >> 8) & 0xFF
    result[2] = (comp_size >> 16) & 0xFF
    result[3] = (comp_size >> 24) & 0xFF
    result[4] = decomp_size & 0xFF
    result[5] = (decomp_size >> 8) & 0xFF
    result[6] = (decomp_size >> 16) & 0xFF
    result[7] = (decomp_size >> 24) & 0xFF
    
    for i in range(out_idx):
        result[8 + i] = output[i]
    
    return result


# ============================================================================
# 辅助函数
# ============================================================================

def read_key_file(key_path: str) -> Optional[bytes]:
    """
    从siglus_key.txt读取16字节加密密钥
    
    支持格式：
    - 十六进制字符串: 735CFC27018D67B6B568A070DC55B64B
    - 逗号分隔: 0x73, 0x5C, 0xFC, ...
    """
    try:
        with open(key_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 移除注释和空白字符
        lines = []
        for line in content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                lines.append(line)
        
        if not lines:
            print(f"错误: 在 {key_path} 中未找到密钥数据")
            return None
        
        # 尝试将第一个非注释行解析为十六进制字符串
        hex_line = lines[0].strip()
        
        # 检查是否为逗号分隔格式，如 "0x73, 0x5C, ..."
        if '0x' in hex_line.lower() and ',' in hex_line:
            # 解析逗号分隔的十六进制值
            parts = hex_line.split(',')
            key_bytes = []
            for part in parts:
                part = part.strip()
                if part.lower().startswith('0x'):
                    key_bytes.append(int(part, 16))
            if len(key_bytes) == 16:
                return bytes(key_bytes)
        else:
            # 尝试作为连续的十六进制字符串
            hex_line = ''.join(c for c in hex_line if c in '0123456789ABCDEFabcdef')
            if len(hex_line) == 32:
                return bytes.fromhex(hex_line)
        
        print(f"错误: {key_path} 中的密钥格式无效。需要16字节（32个十六进制字符）")
        return None
        
    except FileNotFoundError:
        print(f"错误: 密钥文件未找到: {key_path}")
        return None
    except Exception as e:
        print(f"读取密钥文件时出错: {e}")
        return None


def parse_key_string(key_str: str) -> Optional[bytes]:
    """
    直接解析十六位秘钥字符串
    
    支持格式：
    - 十六进制字符串: 735CFC27018D67B6B568A070DC55B64B
    - 逗号分隔: 0x73, 0x5C, 0xFC, ...
    """
    if not key_str:
        return None
    
    key_str = key_str.strip()
    
    # 检查是否为逗号分隔格式
    if '0x' in key_str.lower() and ',' in key_str:
        parts = key_str.split(',')
        key_bytes = []
        for part in parts:
            part = part.strip()
            if part.lower().startswith('0x'):
                key_bytes.append(int(part, 16))
        if len(key_bytes) == 16:
            return bytes(key_bytes)
    else:
        # 尝试作为连续的十六进制字符串
        hex_line = ''.join(c for c in key_str if c in '0123456789ABCDEFabcdef')
        if len(hex_line) == 32:
            return bytes.fromhex(hex_line)
    
    return None


def load_key_list(key_list_path: str = "KeyList.txt") -> List[Tuple[str, bytes]]:
    """
    从 KeyList.txt 加载预设秘钥列表
    
    格式示例：
    神待ちサナちゃん　DL版：
    0x2E, 0x4B, 0xDD, 0x2A, 0x7B, 0xB0, 0x0A, 0xBA, 0xF8, 0x1A, 0xF9, 0x61, 0xB0, 0x18, 0x98, 0x5C
    
    神待ちサナちゃん　PKG版：
    0x2E, 0x4B, 0xDD, 0x2A, 0x7B, 0xB0, 0x0A, 0xBA, 0xF8, 0x1A, 0xF9, 0x61, 0xB0, 0x47, 0xDC, 0x10
    
    返回: [(游戏名称, 秘钥字节), ...]
    """
    keys = []
    
    try:
        with open(key_list_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        current_name = None
        for line in lines:
            line = line.strip()
            
            # 跳过空行
            if not line:
                continue
            
            # 检查是否为逗号分隔的十六进制秘钥
            if '0x' in line.lower() and ',' in line:
                # 解析逗号分隔的十六进制值
                parts = line.split(',')
                key_bytes = []
                for part in parts:
                    part = part.strip()
                    if part.lower().startswith('0x'):
                        try:
                            key_bytes.append(int(part, 16))
                        except ValueError:
                            continue
                
                if len(key_bytes) == 16 and current_name:
                    keys.append((current_name, bytes(key_bytes)))
                current_name = None  # 重置名称
            else:
                # 作为游戏名称（如果不是十六进制秘钥）
                current_name = line
        
        return keys
        
    except FileNotFoundError:
        print(f"提示: {key_list_path} 未找到，将使用空秘钥列表")
        return []
    except Exception as e:
        print(f"读取 {key_list_path} 时出错: {e}")
        return []


def xor_with_key(data: bytearray, key: bytes, mask: int = 0x0F) -> None:
    """使用密钥对数据进行XOR操作（带索引掩码）- 使用 Numba JIT 加速"""
    data_array = np.frombuffer(data, dtype=np.uint8)
    key_array = np.frombuffer(key, dtype=np.uint8)
    xor_with_key_numba(data_array, key_array, mask)


def xor_with_key_256(data: bytearray, key: bytes) -> None:
    """使用256字节密钥对数据进行XOR操作 - 使用 Numba JIT 加速"""
    data_array = np.frombuffer(data, dtype=np.uint8)
    key_array = np.frombuffer(key, dtype=np.uint8)
    xor_with_key_256_numba(data_array, key_array)


def decompress_data(compressed: bytes, decompressed_size: int) -> bytes:
    """
    使用SiglusExtract的LZSS变体算法解压缩数据 - 使用 Numba JIT 加速
    
    算法处理流程：
    读取一个控制字节，后跟8个数据单元。
    控制字节中的每一位表示：
    - 1: 下一个字节是字面量
    - 0: 接下来的2个字节是回溯引用（offset << 4 | length，复制length+2个字节）
    """
    compressed_array = np.frombuffer(compressed, dtype=np.uint8)
    result = decompress_data_numba(compressed_array, decompressed_size)
    return result.tobytes()


def compress_data(data: bytes, level: int = 17) -> bytes:
    """
    使用SiglusExtract的LZSS变体算法压缩数据 - 使用 Numba JIT 加速
    
    算法来自Compression.cpp：
    - 回查窗口大小: 4095 (0xFFF) - 最大偏移量适合12位
    - 匹配长度: 2到(level)字节，level范围: 2-17
    - 控制字节: 每个单元1位，1=字面量，0=回溯引用
    - 回溯引用格式: (offset << 4) | (length - 2)，作为16位值
    
    参数:
        data: 输入数据
        level: 压缩级别 2-17（越高=压缩越好，越慢）
               0 = 不压缩（假压缩）
    """
    if level == 0:
        return fake_compress(data)
    
    level = max(2, min(17, level))
    data_array = np.frombuffer(data, dtype=np.uint8)
    result = compress_data_numba(data_array, level)
    return result.tobytes()


def fake_compress(data: bytes) -> bytes:
    """
    假压缩 - 将数据存储为字面量，不进行实际压缩 - 使用 Numba JIT 加速
    速度快得多，但生成的文件更大。
    
    格式: 每8个字节，输出1个控制字节（0xFF = 全是字面量）+ 8个数据字节
    """
    data_array = np.frombuffer(data, dtype=np.uint8)
    result = fake_compress_numba(data_array)
    return result.tobytes()


class ProgressTracker:
    """线程/进程安全的进度跟踪器，带显示功能"""
    def __init__(self, total: int):
        self.total = total
        self.completed = 0
        self.lock = multiprocessing.Lock() if using_processes else threading.Lock()
        self.start_time = None
        self.last_percent = -1
        
    def start(self):
        """开始计时"""
        self.start_time = time.time()
        
    def update(self, amount: int = 1):
        """按数量更新进度"""
        with self.lock:
            self.completed += amount
            
    def get_progress(self) -> Tuple[int, int, float]:
        """获取当前进度"""
        with self.lock:
            completed = self.completed
            percent = (completed / self.total * 100) if self.total > 0 else 0
            return completed, self.total, percent
    
    def display(self, message: str = ""):
        """显示当前进度"""
        completed, total, percent = self.get_progress()
        bar_length = 50
        filled_length = int(bar_length * completed / total)
        bar = '█' * filled_length + '-' * (bar_length - filled_length)
        
        # 只在进度变化超过1%或完成时才输出，减少日志量
        if int(percent) > self.last_percent or completed == total:
            self.last_percent = int(percent)
            sys.stdout.write(f'\r{message} |{bar}| {completed}/{total} ({percent:.1f}%)')
            sys.stdout.flush()


# ============================================================================
# Gameexe.dat 解密和解包
# ============================================================================

def decrypt_gameexe(file_path: str, private_key: Optional[bytes] = None, 
                    output_path: Optional[str] = None) -> bool:
    """
    解密和解压缩Gameexe.dat文件
    
    算法来自UnpackGameexe.h：
    1. 跳过前8个字节（头部）
    2. 如果提供，与私钥（16字节）进行XOR
    3. 与GameExeKey（256字节）进行XOR
    4. 读取压缩/解压缩大小（8字节）
    5. 解压缩数据
    6. 以UTF-16 LE带BOM格式输出
    """
    print(f"处理 Gameexe.dat: {file_path}")
    
    # 验证文件存在
    if not os.path.exists(file_path):
        print(f"错误: 文件未找到: {file_path}")
        return False
    
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
    except IOError as e:
        print(f"读取文件错误: {e}")
        return False
    
    # 检查最小文件大小
    if len(data) < 16:
        print(f"错误: 文件太小（至少需要16字节）")
        return False
    
    print(f"  文件大小: {len(data)} 字节")
    
    # 跳过前8个字节（头部）
    encrypted_data = bytearray(data[8:])
    
    # 步骤1: 如果提供私钥，与其进行XOR（16字节）
    if private_key:
        print(f"  使用私钥: {private_key.hex().upper()}")
        xor_with_key(encrypted_data, private_key, 0x0F)
    
    # 步骤2: 与GameExeKey（256字节）进行XOR
    xor_with_key_256(encrypted_data, GAMEEXE_KEY)
    
    # 读取压缩头部
    if len(encrypted_data) < 8:
        print("错误: 解密后的数据太小，无法包含压缩头部")
        return False
    
    comp_size = struct.unpack('<I', encrypted_data[0:4])[0]
    decomp_size = struct.unpack('<I', encrypted_data[4:8])[0]
    
    print(f"  压缩大小: {comp_size} 字节")
    print(f"  解压缩大小: {decomp_size} 字节")
    
    # 合理性检查
    if decomp_size > 200 * 1024 * 1024:  # 200MB上限
        print("错误: 解压缩大小太大 - 可能密钥错误")
        return False
    
    if decomp_size == 0:
        print("错误: 解压缩大小为0 - 可能密钥错误或文件损坏")
        return False
    
    # 解压缩
    compressed_data = bytes(encrypted_data[8:])
    
    try:
        decompressed = decompress_data(compressed_data, decomp_size)
    except Exception as e:
        print(f"解压缩过程中出错: {e}")
        return False
    
    # 确定输出路径
    if output_path is None:
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        output_dir = os.path.dirname(file_path) or '.'
        output_path = os.path.join(output_dir, f"{base_name}_decrypted.txt")
    
    # 带UTF-16 LE BOM写入输出
    try:
        with open(output_path, 'wb') as f:
            f.write(b'\xFF\xFE')  # UTF-16 LE BOM
            f.write(decompressed)
        print(f"  输出保存到: {output_path}")
        return True
    except IOError as e:
        print(f"写入输出文件错误: {e}")
        return False


def encrypt_gameexe(input_path: str, private_key: Optional[bytes] = None,
                    output_path: Optional[str] = None, use_key: bool = True,
                    compression_level: int = 17) -> bool:
    """
    将文本文件加密和压缩为Gameexe.dat格式
    
    算法来自CreateGameexe.cpp：
    1. 读取UTF-16 LE文本文件（如果存在则跳过BOM）
    2. 使用LZSS压缩数据
    3. 与GameExeKey（256字节）进行XOR
    4. 如果use_key为True，与PrivateKey（16字节）进行XOR
    5. 写入头部（8字节）+ 加密数据
    
    参数:
        compression_level: 0=不压缩，2-17=压缩级别（越高=越好但越慢）
    """
    print(f"打包 Gameexe.dat 从: {input_path}")
    
    if not os.path.exists(input_path):
        print(f"错误: 文件未找到: {input_path}")
        return False
    
    try:
        with open(input_path, 'rb') as f:
            data = f.read()
    except IOError as e:
        print(f"读取文件错误: {e}")
        return False
    
    # 检查并跳过UTF-16 LE BOM
    if len(data) >= 2 and data[:2] == b'\xFF\xFE':
        data = data[2:]
        print("  跳过UTF-16 LE BOM")
    
    print(f"  输入大小: {len(data)} 字节")
    print(f"  压缩级别: {compression_level}" + (" (不压缩)" if compression_level == 0 else ""))
    
    # 压缩
    compressed = compress_data(data, compression_level)
    print(f"  压缩后大小: {len(compressed)} 字节")
    
    # 转换为bytearray以进行XOR操作
    encrypted = bytearray(compressed)
    
    # 与GameExeKey（256字节）进行XOR
    xor_with_key_256(encrypted, GAMEEXE_KEY)
    
    # 如果提供私钥且use_key为True，与其进行XOR
    if use_key and private_key:
        print(f"  使用私钥: {private_key.hex().upper()}")
        xor_with_key(encrypted, private_key, 0x0F)
    
    # 构建头部: 4字节零 + 4字节密钥标志
    header = struct.pack('<II', 0, 1 if (use_key and private_key) else 0)
    
    # 确定输出路径
    if output_path is None:
        output_dir = os.path.dirname(input_path) or '.'
        output_path = os.path.join(output_dir, 'Gameexe.new.dat')
    
    try:
        with open(output_path, 'wb') as f:
            f.write(header)
            f.write(encrypted)
        print(f"  输出保存到: {output_path}")
        return True
    except IOError as e:
        print(f"写入输出文件错误: {e}")
        return False

# ============================================================================
# Scene.pck 解密和解包
# ============================================================================

class SceneHeader:
    """
    SCENEHEADER 结构来自 PckCommon.h
    
    struct SCENEHEADER {
        int headerLength;          // 0
        HEADERPAIR varInfo;        // 4 (偏移量, 数量)
        HEADERPAIR varNameIndex;   // 12
        HEADERPAIR varName;        // 20
        HEADERPAIR cmdInfo;        // 28
        HEADERPAIR cmdNameIndex;   // 36
        HEADERPAIR cmdName;        // 44
        HEADERPAIR SceneNameIndex; // 52
        HEADERPAIR SceneName;      // 60
        HEADERPAIR SceneInfo;      // 68
        HEADERPAIR SceneData;      // 76
        int ExtraKeyUse;           // 84
        int SourceHeaderLength;    // 88
    };
    """
    SIZE = 92  # 23 * 4字节 (1 + 10*2 + 2)
    
    def __init__(self, data: bytes):
        if len(data) < self.SIZE:
            raise ValueError("数据太小，无法容纳SceneHeader")
        
        values = struct.unpack('<23I', data[:self.SIZE])
        self.header_length = values[0]
        
        # HEADERPAIR结构: (偏移量, 数量)
        self.var_info = (values[1], values[2])
        self.var_name_index = (values[3], values[4])
        self.var_name = (values[5], values[6])
        self.cmd_info = (values[7], values[8])
        self.cmd_name_index = (values[9], values[10])
        self.cmd_name = (values[11], values[12])
        self.scene_name_index = (values[13], values[14])
        self.scene_name = (values[15], values[16])
        self.scene_info = (values[17], values[18])
        self.scene_data = (values[19], values[20])
        self.extra_key_use = values[21]
        self.source_header_length = values[22]


def decrypt_scene_data_1(data: bytearray, key: bytes) -> None:
    """decrypt_1: 与16字节私钥进行XOR"""
    xor_with_key(data, key, 0x0F)


def decrypt_scene_data_2(data: bytearray) -> None:
    """decrypt_2: 与256字节静态密钥进行XOR"""
    for i in range(len(data)):
        data[i] ^= SCENE_KEY[i & 0xFF]


def process_single_scene_unpack(args: Tuple[int, Dict, bytes, SceneHeader, str, Optional[bytes], int]) -> Tuple[int, Optional[bytes], str, int]:
    """处理单个场景的解包（用于多进程/多线程）"""
    i, name_info, data, header, output_dir, private_key, extra_key_use = args
    
    try:
        # 获取场景名
        name_offset, name_length = name_info['offset'], name_info['length']
        name_data_pos = header.scene_name[0] + name_offset * 2
        name_data_end = name_data_pos + name_length * 2
        
        if name_data_end > len(data):
            return (i, None, f"场景 {i} 的名称数据超出边界", 0)
        
        try:
            scene_name = data[name_data_pos:name_data_end].decode('utf-16-le')
        except:
            scene_name = f"scene_{i}"
        
        # 读取场景数据信息对（偏移量，大小）
        info_pair_pos = header.scene_info[0] + i * 8
        if info_pair_pos + 8 > len(data):
            return (i, None, f"场景 {i} 的信息对超出边界", 0)
        
        data_offset, data_size = struct.unpack('<II', data[info_pair_pos:info_pair_pos + 8])
        
        # 获取场景数据
        scene_data_pos = header.scene_data[0] + data_offset
        scene_data_end = scene_data_pos + data_size
        if scene_data_end > len(data):
            return (i, None, f"场景 {i} 的数据超出边界", 0)
        
        scene_bytes = bytearray(data[scene_data_pos:scene_data_end])
        
        # 解密步骤1：与私钥进行XOR（仅在ExtraKeyUse设置且提供密钥时）
        if extra_key_use and private_key:
            decrypt_scene_data_1(scene_bytes, private_key)
        
        # 解密步骤2：与静态密钥进行XOR
        decrypt_scene_data_2(scene_bytes)
        
        # 读取压缩头部
        if len(scene_bytes) < 8:
            return (i, None, f"场景 {i} 的解密数据太小", 0)
        
        comp_size, decomp_size = struct.unpack('<II', scene_bytes[:8])
        
        # 解压缩
        try:
            decompressed = decompress_data(bytes(scene_bytes[8:]), decomp_size)
        except Exception as e:
            return (i, None, f"解压缩场景 {i} 时出错: {e}", 0)
        
        # 生成输出文件名
        output_filename = f"{i}.{scene_name}.ss"
        output_filename = "".join(c for c in output_filename if c not in r'\/:*?"<>|')
        output_path = os.path.join(output_dir, output_filename)
        
        return (i, decompressed, output_path, 1)
    except Exception as e:
        return (i, None, f"处理场景 {i} 时出错: {e}", 0)


def unpack_scene_pck(file_path: str, private_key: Optional[bytes] = None,
                     output_dir: Optional[str] = None, max_workers: int = None) -> bool:
    """
    使用多进程/多线程解包Scene.pck文件
    
    算法来自UnpackScene.h：
    1. 解析SCENEHEADER结构
    2. 对于每个场景条目：
       a. 如果提供，与PrivateKey（16字节）进行XOR
       b. 与Scene密钥（256字节）进行XOR
       c. 读取压缩头部
       d. 解压缩
       e. 保存为.ss文件
    """
    print(f"处理 Scene.pck: {file_path}")
    
    if not os.path.exists(file_path):
        print(f"错误: 文件未找到: {file_path}")
        return False
    
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
    except IOError as e:
        print(f"读取文件错误: {e}")
        return False
    
    print(f"  文件大小: {len(data)} 字节")
    
    # 解析头部
    if len(data) < 88:  # 最小头部大小
        print("错误: 文件太小，不是有效的Scene.pck")
        return False
    
    try:
        header = SceneHeader(data)
    except Exception as e:
        print(f"解析头部时出错: {e}")
        return False
    
    print(f"  头部长度: {header.header_length}")
    print(f"  场景数量: {header.scene_name_index[1]}")
    print(f"  ExtraKeyUse: {header.extra_key_use}")
    
    # 检查是否需要私钥
    if header.extra_key_use and not private_key:
        print("  警告: ExtraKeyUse已设置但未提供私钥！")
        print("           解密可能会失败。使用 -k 选项指定密钥文件。")
    
    # 设置输出目录
    if output_dir is None:
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        output_dir = os.path.join(os.path.dirname(file_path) or '.', f"{base_name}_unpacked")
    
    os.makedirs(output_dir, exist_ok=True)
    print(f"  输出目录: {output_dir}")
    
    # 自动检测最优工作进程数
    if max_workers is None:
        max_workers = max(1, multiprocessing.cpu_count() // 2)
    
    worker_type = "进程" if using_processes else "线程"
    print(f"  使用 {max_workers} 个工作{worker_type}")
    
    # 准备场景名信息
    scene_name_index_offset = header.scene_name_index[0]
    scene_name_count = header.scene_name_index[1]
    
    # 准备任务参数
    tasks = []
    for i in range(scene_name_count):
        # 读取场景名长度对
        name_pair_pos = scene_name_index_offset + i * 8
        if name_pair_pos + 8 > len(data):
            continue
        
        name_offset, name_length = struct.unpack('<II', data[name_pair_pos:name_pair_pos + 8])
        name_info = {'offset': name_offset, 'length': name_length}
        
        tasks.append((i, name_info, data, header, output_dir, private_key, header.extra_key_use))
    
    # 创建进度跟踪器
    progress = ProgressTracker(len(tasks))
    progress.start()
    
    success_count = 0
    error_count = 0
    
    # 使用进程/线程池处理
    with Executor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_index = {executor.submit(process_single_scene_unpack, task): task[0] for task in tasks}
        
        # 处理结果
        for future in future_to_index:
            idx = future_to_index[future]
            try:
                result = future.result()
                i, decompressed, output_path, status = result
                
                if decompressed is not None:
                    # 写入文件
                    try:
                        with open(output_path, 'wb') as f:
                            f.write(decompressed)
                        success_count += 1
                    except IOError as e:
                        error_count += 1
                        print(f"  场景 {i} 写入失败: {e}")
                else:
                    error_count += 1
                    print(f"  场景 {i} 处理失败: {output_path}")
                    
                # 更新进度
                progress.update(1)
                progress.display(f"  解包场景中")
                
            except Exception as e:
                error_count += 1
                progress.update(1)
                progress.display(f"  解包场景中")
                print(f"  场景 {idx} 异常: {e}")
    
    print(f"\n  提取完成: {success_count} 成功, {error_count} 失败")
    return error_count == 0


def process_single_scene_pack(args: Tuple[int, str, bytes, Optional[bytes], bool, int]) -> Tuple[int, bytes, int, str]:
    """处理单个场景的打包（用于多进程/多线程）"""
    i, scene_name, orig_data, private_key, use_key, compression_level = args
    
    try:
        # 查找对应的.ss文件
        ss_filename = f"{i}.{scene_name}.ss"
        ss_filename = "".join(c for c in ss_filename if c not in r'\/:*?"<>|')
        
        # 注意：在多进程环境中，文件路径需要是绝对路径或相对于工作目录
        # 这里我们假设工作目录已经正确设置
        full_path = ss_filename
        
        if not os.path.exists(full_path):
            # 文件不存在，返回空数据
            return (i, b"", 0, f"文件未找到: {ss_filename}")
        
        # 读取.ss文件
        with open(full_path, 'rb') as f:
            ss_data = f.read()
        
        # 压缩
        compressed = compress_data(ss_data, compression_level)
        comp_size = len(compressed)
        
        # 转换为bytearray进行加密
        encrypted = bytearray(compressed)
        
        # 加密步骤1：与Scene密钥进行XOR
        decrypt_scene_data_2(encrypted)  # XOR是自逆的
        
        # 加密步骤2：如果需要，与私钥进行XOR
        if use_key and private_key:
            decrypt_scene_data_1(encrypted, private_key)  # XOR是自逆的
        
        return (i, bytes(encrypted), comp_size, "")
    except Exception as e:
        return (i, b"", 0, f"处理 {scene_name} 时出错: {e}")


def pack_scene_pck(input_dir: str, original_pck: str, private_key: Optional[bytes] = None,
                   output_path: Optional[str] = None, use_key: bool = True,
                   compression_level: int = 17, max_workers: int = None) -> bool:
    """
    使用多进程/多线程将.ss文件打包为Scene.pck格式
    
    算法来自CreateScenePck.cpp：
    1. 读取原始Scene.pck以获取头部结构
    2. 对于每个.ss文件：
       a. 压缩数据
       b. 与Scene密钥（256字节）进行XOR
       c. 如果use_key为True，与PrivateKey（16字节）进行XOR
    3. 更新SceneDataInfo的偏移量/大小
    4. 写入新的Scene.pck
    
    参数:
        compression_level: 0=不压缩，2-17=压缩级别（越高=越好但越慢）
        max_workers: 用于并行处理的工作进程/线程数
    """
    print(f"打包 Scene.pck 从: {input_dir}")
    print(f"使用原始PCK: {original_pck}")
    print(f"压缩级别: {compression_level}" + (" (不压缩)" if compression_level == 0 else ""))
    
    # 自动检测最优工作进程数
    if max_workers is None:
        max_workers = max(1, multiprocessing.cpu_count() // 2)
    
    worker_type = "进程" if using_processes else "线程"
    print(f"使用 {max_workers} 个工作{worker_type}")
    
    if not os.path.exists(input_dir):
        print(f"错误: 目录未找到: {input_dir}")
        return False
    
    if not os.path.exists(original_pck):
        print(f"错误: 原始PCK未找到: {original_pck}")
        return False
    
    # 读取原始PCK
    try:
        with open(original_pck, 'rb') as f:
            orig_data = bytearray(f.read())
    except IOError as e:
        print(f"读取原始PCK错误: {e}")
        return False
    
    # 解析头部
    try:
        header = SceneHeader(orig_data)
    except Exception as e:
        print(f"解析PCK头部时出错: {e}")
        return False
    
    scene_count = header.scene_name_index[1]
    print(f"  场景数量: {scene_count}")
    
    # 获取场景信息
    scene_name_index_offset = header.scene_name_index[0]
    scene_name_offset = header.scene_name[0]
    scene_info_offset = header.scene_info[0]
    
    # 准备场景名列表
    scene_names = []
    for i in range(scene_count):
        # 获取场景名
        name_pair_pos = scene_name_index_offset + i * 8
        if name_pair_pos + 8 > len(orig_data):
            scene_names.append(f"scene_{i}")
            continue
            
        name_offset, name_length = struct.unpack('<II', orig_data[name_pair_pos:name_pair_pos + 8])
        name_data_pos = scene_name_offset + name_offset * 2
        name_data_end = name_data_pos + name_length * 2
        
        if name_data_end > len(orig_data):
            scene_names.append(f"scene_{i}")
            continue
            
        try:
            scene_name = orig_data[name_data_pos:name_data_end].decode('utf-16-le')
        except:
            scene_name = f"scene_{i}"
        
        scene_names.append(scene_name)
    
    # 确定输出路径（在切换工作目录之前）
    if output_path is None:
        output_dir = os.path.dirname(original_pck) or '.'
        output_path = os.path.join(output_dir, 'Scene.new.pck')
    
    # 将输出路径转换为绝对路径
    output_path = os.path.abspath(output_path)
    
    # 保存当前工作目录，以便子进程/线程能正确找到文件
    original_cwd = os.getcwd()
    
    # 切换到输入目录，使文件路径更简单
    os.chdir(input_dir)
    
    try:
        # 准备任务参数
        tasks = []
        for i, scene_name in enumerate(scene_names):
            tasks.append((i, scene_name, orig_data, private_key, use_key, compression_level))
        
        # 创建进度跟踪器
        progress = ProgressTracker(len(tasks))
        progress.start()
        
        # 多进程/线程处理
        scene_results = {}
        success_count = 0
        error_count = 0
        
        with Executor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_index = {executor.submit(process_single_scene_pack, task): task[0] for task in tasks}
            
            # 处理结果
            for future in future_to_index:
                idx = future_to_index[future]
                try:
                    result = future.result()
                    i, encrypted_data, comp_size, error_msg = result
                    
                    if comp_size > 0 and error_msg == "":
                        scene_results[i] = (encrypted_data, comp_size)
                        success_count += 1
                    else:
                        # 使用原始数据作为回退
                        info_pair_pos = scene_info_offset + i * 8
                        if info_pair_pos + 8 <= len(orig_data):
                            orig_offset, orig_size = struct.unpack('<II', orig_data[info_pair_pos:info_pair_pos + 8])
                            scene_data_offset = header.scene_data[0]
                            orig_scene_pos = scene_data_offset + orig_offset
                            orig_scene_end = orig_scene_pos + orig_size
                            
                            if orig_scene_end <= len(orig_data):
                                orig_scene_bytes = orig_data[orig_scene_pos:orig_scene_end]
                                scene_results[i] = (bytes(orig_scene_bytes), orig_size)
                                success_count += 1
                            else:
                                error_count += 1
                                print(f"  场景 {i} 错误: {error_msg}")
                        else:
                            error_count += 1
                            print(f"  场景 {i} 错误: {error_msg}")
                    
                    # 更新进度
                    progress.update(1)
                    progress.display(f"  打包场景中")
                    
                except Exception as e:
                    error_count += 1
                    progress.update(1)
                    progress.display(f"  打包场景中")
                    print(f"  场景 {idx} 异常: {e}")
        
        # 构建新数据
        new_scene_data = bytearray()
        current_offset = 0
        
        # 按顺序处理结果并更新头部
        for i in range(scene_count):
            if i in scene_results:
                encrypted_data, comp_size = scene_results[i]
                
                # 更新SceneDataInfo（偏移量，大小）
                info_pair_pos = scene_info_offset + i * 8
                if info_pair_pos + 8 <= len(orig_data):
                    struct.pack_into('<II', orig_data, info_pair_pos, current_offset, comp_size)
                
                # 添加到新场景数据
                new_scene_data.extend(encrypted_data)
                current_offset += comp_size
            else:
                # 使用原始数据
                info_pair_pos = scene_info_offset + i * 8
                if info_pair_pos + 8 <= len(orig_data):
                    orig_offset, orig_size = struct.unpack('<II', orig_data[info_pair_pos:info_pair_pos + 8])
                    scene_data_offset = header.scene_data[0]
                    orig_scene_pos = scene_data_offset + orig_offset
                    orig_scene_end = orig_scene_pos + orig_size
                    
                    if orig_scene_end <= len(orig_data):
                        orig_scene_bytes = orig_data[orig_scene_pos:orig_scene_end]
                        struct.pack_into('<II', orig_data, info_pair_pos, current_offset, orig_size)
                        new_scene_data.extend(orig_scene_bytes)
                        current_offset += orig_size
                        success_count += 1
                    else:
                        error_count += 1
        
        # 构建输出
        header_data = bytes(orig_data[:header.scene_data[0]])
        
        try:
            with open(output_path, 'wb') as f:
                f.write(header_data)
                f.write(new_scene_data)
            print(f"\n  打包完成: {success_count} 成功, {error_count} 失败")
            print(f"  输出保存到: {output_path}")
            return True
        except IOError as e:
            print(f"写入输出文件错误: {e}")
            return False
    finally:
        # 恢复原始工作目录
        os.chdir(original_cwd)

# ============================================================================
# 主入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Siglus Gameexe.dat 和 Scene.pck 解密/解包/打包工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
解包示例:
  %(prog)s gameexe Gameexe.dat -k siglus_key.txt
  %(prog)s gameexe Gameexe.dat -s 735CFC27018D67B6B568A070DC55B64B
  %(prog)s scene Scene.pck -k siglus_key.txt -o output_dir
  %(prog)s scene Scene.pck -s 735CFC27018D67B6B568A070DC55B64B -o output_dir
  %(prog)s unpack -k siglus_key.txt  # 解包两个文件
  %(prog)s unpack -s 735CFC27018D67B6B568A070DC55B64B  # 解包两个文件

打包示例:
  %(prog)s pack-gameexe Gameexe_decrypted.txt -k siglus_key.txt
  %(prog)s pack-gameexe Gameexe_decrypted.txt -s 735CFC27018D67B6B568A070DC55B64B
  %(prog)s pack-scene Scene_unpacked -p Scene.pck -k siglus_key.txt --workers 8
  %(prog)s pack-scene Scene_unpacked -p Scene.pck -s 735CFC27018D67B6B568A070DC55B64B --workers 8
        '''
    )
    
    subparsers = parser.add_subparsers(dest='command', help='要执行的命令')
    
    # Gameexe解密子命令
    gameexe_parser = subparsers.add_parser('gameexe', help='解密和解压缩Gameexe.dat')
    gameexe_parser.add_argument('file', help='Gameexe.dat文件路径')
    gameexe_parser.add_argument('-k', '--key', help='密钥文件路径 (siglus_key.txt)')
    gameexe_parser.add_argument('-s', '--key-string', help='直接输入16字节秘钥 (十六进制字符串，如: 735CFC27018D67B6B568A070DC55B64B)')
    gameexe_parser.add_argument('-o', '--output', help='输出文件路径')
    gameexe_parser.add_argument('--no-key', action='store_true', help='不使用私钥处理')
    
    # Scene解密子命令
    scene_parser = subparsers.add_parser('scene', help='解密和解包Scene.pck')
    scene_parser.add_argument('file', help='Scene.pck文件路径')
    scene_parser.add_argument('-k', '--key', help='密钥文件路径 (siglus_key.txt)')
    scene_parser.add_argument('-s', '--key-string', help='直接输入16字节秘钥 (十六进制字符串，如: 735CFC27018D67B6B568A070DC55B64B)')
    scene_parser.add_argument('-o', '--output', help='输出目录路径')
    scene_parser.add_argument('--no-key', action='store_true', help='不使用私钥处理')
    scene_parser.add_argument('--workers', type=int, default=None, help='工作进程/线程数 (默认: CPU核心数的一半)')
    
    # 解包两个文件（从'all'重命名为'unpack'）
    unpack_parser = subparsers.add_parser('unpack', aliases=['all'], help='解包Gameexe.dat和Scene.pck两个文件')
    unpack_parser.add_argument('-k', '--key', help='密钥文件路径 (siglus_key.txt)')
    unpack_parser.add_argument('-s', '--key-string', help='直接输入16字节秘钥 (十六进制字符串，如: 735CFC27018D67B6B568A070DC55B64B)')
    unpack_parser.add_argument('-d', '--dir', default='.', help='包含文件的目录')
    unpack_parser.add_argument('--no-key', action='store_true', help='不使用私钥处理')
    unpack_parser.add_argument('--workers', type=int, default=None, help='Scene.pck的工作进程/线程数 (默认: CPU核心数的一半)')
    
    # 打包Gameexe.dat子命令
    pack_gameexe_parser = subparsers.add_parser('pack-gameexe', help='将文本文件打包为Gameexe.dat')
    pack_gameexe_parser.add_argument('file', help='已解密的文本文件路径 (UTF-16 LE)')
    pack_gameexe_parser.add_argument('-k', '--key', help='密钥文件路径 (siglus_key.txt)')
    pack_gameexe_parser.add_argument('-s', '--key-string', help='直接输入16字节秘钥 (十六进制字符串，如: 735CFC27018D67B6B568A070DC55B64B)')
    pack_gameexe_parser.add_argument('-o', '--output', help='输出文件路径')
    pack_gameexe_parser.add_argument('--no-key', action='store_true', help='不使用私钥加密打包')
    pack_gameexe_parser.add_argument('--level', type=int, default=17, help='压缩级别 (0=不压缩, 2-17, 默认: 17)')
    
    # 打包Scene.pck子命令
    pack_scene_parser = subparsers.add_parser('pack-scene', help='将.ss文件打包为Scene.pck')
    pack_scene_parser.add_argument('dir', help='包含.ss文件的目录')
    pack_scene_parser.add_argument('-p', '--pck', required=True, help='原始Scene.pck路径 (用于头部)')
    pack_scene_parser.add_argument('-k', '--key', help='密钥文件路径 (siglus_key.txt)')
    pack_scene_parser.add_argument('-s', '--key-string', help='直接输入16字节秘钥 (十六进制字符串，如: 735CFC27018D67B6B568A070DC55B64B)')
    pack_scene_parser.add_argument('-o', '--output', help='输出文件路径')
    pack_scene_parser.add_argument('--no-key', action='store_true', help='不使用私钥加密打包')
    pack_scene_parser.add_argument('--level', type=int, default=17, help='压缩级别 (0=不压缩, 2-17, 默认: 17)')
    pack_scene_parser.add_argument('--workers', type=int, default=None, help='工作进程/线程数 (默认: CPU核心数的一半)')
    
    # 打包两个文件
    pack_parser = subparsers.add_parser('pack', help='打包Gameexe和Scene.pck')
    pack_parser.add_argument('-k', '--key', help='密钥文件路径 (siglus_key.txt)')
    pack_parser.add_argument('-s', '--key-string', help='直接输入16字节秘钥 (十六进制字符串，如: 735CFC27018D67B6B568A070DC55B64B)')
    pack_parser.add_argument('-d', '--dir', default='.', help='工作目录')
    pack_parser.add_argument('--no-key', action='store_true', help='不使用私钥加密打包')
    pack_parser.add_argument('--level', type=int, default=17, help='压缩级别 (0=不压缩, 2-17, 默认: 17)')
    pack_parser.add_argument('--workers', type=int, default=None, help='Scene.pck的工作进程/线程数 (默认: CPU核心数的一半)')
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return 1
    
    # 如果提供则加载密钥
    private_key = None
    if not getattr(args, 'no_key', False):
        key_path = getattr(args, 'key', None)
        key_string = getattr(args, 'key_string', None)
        
        if key_string:
            private_key = parse_key_string(key_string)
            if private_key is None:
                print("错误: 秘钥字符串格式无效。需要16字节（32个十六进制字符）")
                return 1
            print(f"使用秘钥: {private_key.hex().upper()}")
        elif key_path:
            private_key = read_key_file(key_path)
            if private_key is None:
                return 1
        else:
            # 尝试当前目录下的默认密钥文件
            default_key_path = 'siglus_key.txt'
            if os.path.exists(default_key_path):
                print(f"使用默认密钥文件: {default_key_path}")
                private_key = read_key_file(default_key_path)
    
    success = True
    
    if args.command == 'gameexe':
        success = decrypt_gameexe(args.file, private_key, args.output)
    
    elif args.command == 'scene':
        workers = getattr(args, 'workers', None)
        success = unpack_scene_pck(args.file, private_key, args.output, workers)
    
    elif args.command in ('all', 'unpack'):
        base_dir = args.dir
        
        gameexe_path = os.path.join(base_dir, 'Gameexe.dat')
        scene_path = os.path.join(base_dir, 'Scene.pck')
        workers = getattr(args, 'workers', None)
        
        if os.path.exists(gameexe_path):
            print("=" * 60)
            success1 = decrypt_gameexe(gameexe_path, private_key)
        else:
            print(f"Gameexe.dat 未在 {base_dir} 中找到")
            success1 = False
        
        print()
        
        if os.path.exists(scene_path):
            print("=" * 60)
            success2 = unpack_scene_pck(scene_path, private_key, None, workers)
        else:
            print(f"Scene.pck 未在 {base_dir} 中找到")
            success2 = False
        
        success = success1 or success2
    
    elif args.command == 'pack-gameexe':
        use_key = not getattr(args, 'no_key', False)
        compression_level = getattr(args, 'level', 17)
        success = encrypt_gameexe(args.file, private_key, args.output, use_key, compression_level)
    
    elif args.command == 'pack-scene':
        use_key = not getattr(args, 'no_key', False)
        compression_level = getattr(args, 'level', 17)
        workers = getattr(args, 'workers', None)
        success = pack_scene_pck(args.dir, args.pck, private_key, args.output, use_key, compression_level, workers)
    
    elif args.command == 'pack':
        base_dir = args.dir
        use_key = not getattr(args, 'no_key', False)
        compression_level = getattr(args, 'level', 17)
        workers = getattr(args, 'workers', None)
        
        # 打包Gameexe.dat
        gameexe_txt = os.path.join(base_dir, 'Gameexe_decrypted.txt')
        gameexe_out = os.path.join(base_dir, 'Gameexe.new.dat')
        scene_dir = os.path.join(base_dir, 'Scene_unpacked')
        scene_pck = os.path.join(base_dir, 'Scene.pck')
        scene_out = os.path.join(base_dir, 'Scene.new.pck')
        
        success1 = False
        success2 = False
        
        if os.path.exists(gameexe_txt):
            print("=" * 60)
            success1 = encrypt_gameexe(gameexe_txt, private_key, gameexe_out, use_key, compression_level)
        else:
            print(f"Gameexe_decrypted.txt 未在 {base_dir} 中找到")
        
        print()
        
        if os.path.exists(scene_dir) and os.path.exists(scene_pck):
            print("=" * 60)
            success2 = pack_scene_pck(scene_dir, scene_pck, private_key, scene_out, use_key, compression_level, workers)
        else:
            if not os.path.exists(scene_dir):
                print(f"Scene_unpacked 目录未在 {base_dir} 中找到")
            if not os.path.exists(scene_pck):
                print(f"Scene.pck 未在 {base_dir} 中找到")
        
        success = success1 or success2
    
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())