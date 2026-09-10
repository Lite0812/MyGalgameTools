from __future__ import annotations

import struct
import sys
from pathlib import Path


PATTERNS = (
    {
        "name": "vc9",
        "text":
        "53 55 56 57 8B F1 E8 ?? ?? ?? ?? 8B 7C 24 14 8B 87 0C 01 00 00 "
        "89 86 ?? ?? ?? ?? 40 50 E8",
        "length_at": 23, "data_at": 53, "raw_at": None,
        "calls": (6, 29), "eax_this": False,
    },
    {
        "name": "vc14",
        "text":
        "55 8B EC 53 56 57 8B F9 E8 ?? ?? ?? ?? 8B 5D 08 8B 83 0C 01 00 00 "
        "89 87 ?? ?? ?? ?? 40 50 E8",
        "length_at": 24, "data_at": 44, "raw_at": None,
        "calls": (8, 30), "eax_this": False,
    },
    {
        "name": "early-vc7",
        "text":
        "53 55 56 57 8B F1 E8 ?? ?? ?? ?? 8B 5C 24 14 8B 83 0C 01 00 00 "
        "89 86 ?? ?? ?? ?? 40 50 E8",
        "length_at": 23, "data_at": 55, "raw_at": None,
        "calls": (6, 29), "eax_this": False,
    },
    {
        "name": "frame-vc7",
        "text":
        "55 8B EC 53 56 57 8B F1 E8 ?? ?? ?? ?? 8B 5D 08 8B 83 0C 01 00 00 "
        "89 86 ?? ?? ?? ?? 40 50 E8",
        "length_at": 24, "data_at": 56, "raw_at": None,
        "calls": (8, 30), "eax_this": False,
    },
    {
        "name": "eax-vc8",
        "text":
        "53 55 8B 6C 24 0C 56 57 8B F8 8B 87 ?? ?? ?? ?? 33 DB 3B C3 74 0F "
        "50 E8 ?? ?? ?? ?? 83 C4 04 89 9F",
        "length_at": 64, "data_at": 12, "raw_at": 39,
        "calls": (23, 48, 84), "eax_this": True,
    },
)


def pe_sections(data: bytes) -> list[tuple[str, int, int, bytes]]:
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError("not a PE file")
    section_count, optional_size = struct.unpack_from("<H12xH", data, pe_offset + 6)
    optional = pe_offset + 24
    image_base = struct.unpack_from("<I", data, optional + 28)[0]
    section_table = optional + optional_size
    sections = []
    for index in range(section_count):
        offset = section_table + index * 40
        name = data[offset:offset + 8].rstrip(b"\0").decode("ascii", errors="replace")
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", data, offset + 8)
        characteristics = struct.unpack_from("<I", data, offset + 36)[0]
        sections.append((name, image_base + virtual_address, virtual_address, data[raw_offset:raw_offset + raw_size]))
    return sections


def compile_pattern(text: str) -> tuple[bytes, bytes]:
    tokens = text.split()
    return (
        bytes(0 if token == "??" else int(token, 16) for token in tokens),
        bytes(token != "??" for token in tokens),
    )


def scan(data: bytes, pattern: bytes, mask: bytes) -> list[int]:
    return [
        offset
        for offset in range(len(data) - len(pattern) + 1)
        if all(not mask[index] or data[offset + index] == pattern[index] for index in range(len(pattern)))
    ]


def main() -> int:
    exe_directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "exe"
    supported = 0
    for path in sorted(exe_directory.glob("*.exe")):
        data = path.read_bytes()
        candidates = []
        for section_name, section_va, section_rva, section_data in pe_sections(data):
            for spec in PATTERNS:
                pattern, mask = compile_pattern(spec["text"])
                for offset in scan(section_data, pattern, mask):
                    hit = section_data[offset:]
                    length_offset = struct.unpack_from("<I", hit, spec["length_at"])[0]
                    data_offset = struct.unpack_from("<I", hit, spec["data_at"])[0]
                    raw_offset = (struct.unpack_from("<I", hit, spec["raw_at"])[0]
                                  if spec["raw_at"] is not None else data_offset - 8)
                    image_size = len(data)
                    calls_valid = True
                    for call_at in spec["calls"]:
                        if hit[call_at] != 0xE8:
                            calls_valid = False
                            break
                        call_rva = section_rva + offset + call_at
                        target_rva = call_rva + 5 + struct.unpack_from("<i", hit, call_at + 1)[0]
                        if not 0 <= target_rva < image_size:
                            calls_valid = False
                            break
                    if (calls_valid and data_offset == length_offset + 4 and
                            raw_offset + 4 == length_offset and 0x100 <= data_offset <= 0x100000):
                        candidates.append((spec["name"], section_rva + offset, raw_offset,
                                           length_offset, data_offset, spec["eax_this"]))
        if len(candidates) == 1:
            supported += 1
            name, rva, raw_offset, length_offset, data_offset, eax_this = candidates[0]
            print(
                f"PASS {path.name:<20} {name:<11} RVA=0x{rva:06X} conv={'eax' if eax_this else 'ecx'} "
                f"raw=0x{raw_offset:X} length=0x{length_offset:X} data=0x{data_offset:X}"
            )
        else:
            print(f"SKIP {path.name:<20} candidates={len(candidates)}")
    expected_supported = 14
    if supported != expected_supported:
        print(f"ERROR expected {expected_supported} supported samples, got {supported}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
