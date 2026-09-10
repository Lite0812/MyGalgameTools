using System;
using System.Collections.Generic;

namespace YOX.ScriptTool
{
    internal enum OperandRole
    {
        Any,
        Value,
        StackValue,
        Register,
        String,
        Address,
        Text,
        Immediate
    }

    internal sealed class OperandSpec
    {
        public OperandSpec(string name, OperandRole role)
        {
            Name = name;
            Role = role;
        }

        public string Name { get; }
        public OperandRole Role { get; }
    }

    internal sealed class OpcodeDefinition
    {
        public OpcodeDefinition(byte opcode, string mnemonic, params OperandSpec[] operands)
        {
            Opcode = opcode;
            Mnemonic = mnemonic;
            Operands = operands;
        }

        public byte Opcode { get; }
        public string Mnemonic { get; }
        public IReadOnlyList<OperandSpec> Operands { get; }
        public int Length => 1 + Operands.Count * 5;
    }

    internal sealed class NativeDefinition
    {
        public NativeDefinition(byte id, string mnemonic, uint handler, int popCount, int pushCount, bool inlineText = false)
        {
            Id = id;
            Mnemonic = mnemonic;
            Handler = handler;
            PopCount = popCount;
            PushCount = pushCount;
            InlineText = inlineText;
        }

        public byte Id { get; }
        public string Mnemonic { get; }
        public uint Handler { get; }
        public int PopCount { get; }
        public int PushCount { get; }
        public bool InlineText { get; }
    }

    internal static class OpcodeList
    {
        public const byte KindImmediate = 0x01;
        public const byte KindRegister = 0x02;
        public const byte KindString = 0x04;
        public const byte KindCodeAddress = 0x10;
        public const byte KindText = 0x40;

        private static OperandSpec O(string name, OperandRole role) => new OperandSpec(name, role);

        public static readonly IReadOnlyDictionary<byte, OpcodeDefinition> ByCode;
        public static readonly IReadOnlyDictionary<string, OpcodeDefinition> ByMnemonic;
        public static readonly IReadOnlyDictionary<byte, NativeDefinition> NativeById;
        public static readonly IReadOnlyDictionary<string, NativeDefinition> NativeByMnemonic;

