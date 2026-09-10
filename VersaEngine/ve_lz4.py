"""LZ4 块格式 (raw block，无帧头)，对应 ve::lz4_decompress / ve::lz4_compress。

Package::parse 调用 ve::lz4_decompress(src, src_len, dst, dst_cap) 并要求
返回值恰等于头中的 original_size，故这里是裸块格式而非 LZ4 帧格式。

优先使用 python-lz4 的 block 接口；缺失时回退到纯 Python 实现。
解压是精确的；压缩采用保守的贪心匹配，输出合法但不保证与引擎
所用编码器逐字节相同 —— 所以 repack 默认复用原始压缩字节，只有
内容实际改变时才重新压缩。
"""

from __future__ import annotations

import struct

try:
    from lz4.block import compress as _lz4_compress
    from lz4.block import decompress as _lz4_decompress

    HAVE_LZ4 = True
except ImportError:  # pragma: no cover
    HAVE_LZ4 = False


class Lz4Error(Exception):
    pass


# ---------------------------------------------------------------------------
# 解压
# ---------------------------------------------------------------------------


def decompress(src: bytes, dst_size: int) -> bytes:
    """解压裸 LZ4 块到已知的确切大小。"""
    if HAVE_LZ4:
        try:
            out = _lz4_decompress(
                struct.pack("<i", dst_size) + src, uncompressed_size=dst_size
            )
            if len(out) == dst_size:
                return out
        except Exception:
            pass
    return _decompress_py(src, dst_size)


def _decompress_py(src: bytes, dst_size: int) -> bytes:
    """纯 Python LZ4 块解压。"""
    out = bytearray(dst_size)
    sp = 0
    dp = 0
    n = len(src)

    while sp < n:
        token = src[sp]
        sp += 1

        # 字面量长度
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                if sp >= n:
                    raise Lz4Error("字面量长度域截断")
                b = src[sp]
                sp += 1
                lit_len += b
                if b != 255:
                    break

        if lit_len:
            if sp + lit_len > n:
                raise Lz4Error(f"字面量越界: 需 {lit_len}，剩 {n - sp}")
            if dp + lit_len > dst_size:
                raise Lz4Error(f"输出溢出: {dp + lit_len} > {dst_size}")
            out[dp : dp + lit_len] = src[sp : sp + lit_len]
            sp += lit_len
            dp += lit_len

        # 块尾：最后一个序列只有字面量
        if sp >= n:
            break

        if sp + 2 > n:
            raise Lz4Error("偏移域截断")
        offset = src[sp] | (src[sp + 1] << 8)
        sp += 2
        if offset == 0:
            raise Lz4Error("非法偏移 0")
        if offset > dp:
            raise Lz4Error(f"偏移 {offset} 超出已产出 {dp} 字节")

        match_len = token & 0x0F
        if match_len == 15:
            while True:
                if sp >= n:
                    raise Lz4Error("匹配长度域截断")
                b = src[sp]
                sp += 1
                match_len += b
                if b != 255:
                    break
        match_len += 4  # MINMATCH

        if dp + match_len > dst_size:
            raise Lz4Error(f"匹配输出溢出: {dp + match_len} > {dst_size}")

        # 必须逐字节复制：偏移可小于长度（重叠自引用）
        start = dp - offset
        for i in range(match_len):
            out[dp + i] = out[start + i]
        dp += match_len

    if dp != dst_size:
        raise Lz4Error(f"解压得到 {dp} 字节，期望 {dst_size}")
    return bytes(out)


# ---------------------------------------------------------------------------
# 压缩
# ---------------------------------------------------------------------------


def compress(src: bytes) -> bytes:
    """压缩为裸 LZ4 块（去掉 python-lz4 附加的 4 字节长度前缀）。"""
    if HAVE_LZ4:
        blob = _lz4_compress(src, mode="high_compression", compression=12,
                             store_size=True)
        return blob[4:]
    return _compress_py(src)


def _compress_py(src: bytes) -> bytes:
    """纯 Python LZ4 块压缩，哈希链贪心匹配。"""
    n = len(src)
    out = bytearray()

    if n < 13:  # 太短，全部作为字面量
        _emit_last_literals(out, src, 0, n)
        return bytes(out)

    table: dict[bytes, int] = {}
    anchor = 0
    pos = 0
    limit = n - 12  # 末尾 12 字节必须是字面量（LZ4 规范）

    while pos < limit:
        seq = src[pos : pos + 4]
        cand = table.get(seq, -1)
        table[seq] = pos

        if cand < 0 or pos - cand > 0xFFFF or src[cand : cand + 4] != seq:
            pos += 1
            continue

        # 向后扩展匹配，但不得越过末尾 5 字节
        match_len = 4
        max_len = n - 5 - pos
        while match_len < max_len and src[cand + match_len] == src[pos + match_len]:
            match_len += 1
        if match_len < 4:
            pos += 1
            continue

        lit_len = pos - anchor
        offset = pos - cand
        _emit_sequence(out, src, anchor, lit_len, offset, match_len)

        pos += match_len
        anchor = pos

    _emit_last_literals(out, src, anchor, n)
    return bytes(out)


def _emit_sequence(
    out: bytearray, src: bytes, anchor: int, lit_len: int, offset: int, match_len: int
) -> None:
    ml = match_len - 4
    token = (min(lit_len, 15) << 4) | min(ml, 15)
    out.append(token)

    if lit_len >= 15:
        rest = lit_len - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)

    out += src[anchor : anchor + lit_len]
    out += struct.pack("<H", offset)

    if ml >= 15:
        rest = ml - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)


def _emit_last_literals(out: bytearray, src: bytes, anchor: int, end: int) -> None:
    lit_len = end - anchor
    token = min(lit_len, 15) << 4
    out.append(token)
    if lit_len >= 15:
        rest = lit_len - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)
    out += src[anchor:end]
