"""Director LS 文本 JSON 提取与回写工具。

支持两类单行文本表达式：

- stn("...", 1)
- member("...").text = "..." & theText9 & "..."

提取时只输出一个 message 字段；如果中间夹了变量或表达式，
会在 message 中写成 {{expr}} 形式的占位符。

为避免和正文冲突，正文里的反斜杠与占位符边界会自动转义：

- \\  -> \\\
- {{ -> \\{{
- }} -> \\}}

回写时要求占位符顺序与原脚本一致，这样才能在不改动非字面量结构的前提下
把 JSON 修改安全地还原回 .ls，再继续用 director_ls_roundtrip.py 导回 .cst。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

try:
    import director_ls_roundtrip as director_tools
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parent))
    import director_ls_roundtrip as director_tools


PLACEHOLDER_OPEN = "{{"
PLACEHOLDER_CLOSE = "}}"
IGNORED_EXPRESSIONS = {"EMPTY"}
PRESERVED_TRAILING_CHARS = {"▼", "　"}
TEXT_ASSIGNMENT_RE = re.compile(r"\.text\s*=\s*")


class LiteralSlot(NamedTuple):
    start: int
    end: int
    group_index: int
    original_text: str


class MessageEntry(NamedTuple):
    line_index: int
    message: str
    literal_slots: tuple[LiteralSlot, ...]
    placeholder_sequence: tuple[str, ...]
    literal_group_counts: tuple[int, ...]
    preserved_suffix: str


def read_utf8_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def write_utf8_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8", newline="")


def escape_message_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace(PLACEHOLDER_OPEN, "\\" + PLACEHOLDER_OPEN)
    text = text.replace(PLACEHOLDER_CLOSE, "\\" + PLACEHOLDER_CLOSE)
    return text


def build_placeholder(expr_text: str) -> str:
    return f"{PLACEHOLDER_OPEN}{expr_text}{PLACEHOLDER_CLOSE}"


def is_ascii_only(text: str) -> bool:
    return bool(text) and text.isascii()


def is_only_fullwidth_spaces(text: str) -> bool:
    return bool(text) and all(char == "　" for char in text)


def should_export_literal_text(text: str) -> bool:
    if not text:
        return False
    if is_ascii_only(text):
        return False
    if is_only_fullwidth_spaces(text):
        return False
    return True


def split_preserved_suffix(text: str) -> tuple[str, str]:
    if text and text[-1] in PRESERVED_TRAILING_CHARS:
        return text[:-1], text[-1]
    return text, ""


def find_first_top_level_char(text: str, target_char: str) -> int:
    depth = 0
    index = 0
    in_string = False
    while index < len(text):
        char = text[index]
        if in_string:
            if char == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    continue
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue

        if char == '(':
            depth += 1
        elif char == ')' and depth > 0:
            depth -= 1
        elif char == target_char and depth == 0:
            return index

        index += 1

    return -1


def split_top_level_concat(expr_text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    index = 0
    in_string = False
    while index < len(expr_text):
        char = expr_text[index]
        if in_string:
            if char == '"':
                if index + 1 < len(expr_text) and expr_text[index + 1] == '"':
                    index += 2
                    continue
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue

        if char == '&':
            ranges.append((start, index))
            start = index + 1
        index += 1

    ranges.append((start, len(expr_text)))
    return ranges


def decode_lingo_string_literal(token_text: str) -> str | None:
    if not token_text or token_text[0] != '"' or token_text[-1] != '"':
        return None

    result: list[str] = []
    index = 1
    while index < len(token_text) - 1:
        char = token_text[index]
        if char == '"':
            if index + 1 < len(token_text) - 1 and token_text[index + 1] == '"':
                result.append('"')
                index += 2
                continue
            return None
        result.append(char)
        index += 1

    return "".join(result)


def encode_lingo_string_literal_content(text: str) -> str:
    return text.replace('"', '""')


def parse_expression_parts(expr_text: str, base_offset: int) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    for start, end in split_top_level_concat(expr_text):
        raw_segment = expr_text[start:end]
        trimmed_segment = raw_segment.strip()
        if not trimmed_segment:
            continue

        leading_ws = len(raw_segment) - len(raw_segment.lstrip())
        decoded_literal = decode_lingo_string_literal(trimmed_segment)
        if decoded_literal is not None:
            content_start = base_offset + start + leading_ws + 1
            content_end = content_start + len(trimmed_segment) - 2
            parts.append(
                {
                    "kind": "literal",
                    "text": decoded_literal,
                    "start": content_start,
                    "end": content_end,
                }
            )
            continue

        parts.append(
            {
                "kind": "expr",
                "text": trimmed_segment,
            }
        )

    return parts


def is_ignored_expression(expr_text: str) -> bool:
    return expr_text.strip().upper() in IGNORED_EXPRESSIONS


def parse_stn_line(content: str) -> list[dict[str, object]] | None:
    stripped = content.lstrip()
    if not stripped.startswith("stn("):
        return None

    indent_len = len(content) - len(stripped)
    open_paren_index = indent_len + 3
    close_paren_index = content.rfind(")")
    if close_paren_index <= open_paren_index:
        return None

    args_text = content[open_paren_index + 1:close_paren_index]
    comma_index = find_first_top_level_char(args_text, ",")
    if comma_index < 0:
        return None

    message_expr = args_text[:comma_index]
    return parse_expression_parts(message_expr, open_paren_index + 1)


def parse_text_assignment_line(content: str) -> list[dict[str, object]] | None:
    match = TEXT_ASSIGNMENT_RE.search(content)
    if not match:
        return None

    message_expr = content[match.end():]
    if not message_expr.strip():
        return None

    return parse_expression_parts(message_expr, match.end())


def build_message_entry(line_index: int, parts: list[dict[str, object]]) -> MessageEntry | None:
    literal_slots: list[LiteralSlot] = []
    placeholder_sequence: list[str] = []
    literal_group_counts = [0]
    message_parts: list[str] = []
    literal_text_pieces: list[str] = []
    group_index = 0

    for part in parts:
        if part["kind"] == "literal":
            literal_text = str(part["text"])
            start_value = part.get("start")
            end_value = part.get("end")
            if not isinstance(start_value, int) or not isinstance(end_value, int):
                raise TypeError("literal part 缺少有效的 start/end 位置信息。")
            literal_slots.append(
                LiteralSlot(
                    start=start_value,
                    end=end_value,
                    group_index=group_index,
                    original_text=literal_text,
                )
            )
            literal_group_counts[group_index] += 1
            literal_text_pieces.append(literal_text)
            message_parts.append(escape_message_text(literal_text))
            continue

        expr_text = str(part["text"]).strip()
        if not expr_text or is_ignored_expression(expr_text):
            continue

        placeholder_sequence.append(expr_text)
        message_parts.append(build_placeholder(expr_text))
        group_index += 1
        literal_group_counts.append(0)

    message_text = "".join(message_parts)
    literal_text = "".join(literal_text_pieces)
    preserved_suffix = ""
    if literal_slots:
        _without_suffix, preserved_suffix = split_preserved_suffix(literal_slots[-1].original_text)
        if preserved_suffix and message_text.endswith(preserved_suffix):
            message_text = message_text[:-len(preserved_suffix)]
        else:
            preserved_suffix = ""
        if preserved_suffix and literal_text.endswith(preserved_suffix):
            literal_text = literal_text[:-len(preserved_suffix)]

    if not should_export_literal_text(literal_text):
        return None

    return MessageEntry(
        line_index=line_index,
        message=message_text,
        literal_slots=tuple(literal_slots),
        placeholder_sequence=tuple(placeholder_sequence),
        literal_group_counts=tuple(literal_group_counts),
        preserved_suffix=preserved_suffix,
    )


def parse_message_line(line_index: int, line: str) -> MessageEntry | None:
    content = line.rstrip("\r\n")
    parts = parse_stn_line(content)
    if parts is None:
        parts = parse_text_assignment_line(content)
    if parts is None:
        return None
    return build_message_entry(line_index, parts)


def extract_message_entries(lines: list[str]) -> list[MessageEntry]:
    entries: list[MessageEntry] = []
    for line_index, line in enumerate(lines):
        entry = parse_message_line(line_index, line)
        if entry is not None:
            entries.append(entry)
    return entries


def parse_message_tokens(message: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    current_text: list[str] = []
    index = 0
    while index < len(message):
        if message[index] == "\\":
            if index + 1 >= len(message):
                current_text.append("\\")
                index += 1
                continue

            if message.startswith("\\" + PLACEHOLDER_OPEN, index):
                current_text.append(PLACEHOLDER_OPEN)
                index += len(PLACEHOLDER_OPEN) + 1
                continue

            if message.startswith("\\" + PLACEHOLDER_CLOSE, index):
                current_text.append(PLACEHOLDER_CLOSE)
                index += len(PLACEHOLDER_CLOSE) + 1
                continue

            current_text.append(message[index + 1])
            index += 2
            continue

        if message.startswith(PLACEHOLDER_OPEN, index):
            close_index = message.find(PLACEHOLDER_CLOSE, index + len(PLACEHOLDER_OPEN))
            if close_index < 0:
                raise ValueError("message 中存在未闭合的占位符。")

            if current_text:
                tokens.append(("text", "".join(current_text)))
                current_text = []

            placeholder_text = message[index + len(PLACEHOLDER_OPEN):close_index]
            tokens.append(("placeholder", placeholder_text))
            index = close_index + len(PLACEHOLDER_CLOSE)
            continue

        current_text.append(message[index])
        index += 1

    if current_text:
        tokens.append(("text", "".join(current_text)))
    return tokens


def split_message_blocks(tokens: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    blocks: list[str] = []
    placeholders: list[str] = []
    current_text: list[str] = []
    for token_kind, token_value in tokens:
        if token_kind == "text":
            current_text.append(token_value)
            continue

        placeholders.append(token_value)
        blocks.append("".join(current_text))
        current_text = []

    blocks.append("".join(current_text))
    return blocks, placeholders


def distribute_block_text(block_text: str, slot_count: int) -> list[str]:
    if slot_count == 0:
        if block_text:
            raise ValueError("修改后的文本在原脚本没有字面量槽位的位置新增了正文。")
        return []

    if slot_count == 1:
        return [block_text]

    return [""] * (slot_count - 1) + [block_text]


def apply_message_to_line(original_line: str, entry: MessageEntry, edited_message: str) -> str:
    tokens = parse_message_tokens(edited_message)
    blocks, placeholders = split_message_blocks(tokens)
    if tuple(placeholders) != entry.placeholder_sequence:
        raise ValueError(
            "占位符顺序或内容与原脚本不一致，无法安全回写。"
            f" 原始={list(entry.placeholder_sequence)}"
            f" 新值={placeholders}"
        )

    if len(blocks) != len(entry.literal_group_counts):
        raise ValueError("message 的占位符数量与原脚本不一致。")

    if entry.preserved_suffix:
        blocks[-1] += entry.preserved_suffix

    replaced_literal_texts: list[str] = []
    for block_text, slot_count in zip(blocks, entry.literal_group_counts):
        replaced_literal_texts.extend(distribute_block_text(block_text, slot_count))

    if len(replaced_literal_texts) != len(entry.literal_slots):
        raise ValueError("回写后的字面量数量与原脚本不一致。")

    content = original_line.rstrip("\r\n")
    newline = original_line[len(content):]
    for literal_slot, new_text in reversed(list(zip(entry.literal_slots, replaced_literal_texts))):
        encoded_text = encode_lingo_string_literal_content(new_text)
        content = content[:literal_slot.start] + encoded_text + content[literal_slot.end:]
    return content + newline


def load_message_items(input_json: Path) -> list[dict[str, str]]:
    items = json.loads(input_json.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("JSON 格式错误：根节点必须是数组。")

    normalized_items: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 项不是对象。")
        unknown_keys = set(item.keys()) - {"message"}
        if unknown_keys:
            raise ValueError(f"第 {index} 项包含非法字段: {sorted(unknown_keys)}")
        if "message" not in item:
            raise ValueError(f"第 {index} 项缺少 message 字段。")
        normalized_items.append({"message": "" if item["message"] is None else str(item["message"])})
    return normalized_items


def extract_json_from_ls(input_ls: Path, output_json: Path) -> int:
    if not input_ls.is_file():
        raise FileNotFoundError(f"LS 文件不存在: {input_ls}")

    entries = extract_message_entries(read_utf8_lines(input_ls))
    payload = [{"message": entry.message} for entry in entries]
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return len(entries)


def extract_json_from_ls_dir(input_dir: Path, output_dir: Path) -> tuple[int, int]:
    if not input_dir.is_dir():
        raise FileNotFoundError(f"LS 目录不存在: {input_dir}")

    file_count = 0
    message_count = 0
    for ls_path in sorted(input_dir.rglob("*.ls"), key=lambda path: path.as_posix().lower()):
        entries = extract_message_entries(read_utf8_lines(ls_path))
        if not entries:
            continue

        relative_path = ls_path.relative_to(input_dir).with_suffix(".json")
        output_json = output_dir / relative_path
        payload = [{"message": entry.message} for entry in entries]
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        file_count += 1
        message_count += len(entries)

    return file_count, message_count


def extract_json_from_cst(input_cst: Path, output_dir: Path) -> tuple[int, int]:
    if not input_cst.is_file():
        raise FileNotFoundError(f"CST 文件不存在: {input_cst}")

    with tempfile.TemporaryDirectory(prefix="director_json_extract_") as temp_dir:
        temp_root = Path(temp_dir) / "ls_export"
        director_tools.export_scripts(input_cst, temp_root)
        exported_ls_dir = temp_root / input_cst.stem
        target_json_dir = output_dir / input_cst.stem
        return extract_json_from_ls_dir(exported_ls_dir, target_json_dir)


def import_json_to_ls(input_ls: Path, input_json: Path, output_ls: Path) -> int:
    if not input_ls.is_file():
        raise FileNotFoundError(f"LS 文件不存在: {input_ls}")
    if not input_json.is_file():
        raise FileNotFoundError(f"JSON 文件不存在: {input_json}")

    lines = read_utf8_lines(input_ls)
    entries = extract_message_entries(lines)
    items = load_message_items(input_json)
    if len(items) != len(entries):
        raise ValueError(f"JSON 条目数与 LS 可提取条目数不一致: json={len(items)} ls={len(entries)}")

    output_lines = list(lines)
    for item_index, (item, entry) in enumerate(zip(items, entries), start=1):
        try:
            output_lines[entry.line_index] = apply_message_to_line(lines[entry.line_index], entry, item["message"])
        except Exception as exc:
            raise ValueError(
                "JSON 导回失败:\n"
                f"文件: {input_json}\n"
                f"条目: 第 {item_index} 条\n"
                f"LS 行号: 第 {entry.line_index + 1} 行\n"
                f"原始 message: {entry.message}\n"
                f"修改后 message: {item['message']}\n"
                f"占位符顺序: {list(entry.placeholder_sequence)}\n"
                f"字面量分组: {list(entry.literal_group_counts)}\n"
                f"原因: {exc}"
            ) from exc

    write_utf8_lines(output_ls, output_lines)
    return len(entries)


def import_json_to_ls_dir(input_ls_dir: Path, input_json_dir: Path, output_ls_dir: Path) -> tuple[int, int]:
    if not input_ls_dir.is_dir():
        raise FileNotFoundError(f"LS 目录不存在: {input_ls_dir}")
    if not input_json_dir.is_dir():
        raise FileNotFoundError(f"JSON 目录不存在: {input_json_dir}")

    same_directory = input_ls_dir.resolve() == output_ls_dir.resolve()
    if not same_directory:
        shutil.copytree(input_ls_dir, output_ls_dir, dirs_exist_ok=True)

    file_count = 0
    message_count = 0
    for json_path in sorted(input_json_dir.rglob("*.json"), key=lambda path: path.as_posix().lower()):
        relative_path = json_path.relative_to(input_json_dir)
        source_ls = input_ls_dir / relative_path.with_suffix(".ls")
        target_ls = output_ls_dir / relative_path.with_suffix(".ls")
        if not source_ls.is_file():
            raise FileNotFoundError(f"找不到与 JSON 对应的 LS 文件: {source_ls}")

        message_count += import_json_to_ls(source_ls, json_path, target_ls)
        file_count += 1

    return file_count, message_count


def import_json_dir_to_cst(input_cst: Path, input_json_dir: Path, output_cst: Path) -> tuple[int, int]:
    if not input_cst.is_file():
        raise FileNotFoundError(f"CST 文件不存在: {input_cst}")
    if not input_json_dir.is_dir():
        raise FileNotFoundError(f"JSON 目录不存在: {input_json_dir}")

    with tempfile.TemporaryDirectory(prefix="director_json_import_") as temp_dir:
        temp_root = Path(temp_dir)
        export_root = temp_root / "ls_export"
        patched_root = temp_root / "ls_patched"
        director_tools.export_scripts(input_cst, export_root)
        source_ls_dir = export_root / input_cst.stem
        patched_ls_dir = patched_root / input_cst.stem
        file_count, message_count = import_json_to_ls_dir(source_ls_dir, input_json_dir, patched_ls_dir)
        director_tools.import_ls_folder(input_cst, patched_ls_dir, output_cst)
        return file_count, message_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="提取或回写 Director LS 脚本中的 stn/text 文本 JSON。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract-json", help="从单个 LS 文件或整个 LS 目录提取 message JSON")
    extract_parser.add_argument("input_path", type=Path)
    extract_parser.add_argument("output_path", type=Path)

    extract_cst_parser = subparsers.add_parser("extract-json-from-cst", help="从原始 CST 直接提取整个 JSON 目录")
    extract_cst_parser.add_argument("input_cst", type=Path)
    extract_cst_parser.add_argument("output_dir", type=Path)

    import_parser = subparsers.add_parser("import-json", help="把单个 JSON 或整个 JSON 目录回写到 LS 文件/目录")
    import_parser.add_argument("input_ls", type=Path)
    import_parser.add_argument("input_json", type=Path)
    import_parser.add_argument("output_path", type=Path)

    import_cst_parser = subparsers.add_parser("import-json-to-cst", help="把原始 CST 和 JSON 目录一键导回新的 CST")
    import_cst_parser.add_argument("input_cst", type=Path)
    import_cst_parser.add_argument("input_json_dir", type=Path)
    import_cst_parser.add_argument("output_cst", type=Path)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "extract-json":
            if args.input_path.is_dir():
                file_count, message_count = extract_json_from_ls_dir(args.input_path, args.output_path)
                print(f"已从 {file_count} 个 LS 文件提取 {message_count} 条文本到 {args.output_path}")
            else:
                message_count = extract_json_from_ls(args.input_path, args.output_path)
                print(f"已从 {args.input_path.name} 提取 {message_count} 条文本到 {args.output_path}")
        elif args.command == "extract-json-from-cst":
            file_count, message_count = extract_json_from_cst(args.input_cst, args.output_dir)
            print(f"已从 {args.input_cst.name} 提取 {message_count} 条文本到 {args.output_dir / args.input_cst.stem}")
        elif args.command == "import-json":
            if args.input_ls.is_dir():
                file_count, message_count = import_json_to_ls_dir(args.input_ls, args.input_json, args.output_path)
                print(f"已把 {file_count} 个 JSON 文件回写到 LS 目录，共应用 {message_count} 条文本 -> {args.output_path}")
            else:
                message_count = import_json_to_ls(args.input_ls, args.input_json, args.output_path)
                print(f"已把 {message_count} 条文本回写到 {args.output_path}")
        elif args.command == "import-json-to-cst":
            file_count, message_count = import_json_dir_to_cst(args.input_cst, args.input_json_dir, args.output_cst)
            print(f"已把 {file_count} 个 JSON 文件导回新的 CST，共应用 {message_count} 条文本 -> {args.output_cst}")
        else:
            parser.error(f"未知命令: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())