#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kogado HyPack (PAK/HyPack) 封包工具。

用法:
  python hypack_tool.py list Script.pak
  python hypack_tool.py unpack Script.pak out_dir
  python hypack_tool.py pack out_dir Script.new.pak
  python hypack_tool.py verify Script.pak

说明:
  - 支持 HyPack v0x0100/v0x0200/v0x0300/v0x0301 读取。
  - 自动解压 type=1(Mariel)、type=2(Cocotte)，自动解密 type=3(XOR FF)。
  - pack 默认按原 manifest 重建，type=2 使用 Cocotte store-mode 压缩。
    这保证游戏按 Cocotte 解压路径读取，但不保证压缩字节与原包 bit-perfect。
  - unpack 会生成 _order.txt 和 _hypack_manifest.json。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SIGNATURE = b"HyPack"
DATA_BASE = 0x10
RANGECODER_BLOCKSIZE = 0x2000


CRC16_TABLE = [
    0x0000, 0x1189, 0x2312, 0x329B, 0x4624, 0x57AD, 0x6536, 0x74BF,
    0x8C48, 0x9DC1, 0xAF5A, 0xBED3, 0xCA6C, 0xDBE5, 0xE97E, 0xF8F7,
    0x1081, 0x0108, 0x3393, 0x221A, 0x56A5, 0x472C, 0x75B7, 0x643E,
    0x9CC9, 0x8D40, 0xBFDB, 0xAE52, 0xDAED, 0xCB64, 0xF9FF, 0xE876,
    0x2102, 0x308B, 0x0210, 0x1399, 0x6726, 0x76AF, 0x4434, 0x55BD,
    0xAD4A, 0xBCC3, 0x8E58, 0x9FD1, 0xEB6E, 0xFAE7, 0xC87C, 0xD9F5,
    0x3183, 0x200A, 0x1291, 0x0318, 0x77A7, 0x662E, 0x54B5, 0x453C,
    0xBDCB, 0xAC42, 0x9ED9, 0x8F50, 0xFBEF, 0xEA66, 0xD8FD, 0xC974,
    0x4204, 0x538D, 0x6116, 0x709F, 0x0420, 0x15A9, 0x2732, 0x36BB,
    0xCE4C, 0xDFC5, 0xED5E, 0xFCD7, 0x8868, 0x99E1, 0xAB7A, 0xBAF3,
    0x5285, 0x430C, 0x7197, 0x601E, 0x14A1, 0x0528, 0x37B3, 0x263A,
    0xDECD, 0xCF44, 0xFDDF, 0xEC56, 0x98E9, 0x8960, 0xBBFB, 0xAA72,
    0x6306, 0x728F, 0x4014, 0x519D, 0x2522, 0x34AB, 0x0630, 0x17B9,
    0xEF4E, 0xFEC7, 0xCC5C, 0xDDD5, 0xA96A, 0xB8E3, 0x8A78, 0x9BF1,
    0x7387, 0x620E, 0x5095, 0x411C, 0x35A3, 0x242A, 0x16B1, 0x0738,
    0xFFCF, 0xEE46, 0xDCDD, 0xCD54, 0xB9EB, 0xA862, 0x9AF9, 0x8B70,
    0x8408, 0x9581, 0xA71A, 0xB693, 0xC22C, 0xD3A5, 0xE13E, 0xF0B7,
    0x0840, 0x19C9, 0x2B52, 0x3ADB, 0x4E64, 0x5FED, 0x6D76, 0x7CFF,
    0x9489, 0x8500, 0xB79B, 0xA612, 0xD2AD, 0xC324, 0xF1BF, 0xE036,
    0x18C1, 0x0948, 0x3BD3, 0x2A5A, 0x5EE5, 0x4F6C, 0x7DF7, 0x6C7E,
    0xA50A, 0xB483, 0x8618, 0x9791, 0xE32E, 0xF2A7, 0xC03C, 0xD1B5,
    0x2942, 0x38CB, 0x0A50, 0x1BD9, 0x6F66, 0x7EEF, 0x4C74, 0x5DFD,
    0xB58B, 0xA402, 0x9699, 0x8710, 0xF3AF, 0xE226, 0xD0BD, 0xC134,
    0x39C3, 0x284A, 0x1AD1, 0x0B58, 0x7FE7, 0x6E6E, 0x5CF5, 0x4D7C,
    0xC60C, 0xD785, 0xE51E, 0xF497, 0x8028, 0x91A1, 0xA33A, 0xB2B3,
    0x4A44, 0x5BCD, 0x6956, 0x78DF, 0x0C60, 0x1DE9, 0x2F72, 0x3EFB,
    0xD68D, 0xC704, 0xF59F, 0xE416, 0x90A9, 0x8120, 0xB3BB, 0xA232,
    0x5AC5, 0x4B4C, 0x79D7, 0x685E, 0x1CE1, 0x0D68, 0x3FF3, 0x2E7A,
    0xE70E, 0xF687, 0xC41C, 0xD595, 0xA12A, 0xB0A3, 0x8238, 0x93B1,
    0x6B46, 0x7ACF, 0x4854, 0x59DD, 0x2D62, 0x3CEB, 0x0E70, 0x1FF9,
    0xF78F, 0xE606, 0xD49D, 0xC514, 0xB1AB, 0xA022, 0x92B9, 0x8330,
    0x7BC7, 0x6A4E, 0x58D5, 0x495C, 0x3DE3, 0x2C6A, 0x1EF1, 0x0F78,
]


