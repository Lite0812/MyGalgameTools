#!/usr/bin/env python3
"""
KyouPri/VITAMIN FBX container extractor and repacker.

This is not Autodesk FBX. The game format is:
    "FBX\\x01" + type[3] + tag + u32(compressed_size) + u32(output_size)
    + a small LZ-style compressed stream.

Round-trip mode does not keep the original compressed stream. It stores a
structured token stream and the decoded payload, then re-encodes those tokens.
If a stream leaves unparsed bytes after the structured tokens, extraction fails
instead of preserving those bytes as a raw fallback.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any


MAGIC = b"FBX\x01"
HEADER_SIZE = 16
FORMAT = "kyoupri-fbx-v1"
TYPE_EXT = {
    "BMP": ".bmp",
    "txt": ".txt",
    "gkx": ".gkx",
    "IPT": ".ipt",
    "CPD": ".cpd",
}


class FbxError(Exception):
    pass


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_u16be(data: bytes, offset: int) -> int:
    return (data[offset] << 8) | data[offset + 1]


def write_u16be(value: int) -> bytes:
    return bytes(((value >> 8) & 0xFF, value & 0xFF))


def copy_overlap(out: bytearray, src: int, length: int) -> None:
    if src < 0:
        raise FbxError("invalid back-reference before output start")
    for _ in range(length):
        out.append(out[src])
        src += 1


def parse_header(data: bytes) -> tuple[str, int, int, int, bytes]:
    if len(data) < HEADER_SIZE:
        raise FbxError("file is too small for an FBX container")
    if data[:4] != MAGIC:
        raise FbxError("not an FBX\\x01 container")
    type_raw = data[4:7]
    try:
        fbx_type = type_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FbxError("FBX type is not ASCII") from exc
    tag = data[7]
    comp_size, out_size = struct.unpack_from("<II", data, 8)
    if HEADER_SIZE + comp_size > len(data):
        raise FbxError("compressed stream extends past end of file")
    return fbx_type, tag, comp_size, out_size, data[HEADER_SIZE : HEADER_SIZE + comp_size]


def make_op(kind: str, **fields: Any) -> dict[str, Any]:
    item = {"kind": kind}
    item.update(fields)
    return item


def parse_tokens(stream: bytes, out_size: int) -> tuple[list[dict[str, Any]], bytes, bytes]:
    blocks: list[dict[str, Any]] = []
    out = bytearray()
    pos = 0

    while len(out) < out_size:
        block_start = pos
        groups: list[dict[str, Any]] = []
        run_end = 0
        q = 0
        if pos >= len(stream):
            raise FbxError("unexpected end before control byte")
        control = stream[pos]
        pos += 1
        group = {"control": control, "ops": []}
        groups.append(group)

        while True:
            op = control & 3
            if op == 0:
                if pos >= len(stream):
                    raise FbxError("unexpected end in literal byte")
                group["ops"].append(make_op("lit1"))
                out.append(stream[pos])
                pos += 1
                run_end = 0
                force_new_control = False
            elif op == 1:
                if pos >= len(stream):
                    raise FbxError("unexpected end in literal run")
                length = stream[pos] + 2
                pos += 1
                if pos + length > len(stream):
                    raise FbxError("literal run extends past stream")
                group["ops"].append(make_op("lit", length=length))
                out.extend(stream[pos : pos + length])
                pos += length
                run_end = 0
                force_new_control = False
            elif op == 2:
                if pos + 2 > len(stream):
                    raise FbxError("unexpected end in short back-reference")
                word = read_u16be(stream, pos)
                pos += 2
                length = (word & 0x1F) + 4
                dist = word >> 5
                group["ops"].append(make_op("ref", length=length, distance=dist))
                copy_overlap(out, len(out) - dist - 1, length)
                run_end = 0
                force_new_control = False
            else:
                if pos >= len(stream):
                    raise FbxError("unexpected end in extended opcode")
                marker = stream[pos]
                pos += 1
                mode = marker & 0xC0
                if mode == 0x00:
                    if pos >= len(stream):
                        raise FbxError("unexpected end in long literal")
                    length = stream[pos] + ((marker & 0x3F) << 8) + 258
                    pos += 1
                    if pos + length > len(stream):
                        raise FbxError("long literal extends past stream")
                    group["ops"].append(make_op("long_lit", length=length))
                    out.extend(stream[pos : pos + length])
                    pos += length
                    run_end = 0
                    force_new_control = False
                elif mode == 0x40:
                    if pos + 2 > len(stream):
                        raise FbxError("unexpected end in long back-reference")
                    word = read_u16be(stream, pos)
                    pos += 2
                    length = 32 * (marker & 0x3F) + (word & 0x1F) + 36
                    dist = word >> 5
                    group["ops"].append(make_op("long_ref", length=length, distance=dist))
                    copy_overlap(out, len(out) - dist - 1, length)
                    run_end = 0
                    force_new_control = False
                elif mode == 0xC0:
                    skip_len = marker & 0x3F
                    if pos + skip_len > len(stream):
                        raise FbxError("skip marker extends past stream")
                    skip_data = stream[pos : pos + skip_len]
                    pos += skip_len
                    group["ops"].append(make_op("skip", length=skip_len, data_b64=b64(skip_data)))
                    run_end += 1
                    force_new_control = True
                    if run_end > 1:
                        blocks.append({"start": block_start, "groups": groups})
                        break
                else:
                    raise FbxError("unreachable extended opcode state")

            if run_end > 1:
                break
            q = 4 if force_new_control else q
            q += 1
            if q < 4:
                control >>= 2
            else:
                q = 0
                if pos >= len(stream):
                    raise FbxError("unexpected end before next control byte")
                control = stream[pos]
                pos += 1
                group = {"control": control, "ops": []}
                groups.append(group)

    payload = bytes(out[:out_size])
    trailing = stream[pos:]
    return blocks, payload, trailing


def validate_group_control(group: dict[str, Any]) -> None:
    control = int(group["control"])
    ops = group["ops"]
    for index, op in enumerate(ops[:4]):
        expected = {
            "lit1": 0,
            "lit": 1,
            "ref": 2,
            "long_lit": 3,
            "long_ref": 3,
            "skip": 3,
        }[op["kind"]]
        actual = (control >> (index * 2)) & 3
        if actual != expected:
            raise FbxError(f"control byte does not match token at op {index}: {actual} != {expected}")


def encode_tokens(blocks: list[dict[str, Any]], payload: bytes, trailing: bytes) -> bytes:
    stream = bytearray()
    cursor = 0
    for block in blocks:
        for group in block["groups"]:
            validate_group_control(group)
            stream.append(int(group["control"]) & 0xFF)
            for op in group["ops"]:
                kind = op["kind"]
                if kind == "lit1":
                    if cursor + 1 > len(payload):
                        raise FbxError("payload too short for literal token")
                    stream.append(payload[cursor])
                    cursor += 1
                elif kind == "lit":
                    length = int(op["length"])
                    if not 2 <= length <= 257:
                        raise FbxError(f"invalid literal run length {length}")
                    if cursor + length > len(payload):
                        raise FbxError("payload too short for literal run")
                    stream.append(length - 2)
                    stream.extend(payload[cursor : cursor + length])
                    cursor += length
                elif kind == "ref":
                    length = int(op["length"])
                    dist = int(op["distance"])
                    if not 4 <= length <= 35 or not 0 <= dist <= 0x7FF:
                        raise FbxError("invalid short back-reference token")
                    stream.extend(write_u16be((dist << 5) | (length - 4)))
                    cursor += length
                elif kind == "long_lit":
                    length = int(op["length"])
                    code = length - 258
                    if not 0 <= code <= 0x3FFF:
                        raise FbxError(f"invalid long literal length {length}")
                    if cursor + length > len(payload):
                        raise FbxError("payload too short for long literal")
                    stream.append((code >> 8) & 0x3F)
                    stream.append(code & 0xFF)
                    stream.extend(payload[cursor : cursor + length])
                    cursor += length
                elif kind == "long_ref":
                    length = int(op["length"])
                    dist = int(op["distance"])
                    code = length - 36
                    if code < 0 or code > 0x7FF or not 0 <= dist <= 0x7FF:
                        raise FbxError("invalid long back-reference token")
                    stream.append(0x40 | ((code >> 5) & 0x3F))
                    stream.extend(write_u16be((dist << 5) | (code & 0x1F)))
                    cursor += length
                elif kind == "skip":
                    skip = unb64(op.get("data_b64", ""))
                    length = int(op["length"])
                    if length != len(skip) or length > 0x3F:
                        raise FbxError("invalid skip marker token")
                    stream.append(0xC0 | length)
                    stream.extend(skip)
                else:
                    raise FbxError(f"unknown token kind {kind}")
    if cursor != len(payload):
        raise FbxError(f"token stream consumed {cursor} bytes, payload has {len(payload)} bytes")
    stream.extend(trailing)
    return bytes(stream)


def encode_literal_stream(payload: bytes) -> bytes:
    ops: list[tuple[int, bytes]] = []
    pos = 0
    while pos < len(payload):
        remain = len(payload) - pos
        if remain == 1:
            ops.append((0, payload[pos : pos + 1]))
            pos += 1
        elif remain < 258:
            length = min(remain, 257)
            ops.append((1, bytes([length - 2]) + payload[pos : pos + length]))
            pos += length
        else:
            length = min(remain, 0x3FFF + 258)
            code = length - 258
            ops.append((3, bytes([(code >> 8) & 0x3F, code & 0xFF]) + payload[pos : pos + length]))
            pos += length

    # Two consecutive skip markers end the block. A skip marker forces a new
    # control byte, so the second marker must be in the next group.
    ops.append((3, b"\xC0"))
    ops.append((3, b"\xC0"))

    out = bytearray()
    group: list[tuple[int, bytes]] = []
    for opcode, body in ops:
        group.append((opcode, body))
        if opcode == 3 and body[:1] == b"\xC0":
            flush = True
        else:
            flush = len(group) == 4
        if flush:
            control = 0
            for index, (op, _body) in enumerate(group):
                control |= (op & 3) << (index * 2)
            out.append(control)
            for _op, body_part in group:
                out.extend(body_part)
            group = []
    if group:
        control = 0
        for index, (op, _body) in enumerate(group):
            control |= (op & 3) << (index * 2)
        out.append(control)
        for _op, body_part in group:
            out.extend(body_part)
    return bytes(out)


def decode_fbx(data: bytes) -> dict[str, Any]:
    fbx_type, tag, comp_size, out_size, stream = parse_header(data)
    blocks, payload, trailing = parse_tokens(stream, out_size)
    if len(payload) != out_size:
        raise FbxError("decoded payload size mismatch")
    if trailing:
        raise FbxError("unparsed compressed bytes remain after token stream")
    return {
        "type": fbx_type,
        "tag": tag,
        "compressed_size": comp_size,
        "output_size": out_size,
        "blocks": blocks,
        "payload": payload,
        "trailing": trailing,
    }


def build_fbx(fbx_type: str, tag: int, payload: bytes, stream: bytes) -> bytes:
    type_raw = fbx_type.encode("ascii")
    if len(type_raw) != 3:
        raise FbxError("FBX type must be exactly 3 ASCII bytes")
    header = MAGIC + type_raw + bytes([tag & 0xFF]) + struct.pack("<II", len(stream), len(payload))
    return header + stream


def payload_name(fbx_type: str) -> str:
    return "payload" + TYPE_EXT.get(fbx_type, ".bin")


def extract_fbx(path: Path, out_dir: Path) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FbxError(f"output directory is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    data = path.read_bytes()
    parsed = decode_fbx(data)
    payload_path = payload_name(parsed["type"])
    (out_dir / payload_path).write_bytes(parsed["payload"])
    manifest = {
        "format": FORMAT,
        "source_name": path.name,
        "original_size": len(data),
        "original_sha256": sha256_bytes(data),
        "type": parsed["type"],
        "tag": parsed["tag"],
        "compressed_size": parsed["compressed_size"],
        "output_size": parsed["output_size"],
        "payload_path": payload_path,
        "payload_sha256": sha256_bytes(parsed["payload"]),
        "blocks": parsed["blocks"],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def pack_fbx(out_dir: Path, output: Path, *, literal: bool = False) -> None:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        raise FbxError(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise FbxError("unsupported manifest format")
    payload = (out_dir / manifest["payload_path"]).read_bytes()
    if literal:
        stream = encode_literal_stream(payload)
    else:
        if len(payload) != int(manifest["output_size"]):
            raise FbxError("payload size changed; use --literal for a valid non-identical rebuild")
        stream = encode_tokens(manifest["blocks"], payload, b"")
        check = decode_fbx(build_fbx(manifest["type"], int(manifest["tag"]), payload, stream))["payload"]
        if check != payload:
            raise FbxError("token stream cannot represent modified payload; use --literal")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(build_fbx(manifest["type"], int(manifest["tag"]), payload, stream))


def verify_fbx(path: Path, *, keep: bool = False) -> bool:
    temp_root = Path(tempfile.mkdtemp(prefix="fbx_verify_"))
    try:
        extract_dir = temp_root / "extract"
        rebuilt = temp_root / "rebuilt.fbx"
        extract_fbx(path, extract_dir)
        pack_fbx(extract_dir, rebuilt)
        original_hash = sha256_file(path)
        rebuilt_hash = sha256_file(rebuilt)
        ok = path.stat().st_size == rebuilt.stat().st_size and original_hash == rebuilt_hash
        print(f"original size : {path.stat().st_size}")
        print(f"rebuilt size  : {rebuilt.stat().st_size}")
        print(f"original sha256: {original_hash}")
        print(f"rebuilt  sha256: {rebuilt_hash}")
        print(f"roundtrip: {'OK' if ok else 'MISMATCH'}")
        if keep:
            kept = path.with_name(path.stem + "_fbx_verify_work")
            if kept.exists():
                shutil.rmtree(kept)
            shutil.move(str(temp_root), str(kept))
            print(f"kept work dir: {kept}")
            temp_root = kept
        return ok
    finally:
        if not keep and temp_root.exists():
            shutil.rmtree(temp_root)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract/repack KyouPri FBX containers.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="extract decoded payload and token manifest")
    p_extract.add_argument("fbx", type=Path)
    p_extract.add_argument("out_dir", type=Path)

    p_pack = sub.add_parser("pack", help="rebuild FBX from extracted payload and token manifest")
    p_pack.add_argument("out_dir", type=Path)
    p_pack.add_argument("fbx", type=Path)
    p_pack.add_argument("--literal", action="store_true", help="make a valid new FBX with literal-only compression")

    p_verify = sub.add_parser("verify", help="extract/repack and compare with original")
    p_verify.add_argument("fbx", type=Path)
    p_verify.add_argument("--keep", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "extract":
            extract_fbx(args.fbx, args.out_dir)
            print(f"extracted: {args.fbx} -> {args.out_dir}")
            return 0
        if args.command == "pack":
            pack_fbx(args.out_dir, args.fbx, literal=args.literal)
            print(f"packed: {args.out_dir} -> {args.fbx}")
            return 0
        if args.command == "verify":
            return 0 if verify_fbx(args.fbx, keep=args.keep) else 1
    except FbxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"io error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
