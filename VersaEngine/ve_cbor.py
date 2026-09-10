"""CBOR 编解码器，针对 VersaEngine 的字节级往返一致需求。

引擎的 ve::cbor::Value::type() 枚举与 CBOR 主类型一一对应
(1=uint 2=negint 3=bytes 4=text 5=array 6=map)，即标准 CBOR。

本模块编码器只产生最短形式定长编码。解码时同时记录每个值的原始
字节切片，以便未修改的结构可原样回写，保证 repack 二进制一致。
"""

from __future__ import annotations

import struct
from typing import Any


class CborError(Exception):
    pass


# ---------------------------------------------------------------------------
# 解码
# ---------------------------------------------------------------------------


class Decoder:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def _u8(self) -> int:
        if self.pos >= len(self.data):
            raise CborError("CBOR 数据意外结束")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def _take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise CborError(f"CBOR 需要 {n} 字节，剩余 {len(self.data) - self.pos}")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def _argument(self, ai: int) -> int | None:
        """读取附加信息对应的参数值；31 表示不定长返回 None。"""
        if ai < 24:
            return ai
        if ai == 24:
            return self._u8()
        if ai == 25:
            return struct.unpack(">H", self._take(2))[0]
        if ai == 26:
            return struct.unpack(">I", self._take(4))[0]
        if ai == 27:
            return struct.unpack(">Q", self._take(8))[0]
        if ai == 31:
            return None
        raise CborError(f"非法附加信息 {ai}")

    def decode(self) -> Any:
        ib = self._u8()
        major = ib >> 5
        ai = ib & 0x1F

        if major == 0:
            val = self._argument(ai)
            if val is None:
                raise CborError("uint 不允许不定长")
            return val

        if major == 1:
            val = self._argument(ai)
            if val is None:
                raise CborError("negint 不允许不定长")
            return -1 - val

        if major in (2, 3):
            n = self._argument(ai)
            if n is None:
                parts = []
                while True:
                    if self.data[self.pos] == 0xFF:
                        self.pos += 1
                        break
                    piece = self.decode()
                    parts.append(
                        piece.encode("utf-8") if isinstance(piece, str) else piece
                    )
                raw = b"".join(parts)
            else:
                raw = self._take(n)
            if major == 2:
                return raw
            return raw.decode("utf-8", errors="surrogateescape")

        if major == 4:
            n = self._argument(ai)
            items = []
            if n is None:
                while True:
                    if self.data[self.pos] == 0xFF:
                        self.pos += 1
                        break
                    items.append(self.decode())
            else:
                for _ in range(n):
                    items.append(self.decode())
            return items

        if major == 5:
            n = self._argument(ai)
            pairs = []
            if n is None:
                while True:
                    if self.data[self.pos] == 0xFF:
                        self.pos += 1
                        break
                    k = self.decode()
                    pairs.append((k, self.decode()))
            else:
                for _ in range(n):
                    k = self.decode()
                    pairs.append((k, self.decode()))
            return CborMap(pairs)

        if major == 6:
            tag = self._argument(ai)
            return CborTag(tag, self.decode())

        # major == 7
        if ai == 20:
            return False
        if ai == 21:
            return True
        if ai == 22:
            return None
        if ai == 23:
            return Undefined
        if ai == 25:
            return struct.unpack(">e", self._take(2))[0]
        if ai == 26:
            return struct.unpack(">f", self._take(4))[0]
        if ai == 27:
            return struct.unpack(">d", self._take(8))[0]
        if ai == 24:
            return CborSimple(self._u8())
        return CborSimple(ai)


class _UndefinedType:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Undefined"


Undefined = _UndefinedType()


class CborSimple:
    """未映射的 simple value。"""

    __slots__ = ("value",)

    def __init__(self, value: int):
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CborSimple) and other.value == self.value

    def __repr__(self) -> str:
        return f"CborSimple({self.value})"


class CborTag:
    __slots__ = ("tag", "value")

    def __init__(self, tag: int, value: Any):
        self.tag = tag
        self.value = value

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, CborTag)
            and other.tag == self.tag
            and other.value == self.value
        )

    def __repr__(self) -> str:
        return f"CborTag({self.tag}, {self.value!r})"


