#!/usr/bin/env python3
"""Export Frida text-dump JSONL into an editable CP932 translation table.

Default input is text_dump_clean.jsonl.  The generated TSV is intended to be
edited by translators: keep `original` unchanged and put translated text in the
`translation` column.  Translations are validated as CP932-encodable.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

JP_RE = re.compile(r"[぀-ヿ㐀-鿿＀-￯]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def is_pure_ascii(text: str) -> bool:
    return bool(text) and all(ord(ch) < 0x80 for ch in text)


def clean_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def cp932_ok(text: str) -> bool:
    try:
        text.encode("cp932")
        return True
    except UnicodeEncodeError:
        return False


def should_keep(text: str, keep_ascii: bool) -> bool:
    if not text:
        return False
    if CONTROL_RE.search(text):
        return False
    if not keep_ascii and is_pure_ascii(text):
        return False
    # Avoid GDI-internal mojibake/glyph-index rows such as ExtTextOutW garbage.
    return cp932_ok(text)


def load_items(path: Path, apis: set[str], keep_ascii: bool) -> OrderedDict[str, dict[str, Any]]:
    items: OrderedDict[str, dict[str, Any]] = OrderedDict()
    with path.open("r", encoding="utf-8", errors="replace") as fp:
        for line_no, line in enumerate(fp, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("accepted") is not True:
                continue
            if row.get("api") not in apis:
                continue
            text = clean_text(str(row.get("text") or ""))
            if not should_keep(text, keep_ascii=keep_ascii):
                continue
            item = items.get(text)
            if item is None:
                item = {
                    "id": f"T{len(items) + 1:06d}",
                    "original": text,
                    # Leave blank: empty means no replacement yet.
                    "translation": "",
                    "count": 0,
                    "first_line": line_no,
                    "apis": set(),
                    "first_x": row.get("x", ""),
                    "first_y": row.get("y", ""),
                    "cp932_hex": text.encode("cp932").hex(" "),
                    "note": "",
                }
                items[text] = item
            item["count"] += 1
            item["apis"].add(str(row.get("api") or ""))
    return items


def write_tsv(items: OrderedDict[str, dict[str, Any]], path: Path) -> None:
    fields = ["id", "original", "translation", "count", "first_line", "apis", "first_x", "first_y", "cp932_hex", "note"]
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for item in items.values():
            row = dict(item)
            row["apis"] = ",".join(sorted(item["apis"]))
            writer.writerow(row)


def write_json_map(items: OrderedDict[str, dict[str, Any]], path: Path) -> None:
    # Runtime-friendly skeleton.  Only non-empty translations should be used by
    # a replacer; originals are kept here so the file is self-documenting.
    data = [
        {
            "id": item["id"],
            "original": item["original"],
            "translation": item["translation"],
            "count": item["count"],
            "first_line": item["first_line"],
        }
        for item in items.values()
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_tsv(path: Path) -> int:
    errors = 0
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp, delimiter="\t")
        for row_no, row in enumerate(reader, 2):
            original = row.get("original") or ""
            translation = row.get("translation") or ""
            if not translation:
                continue
            for col, text in (("original", original), ("translation", translation)):
                if CONTROL_RE.search(text):
                    print(f"[ERR] row {row_no} {col}: contains control character")
                    errors += 1
                try:
                    text.encode("cp932")
                except UnicodeEncodeError as e:
                    print(f"[ERR] row {row_no} {col}: not CP932 encodable: {e}")
                    errors += 1
    if errors:
        print(f"[!] validation failed: {errors} error(s)")
        return 1
    print("[+] validation OK: all non-empty translations are CP932 encodable")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Export/validate CP932 translation table from Frida JSONL text dump.")
    p.add_argument("input", nargs="?", default="text_dump_clean.jsonl", help="Input JSONL from frida_text_hook.py")
    p.add_argument("--out-tsv", default="translations_cp932.tsv", help="Editable TSV output")
    p.add_argument("--out-json", default="translations_cp932.json", help="JSON skeleton output")
    p.add_argument("--api", action="append", default=None, help="API to include. Default: ExtTextOutA only. Repeatable.")
    p.add_argument("--keep-ascii", action="store_true", help="Also export pure ASCII strings")
    p.add_argument("--validate-tsv", help="Validate an edited TSV and exit")
    args = p.parse_args()

    if args.validate_tsv:
        return validate_tsv(Path(args.validate_tsv))

    apis = set(args.api or ["ExtTextOutA"])
    items = load_items(Path(args.input), apis=apis, keep_ascii=args.keep_ascii)
    write_tsv(items, Path(args.out_tsv))
    write_json_map(items, Path(args.out_json))
    print(f"[+] exported {len(items)} unique strings from {args.input}")
    print(f"[+] TSV : {Path(args.out_tsv).resolve()}")
    print(f"[+] JSON: {Path(args.out_json).resolve()}")
    print("[*] Edit the TSV `translation` column. Empty translation = keep original.")
    print(f"[*] Validate after editing: python {Path(__file__).name} --validate-tsv {args.out_tsv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
