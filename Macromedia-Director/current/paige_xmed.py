"""Semantic reader and writer for the Hermes Paige XMED payloads in SCRIPT.cxt.

XMED stores a Paige document as a sequence of 20-byte ASCII-hex record
headers.  Each record body uses Paige's typed number/byte packer.  The code
below mirrors PGREAD.C, PGWRITE.C, and PGHTEXT.C from Hermes-Paige while
exposing ordinary JSON-compatible dictionaries instead of serialized bytes.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Callable


TEXT_ENCODING = "cp932"
BYTE_DATA = 0
SHORT_DATA = 1
LONG_DATA = 2
TERMINATOR_DATA = 3
REPEAT_LAST_VALUE = 0x80
REPEAT_LAST_N_TIMES = 0x40
CODE_MASK = 0x0F
ZERO_TEXT_PAD = 2


class PaigeError(ValueError):
    pass


def _hex(value: int) -> bytes:
    if value < 0:
        return b"-" + f"{-value:X}".encode("ascii")
    return f"{value:X}".encode("ascii")


def _read_hex(data: bytes, offset: int) -> tuple[int, int]:
    start = offset
    negative = offset < len(data) and data[offset] == ord("-")
    if negative:
        offset += 1
    digits = offset
    while offset < len(data) and (
        ord("0") <= data[offset] <= ord("9")
        or ord("A") <= data[offset] <= ord("F")
    ):
        offset += 1
    if offset == digits:
        raise PaigeError(f"Missing hexadecimal number at packed offset {start}")
    value = int(data[digits:offset], 16)
    return (-value if negative else value), offset


class PackedReader:
    def __init__(self, data: bytes, *, allow_truncated: bool = False) -> None:
        self.data = data
        self.offset = 0
        self.last_value = 0
        self.last_kind = 0
        self.repeat_count = 0
        self.allow_truncated = allow_truncated

    def number(self, kind: int) -> int:
        if self.repeat_count:
            self.repeat_count -= 1
            if self.last_kind != kind:
                raise PaigeError("Packed repeated number has the wrong type")
            return self.last_value

        if self.offset >= len(self.data):
            if self.allow_truncated:
                self.last_kind = kind
                self.last_value = 0
                return 0
            raise PaigeError("Truncated packed number")

        code = self.data[self.offset]
        self.offset += 1
        real_kind = code & CODE_MASK
        if real_kind != kind:
            raise PaigeError(
                f"Expected packed kind {kind}, found {real_kind} at "
                f"offset {self.offset - 1}"
            )

        if code & REPEAT_LAST_VALUE:
            value = self.last_value
            if code & REPEAT_LAST_N_TIMES:
                if self.offset >= len(self.data):
                    if not self.allow_truncated:
                        raise PaigeError("Truncated packed repeat count")
                    count = 1
                else:
                    count = self.data[self.offset]
                    self.offset += 1
                self.repeat_count = max(count - 1, 0)
        else:
            try:
                value, self.offset = _read_hex(self.data, self.offset)
            except PaigeError:
                if not self.allow_truncated:
                    raise
                self.offset = len(self.data)
                value = 0

        self.last_kind = kind
        self.last_value = value
        return value

    def short(self) -> int:
        return self.number(SHORT_DATA)

    def long(self) -> int:
        return self.number(LONG_DATA)

    def byte_string(self) -> bytes:
        if self.offset >= len(self.data):
            if self.allow_truncated:
                return b""
            raise PaigeError("Truncated packed byte string")
        if self.data[self.offset] != BYTE_DATA:
            raise PaigeError(f"Expected packed byte string at offset {self.offset}")
        self.offset += 1
        size, self.offset = _read_hex(self.data, self.offset)
        if size < 0 or self.offset >= len(self.data) or self.data[self.offset] != ord(","):
            raise PaigeError("Invalid packed byte-string length")
        self.offset += 1
        end = self.offset + size
        if end > len(self.data):
            if not self.allow_truncated:
                raise PaigeError("Truncated packed byte-string data")
            end = len(self.data)
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def finish(self) -> None:
        if self.repeat_count:
            raise PaigeError("Packed record ended inside a repeated number sequence")
        if self.offset < len(self.data) and self.data[self.offset] == TERMINATOR_DATA:
            self.offset += 1
        if self.offset != len(self.data) and not self.allow_truncated:
            raise PaigeError(
                f"Packed record has {len(self.data) - self.offset} trailing bytes"
            )


class PackedWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.last_value = 0
        self.last_kind = 0

    def number(self, kind: int, value: int) -> None:
        value = int(value)
        if self.last_kind and self.last_value == value:
            self.data.append(kind | REPEAT_LAST_VALUE)
        else:
            self.data.append(kind)
            self.data.extend(_hex(value))
        self.last_kind = kind
        self.last_value = value

    def short(self, value: int) -> None:
        self.number(SHORT_DATA, value)

    def long(self, value: int) -> None:
        self.number(LONG_DATA, value)

    def byte_string(self, value: bytes) -> None:
        if not value:
            return
        self.data.append(BYTE_DATA)
        self.data.extend(f"{len(value):X},".encode("ascii"))
        self.data.extend(value)

    def finish(self) -> bytes:
        self.data.append(TERMINATOR_DATA)
        source = bytes(self.data)
        output = bytearray()
        offset = 0
        while offset < len(source):
            code = source[offset]
            if code & REPEAT_LAST_VALUE:
                end = offset + 1
                while end < len(source) and source[end] == code and end - offset < 127:
                    end += 1
                count = end - offset
                if count >= 3:
                    output.extend((code | REPEAT_LAST_N_TIMES, count))
                else:
                    output.extend(source[offset:end])
                offset = end
                continue

            output.append(code)
            offset += 1
            kind = code & CODE_MASK
            if kind == TERMINATOR_DATA:
                break
            if kind in (SHORT_DATA, LONG_DATA):
                _, end = _read_hex(source, offset)
                output.extend(source[offset:end])
                offset = end
            elif kind == BYTE_DATA:
                size, end = _read_hex(source, offset)
                byte_end = end + 1 + size
                output.extend(source[offset:byte_end])
                offset = byte_end
            else:
                raise PaigeError(f"Cannot optimize unknown packed kind {kind}")
        return bytes(output)


def _rect(reader: PackedReader) -> dict[str, int]:
    return {
        "top": reader.long(),
        "left": reader.long(),
        "bottom": reader.long(),
        "right": reader.long(),
    }


def _write_rect(writer: PackedWriter, value: dict[str, Any]) -> None:
    for name in ("top", "left", "bottom", "right"):
        writer.long(value[name])


def _point(reader: PackedReader) -> dict[str, int]:
    return {"vertical": reader.long(), "horizontal": reader.long()}


def _write_point(writer: PackedWriter, value: dict[str, Any]) -> None:
    writer.long(value["vertical"])
    writer.long(value["horizontal"])


def _color(reader: PackedReader) -> dict[str, int]:
    return {
        "red": reader.short(),
        "green": reader.short(),
        "blue": reader.short(),
        "alpha": reader.short(),
    }


def _write_color(writer: PackedWriter, value: dict[str, Any]) -> None:
    for name in ("red", "green", "blue", "alpha"):
        writer.short(value[name])


def _fixed_text(raw: bytes) -> dict[str, Any]:
    if raw and raw[0] <= len(raw) - 1 and all(
        byte == 0 for byte in raw[1 + raw[0] :]
    ):
        content = raw[1 : 1 + raw[0]]
        storage = "pascal"
    else:
        content = raw.split(b"\0", 1)[0]
        storage = "c"
    try:
        text: str | None = content.decode(TEXT_ENCODING)
    except UnicodeDecodeError:
        text = None
    return {
        "text": text,
        "character_bytes": list(content),
        "storage": storage,
        "size": len(raw),
    }


def _write_fixed_text(value: dict[str, Any]) -> bytes:
    if value.get("text") is None:
        encoded = bytes(value["character_bytes"])
    else:
        encoded = value["text"].encode(TEXT_ENCODING)
    size = int(value["size"])
    if value["storage"] == "pascal":
        if len(encoded) > 255 or len(encoded) + 1 > size:
            raise PaigeError("Pascal text does not fit in its fixed field")
        return bytes((len(encoded),)) + encoded + bytes(size - len(encoded) - 1)
    if len(encoded) >= size:
        raise PaigeError("C text does not fit in its fixed field")
    return encoded + bytes(size - len(encoded))


def _parse_paige(reader: PackedReader) -> dict[str, Any]:
    value = {
        "version": reader.long(),
        "platform": reader.long(),
        "flags": reader.long(),
        "flags2": reader.long(),
        "document_type": reader.long(),
        "header_location": reader.long(),
        "footer_location": reader.long(),
        "resolution": reader.long(),
        "tab_base": reader.long(),
        "document_top": reader.long(),
        "document_bottom": reader.long(),
        "unit_horizontal": reader.short(),
        "unit_vertical": reader.short(),
        "append_horizontal": reader.short(),
        "append_vertical": reader.short(),
        "scroll_align_horizontal": reader.long(),
        "scroll_align_vertical": reader.long(),
        "first_word": {"begin": reader.long(), "end": reader.long()},
        "last_word": {"begin": reader.long(), "end": reader.long()},
        "logical_scroll": _point(reader),
        "scale_origin": _point(reader),
        "scale": reader.long(),
        "document_bounds": _rect(reader),
        "background_color": _color(reader),
        "port_origin": _point(reader),
        "text_direction": reader.long(),
        "author": reader.long(),
        "next_id": reader.long(),
        "highlight_flags": reader.long(),
        "base_visible_origin": _point(reader),
        "highlight_anchor": reader.long(),
        "default_named_style": reader.short(),
    }
    return value


def _write_paige(writer: PackedWriter, value: dict[str, Any]) -> None:
    for name in (
        "version", "platform", "flags", "flags2", "document_type",
        "header_location", "footer_location", "resolution", "tab_base",
        "document_top", "document_bottom",
    ):
        writer.long(value[name])
    for name in ("unit_horizontal", "unit_vertical", "append_horizontal", "append_vertical"):
        writer.short(value[name])
    writer.long(value["scroll_align_horizontal"])
    writer.long(value["scroll_align_vertical"])
    for name in ("first_word", "last_word"):
        writer.long(value[name]["begin"])
        writer.long(value[name]["end"])
    _write_point(writer, value["logical_scroll"])
    _write_point(writer, value["scale_origin"])
    writer.long(value["scale"])
    _write_rect(writer, value["document_bounds"])
    _write_color(writer, value["background_color"])
    _write_point(writer, value["port_origin"])
    for name in ("text_direction", "author", "next_id", "highlight_flags"):
        writer.long(value[name])
    _write_point(writer, value["base_visible_origin"])
    writer.long(value["highlight_anchor"])
    writer.short(value["default_named_style"])


def _parse_point_start(reader: PackedReader) -> dict[str, Any]:
    return {
        "offset": reader.short(),
        "extra": reader.short(),
        "baseline": reader.short(),
        "flags": reader.short(),
        "record_number": reader.short(),
        "bounds": _rect(reader),
    }


def _write_point_start(writer: PackedWriter, value: dict[str, Any]) -> None:
    for name in ("offset", "extra", "baseline", "flags", "record_number"):
        writer.short(value[name])
    _write_rect(writer, value["bounds"])


def _parse_text_blocks(reader: PackedReader, count: int) -> list[dict[str, Any]]:
    blocks = []
    for _ in range(count):
        block = {
            "begin": reader.long(),
            "end": reader.long(),
            "flags": reader.short(),
            "first_line_number": reader.long(),
            "line_count": reader.short(),
            "first_paragraph_number": reader.long(),
            "paragraph_count": reader.short(),
            "application_refcon": reader.long(),
            "bounds": _rect(reader),
            "ending_point": _parse_point_start(reader),
        }
        subref_count = reader.long()
        if subref_count:
            raise PaigeError("Paige subreferences are not used by this CXT and are unsupported")
        block["subreferences"] = []
        blocks.append(block)
    return blocks


def _write_text_blocks(writer: PackedWriter, blocks: list[dict[str, Any]]) -> None:
    for block in blocks:
        writer.long(block["begin"])
        writer.long(block["end"])
        writer.short(block["flags"])
        writer.long(block["first_line_number"])
        writer.short(block["line_count"])
        writer.long(block["first_paragraph_number"])
        writer.short(block["paragraph_count"])
        writer.long(block["application_refcon"])
        _write_rect(writer, block["bounds"])
        _write_point_start(writer, block["ending_point"])
        if block.get("subreferences"):
            raise PaigeError("Writing Paige subreferences is unsupported")
        writer.long(0)


def _parse_runs(reader: PackedReader, count: int) -> list[dict[str, int]]:
    return [
        {"offset": reader.long(), "style_index": reader.short()}
        for _ in range(count)
    ]


def _write_runs(writer: PackedWriter, runs: list[dict[str, Any]]) -> None:
    for run in runs:
        writer.long(run["offset"])
        writer.short(run["style_index"])


def _parse_style(reader: PackedReader) -> dict[str, Any]:
    value = {
        "font_index": reader.short(),
        "character_bytes": reader.short(),
        "maximum_characters": reader.short(),
        "ascent": reader.short(),
        "descent": reader.short(),
        "leading": reader.short(),
        "shift_verb": reader.short(),
        "class_bits": reader.long(),
        "style_sheet_id": reader.long(),
        "foreground_color": _color(reader),
        "background_color": _color(reader),
    }
    for name in (
        "character_width", "point_size", "left_overhang", "right_overhang",
        "top_extra", "bottom_extra", "space_extra", "character_extra",
        "user_id", "user_data", "user_data2", "time_stamp",
        "application_refcon", "use_count",
    ):
        value[name] = reader.long()
    value["future"] = [reader.long() for _ in range(8)]
    value["style_values"] = [reader.short() for _ in range(32)]
    value["small_caps_index"] = reader.long()
    value["key_equivalent"] = reader.long()
    value["style_number"] = reader.short()
    value["based_on_style"] = reader.short()
    value["rtf_reserved"] = reader.short()
    value["named_style_index"] = reader.long()
    return value


def _write_style(writer: PackedWriter, value: dict[str, Any]) -> None:
    for name in (
        "font_index", "character_bytes", "maximum_characters", "ascent",
        "descent", "leading", "shift_verb",
    ):
        writer.short(value[name])
    writer.long(value["class_bits"])
    writer.long(value["style_sheet_id"])
    _write_color(writer, value["foreground_color"])
    _write_color(writer, value["background_color"])
    for name in (
        "character_width", "point_size", "left_overhang", "right_overhang",
        "top_extra", "bottom_extra", "space_extra", "character_extra",
        "user_id", "user_data", "user_data2", "time_stamp",
        "application_refcon", "use_count",
    ):
        writer.long(value[name])
    for item in value["future"]:
        writer.long(item)
    for item in value["style_values"]:
        writer.short(item)
    writer.long(value["small_caps_index"])
    writer.long(value["key_equivalent"])
    writer.short(value["style_number"])
    writer.short(value["based_on_style"])
    writer.short(value["rtf_reserved"])
    writer.long(value["named_style_index"])


def _parse_paragraph(reader: PackedReader) -> dict[str, Any]:
    value = {
        "justification": reader.short(),
        "direction": reader.short(),
        "style_sheet_id": reader.short(),
        "indents": {
            "left": reader.long(),
            "right": reader.long(),
            "first_line": reader.long(),
        },
    }
    for name in (
        "spacing", "leading_extra", "leading_fixed", "leading_variable",
        "top_extra", "bottom_extra", "left_extra", "right_extra",
        "user_id", "user_data", "user_data2", "partial_justification",
    ):
        value[name] = reader.long()
    value["outline_level"] = reader.short()
    value["application_refcon"] = reader.long()
    value["use_count"] = reader.long()
    value["future"] = [reader.long() for _ in range(8)]
    tab_count = reader.short()
    value["tabs"] = [
        {
            "type": reader.long(),
            "position": reader.long(),
            "leader": reader.long(),
            "refcon": reader.long(),
        }
        for _ in range(tab_count)
    ]
    value["default_tab_space"] = reader.long()
    value["class_info"] = reader.short()
    value["key_equivalent"] = reader.long()
    value["style_number"] = reader.short()
    value["based_on_style"] = reader.short()
    value["next_style"] = reader.short()
    value["named_style_index"] = reader.long()
    table_names = (
        "columns", "column_width", "cell_height", "border_info",
        "border_spacing", "border_shading", "cell_borders", "grid_borders",
        "cell_horizontal_extra",
    )
    value["table"] = {name: reader.long() for name in table_names}
    value["html_style"] = reader.long()
    for name in ("top_border_color", "left_border_color", "bottom_border_color", "right_border_color"):
        value["table"][name] = reader.long()
    # Paige 4.0.1 appends three paragraph/table extension values after the
    # revision-23 border colours. They are zero in every SCRIPT.cxt member.
    value["version4_table_extension"] = [reader.long() for _ in range(3)]
    return value


def _write_paragraph(writer: PackedWriter, value: dict[str, Any]) -> None:
    writer.short(value["justification"])
    writer.short(value["direction"])
    writer.short(value["style_sheet_id"])
    for name in ("left", "right", "first_line"):
        writer.long(value["indents"][name])
    for name in (
        "spacing", "leading_extra", "leading_fixed", "leading_variable",
        "top_extra", "bottom_extra", "left_extra", "right_extra",
        "user_id", "user_data", "user_data2", "partial_justification",
    ):
        writer.long(value[name])
    writer.short(value["outline_level"])
    writer.long(value["application_refcon"])
    writer.long(value["use_count"])
    for item in value["future"]:
        writer.long(item)
    writer.short(len(value["tabs"]))
    for tab in value["tabs"]:
        for name in ("type", "position", "leader", "refcon"):
            writer.long(tab[name])
    writer.long(value["default_tab_space"])
    writer.short(value["class_info"])
    writer.long(value["key_equivalent"])
    writer.short(value["style_number"])
    writer.short(value["based_on_style"])
    writer.short(value["next_style"])
    writer.long(value["named_style_index"])
    for name in (
        "columns", "column_width", "cell_height", "border_info",
        "border_spacing", "border_shading", "cell_borders", "grid_borders",
        "cell_horizontal_extra",
    ):
        writer.long(value["table"][name])
    writer.long(value["html_style"])
    for name in ("top_border_color", "left_border_color", "bottom_border_color", "right_border_color"):
        writer.long(value["table"][name])
    for item in value["version4_table_extension"]:
        writer.long(item)


def _parse_font(reader: PackedReader) -> dict[str, Any]:
    value = {
        "name": _fixed_text(reader.byte_string()),
        "alternate_name": _fixed_text(reader.byte_string()),
        "environment_flags": reader.short(),
        "typeface": reader.short(),
        "family_id": reader.short(),
        "character_type": reader.short(),
        "code_page": reader.long(),
        "language": reader.long(),
        "application_refcon": reader.long(),
        "machine_values": [reader.long() for _ in range(8)],
        "platform": reader.long(),
        "alternate_id": reader.short(),
    }
    return value


def _write_font(writer: PackedWriter, value: dict[str, Any]) -> None:
    writer.byte_string(_write_fixed_text(value["name"]))
    writer.byte_string(_write_fixed_text(value["alternate_name"]))
    for name in ("environment_flags", "typeface", "family_id", "character_type"):
        writer.short(value[name])
    writer.long(value["code_page"])
    writer.long(value["language"])
    writer.long(value["application_refcon"])
    for item in value["machine_values"]:
        writer.long(item)
    writer.long(value["platform"])
    writer.short(value["alternate_id"])


def _parse_selection(reader: PackedReader) -> dict[str, Any]:
    return {
        "offset": reader.long(),
        "word": {"begin": reader.long(), "end": reader.long()},
        "original_point": _point(reader),
        "line": reader.short(),
        "primary_caret": reader.long(),
        "secondary_caret": reader.long(),
        "flags": reader.short(),
    }


def _write_selection(writer: PackedWriter, value: dict[str, Any]) -> None:
    writer.long(value["offset"])
    writer.long(value["word"]["begin"])
    writer.long(value["word"]["end"])
    _write_point(writer, value["original_point"])
    writer.short(value["line"])
    writer.long(value["primary_caret"])
    writer.long(value["secondary_caret"])
    writer.short(value["flags"])


def _parse_document_info(reader: PackedReader, version: int) -> dict[str, Any]:
    value = {
        "attributes": reader.long(),
        "exclusion_inset": reader.short(),
        "repeat_slop": reader.long(),
        "repeat_offset": _point(reader),
        "print_target": _rect(reader),
        "margins": _rect(reader),
        "page_count": reader.short(),
        "application_refcon": reader.long(),
        "future": [reader.long() for _ in range(8)],
        "maximum_characters_per_line": reader.long(),
        "minimum_widow": reader.short(),
        "minimum_orphan": reader.short(),
    }
    if version >= 0x00010010:
        value.update(
            {
                "section_attributes": reader.long(),
                "scroll_inset": reader.short(),
                "caret_width_extra": reader.short(),
                "offsets": _rect(reader),
                "start_page_number": reader.short(),
                "restart_page_number": reader.short(),
                "page_number_prefix": reader.short(),
                "page_number_separator": reader.short(),
                "page_number_format": reader.short(),
                "page_number_location": _point(reader),
                "hyphenation_hot_zone": reader.long(),
                "consecutive_hyphens": reader.short(),
                "start_line_number": reader.short(),
                "restart_line_number": reader.short(),
                "line_increment": reader.short(),
                "line_number_location": reader.long(),
                "line_number_width": reader.long(),
                "revision_format": reader.short(),
                "revision_bar": reader.short(),
                "column_count": reader.short(),
                "gutter": reader.long(),
                "default_gap": reader.long(),
                "columns": [
                    {"width": reader.long(), "gap": reader.long()}
                    for _ in range(10)
                ],
            }
        )
    if version >= 0x00010011:
        for name in (
            "document_version", "internal_version", "creation_time",
            "revision_time", "print_time", "backup_time", "edit_time",
            "document_id",
        ):
            value[name] = reader.long()
        # These seven optional memory references are absent in this CXT. Paige
        # writes no marker for a null reference, so their absence is semantic.
        value["text_properties"] = {
            name: None
            for name in (
                "title", "subject", "author", "operator", "keywords",
                "comment", "document_comment",
            )
        }
    value["gutter_color"] = reader.long()
    value["page_borders"] = reader.long()
    return value


def _write_document_info(writer: PackedWriter, value: dict[str, Any], version: int) -> None:
    writer.long(value["attributes"])
    writer.short(value["exclusion_inset"])
    writer.long(value["repeat_slop"])
    _write_point(writer, value["repeat_offset"])
    _write_rect(writer, value["print_target"])
    _write_rect(writer, value["margins"])
    writer.short(value["page_count"])
    writer.long(value["application_refcon"])
    for item in value["future"]:
        writer.long(item)
    writer.long(value["maximum_characters_per_line"])
    writer.short(value["minimum_widow"])
    writer.short(value["minimum_orphan"])
    if version >= 0x00010010:
        writer.long(value["section_attributes"])
        writer.short(value["scroll_inset"])
        writer.short(value["caret_width_extra"])
        _write_rect(writer, value["offsets"])
        for name in (
            "start_page_number", "restart_page_number", "page_number_prefix",
            "page_number_separator", "page_number_format",
        ):
            writer.short(value[name])
        _write_point(writer, value["page_number_location"])
        writer.long(value["hyphenation_hot_zone"])
        for name in (
            "consecutive_hyphens", "start_line_number", "restart_line_number",
            "line_increment",
        ):
            writer.short(value[name])
        writer.long(value["line_number_location"])
        writer.long(value["line_number_width"])
        writer.short(value["revision_format"])
        writer.short(value["revision_bar"])
        writer.short(value["column_count"])
        writer.long(value["gutter"])
        writer.long(value["default_gap"])
        for column in value["columns"]:
            writer.long(column["width"])
            writer.long(column["gap"])
    if version >= 0x00010011:
        for name in (
            "document_version", "internal_version", "creation_time",
            "revision_time", "print_time", "backup_time", "edit_time",
            "document_id",
        ):
            writer.long(value[name])
        for item in value["text_properties"].values():
            if item is not None:
                writer.byte_string(item.encode(TEXT_ENCODING))
    writer.long(value["gutter_color"])
    writer.long(value["page_borders"])


GLOBAL_SHORT_NAMES = (
    "line_wrap", "soft_line", "tab", "soft_hyphen", "backspace",
    "form_feed", "container_break", "left_arrow", "right_arrow",
    "up_arrow", "down_arrow", "forward_delete", "text_block_break", "null",
)
GLOBAL_SYMBOL_NAMES = (
    "hyphen", "decimal", "carriage_return_visible", "line_feed_visible",
    "tab_visible", "end_visible", "page_break_visible", "container_visible",
    "space_visible", "flat_single_quote", "flat_double_quote",
    "left_single_quote", "right_single_quote", "left_double_quote",
    "right_double_quote", "ellipsis", "unknown",
)


def _parse_globals(reader: PackedReader) -> dict[str, Any]:
    chars = {name: reader.short() for name in GLOBAL_SHORT_NAMES}
    raw = reader.byte_string()
    if len(raw) % len(GLOBAL_SYMBOL_NAMES):
        raise PaigeError("Invalid Paige global-symbol table size")
    width = len(raw) // len(GLOBAL_SYMBOL_NAMES)
    symbols = {
        name: _fixed_text(raw[index * width : (index + 1) * width])
        for index, name in enumerate(GLOBAL_SYMBOL_NAMES)
    }
    return {
        "characters": chars,
        "symbols": symbols,
        "default_background_color": _color(reader),
        "transparent_color": _color(reader),
    }


def _write_globals(writer: PackedWriter, value: dict[str, Any]) -> None:
    for name in GLOBAL_SHORT_NAMES:
        writer.short(value["characters"][name])
    writer.byte_string(
        b"".join(_write_fixed_text(value["symbols"][name]) for name in GLOBAL_SYMBOL_NAMES)
    )
    _write_color(writer, value["default_background_color"])
    _write_color(writer, value["transparent_color"])


def _parse_hyperlinks(
    reader: PackedReader, count: int, version: int
) -> list[dict[str, Any]]:
    links = []
    for _ in range(count):
        link = {
            "range": {"begin": reader.long(), "end": reader.long()},
            "active_style": reader.short(),
            "state1_style": reader.short(),
            "state2_style": reader.short(),
            "state3_style": reader.short(),
            "unique_id": 0,
            "type": 0,
            "target_id": 0,
            "application_refcon": 0,
        }
        if version >= 0x0002000F:
            link["unique_id"] = reader.long()
            link["type"] = reader.long()
            if version >= 0x00020010:
                link["target_id"] = reader.long()
            if version >= 0x00030002:
                link["application_refcon"] = reader.long()
        byte_length = reader.long()
        raw = reader.byte_string() if byte_length else b""
        try:
            link["url"] = raw.decode(TEXT_ENCODING)
        except UnicodeDecodeError as exc:
            raise PaigeError("Hyperlink URL is not valid CP932") from exc
        links.append(link)
    return links


def _write_hyperlinks(
    writer: PackedWriter, links: list[dict[str, Any]], version: int
) -> None:
    for link in links:
        writer.long(link["range"]["begin"])
        writer.long(link["range"]["end"])
        for name in ("active_style", "state1_style", "state2_style", "state3_style"):
            writer.short(link[name])
        if version >= 0x0002000F:
            writer.long(link["unique_id"])
            writer.long(link["type"])
            if version >= 0x00020010:
                writer.long(link["target_id"])
            if version >= 0x00030002:
                writer.long(link["application_refcon"])
        raw = link["url"].encode(TEXT_ENCODING)
        writer.long(len(raw))
        if raw:
            writer.byte_string(raw)


def _record(key: int, info: int, body: bytes) -> bytes:
    return (
        f"{key & 0xFFFF:04X}{len(body):08X}{info & 0xFFFFFFFF:08X}".encode("ascii")
        + body
    )


def _packed(write: Callable[[PackedWriter], None]) -> bytes:
    writer = PackedWriter()
    write(writer)
    return writer.finish()


def parse_xmed(payload: bytes) -> tuple[dict[str, Any], bytes]:
    records: list[tuple[int, int, bytes, bool]] = []
    offset = 0
    while offset + 20 <= len(payload):
        header = payload[offset : offset + 20]
        try:
            key = int(header[:4], 16)
            size = int(header[4:12], 16)
            info = int(header[12:20], 16)
        except ValueError:
            break
        start = offset + 20
        end = min(start + size, len(payload))
        records.append((key, info, payload[start:end], end - start < size))
        offset = start + size
        if end - start < size:
            break

    document: dict[str, Any] = {
        "format": "hermes-paige-xmed-v1",
        "serialized_size": len(payload),
        "records": [],
    }
    texts: list[bytes] = []
    version = 0
    for key, info, body, truncated in records:
        reader = PackedReader(body, allow_truncated=truncated)
        if key == 0xFFFF:
            document["signature"] = reader.short()
            document["signature_version"] = info
            name = "signature"
        elif key == 0x0000:
            document["document"] = _parse_paige(reader)
            version = document["document"]["version"]
            name = "document"
        elif key == 0x0001:
            document["text_blocks"] = _parse_text_blocks(reader, info)
            name = "text_blocks"
        elif key == 0x0002:
            texts.append(reader.byte_string())
            name = "text"
        elif key == 0x0004:
            document["style_runs"] = _parse_runs(reader, info)
            name = "style_runs"
        elif key == 0x0005:
            document["paragraph_runs"] = _parse_runs(reader, info)
            name = "paragraph_runs"
        elif key == 0x0006:
            document["insert_style"] = reader.short()
            document["styles"] = [_parse_style(reader) for _ in range(info)]
            name = "styles"
        elif key == 0x0007:
            document["paragraphs"] = [_parse_paragraph(reader) for _ in range(info)]
            name = "paragraphs"
        elif key == 0x0008:
            document["fonts"] = [_parse_font(reader) for _ in range(info)]
            name = "fonts"
        elif key in (0x0009, 0x000A, 0x000B):
            name = {0x0009: "visible_shape", 0x000A: "page_shape", 0x000B: "exclusion_shape"}[key]
            document[name] = [_rect(reader) for _ in range(info)]
        elif key == 0x000C:
            document["selections"] = [_parse_selection(reader) for _ in range(info)]
            name = "selections"
        elif key == 0x000F:
            document["document_info"] = _parse_document_info(reader, version)
            name = "document_info"
        elif key == 0x0013:
            document["globals"] = _parse_globals(reader)
            name = "globals"
        elif key in (0x0128, 0x0129):
            name = "hyperlinks" if key == 0x0128 else "hyperlink_targets"
            document[name] = _parse_hyperlinks(reader, info, version)
        else:
            raise PaigeError(f"Unsupported XMED record key 0x{key:04X}")
        try:
            reader.finish()
        except PaigeError as exc:
            raise PaigeError(f"XMED record 0x{key:04X} ({name}): {exc}") from exc
        document["records"].append(
            {"key": key, "name": name, "element_count": info, "truncated": truncated}
        )

    if "hyperlink_targets" not in document:
        document["hyperlink_targets"] = copy.deepcopy(document.get("hyperlinks", []))
    text_length = sum(len(item) for item in texts)
    sentinel = text_length + ZERO_TEXT_PAD
    for name in ("style_runs", "paragraph_runs"):
        if document.get(name):
            document[name][-1]["offset"] = sentinel
    for name in ("hyperlinks", "hyperlink_targets"):
        if document.get(name):
            document[name][-1]["range"] = {"begin": sentinel, "end": sentinel}
    document["source_text_sha256"] = hashlib.sha256(b"".join(texts)).hexdigest()
    return document, b"".join(texts)


def serialize_xmed(document: dict[str, Any], text: bytes, *, preserve_size: bool) -> bytes:
    version = document["document"]["version"]
    blocks = document["text_blocks"]
    lengths = [block["end"] - block["begin"] for block in blocks]
    if sum(lengths) != len(text):
        raise PaigeError(
            f"Text block lengths total {sum(lengths)}, but text has {len(text)} bytes"
        )
    chunks = []
    cursor = 0
    for length in lengths:
        chunks.append(text[cursor : cursor + length])
        cursor += length

    records = [
        _record(0xFFFF, document["signature_version"], _packed(lambda w: w.short(document["signature"]))),
        _record(0x0000, 0, _packed(lambda w: _write_paige(w, document["document"]))),
        _record(0x0001, len(blocks), _packed(lambda w: _write_text_blocks(w, blocks))),
    ]
    for block, chunk in zip(blocks, chunks):
        records.append(
            _record(0x0002, block["begin"], _packed(lambda w, chunk=chunk: w.byte_string(chunk)))
        )
    records.extend(
        [
            _record(0x0004, len(document["style_runs"]), _packed(lambda w: _write_runs(w, document["style_runs"]))),
            _record(0x0005, len(document["paragraph_runs"]), _packed(lambda w: _write_runs(w, document["paragraph_runs"]))),
            _record(0x0006, len(document["styles"]), _packed(lambda w: (w.short(document["insert_style"]), [_write_style(w, item) for item in document["styles"]]))),
            _record(0x0007, len(document["paragraphs"]), _packed(lambda w: [_write_paragraph(w, item) for item in document["paragraphs"]])),
            _record(0x0008, len(document["fonts"]), _packed(lambda w: [_write_font(w, item) for item in document["fonts"]])),
            _record(0x0009, len(document["visible_shape"]), _packed(lambda w: [_write_rect(w, item) for item in document["visible_shape"]])),
            _record(0x000A, len(document["page_shape"]), _packed(lambda w: [_write_rect(w, item) for item in document["page_shape"]])),
            _record(0x000B, len(document["exclusion_shape"]), _packed(lambda w: [_write_rect(w, item) for item in document["exclusion_shape"]])),
            _record(0x000C, len(document["selections"]), _packed(lambda w: [_write_selection(w, item) for item in document["selections"]])),
            _record(0x000F, 0, _packed(lambda w: _write_document_info(w, document["document_info"], version))),
            _record(0x0013, 0, _packed(lambda w: _write_globals(w, document["globals"]))),
            _record(0x0128, len(document["hyperlinks"]), _packed(lambda w: _write_hyperlinks(w, document["hyperlinks"], version))),
            _record(0x0129, len(document["hyperlink_targets"]), _packed(lambda w: _write_hyperlinks(w, document["hyperlink_targets"], version))),
        ]
    )
    result = b"".join(records)
    if preserve_size:
        expected = int(document["serialized_size"])
        if len(result) < expected:
            raise PaigeError(
                f"Semantic XMED is {len(result)} bytes, shorter than stored size {expected}"
            )
        result = result[:expected]
    return result


def _split_text_blocks(text: bytes, maximum: int = 3968) -> list[bytes]:
    if not text:
        return [b""]
    chunks: list[bytes] = []
    offset = 0
    while len(text) - offset > maximum:
        end = offset + maximum
        break_at = text.rfind(b"\r", offset, end + 1)
        if break_at >= offset:
            end = break_at + 1
        else:
            # CP932 trail bytes never contain 0x0D, but a hard split still has
            # to avoid cutting a double-byte character.
            while end > offset:
                try:
                    text[offset:end].decode(TEXT_ENCODING)
                    break
                except UnicodeDecodeError:
                    end -= 1
        if end == offset:
            raise PaigeError("Could not find a CP932-safe text-block boundary")
        chunks.append(text[offset:end])
        offset = end
    chunks.append(text[offset:])
    return chunks


def update_document_text(document: dict[str, Any], text: bytes) -> dict[str, Any]:
    """Update every text-length/layout field after an edited body is encoded."""

    updated = copy.deepcopy(document)
    old_blocks = updated["text_blocks"]
    chunks = _split_text_blocks(text)
    blocks: list[dict[str, Any]] = []
    byte_offset = 0
    line_offset = 0
    vertical = 0
    for index, chunk in enumerate(chunks):
        template = copy.deepcopy(old_blocks[min(index, len(old_blocks) - 1)])
        cr_count = chunk.count(b"\r")
        final = index == len(chunks) - 1
        height = (cr_count + (1 if final else 0)) * 12
        if final:
            ending_offset = len(chunk)
        elif chunk.endswith(b"\r"):
            previous_cr = chunk.rfind(b"\r", 0, len(chunk) - 1)
            ending_offset = previous_cr + 1
        else:
            ending_offset = chunk.rfind(b"\r") + 1
        template.update(
            {
                "begin": byte_offset,
                "end": byte_offset + len(chunk),
                "flags": int(template["flags"]) | 0x4081,
                "first_line_number": line_offset,
                "line_count": cr_count,
                "first_paragraph_number": line_offset,
                "paragraph_count": cr_count,
                "bounds": {
                    "top": vertical,
                    "left": 0,
                    "bottom": vertical + height,
                    "right": 500,
                },
                "subreferences": [],
            }
        )
        template["ending_point"].update(
            {
                "offset": ending_offset,
                "extra": 0,
                "baseline": 2,
                "flags": 0x8E00 if final else 0xCE00,
                "record_number": 0,
                "bounds": {
                    "top": max(vertical + height - 12, vertical),
                    "left": 0,
                    "bottom": vertical + height,
                    "right": 0,
                },
            }
        )
        blocks.append(template)
        byte_offset += len(chunk)
        line_offset += cr_count
        vertical += height

    updated["text_blocks"] = blocks
    updated["document"]["document_bottom"] = vertical
    updated["document"]["document_bounds"]["bottom"] = vertical
    if updated["page_shape"]:
        updated["page_shape"][-1]["bottom"] = vertical
    sentinel = len(text) + ZERO_TEXT_PAD
    for name in ("style_runs", "paragraph_runs"):
        if updated[name]:
            updated[name][-1]["offset"] = sentinel
    for name in ("hyperlinks", "hyperlink_targets"):
        if updated[name]:
            updated[name][-1]["range"] = {"begin": sentinel, "end": sentinel}
    updated["source_text_sha256"] = hashlib.sha256(text).hexdigest()
    return updated
