"""
qile Engine .b file (abmp12 format) unpack & repack tool.

File Structure:
  abmp12       (16-byte marker)
  abdata15     (16-byte marker) + u32le size + payload (1PC bytecode)
  abimage10    (16-byte marker) + u8 entry_count
    N x abimgdat15 entry
  absound10    (16-byte marker) + u8 entry_count
    N x absnddat12 entry

Each abimgdat15 / absnddat12 entry:
  marker       16 bytes
  version      u32le
  name_len     u16le  (UTF-16LE char count)
  name         name_len * 2 bytes (UTF-16LE)
  hash_len     u16le
  hash         hash_len bytes (ASCII)
  unknown      u32le
  padding      26 bytes (zeros)
  data_size    u32le
  data         data_size bytes (PNG/JPG/OGG/WAV)

Usage:
  python qile_tool.py unpack <input.b> [output_dir]
  python qile_tool.py repack <input_dir> [output.b]
"""

import os
import sys
import struct
import json


# ── helpers ──────────────────────────────────────────────────────────────

def pad_marker(name, size=16):
    """Create a 16-byte null-padded ASCII marker."""
    b = name.encode("ascii")
    return b + b"\x00" * (size - len(b))


def read_marker(data, pos):
    raw = data[pos:pos + 16]
    return raw.rstrip(b"\x00").decode("ascii", errors="replace"), pos + 16


def r_u8(data, pos):
    return data[pos], pos + 1


def r_u16(data, pos):
    return struct.unpack_from("<H", data, pos)[0], pos + 2


def r_u32(data, pos):
    return struct.unpack_from("<I", data, pos)[0], pos + 4


def w_u8(v):
    return struct.pack("<B", v)


def w_u16(v):
    return struct.pack("<H", v)


def w_u32(v):
    return struct.pack("<I", v)


def detect_ext(payload):
    if payload[:4] == b"\x89PNG":
        return ".png"
    if payload[:2] == b"\xff\xd8":
        return ".jpg"
    if payload[:4] == b"OggS":
        return ".ogg"
    if payload[:4] == b"RIFF":
        return ".wav"
    return ".bin"


def safe_filename(name):
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip() or "unnamed"


# ── unpack ───────────────────────────────────────────────────────────────

def parse_entry(data, pos, is_image=True):
    """Parse one abimgdat15 / absnddat12 entry.
    Returns dict with metadata + raw payload, and new pos."""
    marker, pos = read_marker(data, pos)
    entry = {"marker": marker}

    version, pos = r_u32(data, pos)
    entry["version"] = version

    name_len, pos = r_u16(data, pos)
    name = ""
    if name_len > 0:
        name = data[pos:pos + name_len * 2].decode("utf-16-le", errors="replace")
        pos += name_len * 2
    entry["name"] = name

    hash_len, pos = r_u16(data, pos)
    hash_str = data[pos:pos + hash_len].decode("ascii", errors="replace") if hash_len > 0 else ""
    pos += hash_len
    entry["hash"] = hash_str

    # abimgdat entries have extra unknown(4) + padding(26) before data_size
    # absnddat entries have extra type_byte(1) before data_size
    if is_image:
        unknown, pos = r_u32(data, pos)
        entry["unknown"] = unknown

        padding = data[pos:pos + 26]
        entry["padding_hex"] = padding.hex()
        pos += 26
    else:
        type_byte, pos = r_u8(data, pos)
        entry["type_byte"] = type_byte

    data_size, pos = r_u32(data, pos)
    entry["data_size"] = data_size

    payload = b""
    if data_size > 0 and pos + data_size <= len(data):
        payload = data[pos:pos + data_size]
        pos += data_size

    return entry, payload, pos


