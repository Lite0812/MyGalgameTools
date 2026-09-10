"""Kogado VM 官方 opcode 定义。

名称来自 Inugami.exe 内置字符串和扩展元数据。空 dispatch 槽显式写成
INVALID_xx，使反汇编器能区分空槽与真正未定义的文件字节。
"""

from __future__ import annotations


def _op(mnemonic: str, length: int = 2, operands=None, stack: str = "0", handler=None,
        valid: bool = True, semantic_status: str = "confirmed"):
    return {
        "mnemonic": mnemonic,
        "length": length,
        "operands": list(operands or []),
        "stack": stack,
        "handler": handler,
        "valid": valid,
        "semantic_status": semantic_status,
    }


OPCODES = {
    0x00: _op("NOP", handler=0x429884),
    0x01: _op("PUSH", 6, [{"type": "imm32", "width": 4}], "0 -> 1", 0x429888),
    0x02: _op("POP", stack="1 -> 0", handler=0x4298A8),
    0x03: _op("JMP", 6, [{"type": "rel_offset", "width": 4, "base": "instruction_start"}], "0 -> 0", 0x4298B4),
    0x04: _op("JZ", 6, [{"type": "rel_offset", "width": 4, "base": "instruction_start"}], "1 -> 0", 0x4298D4),
    0x05: _op("JNZ", 6, [{"type": "rel_offset", "width": 4, "base": "instruction_start"}], "1 -> 0", 0x429904),
    0x06: _op("CALL", stack="1 -> 0/1", handler=0x429934, semantic_status="runtime_pending"),
    0x07: _op("INVALID_07", valid=False),
    0x08: _op("DUP", stack="1 -> 2", handler=0x429C9C),
    0x09: _op("SWAP", stack="2 -> 2", handler=0x429CB8),
    0x0A: _op("DUP2", stack="2 -> 3", handler=0x429CF0),
    0x0B: _op("SWAP2", stack="3 -> 3", handler=0x429D28),
    0x0C: _op("INVALID_0C", valid=False),
    0x0D: _op("INVALID_0D", valid=False),
    0x0E: _op("RET", stack="conditional", handler=0x429D78, semantic_status="runtime_pending"),
    0x0F: _op("EXIT", handler=0x429E20),
    0x10: _op("LNOT", stack="1 -> 1", handler=0x429E28),
    0x11: _op("INVALID_11", valid=False),
    0x12: _op("INVALID_12", valid=False),
    0x13: _op("MUL", stack="2 -> 1", handler=0x429E58),
    0x14: _op("DIV", stack="2 -> 1", handler=0x429E84),
    0x15: _op("REM", stack="2 -> 1", handler=0x429EC8),
    0x16: _op("ADD", stack="2 -> 1", handler=0x429F04),
    0x17: _op("SUB", stack="2 -> 1", handler=0x429F2C),
    0x18: _op("LT", stack="2 -> 1", handler=0x429F58),
    0x19: _op("LE", stack="2 -> 1", handler=0x429F8C),
    0x1A: _op("GT", stack="2 -> 1", handler=0x429FC0),
    0x1B: _op("GE", stack="2 -> 1", handler=0x429FF4),
    0x1C: _op("EQ", stack="2 -> 1", handler=0x42A028),
    0x1D: _op("NE", stack="2 -> 1", handler=0x42A05C),
    0x1E: _op("LAND", stack="2 -> 1", handler=0x42A090),
    0x1F: _op("LOR", stack="2 -> 1", handler=0x42A0D8),
    0x20: _op("SETF", stack="2 -> 0", handler=0x42A120),
    0x21: _op("GETF", stack="1 -> 1", handler=0x42A14C),
    0x22: _op("SETSF", stack="2 -> 0", handler=0x42A178),
    0x23: _op("GETSF", stack="1 -> 1", handler=0x42A1A4),
    0x24: _op("SETV", stack="2 -> 0", handler=0x42A1D0),
    0x25: _op("GETV", stack="1 -> 1", handler=0x42A1F8),
    0x26: _op("SETSV", stack="2 -> 0", handler=0x42A224),
    0x27: _op("INCSV", stack="1 -> 1", handler=0x42A24C, semantic_status="runtime_pending"),
    0x28: _op("SETSTR", stack="2 -> 0", handler=0x42A278),
    0x29: _op("GETSTR", stack="1 -> 1", handler=0x42A30C, semantic_status="runtime_pending"),
    0x2A: _op("SETRES", stack="1 -> 0", handler=0x42A32C),
    0x2B: _op("GETRES", stack="0 -> 1", handler=0x42A348),
    0x2C: _op("GETCF", stack="1 -> 1", handler=0x42A364),
    0x2D: _op("GETCV", stack="1 -> 1", handler=0x42A390),
    0x2E: _op("GETARG", stack="0 -> 1", handler=0x42A3BC),
    0x2F: _op("INVALID_2F", valid=False),
    0x30: _op("SCNCHG", stack="1 -> 0", handler=0x42A3D8, semantic_status="runtime_pending"),
}

