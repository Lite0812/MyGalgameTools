from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


PAGE_COUNT = 48000
FILETIME_SIZE = 8
TABLE_SIZE = PAGE_COUNT * 4
SCRIPT_ID_OFFSET = FILETIME_SIZE
TOKEN_OFFSET_OFFSET = SCRIPT_ID_OFFSET + TABLE_SIZE
FLAGS_OFFSET = TOKEN_OFFSET_OFFSET + TABLE_SIZE
PAGEINFO_SIZE = FLAGS_OFFSET + TABLE_SIZE
HEADER_SIZE = 40
TOKEN_SIZE = 8
IPAGESTART_CMD = 358
GENERATED_GKX_MARKERS = (".rebuild", ".roundtrip", ".callfold", ".redasm")


class PageInfoError(ValueError):
    pass


@dataclass(frozen=True)
class PageRecord:
    script_stem: str
    path: Path
    page_id: int
    token_offset: int
    flags: int
    source: str


@dataclass(frozen=True)
class ScriptIdChoice:
    value: int
    source: str


@dataclass(frozen=True)
class ScanResult:
    gkx_count: int
    skipped_count: int
    records_by_script: dict[str, list[PageRecord]]

    @property
    def page_count(self) -> int:
        return sum(len(records) for records in self.records_by_script.values())

    @property
    def nonzero_flag_count(self) -> int:
        return sum(1 for records in self.records_by_script.values() for record in records if record.flags)


def parse_int(text: str) -> int:
    text = text.strip()
    if text.lower().startswith("-0x"):
        return -int(text[3:], 16)
    return int(text, 0)


def strip_comment(line: str) -> str:
    if ";" not in line:
        return line
    in_quote = False
    for index, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ";" and not in_quote:
            return line[:index]
    return line