RANGECODER_INITFREQ = [
    1400, 640, 320, 240, 160, 120, 80, 64, 48, 40, 32, 24, 20, 20, 20, 20,
    16, 16, 16, 16, 12, 12, 12, 12, 12, 12, 8, 8, 8, 8, 8, 8,
    6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
    5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
] + [4] * 32 + [3] * 38 + [2] * 123


@dataclass
class HypEntry:
    index: int
    name: str
    offset: int
    unpacked_size: int
    size: int
    compression_type: int = 0
    has_checksum: bool = False
    checksum: int = 0
    file_time: int = 0
    name_field_hex: str = ""
    ext_field_hex: str = ""
    raw_index_hex: str = ""


def crc16(data: bytes) -> int:
    value = 0xFFFF
    for b in data:
        value = CRC16_TABLE[(value ^ b) & 0xFF] ^ (value >> 8)
    return value & 0xFFFF


def align16(value: int) -> int:
    return (value + 0x0F) & ~0x0F


def read_cstring(raw: bytes) -> str:
    raw = raw.split(b"\0", 1)[0]
    return raw.decode("cp932")


def encode_fixed_cp932(text: str, size: int, field: str) -> bytes:
    raw = text.encode("cp932")
    if len(raw) > size:
        raise ValueError(f"{field} 太长: {text!r} ({len(raw)} > {size})")
    return raw + b"\0" * (size - len(raw))