        static OpcodeList()
        {
            var opcodes = new[]
            {
                new OpcodeDefinition(0x01, "CALL_NATIVE", O("native", OperandRole.Immediate)),
                new OpcodeDefinition(0x02, "PUSH_ADDR", O("target", OperandRole.Address)),
                new OpcodeDefinition(0x03, "PUSH1", O("value0", OperandRole.StackValue)),
                new OpcodeDefinition(0x04, "PUSH2", O("value0", OperandRole.StackValue), O("value1", OperandRole.StackValue)),
                new OpcodeDefinition(0x05, "PUSH3", O("value0", OperandRole.StackValue), O("value1", OperandRole.StackValue), O("value2", OperandRole.StackValue)),
                new OpcodeDefinition(0x06, "PUSH4", O("value0", OperandRole.StackValue), O("value1", OperandRole.StackValue), O("value2", OperandRole.StackValue), O("value3", OperandRole.StackValue)),
                new OpcodeDefinition(0x07, "POP_REG", O("dst", OperandRole.Register)),
                new OpcodeDefinition(0x08, "END"),
                new OpcodeDefinition(0x09, "TRACE_TEXT", O("text", OperandRole.String)),
                new OpcodeDefinition(0x0A, "TRACE_REGS", O("radix", OperandRole.Immediate)),
                new OpcodeDefinition(0x0B, "SKIP_ARG", O("operand", OperandRole.Any)),
                new OpcodeDefinition(0x0C, "FORMAT_REGS", O("radix", OperandRole.Immediate)),
                new OpcodeDefinition(0x0D, "RET_TASK"),
                new OpcodeDefinition(0x0E, "WAIT", O("ticks", OperandRole.Value)),
                new OpcodeDefinition(0x0F, "WAIT_SKIP", O("ticks", OperandRole.Value)),
                new OpcodeDefinition(0x10, "DJNZ", O("counter", OperandRole.Register), O("target", OperandRole.Address)),
                new OpcodeDefinition(0x11, "JMP", O("target", OperandRole.Address)),
                new OpcodeDefinition(0x12, "JE", O("lhs", OperandRole.Register), O("rhs", OperandRole.Value), O("target", OperandRole.Address)),
                new OpcodeDefinition(0x13, "JNE", O("lhs", OperandRole.Register), O("rhs", OperandRole.Value), O("target", OperandRole.Address)),
                new OpcodeDefinition(0x14, "JL", O("lhs", OperandRole.Register), O("rhs", OperandRole.Value), O("target", OperandRole.Address)),
                new OpcodeDefinition(0x15, "JLE", O("lhs", OperandRole.Register), O("rhs", OperandRole.Value), O("target", OperandRole.Address)),
                new OpcodeDefinition(0x16, "JG", O("lhs", OperandRole.Register), O("rhs", OperandRole.Value), O("target", OperandRole.Address)),
                new OpcodeDefinition(0x17, "JGE", O("lhs", OperandRole.Register), O("rhs", OperandRole.Value), O("target", OperandRole.Address)),
                new OpcodeDefinition(0x18, "SWITCH4", O("selector", OperandRole.Register), O("case0", OperandRole.Address), O("case1", OperandRole.Address), O("case2", OperandRole.Address), O("case3", OperandRole.Address)),
                new OpcodeDefinition(0x19, "CALL", O("target", OperandRole.Address)),
                new OpcodeDefinition(0x1B, "SPAWN_SCRIPT", O("link", OperandRole.Immediate), O("archive", OperandRole.String), O("rid", OperandRole.Value)),
                new OpcodeDefinition(0x1C, "FORK_TASK", O("link", OperandRole.Value), O("entry", OperandRole.Address)),
                new OpcodeDefinition(0x1D, "KILL_TASK_LINK", O("link", OperandRole.Value)),
                new OpcodeDefinition(0x1E, "CALL_SCRIPT", O("archive", OperandRole.String), O("rid", OperandRole.Value)),
                new OpcodeDefinition(0x20, "MOV", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x21, "ADD", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x22, "SUB", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x23, "MUL", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x24, "DIV", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x25, "MOD", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x26, "SET_ADD", O("dst", OperandRole.Register), O("a", OperandRole.Value), O("b", OperandRole.Value)),
                new OpcodeDefinition(0x27, "ADD_CLAMP_MAX", O("dst", OperandRole.Register), O("delta", OperandRole.Value), O("max", OperandRole.Value)),
                new OpcodeDefinition(0x28, "SUB_CLAMP_MIN", O("dst", OperandRole.Register), O("delta", OperandRole.Value), O("min", OperandRole.Value)),
                new OpcodeDefinition(0x29, "ADD_MOD", O("dst", OperandRole.Register), O("delta", OperandRole.Value), O("modulo", OperandRole.Value)),
                new OpcodeDefinition(0x2A, "CLAMP", O("dst", OperandRole.Register), O("min", OperandRole.Value), O("max", OperandRole.Value)),
                new OpcodeDefinition(0x30, "OR", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x31, "AND", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x32, "XOR", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x33, "NOT", O("dst", OperandRole.Register), O("src", OperandRole.Value)),
                new OpcodeDefinition(0x34, "SHL", O("dst", OperandRole.Register), O("bits", OperandRole.Value)),
                new OpcodeDefinition(0x35, "SAR", O("dst", OperandRole.Register), O("bits", OperandRole.Value)),
                new OpcodeDefinition(0x3A, "RAND", O("dst", OperandRole.Register)),
                new OpcodeDefinition(0x3B, "SIN4096", O("degrees", OperandRole.Value), O("dst", OperandRole.Register)),
                new OpcodeDefinition(0x3C, "COS4096", O("degrees", OperandRole.Value), O("dst", OperandRole.Register)),
                new OpcodeDefinition(0x3F, "TEXT", O("text", OperandRole.Text))
            };

            var byCode = new Dictionary<byte, OpcodeDefinition>();
            var byMnemonic = new Dictionary<string, OpcodeDefinition>(StringComparer.OrdinalIgnoreCase);
            foreach (var opcode in opcodes)
            {
                byCode.Add(opcode.Opcode, opcode);
                byMnemonic.Add(opcode.Mnemonic, opcode);
            }

            ByCode = byCode;
            ByMnemonic = byMnemonic;

            var natives = new[]
            {
                N(0x3F, "DISPLAY_TEXT_INLINE", 0x41D0C0, 0, 0, true),
                N(0x40, "MESSAGE_SET_FLAG_1000", 0x41D4F0, 1, 0),
                N(0x41, "MESSAGE_SET_FONT_CONFIG", 0x41D540, 5, 0),
                N(0x42, "MESSAGE_SET_MODE_FLAGS", 0x41D590, 4, 0),
                N(0x43, "MESSAGE_SET_LAYOUT_FIELDS", 0x41D5E0, 4, 0),
                N(0x44, "MESSAGE_SET_CONTROL_FIELDS", 0x41D630, 3, 0),
                N(0x45, "NAMEPLATE_SET_TEXT", 0x41D6C0, 1, 0),
                N(0x46, "CHOICE_SET_STATE", 0x41D8F0, 2, 0),
                N(0x47, "CHOICE_SET_RECT", 0x41D980, 5, 0),
                N(0x48, "MESSAGE_SET_FLAG_2000_INVERTED", 0x41DA50, 1, 0),
                N(0x49, "MESSAGE_SET_WINDOW_MODE", 0x41DA90, 2, 0),
                N(0x4A, "MESSAGE_LINK_LAYER_PAIR", 0x41DAF0, 5, 0),
                N(0x4B, "MESSAGE_SET_RGB_SLOT", 0x41DB90, 4, 0),
                N(0x4C, "MESSAGE_SET_STRING_SLOT_64", 0x41DBE0, 2, 0),
                N(0x4D, "MESSAGE_SET_INTEGER_SLOT", 0x41DC40, 2, 0),
                N(0x4E, "MESSAGE_SET_RENDER_FIELDS", 0x41DC80, 3, 0),
                N(0x4F, "MESSAGE_SET_LABEL_SLOT_16", 0x41DCC0, 2, 0),
                N(0x50, "MESSAGE_SET_ITEM", 0x41DD10, 3, 0),
                N(0x51, "MESSAGE_BUFFER_SET_HEAD", 0x41DD70, 1, 0),
                N(0x52, "MESSAGE_BUFFER_CLEAR", 0x41DE00, 1, 0),
                N(0x53, "MESSAGE_SET_RECORD_FIELDS", 0x41DE80, 5, 0),
                N(0x54, "TIMER_SET", 0x41F130, 2, 0),
                N(0x55, "TIMER_BRANCH_WHILE_BELOW", 0x41F170, 3, 0),
                N(0x56, "TIMER_GET_ELAPSED", 0x41F1D0, 1, 1),
                N(0x57, "NAMEPLATE_SET_TARGET", 0x41D670, 3, 0),
                N(0x58, "MESSAGE_SET_DWORD_SLOT", 0x41D8B0, 2, 0),
                N(0x59, "CHOICE_SET_TIMEOUT_60HZ", 0x41D9F0, 1, 0),
                N(0x5A, "CURSOR_LOAD", 0x41CBE0, 1, 0),
                N(0x5B, "CURSOR_UNLOAD", 0x41CCE0, 1, 0),
                N(0x5C, "BUTTON_SET_MODE", 0x41CD20, 2, 0),
                N(0x5D, "BUTTON_LAYER_CONFIGURE", 0x41CD60, 6, 0),
                N(0x5E, "BUTTON_LAYER_SET_PAIR", 0x41CE30, 4, 0),
                N(0x5F, "BUTTON_LAYER_GET_ACTIVE", 0x41CE90, 0, 1),
                N(0x60, "SYSTEM_FLAG_GET", 0x41E240, 1, 1),
                N(0x61, "SYSTEM_FLAG_SET_RANGE", 0x41E290, 4, 0),
                N(0x62, "SYSTEM_VALUE_GET", 0x41E2F0, 1, 1),
                N(0x63, "SYSTEM_VALUE_SET_RANGE", 0x41E340, 4, 0),
                N(0x64, "GAME_FLAG_GET", 0x41E3A0, 1, 1),
                N(0x65, "GAME_FLAG_SET_RANGE", 0x41E3F0, 3, 0),
                N(0x66, "GAME_VALUE_GET", 0x41E440, 1, 1),
                N(0x67, "GAME_VALUE_SET_RANGE", 0x41E490, 3, 0),
                N(0x68, "SYSTEM_DATA_SAVE", 0x41E4E0, 1, 0),
                N(0x69, "GAME_DATA_SAVE_SLOT", 0x41E510, 1, 0),
                N(0x6A, "GAME_SAVE_NAME_SET", 0x41E550, 2, 0),
                N(0x6B, "GAME_VALUE_MAX_INDEX", 0x41E5B0, 2, 1),
                N(0x6C, "GAME_VALUE_MIN_INDEX", 0x41E630, 2, 1),
                N(0x6E, "GAME_VALUE_SET_PAIR", 0x41E6B0, 3, 0),
                N(0x6F, "GAME_VALUE_SET_TRIPLE", 0x41E700, 4, 0),
                N(0x70, "GRAPHIC_LOAD", 0x41B0E0, 6, 0),
                N(0x71, "GRAPHIC_UNLOAD", 0x41B1A0, 1, 0),
                N(0x72, "LAYER_SET_ENABLED", 0x41B1F0, 2, 0),
                N(0x73, "LAYER_SET_TYPE", 0x41B230, 2, 0),
                N(0x74, "TWEEN_SET_FOUR_CHANNELS", 0x41B270, 7, 0),
                N(0x75, "LAYER_TWEEN_POSITION", 0x41B2D0, 6, 0),
                N(0x76, "GRAPHIC_MOVE_SLOT", 0x41B400, 2, 0),
                N(0x77, "TWEEN_SET_PRIMARY_GROUP", 0x41B4E0, 6, 0),
                N(0x78, "TWEEN_SET_SECONDARY_GROUP", 0x41B560, 6, 0),
                N(0x79, "LAYER_TWEEN_COLOR", 0x41B5E0, 5, 0),
                N(0x7A, "LAYER_SET_BLEND_MODE", 0x41B700, 2, 0),
                N(0x7B, "LAYER_TWEEN_COMPONENT", 0x41B740, 6, 0),
                N(0x7C, "LAYER_ATTACH_CHILD", 0x41B7B0, 9, 0),
                N(0x7D, "LAYER_SET_TEXTURE_RECT", 0x41B960, 5, 0),
                N(0x7E, "LAYER_RESET_TWEEN", 0x41BA00, 2, 0),
                N(0x7F, "TWEEN_SET_FLAG2", 0x41BA40, 3, 0),
                N(0x80, "LAYER_QUERY_RESOURCE", 0x41BA80, 1, 2),
                N(0x81, "GRAPHIC_GET_SIZE", 0x41BB10, 1, 2),
                N(0x82, "LAYER_GET_POSITION", 0x41BB70, 2, 2),
                N(0x84, "LAYER_COPY_TRANSFORM", 0x41BC10, 2, 0),
                N(0x85, "LAYER_SET_POSITION_PRIMARY", 0x41BDD0, 3, 0),
                N(0x86, "LAYER_SET_POSITION_SECONDARY", 0x41BE10, 3, 0),
                N(0x87, "GRAPHIC_SET_ACTIVE_SLOT", 0x41BE50, 1, 0),
                N(0x88, "GRAPHIC_SET_GLOBAL_RECT", 0x41BE80, 4, 0),
                N(0x89, "SCREEN_EFFECT_ENABLE", 0x41BED0, 2, 0),
                N(0x8A, "TWEEN_START", 0x41BF10, 4, 0),
                N(0x8B, "SCREEN_EFFECT_SET_COLOR", 0x41BF70, 3, 0),
                N(0x8C, "VFX_CREATE_PRESET", 0x41BFB0, 7, 0),
                N(0x8D, "VFX_UPDATE", 0x41C230, 6, 0),
                N(0x8E, "VFX_SET_GLOBAL", 0x41C290, 1, 0),
                N(0x8F, "TWEEN_START_SCOPE", 0x41C2C0, 5, 0),
                N(0x90, "AUDIO_UNLOAD", 0x41E8A0, 1, 0),
                N(0x91, "AUDIO_LOAD_PLAY_EXTENDED", 0x41E8E0, 8, 0),
                N(0x92, "AUDIO_LOAD_PLAY_ACTIVE", 0x41E960, 6, 0),
                N(0x93, "VOICE_PLAY_AND_MARK", 0x41E9E0, 3, 0),
                N(0x94, "AUDIO_SET_CHANNEL_MODE", 0x41EAD0, 2, 0),
                N(0x95, "AUDIO_SET_CHANNEL_MODE_TIMED", 0x41EB10, 3, 0),
                N(0x96, "AUDIO_POLL_CHANNEL", 0x41EB90, 1, 0),
                N(0x98, "VIDEO_PLAY", 0x41E090, 2, 0),
                N(0xA0, "ENGINE_SWITCH_STATE", 0x41EC60, 1, 0),
                N(0xA1, "TWEEN_START_AND_SCALE_TIME", 0x41ECC0, 4, 0),
                N(0xA2, "ENGINE_SET_RUN_STATE", 0x41ED60, 1, 0),
                N(0xA3, "ENGINE_QUERY_MODE", 0x41EDA0, 1, 1),
                N(0xA4, "PROCESS_RUN_WAIT", 0x41EE50, 2, 1),
                N(0xA5, "WORK_MEMORY_REALLOC_MB", 0x41EF50, 1, 0),
                N(0xA8, "DIALOG_OPEN_AND_SWITCH_STATE", 0x41EF90, 4, 0),
                N(0xA9, "BUTTON_SET_STATE", 0x41F010, 2, 0),
                N(0xB0, "COMPAT_VALIDATE_STRING_MODE_YIELD", 0x41C940, 4, 0),
                N(0xB1, "COMPAT_DISCARD1_FIRST", 0x41C9B0, 1, 0),
                N(0xB2, "COMPAT_DISCARD3_FIRST", 0x41C9D0, 3, 0),
                N(0xB3, "COMPAT_DISCARD1_SECOND", 0x41C9B0, 1, 0),
                N(0xB4, "COMPAT_DISCARD3_SECOND", 0x41C9D0, 3, 0),
                N(0xB5, "COMPAT_VALIDATE_STRING", 0x41CA00, 3, 0),
                N(0xB6, "COMPAT_VALIDATE_SIX_ARGS_FIRST", 0x41CA50, 6, 0),
                N(0xB7, "COMPAT_VALIDATE_SIX_ARGS_SECOND", 0x41CA50, 6, 0),
                N(0xB8, "COMPAT_VALIDATE_SIX_ARGS_THIRD", 0x41CA50, 6, 0),
                N(0xB9, "COMPAT_DISCARD5", 0x41CAD0, 5, 0),
                N(0xC0, "TWEEN_SET_CONTEXT", 0x41C3A0, 1, 0),
                N(0xC1, "TWEEN_START_PAIR_FIRST", 0x41C3D0, 6, 0),
                N(0xC2, "TWEEN_START_SINGLE", 0x41C4A0, 5, 0),
                N(0xC3, "TWEEN_START_PAIR_SECOND", 0x41C530, 6, 0),
                N(0xC8, "TWEEN_GET_PAIR_VALUES", 0x41C600, 1, 2)
            };

            var nativeById = new Dictionary<byte, NativeDefinition>();
            var nativeByMnemonic = new Dictionary<string, NativeDefinition>(StringComparer.OrdinalIgnoreCase);
            foreach (var native in natives)
            {
                nativeById.Add(native.Id, native);
                nativeByMnemonic.Add(native.Mnemonic, native);
            }

            NativeById = nativeById;
            NativeByMnemonic = nativeByMnemonic;
        }

        private static NativeDefinition N(byte id, string mnemonic, uint handler, int pop, int push, bool inline = false)
            => new NativeDefinition(id, mnemonic, handler, pop, push, inline);

        public static bool IsKindAllowed(OperandRole role, byte kind)
        {
            switch (role)
            {
                case OperandRole.Any:
                    return true;
                case OperandRole.Immediate:
                    return kind == KindImmediate;
                case OperandRole.Value:
                    return kind == KindImmediate || kind == KindRegister;
                case OperandRole.StackValue:
                    return kind == KindImmediate || kind == KindRegister || kind == KindString;
                case OperandRole.Register:
                    return kind == KindRegister;
                case OperandRole.String:
                    return kind == KindString;
                case OperandRole.Address:
                    return kind == KindCodeAddress;
                case OperandRole.Text:
                    return kind == KindText;
                default:
                    return false;
            }
        }
    }
}