def split_fields(text: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    in_quote = False
    for ch in text.strip():
        if ch == '"':
            in_quote = not in_quote
            current.append(ch)
            continue
        if ch.isspace() and not in_quote:
            if current:
                fields.append("".join(current))
                current = []
            continue
        current.append(ch)
    if in_quote:
        raise PageInfoError(f"unterminated quoted string: {text}")
    if current:
        fields.append("".join(current))
    return fields


def parse_kv(fields: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in fields:
        if "=" not in field:
            continue
        key, value = field.split("=", 1)
        result[key] = value
    return result


def read_pageinfo(path: Path) -> tuple[bytes, list[int], list[int], list[int]]:
    data = path.read_bytes()
    if len(data) != PAGEINFO_SIZE:
        raise PageInfoError(f"{path}: expected {PAGEINFO_SIZE} bytes, got {len(data)}")
    filetime = data[:FILETIME_SIZE]
    script_ids = list(struct.unpack_from(f"<{PAGE_COUNT}I", data, SCRIPT_ID_OFFSET))
    token_offsets = list(struct.unpack_from(f"<{PAGE_COUNT}I", data, TOKEN_OFFSET_OFFSET))
    flags = list(struct.unpack_from(f"<{PAGE_COUNT}I", data, FLAGS_OFFSET))
    return filetime, script_ids, token_offsets, flags


def write_pageinfo(path: Path, filetime: bytes, script_ids: list[int], token_offsets: list[int], flags: list[int]) -> None:
    if len(filetime) != FILETIME_SIZE:
        raise PageInfoError("filetime must be exactly 8 bytes")
    out = bytearray(PAGEINFO_SIZE)
    out[:FILETIME_SIZE] = filetime
    struct.pack_into(f"<{PAGE_COUNT}I", out, SCRIPT_ID_OFFSET, *[value & 0xFFFFFFFF for value in script_ids])
    struct.pack_into(f"<{PAGE_COUNT}I", out, TOKEN_OFFSET_OFFSET, *[value & 0xFFFFFFFF for value in token_offsets])
    struct.pack_into(f"<{PAGE_COUNT}I", out, FLAGS_OFFSET, *[value & 0xFFFFFFFF for value in flags])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


def filetime_text(filetime: bytes) -> str:
    return f"0x{int.from_bytes(filetime, 'little'):016X}"


def dump_pageinfo(input_path: Path, output_path: Path) -> None:
    filetime, script_ids, token_offsets, flags = read_pageinfo(input_path)
    lines = [
        '.format "kyoupri-pageinfo"',
        f".filetime {filetime_text(filetime)}",
        "",
    ]
    for page_id, (script_id, token_offset, flag) in enumerate(zip(script_ids, token_offsets, flags)):
        if script_id or token_offset or flag:
            lines.append(
                f".page id={page_id} script_id={script_id} "
                f"token_offset=0x{token_offset:08X} flags=0x{flag:08X}"
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def set_page_entry(
    script_ids: list[int],
    token_offsets: list[int],
    flags: list[int],
    seen_pages: dict[int, str],
    page_id: int,
    script_id: int,
    token_offset: int,
    flag: int,
    source: str,
    allow_duplicate_pages: bool,
) -> None:
    if not (0 <= page_id < PAGE_COUNT):
        raise PageInfoError(f"{source}: page id out of range: {page_id}")
    previous = seen_pages.get(page_id)
    if previous is not None and not allow_duplicate_pages:
        raise PageInfoError(f"duplicate page id {page_id}: {previous} and {source}")
    seen_pages[page_id] = source
    script_ids[page_id] = script_id
    token_offsets[page_id] = token_offset
    flags[page_id] = flag


def build_pageinfo(input_path: Path, output_path: Path, allow_duplicate_pages: bool = False) -> None:
    filetime = b"\x00" * FILETIME_SIZE
    script_ids = [0] * PAGE_COUNT
    token_offsets = [0] * PAGE_COUNT
    flags = [0] * PAGE_COUNT
    seen_pages: dict[int, str] = {}
    for lineno, raw_line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), 1):
        line = strip_comment(raw_line).strip()
        if not line or line.startswith(".format "):
            continue
        if line.startswith(".filetime "):
            value = parse_int(line[len(".filetime ") :])
            filetime = value.to_bytes(FILETIME_SIZE, "little", signed=False)
            continue
        if line.startswith(".page "):
            kv = parse_kv(split_fields(line)[1:])
            try:
                page_id = parse_int(kv["id"])
                script_id = parse_int(kv["script_id"])
                token_offset = parse_int(kv["token_offset"])
                flag = parse_int(kv.get("flags", "0"))
            except KeyError as exc:
                raise PageInfoError(f"line {lineno}: .page missing {exc.args[0]}") from exc
            set_page_entry(
                script_ids,
                token_offsets,
                flags,
                seen_pages,
                page_id,
                script_id,
                token_offset,
                flag,
                f"line {lineno}",
                allow_duplicate_pages,
            )
            continue
        raise PageInfoError(f"line {lineno}: unsupported line: {line}")
    write_pageinfo(output_path, filetime, script_ids, token_offsets, flags)


def parse_gsx_tokens(path: Path) -> list[tuple[int, int, int, int, int]]:
    data = path.read_bytes()
    if len(data) < HEADER_SIZE or data[:4] != b"GSX1":
        raise PageInfoError(f"{path}: not a GSX1 file")
    fields = struct.unpack_from("<9I", data, 4)
    token_offset = fields[7]
    token_size = fields[8]
    if token_offset > len(data) or token_size > len(data) - token_offset or token_size % TOKEN_SIZE:
        raise PageInfoError(f"{path}: invalid token stream range")
    tokens = []
    stream = data[token_offset : token_offset + token_size]
    for index, (kind, argc, opcode_or_type, value) in enumerate(struct.iter_unpack("<BBhI", stream)):
        tokens.append((index * TOKEN_SIZE, kind, argc, opcode_or_type, value))
    return tokens


def load_script_id_map(manifest_path: Path | None) -> dict[str, int]:
    if manifest_path is None:
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result: dict[str, int] = {}
    for entry in manifest.get("entries", []):
        name = entry.get("name")
        index = entry.get("index")
        if not isinstance(name, str) or not isinstance(index, int):
            continue
        stem = Path(name).stem.lower()
        if stem != "pageinfo":
            result[stem] = index
    return result


def parse_script_id_overrides(items: list[str] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items or []:
        if "=" not in item:
            raise PageInfoError(f"script id override must be name=id, got {item!r}")
        name, value = item.split("=", 1)
        stem = Path(name).stem.lower()
        if not stem:
            raise PageInfoError(f"script id override has empty name: {item!r}")
        result[stem] = parse_int(value)
    return result


def is_generated_gkx(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in GENERATED_GKX_MARKERS)


def scan_gkx_pages(input_dir: Path) -> ScanResult:
    if not input_dir.is_dir():
        raise PageInfoError(f"{input_dir}: not a directory")
    records_by_script: dict[str, list[PageRecord]] = {}
    gkx_count = 0
    skipped_count = 0
    for path in sorted(input_dir.rglob("*.gkx"), key=lambda item: str(item).lower()):
        if is_generated_gkx(path):
            skipped_count += 1
            continue
        gkx_count += 1
        script_stem = path.stem.lower()
        records = records_by_script.setdefault(script_stem, [])
        tokens = parse_gsx_tokens(path)
        for index, (offset, kind, argc, opcode_or_type, _value) in enumerate(tokens):
            if kind != 0x08 or opcode_or_type != IPAGESTART_CMD:
                continue
            if argc == 1:
                if index < 1:
                    raise PageInfoError(f"{path}: IPageStart at 0x{offset:08X} lacks page id token")
                page_id = tokens[index - 1][4]
                page_flags = 0
                page_token_offset = offset - TOKEN_SIZE
            elif argc == 2:
                if index < 2:
                    raise PageInfoError(f"{path}: IPageStart at 0x{offset:08X} lacks page id/flags tokens")
                page_id = tokens[index - 2][4]
                page_flags = tokens[index - 1][4]
                page_token_offset = offset - 2 * TOKEN_SIZE
            else:
                raise PageInfoError(f"{path}: IPageStart at 0x{offset:08X} has unsupported argc {argc}")
            if not (0 <= page_id < PAGE_COUNT):
                raise PageInfoError(f"{path}: page id out of range at 0x{offset:08X}: {page_id}")
            records.append(
                PageRecord(
                    script_stem,
                    path,
                    page_id,
                    page_token_offset,
                    page_flags,
                    f"{path}: page {page_id} at 0x{page_token_offset:08X}",
                )
            )
    if gkx_count == 0:
        raise PageInfoError(f"{input_dir}: no .gkx files found")
    return ScanResult(gkx_count, skipped_count, records_by_script)


def infer_script_ids_from_reference(
    reference_path: Path | None,
    scan: ScanResult,
) -> tuple[dict[str, int], list[str]]:
    if reference_path is None:
        return {}, []
    _filetime, script_ids, token_offsets, flags = read_pageinfo(reference_path)
    inferred: dict[str, int] = {}
    warnings: list[str] = []
    for stem, records in sorted(scan.records_by_script.items()):
        if not records:
            continue
        candidates: Counter[int] = Counter()
        exact_matches = 0
        flag_mismatches = 0
        for record in records:
            script_id = script_ids[record.page_id]
            token_offset = token_offsets[record.page_id]
            flag = flags[record.page_id]
            if not (script_id or token_offset or flag):
                continue
            candidates[script_id] += 1
            if token_offset == record.token_offset and flag == record.flags:
                exact_matches += 1
            elif flag != record.flags:
                flag_mismatches += 1
        if not candidates:
            continue
        [(best_id, best_count)] = candidates.most_common(1)
        tied = [script_id for script_id, count in candidates.items() if count == best_count]
        if len(tied) > 1:
            warnings.append(f"reference could not choose one script id for {stem}: tied {sorted(tied)}")
            continue
        inferred[stem] = best_id
        if exact_matches == 0:
            warnings.append(
                f"reference inferred {stem}={best_id} by page ids; token offsets differ from reference"
            )
        if flag_mismatches:
            warnings.append(f"reference saw {flag_mismatches} flag mismatch(es) while inferring {stem}={best_id}")
    return inferred, warnings


def resolve_script_id_map(
    scan: ScanResult,
    reference_path: Path | None,
    manifest_path: Path | None,
    script_id_overrides: list[str] | None,
    allow_fallback_script_ids: bool,
) -> tuple[dict[str, ScriptIdChoice], list[str]]:
    warnings: list[str] = []
    overrides = parse_script_id_overrides(script_id_overrides)
    reference_ids, reference_warnings = infer_script_ids_from_reference(reference_path, scan)
    manifest_ids = load_script_id_map(manifest_path)
    warnings.extend(reference_warnings)
    choices: dict[str, ScriptIdChoice] = {}
    fallback_ids = {stem: index for index, stem in enumerate(sorted(scan.records_by_script))}
    for stem, records in sorted(scan.records_by_script.items()):
        if not records:
            continue
        if stem in overrides:
            choices[stem] = ScriptIdChoice(overrides[stem], "override")
            continue
        if stem in reference_ids:
            reference_id = reference_ids[stem]
            manifest_id = manifest_ids.get(stem)
            if manifest_id is not None and manifest_id != reference_id:
                warnings.append(
                    f"manifest disagreed for {stem}: manifest={manifest_id} reference={reference_id}; using reference"
                )
            choices[stem] = ScriptIdChoice(reference_id, "reference")
            continue
        if stem in manifest_ids:
            choices[stem] = ScriptIdChoice(manifest_ids[stem], "manifest")
            continue
        if allow_fallback_script_ids:
            choices[stem] = ScriptIdChoice(fallback_ids[stem], "fallback")
            warnings.append(f"using sorted fallback script id for {stem}: {fallback_ids[stem]}")
            continue
        raise PageInfoError(
            f"no script id for {stem}; use --reference original_pageinfo.ipt, --script-id {stem}=id, "
            "--manifest, or --allow-fallback-script-ids"
        )
    return choices, warnings


def choose_regen_filetime(
    filetime_hex: str | None,
    reference_path: Path | None,
    output_path: Path,
) -> tuple[bytes, str, list[str]]:
    if filetime_hex is not None:
        return parse_int(filetime_hex).to_bytes(FILETIME_SIZE, "little", signed=False), "--filetime", []
    if reference_path is not None:
        filetime, _script_ids, _token_offsets, _flags = read_pageinfo(reference_path)
        return filetime, "reference", []
    if output_path.exists():
        filetime, _script_ids, _token_offsets, _flags = read_pageinfo(output_path)
        return filetime, "existing output", []
    return b"\x00" * FILETIME_SIZE, "zero", ["no --reference/--filetime supplied; writing zero FILETIME"]


def default_regen_output(input_dir: Path) -> Path:
    return input_dir / "pageinfo.ipt"


def regenerate_pageinfo(
    input_dir: Path,
    output_path: Path,
    manifest_path: Path | None = None,
    filetime_hex: str | None = None,
    script_id_overrides: list[str] | None = None,
    reference_path: Path | None = None,
    force: bool = False,
    allow_fallback_script_ids: bool = False,
    allow_duplicate_pages: bool = False,
) -> list[str]:
    if output_path.exists() and not force:
        raise PageInfoError(f"output already exists: {output_path}; pass --force to overwrite")
    scan = scan_gkx_pages(input_dir)
    script_id_map, warnings = resolve_script_id_map(
        scan,
        reference_path,
        manifest_path,
        script_id_overrides,
        allow_fallback_script_ids,
    )
    filetime, filetime_source, filetime_warnings = choose_regen_filetime(filetime_hex, reference_path, output_path)
    warnings.extend(filetime_warnings)
    script_ids = [0] * PAGE_COUNT
    token_offsets = [0] * PAGE_COUNT
    flags = [0] * PAGE_COUNT
    seen_pages: dict[int, str] = {}
    for stem, records in sorted(scan.records_by_script.items()):
        if not records:
            continue
        script_id = script_id_map[stem].value
        for record in records:
            set_page_entry(
                script_ids,
                token_offsets,
                flags,
                seen_pages,
                record.page_id,
                script_id,
                record.token_offset,
                record.flags,
                record.source,
                allow_duplicate_pages,
            )
    write_pageinfo(output_path, filetime, script_ids, token_offsets, flags)
    lines = [
        f"scanned gkx files: {scan.gkx_count}",
        f"skipped generated gkx files: {scan.skipped_count}",
        f"IPageStart records: {scan.page_count}",
        f"nonzero flags: {scan.nonzero_flag_count}",
        f"filetime: {filetime_text(filetime)} from {filetime_source}",
        "script ids:",
    ]
    for stem, choice in sorted(script_id_map.items()):
        lines.append(f"  {stem} = {choice.value}  {choice.source}")
    if warnings:
        lines.append("warnings:")
        lines.extend(f"  {warning}" for warning in warnings)
    return lines


def compare_pageinfo(old_path: Path, new_path: Path, max_diffs: int = 20) -> str:
    old_filetime, old_script_ids, old_token_offsets, old_flags = read_pageinfo(old_path)
    new_filetime, new_script_ids, new_token_offsets, new_flags = read_pageinfo(new_path)
    script_diff_count = sum(1 for old, new in zip(old_script_ids, new_script_ids) if old != new)
    token_diff_count = sum(1 for old, new in zip(old_token_offsets, new_token_offsets) if old != new)
    flag_diff_count = sum(1 for old, new in zip(old_flags, new_flags) if old != new)
    changed_pages = [
        page_id
        for page_id in range(PAGE_COUNT)
        if (
            old_script_ids[page_id],
            old_token_offsets[page_id],
            old_flags[page_id],
        )
        != (
            new_script_ids[page_id],
            new_token_offsets[page_id],
            new_flags[page_id],
        )
    ]
    lines = [
        f"old: {old_path}",
        f"new: {new_path}",
        f"filetime: {filetime_text(old_filetime)} -> {filetime_text(new_filetime)} "
        f"({'same' if old_filetime == new_filetime else 'different'})",
        f"script_id differences: {script_diff_count}",
        f"token_offset differences: {token_diff_count}",
        f"flags differences: {flag_diff_count}",
        f"changed pages: {len(changed_pages)}",
    ]
    if changed_pages:
        lines.append(f"first {min(max_diffs, len(changed_pages))} changed page(s):")
        for page_id in changed_pages[:max_diffs]:
            lines.append(
                f"  page {page_id}: "
                f"old script={old_script_ids[page_id]} offset=0x{old_token_offsets[page_id]:08X} flags=0x{old_flags[page_id]:08X}; "
                f"new script={new_script_ids[page_id]} offset=0x{new_token_offsets[page_id]:08X} flags=0x{new_flags[page_id]:08X}"
            )
    return "\n".join(lines)


def default_dump_output(input_path: Path) -> Path:
    return input_path.with_suffix(input_path.suffix + ".txt")


def default_build_output(input_path: Path) -> Path:
    if input_path.name.endswith(".txt"):
        return input_path.with_name(input_path.name[:-4] + ".ipt")
    return input_path.with_suffix(".ipt")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dump, build, compare, or regenerate KyouPri PageInfo IPT files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dump_parser = subparsers.add_parser("dump", help="Dump pageinfo.ipt to semantic text.")
    dump_parser.add_argument("input", type=Path)
    dump_parser.add_argument("-o", "--output", type=Path)

    build_parser = subparsers.add_parser("build", help="Build pageinfo.ipt from semantic text.")
    build_parser.add_argument("input", type=Path)
    build_parser.add_argument("-o", "--output", type=Path)
    build_parser.add_argument("--allow-duplicate-pages", action="store_true", help="Allow later .page lines to overwrite earlier ones.")

    regen_parser = subparsers.add_parser("regen", help="Regenerate pageinfo.ipt by scanning GSX1 .gkx files.")
    regen_parser.add_argument("input_dir", type=Path)
    regen_parser.add_argument("-o", "--output", type=Path, help="Output pageinfo.ipt; default: INPUT_DIR/pageinfo.ipt.")
    regen_parser.add_argument(
        "--reference",
        type=Path,
        help="Original pageinfo.ipt used to copy FILETIME and infer script IDs. Recommended.",
    )
    regen_parser.add_argument(
        "--manifest",
        type=Path,
        help="Optional manifest with script IDs. Note: FPK entry indices may not match PageInfo script IDs; prefer --reference.",
    )
    regen_parser.add_argument("--script-id", action="append", help="Override script resource ID as name=id, e.g. sce01=12.")
    regen_parser.add_argument("--filetime", help="Optional FILETIME value, e.g. 0x0123456789ABCDEF.")
    regen_parser.add_argument("--force", action="store_true", help="Overwrite an existing output file.")
    regen_parser.add_argument(
        "--allow-fallback-script-ids",
        action="store_true",
        help="Use sorted .gkx order for scripts whose ID cannot be inferred or loaded.",
    )
    regen_parser.add_argument("--allow-duplicate-pages", action="store_true", help="Allow later page records to overwrite earlier ones.")

    compare_parser = subparsers.add_parser("compare", help="Compare two pageinfo.ipt files.")
    compare_parser.add_argument("old", type=Path)
    compare_parser.add_argument("new", type=Path)
    compare_parser.add_argument("--max-diffs", type=int, default=20, help="Maximum changed pages to print, default: 20.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "dump":
            output = args.output or default_dump_output(args.input)
            dump_pageinfo(args.input.resolve(), output.resolve())
            print(f"{args.input.resolve()} -> {output.resolve()}")
        elif args.command == "build":
            output = args.output or default_build_output(args.input)
            build_pageinfo(args.input.resolve(), output.resolve(), args.allow_duplicate_pages)
            print(f"{args.input.resolve()} -> {output.resolve()}")
        elif args.command == "regen":
            input_dir = args.input_dir.resolve()
            output = (args.output or default_regen_output(args.input_dir)).resolve()
            lines = regenerate_pageinfo(
                input_dir,
                output,
                args.manifest.resolve() if args.manifest else None,
                args.filetime,
                args.script_id,
                args.reference.resolve() if args.reference else None,
                args.force,
                args.allow_fallback_script_ids,
                args.allow_duplicate_pages,
            )
            print("\n".join(lines))
            print(f"{input_dir} -> {output}")
        elif args.command == "compare":
            print(compare_pageinfo(args.old.resolve(), args.new.resolve(), args.max_diffs))
    except PageInfoError as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