for _opcode in range(0x31, 0x40):
    OPCODES[_opcode] = _op(f"INVALID_{_opcode:02X}", valid=False)


_EXTENSIONS = [
    (0x40, "Text", 1, 0, 0x42A4B4), (0x41, "NewLine", 0, 0, 0x42A544),
    (0x42, "NewPage", 0, 0, 0x42A54C), (0x43, "TextShow", 0, 0, 0x42A554),
    (0x44, "TextHide", 0, 0, 0x42A55C), (0x45, "TextSpeed", 1, 0, 0x42A564),
    (0x46, "NovelMode", 1, 0, 0x42A580), (0x47, "Locate", 2, 0, 0x42A5C8),
    (0x48, "Ruby", 2, 0, 0x42A5F0), (0x49, "TextSize", 1, 0, 0x42A690),
    (0x4C, "Wait", 1, 0, 0x42A6A0), (0x4D, "KeyWait", 0, 0, 0x42A6C4),
    (0x4E, "PageWait", 0, 0, 0x42A6F4), (0x4F, "GoTitle", 0, 0, 0x42A728),
    (0x50, "GoBattle", 1, 1, 0x42A730), (0x51, "Select", 4, 1, 0x42A768),
    (0x52, "ExSelect", 1, 1, 0x42A980), (0x55, "SkipDisable", 1, 0, 0x42A99C),
    (0x56, "SetStage", 1, 0, 0x42A9B4), (0x57, "Random", 1, 1, 0x42A9D0),
    (0x58, "SetAlbum", 1, 0, 0x42A9FC), (0x59, "ShowCursor", 0, 0, 0x42AA34),
    (0x5A, "HideCursor", 0, 0, 0x42AA54), (0x5B, "ShowMovie", 1, 0, 0x42AA74),
    (0x5C, "ShowOpening", 0, 0, 0x42AAB0), (0x5D, "ShowEnding", 1, 0, 0x42AAD8),
    (0x5E, "BacklogClear", 0, 0, 0x42AB0C), (0x5F, "SetWeather", 1, 0, 0x42AB18),
    (0x60, "GetWeather", 0, 1, 0x42AB34), (0x61, "SetDate", 1, 0, 0x42AB4C),
    (0x62, "GetDate", 0, 1, 0x42AB74), (0x63, "GetMonth", 0, 1, 0x42AB9C),
    (0x64, "GetDay", 0, 1, 0x42ABB0), (0x65, "GetWeek", 0, 1, 0x42ABC4),
    (0x66, "SetTime", 1, 0, 0x42ABDC), (0x67, "GetTime", 0, 1, 0x42AC04),
    (0x68, "GetHour", 0, 1, 0x42AC2C), (0x69, "GetMin", 0, 1, 0x42AC40),
    (0x6A, "AddDay", 1, 0, 0x42AC54), (0x6B, "AddMin", 1, 0, 0x42AC7C),
    (0x6C, "DateShow", 1, 0, 0x42ACA4), (0x6D, "DateHide", 1, 0, 0x42ACC8),
    (0x6E, "TimeShow", 1, 0, 0x42ACEC), (0x6F, "TimeHide", 1, 0, 0x42AD10),
    (0x70, "ShowPlace", 1, 0, 0x42AD34), (0x75, "Voice", 1, 0, 0x42AD54),
    (0x76, "VoiceVol", 1, 0, 0x42ADAC), (0x77, "VoicePos", 2, 0, 0x42ADD4),
    (0x78, "BGMPlay", 1, 0, 0x42AE14), (0x79, "BGMStop", 0, 0, 0x42AE4C),
    (0x7A, "BGMPlayQ", 1, 0, 0x42AE68), (0x7B, "BGMStopQ", 0, 0, 0x42AEA0),
    (0x7C, "BGMVol", 1, 0, 0x42AEBC), (0x7D, "SongPlay", 1, 0, 0x42AEE4),
    (0x7E, "SongStop", 0, 0, 0x42AF10), (0x7F, "SongPlayQ", 1, 0, 0x42AF2C),
    (0x80, "SongStopQ", 0, 0, 0x42AF58), (0x81, "SongVol", 1, 0, 0x42AF74),
    (0x82, "SEPlay", 2, 0, 0x42AF9C), (0x83, "SEStop", 1, 0, 0x42B014),
    (0x84, "SEVol", 2, 0, 0x42B03C), (0x85, "SEPos", 3, 0, 0x42B080),
    (0x86, "EnvPlay", 2, 0, 0x42B0E0), (0x87, "EnvStop", 1, 0, 0x42B17C),
    (0x88, "EnvPlayQ", 2, 0, 0x42B1B0), (0x89, "EnvStopQ", 1, 0, 0x42B24C),
    (0x8A, "EnvVol", 2, 0, 0x42B280), (0x8B, "EnvPos", 3, 0, 0x42B2C4),
    (0x8C, "RainVol", 1, 0, 0x42B324), (0x8D, "SetRainPower", 1, 0, 0x42B34C),
    (0x8E, "GetRainPower", 0, 1, 0x42B380), (0x8F, "SetRainLevel", 1, 0, 0x42B39C),
    (0x90, "GetRainLevel", 0, 1, 0x42B3DC), (0x91, "AddRainPower", 1, 0, 0x42B3F8),
    (0x92, "SubRainPower", 1, 0, 0x42B43C), (0x93, "AddRainPowLv", 1, 0, 0x42B488),
    (0x94, "SubRainPowLv", 1, 0, 0x42B528), (0x99, "SetBG", 1, 0, 0x42B5B8),
    (0x9A, "SetWhite", 0, 0, 0x42B5D0), (0x9B, "SetBlack", 0, 0, 0x42B5DC),
    (0x9C, "Fade", 1, 0, 0x42B5E8), (0x9D, "FadeBG", 2, 0, 0x42B600),
    (0x9E, "WhiteOut", 1, 0, 0x42B630), (0x9F, "BlackOut", 1, 0, 0x42B658),
    (0xA0, "FadeSpeed", 1, 0, 0x42B680), (0xA1, "ActionChar", 0, 0, 0x42B69C),
    (0xA2, "SetShowChar", 4, 0, 0x42B6A4), (0xA3, "ShowCharLK", 2, 0, 0x42B744),
    (0xA4, "ShowCharUL", 2, 0, 0x42B794), (0xA5, "SetHideChar", 3, 0, 0x42B7E4),
    (0xA6, "HideCharLK", 1, 0, 0x42B868), (0xA7, "HideCharUL", 1, 0, 0x42B8AC),
    (0xA8, "SetMoveChar", 3, 0, 0x42B8F0), (0xA9, "MoveCharLK", 2, 0, 0x42B978),
    (0xAA, "MoveCharUL", 2, 0, 0x42B9EC), (0xAB, "AllMoveChar", 1, 0, 0x42BA60),
    (0xAC, "AllHideChar", 0, 0, 0x42BAA8), (0xAD, "SetChangeChar", 2, 0, 0x42BABC),
    (0xAE, "ChangeChar", 2, 0, 0x42BB04), (0xAF, "SetPriority", 1, 0, 0x42BB58),
    (0xB0, "Effect", 1, 0, 0x42BB74), (0xB5, "SpeakPos", 1, 0, 0x42BB98),
    (0xB6, "SpeakPosAll", 0, 0, 0x42BBD4), (0xB7, "SpeakClear", 0, 0, 0x42BBF8),
    (0xB8, "SpeakOn", 1, 0, 0x42BC1C), (0xB9, "SpeakOff", 0, 0, 0x42BC40),
    (0xBA, "SpeakChar", 1, 0, 0x42BC50),
]

