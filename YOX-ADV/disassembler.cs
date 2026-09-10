using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace YOX.ScriptTool
{
    internal sealed class DecodedOperand
    {
        public DecodedOperand(OperandSpec spec, byte kind, uint value)
        {
            Spec = spec;
            Kind = kind;
            Value = value;
        }

        public OperandSpec Spec { get; }
        public byte Kind { get; }
        public uint Value { get; }
    }

    internal sealed class DecodedInstruction
    {
        public DecodedInstruction(int offset, OpcodeDefinition definition, NativeDefinition? native, List<DecodedOperand> operands, int length)
        {
            Offset = offset;
            Definition = definition;
            Native = native;
            Operands = operands;
            Length = length;
        }

        public int Offset { get; }
        public OpcodeDefinition Definition { get; }
        public NativeDefinition? Native { get; }
        public List<DecodedOperand> Operands { get; }
        public int Length { get; }
    }

    internal static class Disassembler
    {
        private static readonly UTF8Encoding Utf8NoBom = new UTF8Encoding(false);

        public static void DisassembleFile(string inputPath, string outputPath, string encodingName)
        {
            byte[] data = File.ReadAllBytes(inputPath);
            string text = Disassemble(data, encodingName);
            File.WriteAllText(outputPath, text, Utf8NoBom);
        }

        public static string Disassemble(byte[] data, string encodingName)
        {
            Encoding encoding = EncodingSupport.GetStrictEncoding(encodingName);
            if (data.Length < 0x20)
                throw new InvalidDataException("文件短于 0x20 字节 YOX 头。 ");
            if (data[0] != (byte)'Y' || data[1] != (byte)'O' || data[2] != (byte)'X' || data[3] != 0)
                throw new InvalidDataException("YOX 魔数不正确。 ");

            uint version = ReadU32(data, 0x04);
            uint codeSizeU = ReadU32(data, 0x08);
            uint stringSizeU = ReadU32(data, 0x0C);
            if (codeSizeU > int.MaxValue || stringSizeU > int.MaxValue)
                throw new InvalidDataException("代码区或字符串池过大。 ");
            int codeSize = (int)codeSizeU;
            int stringSize = (int)stringSizeU;
            long expectedSize = 0x20L + codeSize + stringSize;
            if (expectedSize != data.Length)
                throw new InvalidDataException($"YOX 大小不一致：头部要求 {expectedSize} 字节，实际 {data.Length} 字节。 ");

            ushort[] timestamp = new ushort[6];
            for (int i = 0; i < timestamp.Length; i++)
                timestamp[i] = ReadU16(data, 0x10 + i * 2);
            uint compilerMeta = ReadU32(data, 0x1C);

            byte[] code = new byte[codeSize];
            byte[] strings = new byte[stringSize];
            Buffer.BlockCopy(data, 0x20, code, 0, codeSize);
            Buffer.BlockCopy(data, 0x20 + codeSize, strings, 0, stringSize);

            List<DecodedInstruction> instructions = DecodeCode(code);
            var boundaries = new HashSet<uint>(instructions.Select(i => (uint)i.Offset));
            var codeLabels = new SortedSet<uint> { 0 };

            foreach (DecodedInstruction instruction in instructions)
            {
                foreach (DecodedOperand operand in instruction.Operands)
                {
                    if (operand.Spec.Role == OperandRole.Address)
                    {
                        if (operand.Value == 0)
                            continue;
                        if (operand.Value >= codeSizeU || !boundaries.Contains(operand.Value))
                            throw new InvalidDataException($"0x{instruction.Offset:X8}: 代码目标 0x{operand.Value:X8} 不在指令边界。 ");
                        codeLabels.Add(operand.Value);
                    }
                    else if (IsStringReference(operand))
                    {
                        if (operand.Value >= stringSizeU)
                            throw new InvalidDataException($"0x{instruction.Offset:X8}: 字符串偏移 0x{operand.Value:X8} 越界。 ");
                    }
                }
            }

            var output = new StringBuilder(Math.Max(4096, data.Length * 2));
            output.AppendLine("; YOX semantic assembly");
            output.Append(".encoding \"").Append(encodingName).AppendLine("\"");
            output.Append(".version 0x").Append(version.ToString("X8", CultureInfo.InvariantCulture)).AppendLine();
            output.Append(".timestamp ").Append(string.Join(", ", timestamp.Select(v => v.ToString(CultureInfo.InvariantCulture)))).AppendLine();
            output.Append(".compiler_meta 0x").Append(compilerMeta.ToString("X8", CultureInfo.InvariantCulture)).AppendLine();
            output.AppendLine(".code");

            foreach (DecodedInstruction instruction in instructions)
            {
                if (codeLabels.Contains((uint)instruction.Offset))
                {
                    output.AppendLine();
                    output.Append("loc_").Append(instruction.Offset.ToString("X8", CultureInfo.InvariantCulture)).AppendLine(":");
                }

                output.Append("    ").Append(instruction.Definition.Mnemonic);
                List<string> operands = FormatOperands(instruction, strings, encoding);
                if (operands.Count != 0)
                    output.Append(' ').Append(string.Join(", ", operands));
                output.AppendLine();
            }

            return output.ToString();
        }

        private static List<DecodedInstruction> DecodeCode(byte[] code)
        {
            var result = new List<DecodedInstruction>();
            int offset = 0;
            while (offset < code.Length)
            {
                byte opcode = code[offset];
                if (!OpcodeList.ByCode.TryGetValue(opcode, out OpcodeDefinition? definition))
                    throw new InvalidDataException($"0x{offset:X8}: 未定义 Opcode 0x{opcode:X2}。 ");

                var operands = new List<DecodedOperand>();
                NativeDefinition? native = null;
                int length;
                if (opcode == 0x01)
                {
                    DecodedOperand nativeOperand = ReadOperand(code, offset + 1, definition.Operands[0], offset);
                    operands.Add(nativeOperand);
                    if (nativeOperand.Value > byte.MaxValue || !OpcodeList.NativeById.TryGetValue((byte)nativeOperand.Value, out native))
                        throw new InvalidDataException($"0x{offset:X8}: 未注册 Native ID 0x{nativeOperand.Value:X8}。 ");
                    if (native.InlineText)
                    {
                        operands.Add(ReadOperand(code, offset + 6, new OperandSpec("text", OperandRole.Text), offset));
                        length = 11;
                    }
                    else
                    {
                        length = 6;
                    }
                }
                else
                {
                    length = definition.Length;
                    for (int i = 0; i < definition.Operands.Count; i++)
                        operands.Add(ReadOperand(code, offset + 1 + i * 5, definition.Operands[i], offset));
                }

                if (offset + length > code.Length)
                    throw new InvalidDataException($"0x{offset:X8}: 指令越过代码区末尾。 ");
                result.Add(new DecodedInstruction(offset, definition, native, operands, length));
                offset += length;
            }

            if (offset != code.Length)
                throw new InvalidDataException("代码区未被完整覆盖。 ");
            return result;
        }

        private static DecodedOperand ReadOperand(byte[] code, int position, OperandSpec spec, int instructionOffset)
        {
            if (position < 0 || position + 5 > code.Length)
                throw new InvalidDataException($"0x{instructionOffset:X8}: 操作数越过代码区末尾。 ");
            byte kind = code[position];
            uint value = ReadU32(code, position + 1);
            if (!OpcodeList.IsKindAllowed(spec.Role, kind))
                throw new InvalidDataException($"0x{instructionOffset:X8}: 操作数 {spec.Name} 的 kind 0x{kind:X2} 不合法。 ");
            if (spec.Role != OperandRole.Any && kind == OpcodeList.KindRegister && value > 15)
                throw new InvalidDataException($"0x{instructionOffset:X8}: 寄存器编号 {value} 超出 R0–R15。 ");
            return new DecodedOperand(spec, kind, value);
        }

        private static bool IsStringReference(DecodedOperand operand)
        {
            if (operand.Spec.Role == OperandRole.Any)
                return false;
            return operand.Kind == OpcodeList.KindString || operand.Kind == OpcodeList.KindText;
        }

        private static List<string> FormatOperands(DecodedInstruction instruction, byte[] strings, Encoding encoding)
        {
            var result = new List<string>();
            int start = 0;
            if (instruction.Definition.Opcode == 0x01)
            {
                result.Add(instruction.Native!.Mnemonic);
                start = 1;
            }

            for (int i = start; i < instruction.Operands.Count; i++)
                result.Add(FormatOperand(instruction.Operands[i], strings, encoding));
            return result;
        }

        private static string FormatOperand(DecodedOperand operand, byte[] strings, Encoding encoding)
        {
            if (operand.Spec.Role != OperandRole.Any
                && (operand.Kind == OpcodeList.KindString || operand.Kind == OpcodeList.KindText))
            {
                int start = checked((int)operand.Value);
                int end = Array.IndexOf(strings, (byte)0, start);
                if (end < 0)
                    end = strings.Length;
                return "\"" + FormatText(strings, start, end - start, encoding) + "\"";
            }

            switch (operand.Kind)
            {
                case OpcodeList.KindImmediate:
                    return "0x" + operand.Value.ToString("X8", CultureInfo.InvariantCulture);
                case OpcodeList.KindRegister:
                    return "R" + operand.Value.ToString(CultureInfo.InvariantCulture);
                case OpcodeList.KindCodeAddress:
                    return operand.Value == 0
                        ? "0x00000000"
                        : "loc_" + operand.Value.ToString("X8", CultureInfo.InvariantCulture);
                default:
                    return "KIND_" + operand.Kind.ToString("X2", CultureInfo.InvariantCulture)
                        + "(0x" + operand.Value.ToString("X8", CultureInfo.InvariantCulture) + ")";
            }
        }

        private static string FormatText(byte[] bytes, int offset, int length, Encoding encoding)
        {
            var result = new StringBuilder(length);
            int end = offset + length;
            int position = offset;
            while (position < end)
            {
                if (TryDecodeUnit(bytes, position, end - position, encoding, out int consumed, out string? decoded))
                {
                    if (IsSafeText(decoded!))
                        result.Append(decoded);
                    else
                        AppendPlaceholder(result, bytes, position, consumed);
                    position += consumed;
                }
                else
                {
                    AppendPlaceholder(result, bytes, position, 1);
                    position++;
                }
            }
            return result.ToString();
        }

        private static bool TryDecodeUnit(byte[] bytes, int offset, int available, Encoding encoding, out int consumed, out string? decoded)
        {
            int maximum = Math.Min(8, available);
            for (int count = 1; count <= maximum; count++)
            {
                try
                {
                    string text = encoding.GetString(bytes, offset, count);
                    if (text.Length == 0)
                        continue;
                    byte[] roundTrip = encoding.GetBytes(text);
                    if (roundTrip.Length != count)
                        continue;
                    bool equal = true;
                    for (int i = 0; i < count; i++)
                    {
                        if (roundTrip[i] != bytes[offset + i])
                        {
                            equal = false;
                            break;
                        }
                    }
                    if (!equal)
                        continue;
                    consumed = count;
                    decoded = text;
                    return true;
                }
                catch (DecoderFallbackException)
                {
                }
                catch (EncoderFallbackException)
                {
                }
            }

            consumed = 0;
            decoded = null;
            return false;
        }

        private static bool IsSafeText(string text)
        {
            foreach (char c in text)
            {
                if (c == '"' || c == '{' || c == '}' || c == '\r' || c == '\n' || c == '\t' || c == 0x7F)
                    return false;
                UnicodeCategory category = char.GetUnicodeCategory(c);
                if (category == UnicodeCategory.Control
                    || category == UnicodeCategory.PrivateUse
                    || category == UnicodeCategory.Surrogate
                    || category == UnicodeCategory.OtherNotAssigned
                    || category == UnicodeCategory.LineSeparator
                    || category == UnicodeCategory.ParagraphSeparator)
                    return false;
            }
            return true;
        }

        private static void AppendPlaceholder(StringBuilder output, byte[] bytes, int offset, int count)
        {
            output.Append("{{");
            for (int i = 0; i < count; i++)
            {
                if (i != 0)
                    output.Append(':');
                output.Append(bytes[offset + i].ToString("X2", CultureInfo.InvariantCulture));
            }
            output.Append("}}");
        }

        private static ushort ReadU16(byte[] data, int offset)
            => BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(offset, 2));

        private static uint ReadU32(byte[] data, int offset)
            => BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(offset, 4));
    }

    internal static class EncodingSupport
    {
        static EncodingSupport()
        {
            Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
        }

        public static Encoding GetStrictEncoding(string name)
        {
            try
            {
                if (name.Equals("cp932", StringComparison.OrdinalIgnoreCase)
                    || name.Equals("windows-31j", StringComparison.OrdinalIgnoreCase)
                    || name.Equals("ms932", StringComparison.OrdinalIgnoreCase))
                    return Encoding.GetEncoding(932, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
                return Encoding.GetEncoding(name, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
            }
            catch (Exception ex) when (ex is ArgumentException || ex is NotSupportedException)
            {
                throw new InvalidDataException($"不支持文本编码：{name}", ex);
            }
        }
    }
}