def unpack(filepath, output_dir):
    with open(filepath, "rb") as f:
        data = f.read()

    print(f"Input : {filepath} ({len(data)} bytes)")
    os.makedirs(output_dir, exist_ok=True)

    meta = {"source_file": os.path.basename(filepath), "file_size": len(data)}
    pos = 0

    # ── abmp12 header ──
    marker, pos = read_marker(data, pos)
    meta["header_marker"] = marker
    print(f"Header: {marker}")

    # ── abdata15 section ──
    marker, pos = read_marker(data, pos)
    meta["abdata_marker"] = marker
    payload_size, pos = r_u32(data, pos)
    meta["abdata_size"] = payload_size

    op_data = data[pos:pos + payload_size]
    pos += payload_size

    op_file = "abdata_ops.bin"
    with open(os.path.join(output_dir, op_file), "wb") as f:
        f.write(op_data)
    meta["abdata_file"] = op_file

    # decode 1PC header info
    if len(op_data) >= 12 and op_data[:3] == b"1PC":
        meta["1pc_version"] = op_data[3]
        meta["1pc_val1"] = struct.unpack_from("<I", op_data, 4)[0]
        meta["1pc_val2"] = struct.unpack_from("<I", op_data, 8)[0]
        print(f"  1PC v=0x{op_data[3]:02x} val1={meta['1pc_val1']} val2={meta['1pc_val2']} opcodes={len(op_data)-12}B")

    # ── sections (abimage10 / absound10) ──
    meta["sections"] = []
    file_index = 0

    while pos < len(data):
        marker, new_pos = read_marker(data, pos)

        if marker.startswith("abimage") or marker.startswith("absound"):
            pos = new_pos
            is_image = marker.startswith("abimage")
            section = {"marker": marker, "entries": []}

            count, pos = r_u8(data, pos)
            section["count"] = count
            print(f"\nSection: {marker}  entries={count}")

            for i in range(count):
                entry, payload, pos = parse_entry(data, pos, is_image=is_image)

                if payload:
                    ext = detect_ext(payload)
                    fname = safe_filename(entry["name"]) + ext
                    with open(os.path.join(output_dir, fname), "wb") as f:
                        f.write(payload)
                    entry["file"] = fname
                    file_index += 1
                    print(f"  [{i+1}/{count}] {fname} ({len(payload)} bytes)")
                else:
                    entry["file"] = None
                    print(f"  [{i+1}/{count}] {entry['name']} (empty)")

                # don't store raw data in json
                del entry["data_size"]
                section["entries"].append(entry)

            meta["sections"].append(section)
        else:
            pos += 1

    # save metadata
    meta_path = os.path.join(output_dir, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\nDone! Output: {output_dir}")
    print(f"Metadata: metadata.json")


# ── repack ───────────────────────────────────────────────────────────────

def repack(input_dir, output_file):
    meta_path = os.path.join(input_dir, "metadata.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    print(f"Repacking from: {input_dir}")
    out = bytearray()

    # ── abmp12 header ──
    out += pad_marker(meta["header_marker"])

    # ── abdata15 section ──
    out += pad_marker(meta["abdata_marker"])
    op_path = os.path.join(input_dir, meta["abdata_file"])
    with open(op_path, "rb") as f:
        op_data = f.read()
    out += w_u32(len(op_data))
    out += op_data

    # ── sections ──
    for section in meta["sections"]:
        out += pad_marker(section["marker"])
        out += w_u8(section["count"])
        is_image = section["marker"].startswith("abimage")

        for entry in section["entries"]:
            # marker
            out += pad_marker(entry["marker"])
            # version
            out += w_u32(entry["version"])
            # name (UTF-16LE)
            name_encoded = entry["name"].encode("utf-16-le")
            name_charcount = len(name_encoded) // 2
            out += w_u16(name_charcount)
            out += name_encoded
            # hash
            hash_bytes = entry["hash"].encode("ascii")
            out += w_u16(len(hash_bytes))
            out += hash_bytes
            # image entries have extra unknown + padding
            # sound entries have extra type_byte
            if is_image:
                out += w_u32(entry["unknown"])
                padding = bytes.fromhex(entry.get("padding_hex", "00" * 26))
                out += padding
            else:
                out += w_u8(entry.get("type_byte", 0))
            # data
            if entry.get("file"):
                fpath = os.path.join(input_dir, entry["file"])
                with open(fpath, "rb") as f:
                    payload = f.read()
                out += w_u32(len(payload))
                out += payload
            else:
                out += w_u32(0)

    with open(output_file, "wb") as f:
        f.write(out)

    print(f"Output: {output_file} ({len(out)} bytes)")
    print("Done!")


# ── main ─────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python qile_tool.py unpack <input.b> [output_dir]")
        print("  python qile_tool.py repack <input_dir> [output.b]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "unpack":
        inp = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(inp)[0] + "_out"
        unpack(inp, out)

    elif cmd == "repack":
        inp = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else "repacked.b"
        repack(inp, out)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