def parse_archive(path: Path) -> tuple[int, int, list[HypEntry], bytes]:
    data = path.read_bytes()
    if not data.startswith(SIGNATURE):
        raise ValueError("不是 HyPack 封包，文件头不是 HyPack")
    version = struct.unpack_from("<H", data, 6)[0]
    entry_size = {0x100: 32, 0x200: 40, 0x300: 48, 0x301: 48}.get(version)
    if entry_size is None:
        raise ValueError(f"不支持的 HyPack 版本: 0x{version:04X}")
    index_offset = DATA_BASE + struct.unpack_from("<I", data, 8)[0]
    count = struct.unpack_from("<I", data, 12)[0]
    if count <= 0 or count > 0xFFFFF:
        raise ValueError(f"目录数量异常: {count}")
    if index_offset + count * entry_size > len(data):
        raise ValueError("目录越界")

    entries: list[HypEntry] = []
    for i in range(count):
        pos = index_offset + i * entry_size
        name_field = data[pos:pos + 0x15]
        ext_field = data[pos + 0x15:pos + 0x18]
        name = read_cstring(name_field) or f"{i:05d}"
        ext = read_cstring(ext_field)
        if ext:
            name = f"{name}.{ext}"
        rel_offset = struct.unpack_from("<I", data, pos + 0x18)[0]
        if version >= 0x200:
            unpacked_size, size = struct.unpack_from("<II", data, pos + 0x1C)
            comp = data[pos + 0x24]
            has_sum = False
            checksum = 0
            file_time = 0
            if version >= 0x300:
                has_sum = data[pos + 0x25] != 0
                checksum = struct.unpack_from("<H", data, pos + 0x26)[0]
                file_time = struct.unpack_from("<q", data, pos + 0x28)[0]
        else:
            size = struct.unpack_from("<I", data, pos + 0x1C)[0]
            unpacked_size = size
            comp = 0
            has_sum = False
            checksum = 0
            file_time = 0
        if DATA_BASE + rel_offset + size > len(data):
            raise ValueError(f"{name} 数据越界")
        entries.append(HypEntry(
            i, name, rel_offset, unpacked_size, size, comp,
            has_sum, checksum, file_time,
            name_field.hex(), ext_field.hex(), data[pos:pos + entry_size].hex(),
        ))
    return version, index_offset, entries, data


class QSModel:
    TBLSHIFT = 7

    def __init__(self, n: int = 257, lg_totf: int = 12, rescale: int = 2000,
                 init: list[int] | None = None):
        self.n = n
        self.targetrescale = rescale
        self.searchshift = max(0, lg_totf - self.TBLSHIFT)
        self.cf = [0] * (n + 1)
        self.newf = [0] * (n + 1)
        self.search = [0] * ((1 << self.TBLSHIFT) + 1)
        self.cf[n] = 1 << lg_totf
        self.reset(init)

    def reset(self, init: list[int] | None) -> None:
        self.rescale = (self.n >> 4) | 2
        self.nextleft = 0
        if init is None:
            initval = self.cf[self.n] // self.n
            end = self.cf[self.n] % self.n
            for i in range(self.n):
                self.newf[i] = initval + (1 if i < end else 0)
        else:
            for i in range(self.n):
                self.newf[i] = init[i]
        self.do_rescale()

    def do_rescale(self) -> None:
        if self.nextleft:
            self.incr += 1
            self.left = self.nextleft
            self.nextleft = 0
            return
        if self.rescale < self.targetrescale:
            self.rescale = min(self.rescale << 1, self.targetrescale)
        cf = missing = self.cf[self.n]
        for i in range(self.n - 1, 0, -1):
            tmp = self.newf[i]
            cf -= tmp
            self.cf[i] = cf
            tmp = (tmp >> 1) | 1
            missing -= tmp
            self.newf[i] = tmp
        if cf != self.newf[0]:
            raise RuntimeError("QSModel rescale failed")
        self.newf[0] = (self.newf[0] >> 1) | 1
        missing -= self.newf[0]
        self.incr = missing // self.rescale
        self.nextleft = missing % self.rescale
        self.left = self.rescale - self.nextleft

        i = self.n
        self.search[1 << self.TBLSHIFT] = self.n - 1
        while i:
            end = (self.cf[i] - 1) >> self.searchshift
            i -= 1
            start = self.cf[i] >> self.searchshift
            for j in range(start, end + 1):
                self.search[j] = i

    def get_sym(self, ltfreq: int) -> int:
        tmp = ltfreq >> self.searchshift
        lo = self.search[tmp]
        hi = self.search[tmp + 1] + 1
        while lo + 1 < hi:
            mid = (lo + hi) >> 1
            if ltfreq < self.cf[mid]:
                hi = mid
            else:
                lo = mid
        return lo

    def get_freq(self, sym: int) -> tuple[int, int]:
        lt = self.cf[sym]
        return self.cf[sym + 1] - lt, lt

    def update(self, sym: int) -> None:
        if self.left <= 0:
            self.do_rescale()
        self.left -= 1
        self.newf[sym] += self.incr