for _opcode, _name, _pop, _push, _handler in _EXTENSIONS:
    _status = "runtime_pending" if _opcode in (0x50, 0x51, 0x52, 0x57) else "confirmed"
    OPCODES[_opcode] = _op(_name, stack=f"{_pop} -> {_push}", handler=_handler, semantic_status=_status)

for _opcode in (0x4A, 0x4B, 0x53, 0x54, 0x71, 0x72, 0x73, 0x74,
                0x95, 0x96, 0x97, 0x98, 0xB1, 0xB2, 0xB3, 0xB4):
    OPCODES[_opcode] = _op(f"INVALID_{_opcode:02X}", valid=False)


NAME_TO_OPCODE = {definition["mnemonic"]: opcode for opcode, definition in OPCODES.items()}
EXTENSION_METADATA = {
    opcode: {"mnemonic": OPCODES[opcode]["mnemonic"], "pop": int(OPCODES[opcode]["stack"].split()[0]),
             "push": int(OPCODES[opcode]["stack"].split()[-1]), "handler": OPCODES[opcode]["handler"]}
    for opcode in OPCODES if 0x40 <= opcode <= 0xBA and OPCODES[opcode]["valid"]
}


def instruction_length(opcode: int) -> int:
    """返回 uint16 opcode 在 VM 解码器中的指令长度。"""
    if opcode in (0x01, 0x03, 0x04, 0x05):
        return 6
    return 2


def get_opcode(mnemonic: str):
    opcode = NAME_TO_OPCODE.get(mnemonic)
    if opcode is None:
        raise KeyError(f"未找到官方 opcode 名称：{mnemonic}")
    return opcode, OPCODES[opcode]