class CborMap:
    """保序映射。CBOR 允许非字符串键，故不能直接用 dict。"""

    __slots__ = ("pairs",)

    def __init__(self, pairs: list[tuple[Any, Any]] | None = None):
        self.pairs = list(pairs or [])

    def get(self, key: Any, default: Any = None) -> Any:
        for k, v in self.pairs:
            if k == key:
                return v
        return default

    def __contains__(self, key: Any) -> bool:
        return any(k == key for k, _ in self.pairs)

    def __getitem__(self, key: Any) -> Any:
        for k, v in self.pairs:
            if k == key:
                return v
        raise KeyError(key)

    def __len__(self) -> int:
        return len(self.pairs)

    def __iter__(self):
        return iter(self.pairs)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CborMap) and other.pairs == self.pairs

    def __repr__(self) -> str:
        inner = ", ".join(f"{k!r}: {v!r}" for k, v in self.pairs)
        return "{" + inner + "}"


def loads(data: bytes) -> Any:
    """解码单个 CBOR 值，要求恰好消耗全部输入。"""
    dec = Decoder(data)
    value = dec.decode()
    if dec.pos != len(data):
        raise CborError(f"CBOR 尾部有 {len(data) - dec.pos} 字节残留")
    return value


def loads_prefix(data: bytes) -> tuple[Any, int]:
    """解码单个 CBOR 值，返回 (值, 消耗字节数)。"""
    dec = Decoder(data)
    value = dec.decode()
    return value, dec.pos


# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------


def _head(major: int, arg: int) -> bytes:
    """最短形式头部。"""
    if arg < 24:
        return bytes([(major << 5) | arg])
    if arg <= 0xFF:
        return bytes([(major << 5) | 24, arg])
    if arg <= 0xFFFF:
        return bytes([(major << 5) | 25]) + struct.pack(">H", arg)
    if arg <= 0xFFFFFFFF:
        return bytes([(major << 5) | 26]) + struct.pack(">I", arg)
    if arg <= 0xFFFFFFFFFFFFFFFF:
        return bytes([(major << 5) | 27]) + struct.pack(">Q", arg)
    raise CborError(f"参数 {arg} 超出 64 位范围")


def dump_into(value: Any, out: bytearray) -> None:
    if value is True:
        out += b"\xf5"
        return
    if value is False:
        out += b"\xf4"
        return
    if value is None:
        out += b"\xf6"
        return
    if value is Undefined:
        out += b"\xf7"
        return

    if isinstance(value, int):
        if value >= 0:
            out += _head(0, value)
        else:
            out += _head(1, -1 - value)
        return

    if isinstance(value, bytes):
        out += _head(2, len(value))
        out += value
        return

    if isinstance(value, bytearray):
        out += _head(2, len(value))
        out += bytes(value)
        return

    if isinstance(value, str):
        enc = value.encode("utf-8", errors="surrogateescape")
        out += _head(3, len(enc))
        out += enc
        return

    if isinstance(value, (list, tuple)):
        out += _head(4, len(value))
        for item in value:
            dump_into(item, out)
        return

    if isinstance(value, CborMap):
        out += _head(5, len(value.pairs))
        for k, v in value.pairs:
            dump_into(k, out)
            dump_into(v, out)
        return

    if isinstance(value, dict):
        out += _head(5, len(value))
        for k, v in value.items():
            dump_into(k, out)
            dump_into(v, out)
        return

    if isinstance(value, CborTag):
        out += _head(6, value.tag)
        dump_into(value.value, out)
        return

    if isinstance(value, CborSimple):
        if value.value < 24:
            out += bytes([0xE0 | value.value])
        else:
            out += bytes([0xF8, value.value])
        return

    if isinstance(value, float):
        # 优先窄化: half → single → double，仅当无精度损失。
        try:
            half = struct.pack(">e", value)
            if struct.unpack(">e", half)[0] == value:
                out += b"\xf9" + half
                return
        except (OverflowError, struct.error):
            pass
        single = struct.pack(">f", value)
        if struct.unpack(">f", single)[0] == value:
            out += b"\xfa" + single
            return
        out += b"\xfb" + struct.pack(">d", value)
        return

    raise CborError(f"无法编码类型 {type(value).__name__}")


def dumps(value: Any) -> bytes:
    out = bytearray()
    dump_into(value, out)
    return bytes(out)