class RangeDecoder:
    CODE_BITS = 32
    SHIFT_BITS = CODE_BITS - 9
    EXTRA_BITS = (CODE_BITS - 2) % 8 + 1
    TOP_VALUE = 1 << (CODE_BITS - 1)
    BOTTOM_VALUE = TOP_VALUE >> 8

    def __init__(self, src: bytes, model: QSModel):
        self.src = src
        self.src_index = 0
        self.low = 0
        self.range = 0
        self.help = 0
        self.buffer = 0
        self.model = model

    def get_byte(self) -> int | None:
        if self.src_index >= len(self.src):
            return None
        b = self.src[self.src_index]
        self.src_index += 1
        return b

    def start(self) -> bool:
        first = self.get_byte()
        buf = self.get_byte()
        if first is None or buf is None:
            return False
        self.buffer = buf
        self.low = self.buffer >> (8 - self.EXTRA_BITS)
        self.range = 1 << self.EXTRA_BITS
        return True

    def normalize(self) -> bool:
        while self.range <= self.BOTTOM_VALUE:
            self.low = ((self.low << 8) | ((self.buffer << self.EXTRA_BITS) & 0xFF)) & 0xFFFFFFFF
            nxt = self.get_byte()
            if nxt is None:
                return False
            self.buffer = nxt
            self.low |= self.buffer >> (8 - self.EXTRA_BITS)
            self.range = (self.range << 8) & 0xFFFFFFFF
        return True

    def decode_culshift(self, shift: int) -> int:
        self.normalize()
        self.help = self.range >> shift
        tmp = self.low // self.help
        return (1 << shift) - 1 if tmp >> shift else tmp

    def decode_update(self, sy_f: int, lt_f: int, tot_f: int) -> None:
        tmp = self.help * lt_f
        self.low = (self.low - tmp) & 0xFFFFFFFF
        if lt_f + sy_f < tot_f:
            self.range = self.help * sy_f
        else:
            self.range -= tmp

    def decode(self, dest_size: int) -> bytes:
        if not self.start():
            raise ValueError("RangeCoder 数据过短")
        out = bytearray()
        while self.src_index < len(self.src):
            lt = self.decode_culshift(12)
            ch = self.model.get_sym(lt)
            if ch == 256:
                break
            if len(out) >= dest_size:
                raise ValueError("RangeCoder 输出超过目标长度")
            out.append(ch)
            sy, lt = self.model.get_freq(ch)
            self.decode_update(sy, lt, 1 << 12)
            self.model.update(ch)
        sy, lt = self.model.get_freq(256)
        self.decode_update(sy, lt, 1 << 12)
        self.normalize()
        if len(out) != dest_size:
            raise ValueError(f"RangeCoder 输出长度异常: {len(out)} != {dest_size}")
        return bytes(out)


class MTF:
    def __init__(self):
        self.table = bytearray(range(256))

    def decode(self, data: bytes) -> bytes:
        out = bytearray(len(data))
        table = self.table
        for i, n in enumerate(data):
            c = table[n]
            if n:
                del table[n]
                table.insert(0, c)
            out[i] = c
        return bytes(out)

    def encode(self, data: bytes) -> bytes:
        out = bytearray(len(data))
        table = self.table
        for i, c in enumerate(data):
            n = table.index(c)
            if n:
                del table[n]
                table.insert(0, c)
            out[i] = n
        return bytes(out)


def bwt_decode(data: bytes) -> bytes:
    if len(data) < 2:
        raise ValueError("BWT 数据过短")
    top = data[0] | (data[1] << 8)
    last = data[2:]
    size = len(last)
    if size == 0:
        return b""
    if top >= size:
        raise ValueError("BWT top 越界")
    count = [0] * 256
    for b in last:
        count[b] += 1
    for i in range(1, 256):
        count[i] += count[i - 1]
    sort_table = [0] * size
    for i in range(size - 1, -1, -1):
        b = last[i]
        count[b] -= 1
        sort_table[count[b]] = i
    out = bytearray(size)
    ptr = sort_table[top]
    for i in range(size):
        out[i] = last[ptr]
        ptr = sort_table[ptr]
    return bytes(out)


