from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


COMMAND_PREFIX = b"M#N"
END_MARKER = b"M$"
TEXT_PREFIX = b"S"

TOKEN_N = "N"
TOKEN_A = "A"
CMP_TOKENS = {ord("<"), ord(">"), ord("="), ord("!")}


@dataclass(frozen=True)
class OpcodeSpec:
    opcode: int
    mnemonic: str
    operands: Tuple[str, ...] = ()
    control_flow: Optional[str] = None
    description: str = ""
    target_operands: Tuple[int, ...] = field(default_factory=tuple)


OPCODES: Dict[int, OpcodeSpec] = {
    0: OpcodeSpec(0, "LOAD_SCRIPT_ENTRY", ("N",), "entry"),
    1: OpcodeSpec(1, "LJAMP", ("N",), "record", target_operands=(0,)),
    2: OpcodeSpec(2, "SCALL", ("N",), "entry"),
    3: OpcodeSpec(3, "VAR_SET", ("A", "N/A")),
    4: OpcodeSpec(4, "VAR_ADD", ("A", "N/A", "N/A")),
    5: OpcodeSpec(5, "VAR_SUB", ("A", "N/A", "N/A")),
    6: OpcodeSpec(6, "VAR_MUL", ("A", "N/A", "N/A")),
    7: OpcodeSpec(7, "VAR_DIV", ("A", "N/A", "N/A")),
    8: OpcodeSpec(8, "IF_COMPARE_SKIP", ("N/A", "CMP", "N/A", "N"), "if"),
    9: OpcodeSpec(9, "CODE_ELSE", ("N",), "if"),
    10: OpcodeSpec(10, "CODE_ENDIF", ("N",), "if"),
    11: OpcodeSpec(11, "LJAMP_ENTRY_REC", ("N", "N"), "entry_record", target_operands=(1,)),
    12: OpcodeSpec(12, "BLD_DRAW", ("N", "N", "N", "N")),
    13: OpcodeSpec(13, "SCREEN_CLEAR_AND_PRESENT"),
    14: OpcodeSpec(14, "SCREEN_PRESENT"),
    15: OpcodeSpec(15, "BLD_LOAD_SHOW", ("N",)),
    16: OpcodeSpec(16, "BLD_ALLDEL_COLOR", ("N",)),
    17: OpcodeSpec(17, "SEL_START"),
    18: OpcodeSpec(18, "CASE", ("N",)),
    19: OpcodeSpec(19, "SEL_END", ("A", "N")),
    20: OpcodeSpec(20, "ANIM_START", ("N", "N", "N", "N")),
    21: OpcodeSpec(21, "SELJAMP_START"),
    22: OpcodeSpec(22, "CASEJAMP", ("N",)),
    23: OpcodeSpec(23, "SELJAMP_END", ("N",)),
    24: OpcodeSpec(24, "UI_FLUSH"),
    25: OpcodeSpec(25, "BLD_DELETE", ("N",)),
    26: OpcodeSpec(26, "BLD_EFFECT_COMMIT"),
    27: OpcodeSpec(27, "BLD_RESOURCE_RELEASE", ("N",)),
    28: OpcodeSpec(28, "DYNCLICKABLEMAP_SET", ("N", "A", "N")),
    29: OpcodeSpec(29, "IF_STATE_CLEAR"),
    30: OpcodeSpec(30, "UI_FLUSH_NO_ADVANCE"),
    31: OpcodeSpec(31, "SLEEPWAIT", ("N",)),
    32: OpcodeSpec(32, "EFFECT_TARGET", ("N",)),
    33: OpcodeSpec(33, "GRAPHICGROUP_RESTORE_ALL"),
    34: OpcodeSpec(34, "GRAPHICGROUP_RESET_HELPER"),
    35: OpcodeSpec(35, "GRAPHICGROUP_ALL", ("N",), "record", target_operands=(0,)),
    36: OpcodeSpec(36, "DYNCLICKABLEMAP", ("N", "N", "N")),
    37: OpcodeSpec(37, "DYNCLICKABLEMAP_ENABLE_ITEM", ("N", "N")),
    38: OpcodeSpec(38, "NOOP_38"),
    39: OpcodeSpec(39, "SETBGCOLOR", ("N",)),
    40: OpcodeSpec(40, "POPSEL", ("N", "N", "N"), "record", target_operands=(2,)),
    41: OpcodeSpec(41, "POPCASE", ("N",)),
    42: OpcodeSpec(42, "POPSEL_END", ("A", "N")),
    43: OpcodeSpec(43, "SPEAK_START", ("N",)),
    44: OpcodeSpec(44, "BGMSTART", ("N",)),
    45: OpcodeSpec(45, "SPEAK_STOP"),
    46: OpcodeSpec(46, "SPEAK_ALT_START", ("N",)),
    47: OpcodeSpec(47, "IGNOREGG_ON", ("N",), "record", target_operands=(0,)),
    48: OpcodeSpec(48, "IGNOREGG_OFF", ("N",)),
    49: OpcodeSpec(49, "IGNOREGG_CLEAR", ("N",), "record", target_operands=(0,)),
    50: OpcodeSpec(50, "CLICKWAIT", ("N", "N", "N"), "record", target_operands=(2,)),
    51: OpcodeSpec(51, "TEXT_AUTOMODE_OFF"),
    52: OpcodeSpec(52, "TEXT_AUTOMODE_ON"),
    53: OpcodeSpec(53, "TEXT_WINDOW_UPDATE"),
    54: OpcodeSpec(54, "RESET_CURSOR_54"),
    55: OpcodeSpec(55, "TEXT_WINDOW_COMMIT"),
    56: OpcodeSpec(56, "TEXT_ADVANCE"),
    57: OpcodeSpec(57, "TEXT_ADVANCE_FLUSH"),
    58: OpcodeSpec(58, "TEXT_WAIT_RESET"),
    59: OpcodeSpec(59, "SAVE_TITLE_SET"),
    60: OpcodeSpec(60, "STATE_TEXT_SET"),
    61: OpcodeSpec(61, "TEXT_CLEAR_BUFFERS"),
    62: OpcodeSpec(62, "CALL_RETURN"),
    63: OpcodeSpec(63, "SAVE_MODE", ("A",)),
    64: OpcodeSpec(64, "LOAD_MODE", ("A",)),
    65: OpcodeSpec(65, "LOAD_CONFIRM_YES"),
    66: OpcodeSpec(66, "SAVELOAD_MODE_1"),
    67: OpcodeSpec(67, "SAVELOAD_MODE_2"),
    68: OpcodeSpec(68, "SAVELOAD_MENU_BUILD", ("N",)),
    69: OpcodeSpec(69, "RESET_CURSOR_69", ("N",)),
    70: OpcodeSpec(70, "VOICE_ADVANCE_READY"),
    71: OpcodeSpec(71, "POST_CLOSE"),
    72: OpcodeSpec(72, "MOVIE", ("N", "N"), "record", target_operands=(1,)),
    73: OpcodeSpec(73, "MOVIE_END_CHECK"),
    74: OpcodeSpec(74, "SCENE_RESET_TO_ZERO"),
    75: OpcodeSpec(75, "SYS_STOP_MESSAGE"),
    76: OpcodeSpec(76, "RETURN_AND_CLEAR_ADVANCE"),
    77: OpcodeSpec(77, "LOCAL_ALLOC", ("N",)),
    78: OpcodeSpec(78, "TXTSIZE", ("N",)),
    79: OpcodeSpec(79, "REFFECT", ("N", "N")),
    80: OpcodeSpec(80, "GRAPHICGROUP_SHOW", ("N/A",), "record", target_operands=(0,)),
    81: OpcodeSpec(81, "GRAPHICGROUP_HIDE", ("N/A",), "record", target_operands=(0,)),
    82: OpcodeSpec(82, "GRAPHICGROUP_FORCE_SHOW"),
    83: OpcodeSpec(83, "GRAPHICGROUP_FORCE_HIDE"),
    84: OpcodeSpec(84, "GRAPHICGROUP_RESTORE_VISIBILITY"),
    85: OpcodeSpec(85, "SAVE_RAW_DATA"),
    86: OpcodeSpec(86, "LOAD_RAW_DATA"),
    87: OpcodeSpec(87, "VOICE_ADVANCE_READY_SOFT"),
    88: OpcodeSpec(88, "NOOP_88"),
    89: OpcodeSpec(89, "NOOP_89"),
    90: OpcodeSpec(90, "RBUTTON_POPCASE", ("N",)),
    91: OpcodeSpec(91, "POPSELJUMP", ("N", "N", "N"), "record", target_operands=(2,)),
    92: OpcodeSpec(92, "POPCASEJAMP", ("N",)),
    93: OpcodeSpec(93, "POPSELJAMP_END", ("N",)),
    94: OpcodeSpec(94, "RBUTTON_POPCASE_ALT", ("N",)),
    95: OpcodeSpec(95, "TEXT_WINDOW_MODE"),
    96: OpcodeSpec(96, "DYNCLICKABLEMAP_DRAW", ("N", "N")),
    97: OpcodeSpec(97, "SPEAK_PAUSE"),
    98: OpcodeSpec(98, "SPEAK_RESUME"),
    99: OpcodeSpec(99, "SPEAK_QUEUE_NEXT"),
    100: OpcodeSpec(100, "INPUT_BRANCH_WAIT_TIMED", ("N", "N", "N"), target_operands=(0, 1)),
    101: OpcodeSpec(101, "EXIT_REQUEST_SET"),
    102: OpcodeSpec(102, "EXIT_REQUEST_CLEAR"),
    103: OpcodeSpec(103, "INPUT_STATE_SAVE_AND_CLEAR"),
    104: OpcodeSpec(104, "INPUT_STATE_RESTORE"),
    105: OpcodeSpec(105, "GRAPHICGROUP_ITEM_JUMP", ("N", "N"), target_operands=(1,)),
    106: OpcodeSpec(106, "GRAPHICGROUP_JUMP", ("N",), target_operands=(0,)),
    107: OpcodeSpec(107, "INPUT_LOCK_ON"),
    108: OpcodeSpec(108, "INPUT_LOCK_OFF"),
    109: OpcodeSpec(109, "VOICE_CHANNEL_LOAD", ("N",)),
    110: OpcodeSpec(110, "GRAPHIC_EFFECT_MODE_3", ("N", "N", "N")),
    111: OpcodeSpec(111, "GRAPHIC_EFFECT_MODE_4", ("N", "N", "N")),
    112: OpcodeSpec(112, "OVERLAY_TEXTURE_SHOW", ("N",)),
    113: OpcodeSpec(113, "RESERVED_113"),
    114: OpcodeSpec(114, "AUDIO_SLOT3_SET", ("N",)),
    115: OpcodeSpec(115, "GRAPHICGROUP_ITEM_ACTION", ("N", "N")),
    116: OpcodeSpec(116, "UI_CANCEL_RESET"),
    117: OpcodeSpec(117, "EXIT_CONFIRM"),
    118: OpcodeSpec(118, "EXIT_CONFIRM_INFO_MODE_ON"),
    119: OpcodeSpec(119, "RETURN_CONFIRM"),
    120: OpcodeSpec(120, "OVERLAY_FLAG_ON"),
    121: OpcodeSpec(121, "OVERLAY_FLAG_OFF"),
    122: OpcodeSpec(122, "UI_RESOURCE_ID_SET", ("N",)),
    123: OpcodeSpec(123, "UI_RESOURCE_ID_CLEAR"),
    124: OpcodeSpec(124, "ARRAY_SET", ("N", "N/A", "N/A")),
    125: OpcodeSpec(125, "ARRAY_GET", ("N", "N/A", "A")),
    126: OpcodeSpec(126, "INTERACTION_STATE_RESET"),
    127: OpcodeSpec(127, "UI_MODE3_OPEN", ("N", "N", "N", "N")),
    128: OpcodeSpec(128, "UI_SELECTION_PREPARE", ("N", "N/A")),
    129: OpcodeSpec(129, "UI_SELECTION_COMMIT", ("A",)),
    130: OpcodeSpec(130, "WINDOW_TITLE_MODE_TOGGLE"),
    131: OpcodeSpec(131, "AUDIO_TRACK_PLAY_FADE", ("N", "N")),
    132: OpcodeSpec(132, "AUDIO_TRACK_STOP_FADE", ("N",)),
    133: OpcodeSpec(133, "RESERVED_133"),
    134: OpcodeSpec(134, "UI_MODE4_OPEN", ("N", "N", "N")),
    135: OpcodeSpec(135, "UI_MODE4_SELECT", ("N",)),
    136: OpcodeSpec(136, "UI_MODE4_COMMIT", ("A", "N")),
    137: OpcodeSpec(137, "TABLE_GET", ("N", "N/A", "N/A", "N/A")),
    138: OpcodeSpec(138, "AUDIO_TRACK_PLAY", ("N",)),
    139: OpcodeSpec(139, "SCRIPT_CONFIG_EXEC", ("STR", "N")),
    140: OpcodeSpec(140, "RESERVED_140"),
    141: OpcodeSpec(141, "RESERVED_141"),
    142: OpcodeSpec(142, "RESERVED_142"),
    143: OpcodeSpec(143, "WAIT_MILLISECONDS", ("N/A",)),
    144: OpcodeSpec(144, "STATE_TABLE_SET", ("N", "A")),
    145: OpcodeSpec(145, "LINEAR_INTERPOLATE", ("A", "N/A", "N/A", "N/A", "N/A")),
    146: OpcodeSpec(146, "RANDOM_RANGE", ("A", "N/A", "N/A")),
    147: OpcodeSpec(147, "ARRAY_COPY", ("N", "N")),
    148: OpcodeSpec(148, "SCREENSHOT_SAVE"),
    149: OpcodeSpec(149, "SCREENSHOT_BUFFER", ("N/A",)),
    150: OpcodeSpec(150, "AUDIO_SLOT4_SET", ("N",)),
    151: OpcodeSpec(151, "UI_STATE_1_TO_2"),
    152: OpcodeSpec(152, "UI_STATE_2_TO_1"),
    153: OpcodeSpec(153, "RETURN_TO_TITLE_CONFIRM"),
    154: OpcodeSpec(154, "SCENE_STATE_COMMIT"),
    155: OpcodeSpec(155, "UI_SUBSYSTEM_RESOURCE_SET", ("N",)),
    156: OpcodeSpec(156, "RENDER_REFRESH_REQUEST"),
    157: OpcodeSpec(157, "SYSTEM_MENU_STATE_INIT"),
    158: OpcodeSpec(158, "VOICE_STATE_RESTART"),
    159: OpcodeSpec(159, "SYSTEM_MENU_TEXTURES_LOAD"),
    160: OpcodeSpec(160, "RESERVED_160"),
    161: OpcodeSpec(161, "SCRIPT_HISTORY_NEXT", ("A",)),
    162: OpcodeSpec(162, "UI_STATE_FLAG_ON"),
    163: OpcodeSpec(163, "UI_STATE_FLAG_OFF"),
    164: OpcodeSpec(164, "AMBIENT_TEXTURE_SET", ("N",)),
    165: OpcodeSpec(165, "SCRIPT_VALUE_EVALUATE", ("N/A", "N")),
    167: OpcodeSpec(167, "INPUT_OR_TIMEOUT_WAIT", ("N/A",)),
}

MNEMONIC_TO_OPCODE = {spec.mnemonic: opcode for opcode, spec in OPCODES.items()}
MNEMONIC_TO_OPCODE["NOOP_60_TEXT_PAYLOAD"] = 60
MNEMONIC_TO_OPCODE["END_MARKER"] = 9999


def get_opcode_spec(opcode: int) -> Optional[OpcodeSpec]:
    return OPCODES.get(opcode)


def opcode_mnemonic(opcode: int) -> str:
    if opcode == 9999:
        return "END_MARKER"
    spec = get_opcode_spec(opcode)
    if spec is None:
        return f"OP_{opcode}"
    return spec.mnemonic


def opcode_by_mnemonic(mnemonic: str) -> Optional[int]:
    if mnemonic.startswith("OP_"):
        try:
            return int(mnemonic[3:], 0)
        except ValueError:
            return None
    return MNEMONIC_TO_OPCODE.get(mnemonic.upper())