def bwt_encode(data: bytes) -> bytes:
    size = len(data)
    if size == 0:
        return b"\0\0"
    doubled = data + data
    order = sorted(range(size), key=lambda i: doubled[i:i + size])
    top = order.index(0)
    out = bytearray(2 + size)
    struct.pack_into("<H", out, 0, top)
    for i, ptr in enumerate(order):
        out[i + 2] = doubled[ptr + size - 1]
    return bytes(out)


def cocotte_decode(packed: bytes, expected_size: int | None = None) -> bytes:
    src = 0
    out = bytearray()
    mtf = MTF()
    model = QSModel(init=RANGECODER_INITFREQ)
    while src < len(packed):
        if src + 4 > len(packed):
            raise ValueError("Cocotte 块头不完整")
        src_block_size, dest_block_size = struct.unpack_from("<HH", packed, src)
        if src_block_size <= 4 or dest_block_size == 0:
            raise ValueError("Cocotte 块尺寸异常")
        if src + src_block_size > len(packed):
            raise ValueError("Cocotte 块越界")
        comp = packed[src + 4:src + src_block_size]
        decomp_size = dest_block_size + 2
        if len(comp) == decomp_size:
            decoded = comp
            model = QSModel(init=RANGECODER_INITFREQ)
        else:
            decoded = RangeDecoder(comp, model).decode(decomp_size)
        decoded = mtf.decode(decoded)
        out.extend(bwt_decode(decoded))
        src += src_block_size
    if expected_size is not None and len(out) != expected_size:
        raise ValueError(f"Cocotte 解压长度异常: {len(out)} != {expected_size}")
    return bytes(out)


def cocotte_encode_store(data: bytes) -> bytes:
    out = bytearray()
    mtf = MTF()
    for pos in range(0, len(data), RANGECODER_BLOCKSIZE):
        chunk = data[pos:pos + RANGECODER_BLOCKSIZE]
        encoded = bwt_encode(chunk)
        encoded = mtf.encode(encoded)
        src_block_size = len(encoded) + 4
        if src_block_size > 0xFFFF:
            raise ValueError("Cocotte 块过大")
        out += struct.pack("<HH", src_block_size, len(chunk))
        out += encoded
    return bytes(out)


def mariel_decode(packed: bytes, dest_size: int) -> bytes:
    out = bytearray(dest_size)
    out_pos = 0
    src = 0
    bits = 0
    while dest_size > 0 and src < len(packed):
        carry = (bits & 0x80000000) != 0
        bits = (bits << 1) & 0xFFFFFFFF
        if bits == 0:
            if src + 4 > len(packed):
                break
            bits = struct.unpack_from("<I", packed, src)[0]
            src += 4
            carry = (bits & 0x80000000) != 0
            bits = ((bits << 1) | 1) & 0xFFFFFFFF
        if src >= len(packed):
            break
        b = packed[src]
        src += 1
        if not carry:
            out[out_pos] = b
            out_pos += 1
            dest_size -= 1
            continue
        offset = (b & 0x0F) + 1
        count = ((b >> 4) & 0x0F) + 1
        if count == 0x0F:
            if src >= len(packed):
                break
            count = packed[src]
            src += 1
        elif count > 0x0F:
            if src + 2 > len(packed):
                break
            count = struct.unpack_from("<H", packed, src)[0]
            src += 2
        if offset >= 0x0B:
            if src >= len(packed):
                break
            offset = ((offset - 0x0B) << 8) | packed[src]
            src += 1
        count = min(count, dest_size)
        copy_src = out_pos - offset
        if copy_src < 0 or copy_src >= out_pos:
            break
        for _ in range(count):
            out[out_pos] = out[copy_src]
            out_pos += 1
            copy_src += 1
        dest_size -= count
    return bytes(out[:out_pos])


def unpack_entry(data: bytes, entry: HypEntry) -> bytes:
    raw = data[DATA_BASE + entry.offset:DATA_BASE + entry.offset + entry.size]
    if entry.has_checksum and crc16(raw) != entry.checksum:
        raise ValueError(f"{entry.name}: CRC16 校验失败")
    if entry.compression_type == 0:
        return raw
    if entry.compression_type == 1:
        out = mariel_decode(raw, entry.unpacked_size)
        if len(out) != entry.unpacked_size:
            raise ValueError(f"{entry.name}: Mariel 解压长度异常")
        return out
    if entry.compression_type == 2:
        return cocotte_decode(raw, entry.unpacked_size)
    if entry.compression_type == 3:
        return bytes((b ^ 0xFF) for b in raw)
    raise ValueError(f"{entry.name}: 未知压缩类型 {entry.compression_type}")


def pack_payload(plain: bytes, method: int) -> bytes:
    if method == 0:
        return plain
    if method == 1:
        print("[WARN] Mariel 重新封包暂用未压缩存储；manifest 会改为 method=0")
        return plain
    if method == 2:
        return cocotte_encode_store(plain)
    if method == 3:
        return bytes((b ^ 0xFF) for b in plain)
    raise ValueError(f"未知压缩类型 {method}")


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest().upper()


def write_log(name: str, lines: Iterable[str]) -> Path:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"{stamp}_{name}.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def cmd_list(args: argparse.Namespace) -> None:
    version, index_offset, entries, data = parse_archive(Path(args.archive))
    print(f"HyPack version=0x{version:04X} count={len(entries)} index=0x{index_offset:X} size={len(data)}")
    print("idx  method  packed/unpacked  checksum  name")
    for e in entries:
        print(f"{e.index:03d}  {e.compression_type:>6}  {e.size:>7}/{e.unpacked_size:<7}  "
              f"{e.checksum:04X}      {e.name}")


def cmd_unpack(args: argparse.Namespace) -> None:
    archive = Path(args.archive)
    out_dir = Path(args.out_dir)
    version, index_offset, entries, data = parse_archive(archive)
    if out_dir.exists() and any(out_dir.iterdir()) and not args.force:
        raise SystemExit(f"输出目录已存在且非空: {out_dir}，如需覆盖请加 --force")
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries = []
    order_lines = ["# index\tfile"]
    log_lines = [
        f"archive={archive}",
        f"md5={md5(data)}",
        f"version=0x{version:04X}",
        f"index_offset=0x{index_offset:X}",
        f"count={len(entries)}",
    ]
    for e in entries:
        plain = unpack_entry(data, e)
        out_path = out_dir / e.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(plain)
        order_lines.append(f"{e.index}\t{e.name}")
        raw = data[DATA_BASE + e.offset:DATA_BASE + e.offset + e.size]
        manifest_entries.append({
            "index": e.index,
            "file": e.name,
            "name": Path(e.name).stem,
            "ext": Path(e.name).suffix.lstrip("."),
            "offset": e.offset,
            "unpacked_size": e.unpacked_size,
            "packed_size": e.size,
            "compression_type": e.compression_type,
            "has_checksum": e.has_checksum,
            "checksum": e.checksum,
            "file_time": e.file_time,
            "name_field_hex": e.name_field_hex,
            "ext_field_hex": e.ext_field_hex,
            "raw_packed_md5": md5(raw),
            "unpacked_md5": md5(plain),
        })
        print(f"[OK] {e.index:03d} {e.name}  {e.size}->{len(plain)}  method={e.compression_type}")

    manifest = {
        "tool": "hypack_tool",
        "format": "Kogado HyPack",
        "version": 1,
        "archive_name": archive.name,
        "archive_md5": md5(data),
        "hy_version": f"0x{version:04X}",
        "orig_file_size": len(data),
        "index_offset": index_offset,
        "count": len(entries),
        "note": "unpack 已自动解压；pack 对 Cocotte(type=2) 使用 store-mode 兼容压缩，不保证压缩字节 bit-perfect。",
        "entries": manifest_entries,
    }
    (out_dir / "_order.txt").write_text("\n".join(order_lines) + "\n", encoding="utf-8")
    (out_dir / "_hypack_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log_lines.append(f"out_dir={out_dir}")
    log_lines.append("status=unpack complete")
    log_path = write_log("hypack_unpack", log_lines)
    print(f"[DONE] 解包完成: {out_dir}")
    print(f"[LOG] {log_path}")


def read_order(folder: Path) -> list[str]:
    path = folder / "_order.txt"
    if not path.exists():
        raise FileNotFoundError(f"缺少 {path}")
    files = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            raise ValueError(f"_order.txt 行格式错误: {line}")
        files.append(parts[1])
    return files


def load_manifest(folder: Path) -> dict:
    path = folder / "_hypack_manifest.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def filetime_now() -> int:
    # Windows FILETIME: 100ns ticks since 1601-01-01 UTC.
    return int((time.time() + 11644473600) * 10_000_000)


def cmd_pack(args: argparse.Namespace) -> None:
    folder = Path(args.folder)
    output = Path(args.output)
    names = read_order(folder)
    manifest = load_manifest(folder)
    entry_meta = {e["file"]: e for e in manifest.get("entries", [])}
    version_text = manifest.get("hy_version", "0x0300")
    version = int(version_text, 16) if isinstance(version_text, str) else int(version_text)
    if version not in (0x200, 0x300, 0x301):
        version = 0x300
    entry_size = 40 if version == 0x200 else 48

    out = bytearray(b"\0" * DATA_BASE)
    new_entries = []
    current_offset = 0
    for idx, name in enumerate(names):
        plain_path = folder / name
        if not plain_path.exists():
            raise FileNotFoundError(f"缺少待封包文件: {plain_path}")
        plain = plain_path.read_bytes()
        meta = entry_meta.get(name, {})
        method = int(meta.get("compression_type", 0))
        packed_method = 0 if method == 1 else method
        packed = pack_payload(plain, method)
        checksum = crc16(packed)

        if len(out) < DATA_BASE + current_offset:
            out.extend(b"\0" * (DATA_BASE + current_offset - len(out)))
        out[DATA_BASE + current_offset:DATA_BASE + current_offset + len(packed)] = packed

        new_entries.append({
            "index": idx,
            "file": name,
            "offset": current_offset,
            "unpacked_size": len(plain),
            "packed_size": len(packed),
            "compression_type": packed_method,
            "has_checksum": True if version >= 0x300 else bool(meta.get("has_checksum", False)),
            "checksum": checksum,
            "file_time": int(meta.get("file_time") or filetime_now()),
            "unpacked_md5": md5(plain),
            "packed_md5": md5(packed),
        })
        print(f"[ADD] {idx:03d} {name}  {len(plain)}->{len(packed)}  method={packed_method}")
        current_offset = align16(current_offset + len(packed))
        if len(out) < DATA_BASE + current_offset:
            out.extend(b"\0" * (DATA_BASE + current_offset - len(out)))

    index_offset = current_offset
    index = bytearray()
    for e in new_entries:
        p = Path(e["file"])
        name_raw = encode_fixed_cp932(p.stem, 0x15, "文件名")
        ext_raw = encode_fixed_cp932(p.suffix.lstrip(".").lower(), 3, "扩展名")
        index += name_raw
        index += ext_raw
        index += struct.pack("<I", e["offset"])
        index += struct.pack("<II", e["unpacked_size"], e["packed_size"])
        index.append(e["compression_type"])
        if version >= 0x300:
            index.append(1 if e["has_checksum"] else 0)
            index += struct.pack("<Hq", e["checksum"], e["file_time"])
        elif version == 0x200:
            pass
        if len(index) % entry_size:
            raise RuntimeError("内部目录长度错误")
    out[DATA_BASE + index_offset:DATA_BASE + index_offset] = index
    struct.pack_into("<I", out, 0, 0x61507948)
    if version >= 0x300:
        struct.pack_into("<I", out, 4, 0x03006B63 if version == 0x300 else 0x03016B63)
    else:
        struct.pack_into("<H", out, 6, version)
    struct.pack_into("<I", out, 8, index_offset)
    struct.pack_into("<I", out, 12, len(new_entries))

    whole_crc = crc16(bytes(out))
    out += struct.pack("<H", whole_crc)
    output.write_bytes(out)

    new_manifest = {
        "tool": "hypack_tool",
        "format": "Kogado HyPack",
        "version": 1,
        "archive_name": output.name,
        "archive_md5": md5(bytes(out)),
        "hy_version": f"0x{version:04X}",
        "file_size": len(out),
        "index_offset": DATA_BASE + index_offset,
        "count": len(new_entries),
        "note": "此 manifest 来自 pack；Cocotte(type=2) 为 store-mode 兼容压缩。",
        "entries": new_entries,
    }
    if args.write_manifest:
        (folder / "_hypack_manifest.packed.json").write_text(
            json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    log_path = write_log("hypack_pack", [
        f"folder={folder}",
        f"output={output}",
        f"md5={md5(bytes(out))}",
        f"count={len(new_entries)}",
        f"index_offset=0x{DATA_BASE + index_offset:X}",
        "status=pack complete",
    ])
    print(f"[DONE] 封包完成: {output}")
    print(f"[LOG] {log_path}")


def cmd_verify(args: argparse.Namespace) -> None:
    archive = Path(args.archive)
    work_root = Path("_work")
    work_root.mkdir(exist_ok=True)
    tag = f"hypack_verify_{int(time.time())}"
    work = work_root / tag
    out_dir = work / "unpack"
    rebuilt = work / "rebuilt.pak"
    try:
        cmd_unpack(argparse.Namespace(archive=str(archive), out_dir=str(out_dir), force=True))
        cmd_pack(argparse.Namespace(folder=str(out_dir), output=str(rebuilt), write_manifest=False))
        _, _, orig_entries, orig_data = parse_archive(archive)
        _, _, new_entries, new_data = parse_archive(rebuilt)
        if len(orig_entries) != len(new_entries):
            raise ValueError("重封包目录数量不一致")
        failures = []
        for old, new in zip(orig_entries, new_entries):
            old_plain = unpack_entry(orig_data, old)
            new_plain = unpack_entry(new_data, new)
            if old.name != new.name or old_plain != new_plain:
                failures.append(old.name)
        if failures:
            raise ValueError("重封包内容验证失败: " + ", ".join(failures[:10]))
        print("[VERIFY] 解包 -> 重封包 -> 再解包内容一致")
        print(f"[INFO] 原包 MD5: {md5(orig_data)}")
        print(f"[INFO] 新包 MD5: {md5(new_data)}")
        print("[INFO] Cocotte 使用兼容 store-mode，MD5 不一致属于预期。")
        log_path = write_log("hypack_verify", [
            f"archive={archive}",
            f"orig_md5={md5(orig_data)}",
            f"rebuilt_md5={md5(new_data)}",
            "status=verify content-identical",
        ])
        print(f"[LOG] {log_path}")
    finally:
        if args.keep_work:
            print(f"[KEEP] 临时目录保留: {work}")
        elif work.exists():
            shutil.rmtree(work)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kogado HyPack 封包/解包工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出封包内容")
    p.add_argument("archive")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("unpack", help="解包并自动解压")
    p.add_argument("archive")
    p.add_argument("out_dir")
    p.add_argument("--force", action="store_true", help="允许输出到非空目录")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("pack", help="从目录重新封包")
    p.add_argument("folder")
    p.add_argument("output")
    p.add_argument("--write-manifest", action="store_true", help="额外写出 _hypack_manifest.packed.json")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("verify", help="解包、重封包、再解包并验证内容一致")
    p.add_argument("archive")
    p.add_argument("--keep-work", action="store_true", help="保留 _work 下的临时目录")
    p.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        args.func(args)
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
