using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace YOX.ScriptTool
{
    internal sealed class SourceInstruction
    {
        public SourceInstruction(int lineNumber, int offset, OpcodeDefinition definition, NativeDefinition? native, string[] operands, int length)
        {
            LineNumber = lineNumber;
            Offset = offset;
            Definition = definition;
            Native = native;
            Operands = operands;
            Length = length;
        }

        public int LineNumber { get; }
        public int Offset { get; }
        public OpcodeDefinition Definition { get; }
        public NativeDefinition? Native { get; }
        public string[] Operands { get; }
        public int Length { get; }
    }

    internal readonly struct EncodedOperand
    {
        public EncodedOperand(byte kind, uint value)
        {
            Kind = kind;
            Value = value;
        }

        public byte Kind { get; }
        public uint Value { get; }
    }

    internal static class Assembler
    {
        public static void AssembleFile(string inputPath, string outputPath, string? encodingOverride)
        {
            string text = File.ReadAllText(inputPath, Encoding.UTF8);
            byte[] data = Assemble(text, encodingOverride);
            File.WriteAllBytes(outputPath, data);
        }

        public static byte[] Assemble(string text, string? encodingOverride)
        {
            string[] lines = text.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n').Split('\n');
            string encodingName = encodingOverride ?? FindEncodingDirective(lines) ?? "cp932";
            Encoding encoding = EncodingSupport.GetStrictEncoding(encodingName);

            uint? version = null;
            uint? compilerMeta = null;
            ushort[]? timestamp = null;
            string section = string.Empty;
            int codeOffset = 0;
            int stringOffset = 0;
            var instructions = new List<SourceInstruction>();
            var codeLabels = new Dictionary<string, uint>(StringComparer.OrdinalIgnoreCase);
            var stringLabels = new Dictionary<string, uint>(StringComparer.OrdinalIgnoreCase);
            var allLabels = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            using var stringData = new MemoryStream();

            for (int index = 0; index < lines.Length; index++)
            {
                int lineNumber = index + 1;
                string line = StripComment(lines[index]).Trim();
                if (line.Length == 0)
                    continue;

                if (line[0] == '.')
                {
                    if (line.Equals(".code", StringComparison.OrdinalIgnoreCase))
                    {
                        section = "code";
                        continue;
                    }
                    if (line.Equals(".strings", StringComparison.OrdinalIgnoreCase))
                    {
                        section = "strings";
                        continue;
                    }
                    if (line.StartsWith(".encoding", StringComparison.OrdinalIgnoreCase))
                    {
                        ParseQuotedDirective(line, ".encoding", lineNumber);
                        continue;
                    }
                    if (line.StartsWith(".version", StringComparison.OrdinalIgnoreCase))
                    {
                        version = ParseUInt32Bits(DirectiveArgument(line, ".version", lineNumber), lineNumber);
                        continue;
                    }
                    if (line.StartsWith(".compiler_meta", StringComparison.OrdinalIgnoreCase))
                    {
                        compilerMeta = ParseUInt32Bits(DirectiveArgument(line, ".compiler_meta", lineNumber), lineNumber);
                        continue;
                    }
                    if (line.StartsWith(".timestamp", StringComparison.OrdinalIgnoreCase))
                    {
                        string[] values = SplitOperands(DirectiveArgument(line, ".timestamp", lineNumber));
                        if (values.Length != 6)
                            throw Error(lineNumber, ".timestamp 必须有 6 个 u16 值。 ");
                        timestamp = new ushort[6];
                        for (int i = 0; i < values.Length; i++)
                        {
                            uint value = ParseUInt32Bits(values[i], lineNumber);
                            if (value > ushort.MaxValue)
                                throw Error(lineNumber, ".timestamp 值超出 u16。 ");
                            timestamp[i] = (ushort)value;
                        }
                        continue;
                    }
                    if (line.StartsWith(".byte", StringComparison.OrdinalIgnoreCase))
                    {
                        if (!section.Equals("strings", StringComparison.OrdinalIgnoreCase))
                            throw Error(lineNumber, ".byte 只允许用于字符串/数据区。 ");
                        string[] values = SplitOperands(DirectiveArgument(line, ".byte", lineNumber));
                        foreach (string valueText in values)
                        {
                            uint value = ParseUInt32Bits(valueText, lineNumber);
                            if (value > byte.MaxValue)
                                throw Error(lineNumber, ".byte 值超出 0..255。 ");
                            stringData.WriteByte((byte)value);
                            stringOffset++;
                        }
                        continue;
                    }
                    throw Error(lineNumber, $"未知伪指令：{line}");
                }

                if (line.EndsWith(":", StringComparison.Ordinal))
                {
                    string label = line.Substring(0, line.Length - 1).Trim();
                    ValidateLabel(label, lineNumber);
                    if (!allLabels.Add(label))
                        throw Error(lineNumber, $"标签重复：{label}");
                    if (section.Equals("code", StringComparison.OrdinalIgnoreCase))
                        codeLabels.Add(label, (uint)codeOffset);
                    else if (section.Equals("strings", StringComparison.OrdinalIgnoreCase))
                        stringLabels.Add(label, (uint)stringOffset);
                    else
                        throw Error(lineNumber, "标签必须位于 .code 或 .strings 区。 ");
                    continue;
                }

                if (section.Equals("code", StringComparison.OrdinalIgnoreCase))
                {
                    ParseCodeLine(line, lineNumber, codeOffset, out SourceInstruction instruction);
                    instructions.Add(instruction);
                    codeOffset = checked(codeOffset + instruction.Length);
                }
                else if (section.Equals("strings", StringComparison.OrdinalIgnoreCase))
                {
                    if (!line.StartsWith("TEXT", StringComparison.OrdinalIgnoreCase)
                        || (line.Length > 4 && !char.IsWhiteSpace(line[4])))
                        throw Error(lineNumber, "字符串区只接受 TEXT 或 .byte。 ");
                    string literal = ParseTextLiteral(line, lineNumber);
                    byte[] bytes = EncodeTextLiteral(literal, encoding, lineNumber);
                    stringData.Write(bytes, 0, bytes.Length);
                    stringOffset = checked(stringOffset + bytes.Length);
                }
                else
                {
                    throw Error(lineNumber, "内容必须位于 .code 或 .strings 区。 ");
                }
            }

            if (!version.HasValue)
                throw new InvalidDataException("缺少 .version。 ");
            if (timestamp == null)
                throw new InvalidDataException("缺少 .timestamp。 ");
            if (!compilerMeta.HasValue)
                throw new InvalidDataException("缺少 .compiler_meta。 ");

            byte[] code = new byte[codeOffset];
            using var rebuiltStrings = new MemoryStream();
            stringData.Position = 0;
            stringData.CopyTo(rebuiltStrings);
            foreach (SourceInstruction instruction in instructions)
                EncodeInstruction(code, instruction, codeLabels, stringLabels, rebuiltStrings, encoding);
            byte[] strings = rebuiltStrings.ToArray();

            byte[] result = new byte[checked(0x20 + code.Length + strings.Length)];
            result[0] = (byte)'Y';
            result[1] = (byte)'O';
            result[2] = (byte)'X';
            result[3] = 0;
            WriteU32(result, 0x04, version.Value);
            WriteU32(result, 0x08, (uint)code.Length);
            WriteU32(result, 0x0C, (uint)strings.Length);
            for (int i = 0; i < timestamp.Length; i++)
                WriteU16(result, 0x10 + i * 2, timestamp[i]);
            WriteU32(result, 0x1C, compilerMeta.Value);
            Buffer.BlockCopy(code, 0, result, 0x20, code.Length);
            Buffer.BlockCopy(strings, 0, result, 0x20 + code.Length, strings.Length);
            return result;
        }

        private static string? FindEncodingDirective(string[] lines)
        {
            for (int index = 0; index < lines.Length; index++)
            {
                string line = StripComment(lines[index]).Trim();
                if (line.StartsWith(".encoding", StringComparison.OrdinalIgnoreCase))
                    return ParseQuotedDirective(line, ".encoding", index + 1);
            }
            return null;
        }

        private static void ParseCodeLine(string line, int lineNumber, int offset, out SourceInstruction instruction)
        {
            int split = 0;
            while (split < line.Length && !char.IsWhiteSpace(line[split]))
                split++;
            string mnemonic = line.Substring(0, split);
            string operandText = split < line.Length ? line.Substring(split).Trim() : string.Empty;
            string[] operands = operandText.Length == 0 ? Array.Empty<string>() : SplitOperands(operandText);

            if (!OpcodeList.ByMnemonic.TryGetValue(mnemonic, out OpcodeDefinition? definition))
                throw Error(lineNumber, $"未知助记符：{mnemonic}");

            NativeDefinition? native = null;
            int length = definition.Length;
            if (definition.Opcode == 0x01)
            {
                if (operands.Length < 1)
                    throw Error(lineNumber, "CALL_NATIVE 缺少语义 Native 名称。 ");
                native = ParseNative(operands[0], lineNumber);
                int expected = native.InlineText ? 2 : 1;
                if (operands.Length != expected)
                    throw Error(lineNumber, $"CALL_NATIVE {native.Mnemonic} 需要 {expected} 个操作数。 ");
                length = native.InlineText ? 11 : 6;
            }
            else if (operands.Length != definition.Operands.Count)
            {
                throw Error(lineNumber, $"{definition.Mnemonic} 需要 {definition.Operands.Count} 个操作数，实际 {operands.Length}。 ");
            }

            instruction = new SourceInstruction(lineNumber, offset, definition, native, operands, length);
        }

        private static void EncodeInstruction(
            byte[] code,
            SourceInstruction instruction,
            Dictionary<string, uint> codeLabels,
            Dictionary<string, uint> stringLabels,
            MemoryStream stringData,
            Encoding encoding)
        {
            int position = instruction.Offset;
            code[position++] = instruction.Definition.Opcode;
            if (instruction.Definition.Opcode == 0x01)
            {
                WriteOperand(code, position, new EncodedOperand(OpcodeList.KindImmediate, instruction.Native!.Id));
                position += 5;
                if (instruction.Native.InlineText)
                {
                    EncodedOperand operand = ParseOperand(instruction.Operands[1], OperandRole.Text, instruction.LineNumber, codeLabels, stringLabels, stringData, encoding);
                    WriteOperand(code, position, operand);
                }
                return;
            }

            for (int i = 0; i < instruction.Definition.Operands.Count; i++)
            {
                OperandSpec spec = instruction.Definition.Operands[i];
                EncodedOperand operand = ParseOperand(instruction.Operands[i], spec.Role, instruction.LineNumber, codeLabels, stringLabels, stringData, encoding);
                WriteOperand(code, position, operand);
                position += 5;
            }
        }

        private static EncodedOperand ParseOperand(
            string token,
            OperandRole role,
            int lineNumber,
            Dictionary<string, uint> codeLabels,
            Dictionary<string, uint> stringLabels,
            MemoryStream stringData,
            Encoding encoding)
        {
            token = token.Trim();
            if (role == OperandRole.Any && TryParseRawKind(token, lineNumber, out EncodedOperand raw))
                return raw;

            if (role == OperandRole.Register)
                return new EncodedOperand(OpcodeList.KindRegister, ParseRegister(token, lineNumber));
            if (role == OperandRole.Immediate)
                return new EncodedOperand(OpcodeList.KindImmediate, ParseUInt32Bits(token, lineNumber));
            if (role == OperandRole.Address)
            {
                if (IsNumeric(token))
                {
                    uint value = ParseUInt32Bits(token, lineNumber);
                    if (value != 0)
                        throw Error(lineNumber, "非零 CODEADDR 必须使用代码标签。 ");
                    return new EncodedOperand(OpcodeList.KindCodeAddress, 0);
                }
                if (!codeLabels.TryGetValue(token, out uint target))
                    throw Error(lineNumber, $"未定义代码标签：{token}");
                return new EncodedOperand(OpcodeList.KindCodeAddress, target);
            }
            if (role == OperandRole.String || role == OperandRole.Text)
            {
                if (IsQuotedLiteral(token))
                    return AppendInlineString(token, role == OperandRole.Text ? OpcodeList.KindText : OpcodeList.KindString, lineNumber, stringData, encoding);
                if (!stringLabels.TryGetValue(token, out uint target))
                    throw Error(lineNumber, $"字符串操作数必须使用双引号文本：{token}");
                return new EncodedOperand(role == OperandRole.Text ? OpcodeList.KindText : OpcodeList.KindString, target);
            }
            if (role == OperandRole.Value)
            {
                if (LooksLikeRegister(token))
                    return new EncodedOperand(OpcodeList.KindRegister, ParseRegister(token, lineNumber));
                return new EncodedOperand(OpcodeList.KindImmediate, ParseUInt32Bits(token, lineNumber));
            }
            if (role == OperandRole.StackValue)
            {
                if (LooksLikeRegister(token))
                    return new EncodedOperand(OpcodeList.KindRegister, ParseRegister(token, lineNumber));
                if (IsQuotedLiteral(token))
                    return AppendInlineString(token, OpcodeList.KindString, lineNumber, stringData, encoding);
                if (stringLabels.TryGetValue(token, out uint target))
                    return new EncodedOperand(OpcodeList.KindString, target);
                return new EncodedOperand(OpcodeList.KindImmediate, ParseUInt32Bits(token, lineNumber));
            }
            if (role == OperandRole.Any)
            {
                if (LooksLikeRegister(token))
                    return new EncodedOperand(OpcodeList.KindRegister, ParseRegister(token, lineNumber));
                if (IsQuotedLiteral(token))
                    return AppendInlineString(token, OpcodeList.KindString, lineNumber, stringData, encoding);
                if (stringLabels.TryGetValue(token, out uint stringTarget))
                    return new EncodedOperand(OpcodeList.KindString, stringTarget);
                if (codeLabels.TryGetValue(token, out uint codeTarget))
                    return new EncodedOperand(OpcodeList.KindCodeAddress, codeTarget);
                return new EncodedOperand(OpcodeList.KindImmediate, ParseUInt32Bits(token, lineNumber));
            }
            throw Error(lineNumber, $"无法编码操作数：{token}");
        }

        private static NativeDefinition ParseNative(string token, int lineNumber)
        {
            if (OpcodeList.NativeByMnemonic.TryGetValue(token, out NativeDefinition? native))
                return native;
            if (IsNumeric(token))
            {
                uint id = ParseUInt32Bits(token, lineNumber);
                if (id <= byte.MaxValue && OpcodeList.NativeById.TryGetValue((byte)id, out native))
                    return native;
                throw Error(lineNumber, $"未注册 Native ID：0x{id:X8}");
            }
            throw Error(lineNumber, $"未知 Native 助记符：{token}");
        }

        private static bool TryParseRawKind(string token, int lineNumber, out EncodedOperand operand)
        {
            if (token.Length >= 12
                && token.StartsWith("KIND_", StringComparison.OrdinalIgnoreCase)
                && token[7] == '('
                && token[token.Length - 1] == ')')
            {
                string kindText = token.Substring(5, 2);
                if (!byte.TryParse(kindText, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out byte kind))
                    throw Error(lineNumber, $"非法 kind：{kindText}");
                string valueText = token.Substring(8, token.Length - 9);
                operand = new EncodedOperand(kind, ParseUInt32Bits(valueText, lineNumber));
                return true;
            }
            operand = default;
            return false;
        }

        private static uint ParseRegister(string token, int lineNumber)
        {
            if (!LooksLikeRegister(token)
                || !uint.TryParse(token.Substring(1), NumberStyles.None, CultureInfo.InvariantCulture, out uint register)
                || register > 15)
                throw Error(lineNumber, $"寄存器必须为 R0–R15：{token}");
            return register;
        }

        private static bool LooksLikeRegister(string token)
            => token.Length >= 2 && (token[0] == 'R' || token[0] == 'r') && char.IsDigit(token[1]);

        private static bool IsNumeric(string token)
        {
            token = token.Trim();
            return token.Length != 0 && (char.IsDigit(token[0]) || token[0] == '-' || token[0] == '+');
        }

        private static uint ParseUInt32Bits(string token, int lineNumber)
        {
            token = token.Trim();
            try
            {
                bool negative = token.StartsWith("-", StringComparison.Ordinal);
                bool positive = token.StartsWith("+", StringComparison.Ordinal);
                string unsigned = negative || positive ? token.Substring(1) : token;
                if (unsigned.StartsWith("0x", StringComparison.OrdinalIgnoreCase))
                {
                    if (!ulong.TryParse(unsigned.Substring(2), NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out ulong hex)
                        || hex > uint.MaxValue)
                        throw Error(lineNumber, $"非法 u32：{token}");
                    if (negative)
                    {
                        if (hex > 0x80000000UL)
                            throw Error(lineNumber, $"负十六进制值超出 int32：{token}");
                        return unchecked((uint)-(long)hex);
                    }
                    return (uint)hex;
                }

                if (!long.TryParse(token, NumberStyles.Integer, CultureInfo.InvariantCulture, out long value)
                    || value < int.MinValue
                    || value > uint.MaxValue)
                    throw Error(lineNumber, $"非法 32 位整数：{token}");
                return unchecked((uint)value);
            }
            catch (OverflowException)
            {
                throw Error(lineNumber, $"32 位整数溢出：{token}");
            }
        }

        private static string ParseTextLiteral(string line, int lineNumber)
        {
            int firstQuote = line.IndexOf('"');
            int lastQuote = line.LastIndexOf('"');
            if (firstQuote < 0 || lastQuote == firstQuote || line.Substring(0, firstQuote).Trim().Length != 4 || line.Substring(0, firstQuote).Trim().ToUpperInvariant() != "TEXT")
                throw Error(lineNumber, "TEXT 必须写成 TEXT \"...\"。 ");
            if (line.Substring(lastQuote + 1).Trim().Length != 0)
                throw Error(lineNumber, "TEXT 结束引号后存在多余内容。 ");
            return line.Substring(firstQuote + 1, lastQuote - firstQuote - 1);
        }

        private static bool IsQuotedLiteral(string token)
        {
            token = token.Trim();
            return token.Length >= 2 && token[0] == '"' && token[token.Length - 1] == '"';
        }

        private static string ParseQuotedLiteral(string token, int lineNumber)
        {
            token = token.Trim();
            if (!IsQuotedLiteral(token))
                throw Error(lineNumber, $"字符串操作数必须使用双引号：{token}");
            return token.Substring(1, token.Length - 2);
        }

        private static EncodedOperand AppendInlineString(
            string token,
            byte kind,
            int lineNumber,
            MemoryStream stringData,
            Encoding encoding)
        {
            string literal = ParseQuotedLiteral(token, lineNumber);
            byte[] bytes = EncodeTextLiteral(literal, encoding, lineNumber);
            if (stringData.Position > uint.MaxValue)
                throw Error(lineNumber, "字符串池超过 u32 地址空间。 ");
            uint offset = (uint)stringData.Position;
            stringData.Write(bytes, 0, bytes.Length);
            stringData.WriteByte(0);
            return new EncodedOperand(kind, offset);
        }

        private static byte[] EncodeTextLiteral(string literal, Encoding encoding, int lineNumber)
        {
            using var output = new MemoryStream();
            var plain = new StringBuilder();

            void FlushPlain()
            {
                if (plain.Length == 0)
                    return;
                try
                {
                    byte[] encoded = encoding.GetBytes(plain.ToString());
                    output.Write(encoded, 0, encoded.Length);
                    plain.Clear();
                }
                catch (EncoderFallbackException ex)
                {
                    throw Error(lineNumber, $"文本无法按所选编码写入：{ex.Message}");
                }
            }

            int position = 0;
            while (position < literal.Length)
            {
                if (position + 1 < literal.Length && literal[position] == '{' && literal[position + 1] == '{')
                {
                    int close = literal.IndexOf("}}", position + 2, StringComparison.Ordinal);
                    if (close < 0)
                        throw Error(lineNumber, "占位符缺少 }}。 ");
                    FlushPlain();
                    string body = literal.Substring(position + 2, close - position - 2);
                    string[] parts = body.Split(':');
                    if (parts.Length == 0 || parts.Any(p => p.Length != 2))
                        throw Error(lineNumber, $"非法字节占位符：{{{{{body}}}}}");
                    foreach (string part in parts)
                    {
                        if (!byte.TryParse(part, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out byte value))
                            throw Error(lineNumber, $"非法字节占位符：{{{{{body}}}}}");
                        output.WriteByte(value);
                    }
                    position = close + 2;
                }
                else
                {
                    plain.Append(literal[position]);
                    position++;
                }
            }
            FlushPlain();
            return output.ToArray();
        }

        private static string StripComment(string line)
        {
            bool quoted = false;
            for (int i = 0; i < line.Length; i++)
            {
                if (line[i] == '"')
                    quoted = !quoted;
                else if (line[i] == ';' && !quoted)
                    return line.Substring(0, i);
            }
            return line;
        }

        private static string DirectiveArgument(string line, string directive, int lineNumber)
        {
            if (!line.StartsWith(directive, StringComparison.OrdinalIgnoreCase)
                || (line.Length > directive.Length && !char.IsWhiteSpace(line[directive.Length])))
                throw Error(lineNumber, $"非法指令格式：{line}");
            string argument = line.Substring(directive.Length).Trim();
            if (argument.Length == 0)
                throw Error(lineNumber, $"{directive} 缺少参数。 ");
            return argument;
        }

        private static string ParseQuotedDirective(string line, string directive, int lineNumber)
        {
            string argument = DirectiveArgument(line, directive, lineNumber);
            if (argument.Length < 2 || argument[0] != '"' || argument[argument.Length - 1] != '"')
                throw Error(lineNumber, $"{directive} 参数必须使用双引号。 ");
            return argument.Substring(1, argument.Length - 2);
        }

        private static string[] SplitOperands(string text)
        {
            var result = new List<string>();
            int depth = 0;
            int start = 0;
            bool quoted = false;
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == '"')
                    quoted = !quoted;
                else if (!quoted && text[i] == '(')
                    depth++;
                else if (!quoted && text[i] == ')')
                    depth--;
                else if (!quoted && text[i] == ',' && depth == 0)
                {
                    result.Add(text.Substring(start, i - start).Trim());
                    start = i + 1;
                }
            }
            result.Add(text.Substring(start).Trim());
            if (result.Any(item => item.Length == 0))
                return result.ToArray();
            return result.ToArray();
        }

        private static void ValidateLabel(string label, int lineNumber)
        {
            if (label.Length == 0 || !(char.IsLetter(label[0]) || label[0] == '_'))
                throw Error(lineNumber, $"非法标签：{label}");
            for (int i = 1; i < label.Length; i++)
            {
                if (!(char.IsLetterOrDigit(label[i]) || label[i] == '_'))
                    throw Error(lineNumber, $"非法标签：{label}");
            }
        }

        private static void WriteOperand(byte[] output, int offset, EncodedOperand operand)
        {
            output[offset] = operand.Kind;
            WriteU32(output, offset + 1, operand.Value);
        }

        private static void WriteU16(byte[] output, int offset, ushort value)
            => BinaryPrimitives.WriteUInt16LittleEndian(output.AsSpan(offset, 2), value);

        private static void WriteU32(byte[] output, int offset, uint value)
            => BinaryPrimitives.WriteUInt32LittleEndian(output.AsSpan(offset, 4), value);

        private static InvalidDataException Error(int lineNumber, string message)
            => new InvalidDataException($"第 {lineNumber} 行：{message}");
    }

    public static class Program
    {
        public static int Main(string[] args)
        {
            Console.OutputEncoding = Encoding.UTF8;
            try
            {
                if (args.Length == 0 || IsHelp(args[0]))
                {
                    PrintUsage();
                    return args.Length == 0 ? 1 : 0;
                }

                string first = args[0].ToLowerInvariant();
                if (first == "disasm" || first == "d")
                    return RunExplicit(args, 1, "disasm");
                if (first == "asm" || first == "a")
                    return RunExplicit(args, 1, "asm");
                if (first == "verify" || first == "v")
                    return RunExplicit(args, 1, "verify");
                return RunAutomatic(args);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("错误：" + ex.Message);
                return 1;
            }
        }

        private static int RunExplicit(string[] args, int start, string mode)
        {
            ParseOptions(args, start, out List<string> inputs, out string? output, out string? encoding);
            if (inputs.Count == 0)
                throw new ArgumentException("缺少输入文件或目录。 ");
            List<string> files = ExpandInputs(inputs, mode);
            if (files.Count == 0)
                throw new FileNotFoundException("没有找到可处理文件。 ");

            if (mode == "verify")
                return VerifyFiles(files, encoding ?? "cp932");

            bool outputIsDirectory = files.Count > 1 || (output != null && (Directory.Exists(output) || EndsWithSeparator(output)));
            if (files.Count > 1 && output != null)
                Directory.CreateDirectory(output);

            foreach (string input in files)
            {
                string destination;
                if (output != null && !outputIsDirectory)
                    destination = Path.GetFullPath(output);
                else if (mode == "disasm")
                    destination = Path.Combine(output ?? Path.GetDirectoryName(input)!, Path.GetFileName(input) + ".asm.txt");
                else
                    destination = Path.Combine(output ?? Path.GetDirectoryName(input)!, AssemblyOutputName(input));

                EnsureParent(destination);
                if (mode == "disasm")
                    Disassembler.DisassembleFile(input, destination, encoding ?? "cp932");
                else
                    Assembler.AssembleFile(input, destination, encoding);
                Console.WriteLine($"{Path.GetFileName(input)} -> {destination}");
            }
            return 0;
        }

        private static int RunAutomatic(string[] args)
        {
            ParseOptions(args, 0, out List<string> inputs, out string? output, out string? encoding);
            if (inputs.Count == 0)
                throw new ArgumentException("缺少输入文件或目录。 ");
            if (output != null && inputs.Count != 1)
                throw new ArgumentException("自动模式下 -o 只支持单个输入。 ");

            var files = new List<string>();
            foreach (string input in inputs)
            {
                string full = Path.GetFullPath(input);
                if (Directory.Exists(full))
                    files.AddRange(Directory.EnumerateFiles(full, "*", SearchOption.TopDirectoryOnly).Where(HasYoxMagic));
                else if (File.Exists(full))
                    files.Add(full);
                else
                    throw new FileNotFoundException("输入不存在。 ", full);
            }
            files.Sort(StringComparer.OrdinalIgnoreCase);

            foreach (string input in files)
            {
                bool binaryScript = HasYoxMagic(input);
                bool assemble = !binaryScript && LooksLikeAssembly(input);
                if (!binaryScript && !assemble)
                    throw new InvalidDataException($"无法按魔数或汇编头识别输入：{input}");
                string destination;
                if (output != null)
                    destination = Path.GetFullPath(output);
                else if (assemble)
                    destination = Path.Combine(Path.GetDirectoryName(input)!, AssemblyOutputName(input));
                else if (files.Count == 1)
                    destination = Path.Combine(Path.GetDirectoryName(input)!, "asm.txt");
                else
                    destination = Path.Combine(Path.GetDirectoryName(input)!, Path.GetFileName(input) + ".asm.txt");

                EnsureParent(destination);
                if (assemble)
                    Assembler.AssembleFile(input, destination, encoding);
                else
                    Disassembler.DisassembleFile(input, destination, encoding ?? "cp932");
                Console.WriteLine($"{Path.GetFileName(input)} -> {destination}");
            }
            return 0;
        }

        private static int VerifyFiles(List<string> files, string encoding)
        {
            int verified = 0;
            foreach (string file in files)
            {
                byte[] original = File.ReadAllBytes(file);
                string assembly = Disassembler.Disassemble(original, encoding);
                byte[] rebuilt = Assembler.Assemble(assembly, null);
                string rebuiltAssembly = Disassembler.Disassemble(rebuilt, encoding);
                if (!string.Equals(assembly, rebuiltAssembly, StringComparison.Ordinal))
                    throw new InvalidDataException($"{file}: 重建后二次反汇编的语义文本不一致。 ");
                verified++;
            }
            Console.WriteLine($"验证通过：{verified} 个文件语义往返一致。 ");
            return 0;
        }

        private static int FirstDifference(byte[] left, byte[] right)
        {
            int count = Math.Min(left.Length, right.Length);
            for (int i = 0; i < count; i++)
            {
                if (left[i] != right[i])
                    return i;
            }
            return count;
        }

        private static List<string> ExpandInputs(List<string> inputs, string mode)
        {
            var files = new List<string>();
            foreach (string input in inputs)
            {
                string full = Path.GetFullPath(input);
                if (Directory.Exists(full))
                {
                    IEnumerable<string> candidates = Directory.EnumerateFiles(full, "*", SearchOption.TopDirectoryOnly);
                    files.AddRange(mode == "asm" ? candidates.Where(LooksLikeAssembly) : candidates.Where(HasYoxMagic));
                }
                else if (File.Exists(full))
                {
                    if (mode == "asm" && !LooksLikeAssembly(full))
                        throw new InvalidDataException($"输入不包含有效汇编头：{full}");
                    if (mode != "asm" && !HasYoxMagic(full))
                        throw new InvalidDataException($"输入没有 YOX 魔数：{full}");
                    files.Add(full);
                }
                else
                {
                    throw new FileNotFoundException("输入不存在。 ", full);
                }
            }
            files.Sort(StringComparer.OrdinalIgnoreCase);
            return files;
        }

        private static bool HasYoxMagic(string path)
        {
            try
            {
                using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
                return stream.Length >= 4
                    && stream.ReadByte() == 'Y'
                    && stream.ReadByte() == 'O'
                    && stream.ReadByte() == 'X'
                    && stream.ReadByte() == 0;
            }
            catch (IOException)
            {
                return false;
            }
            catch (UnauthorizedAccessException)
            {
                return false;
            }
        }

        private static bool LooksLikeAssembly(string path)
        {
            try
            {
                bool hasVersion = false;
                bool hasCode = false;
                foreach (string sourceLine in File.ReadLines(path, Encoding.UTF8).Take(128))
                {
                    string line = sourceLine.Trim();
                    hasVersion |= line.StartsWith(".version", StringComparison.OrdinalIgnoreCase);
                    hasCode |= line.Equals(".code", StringComparison.OrdinalIgnoreCase);
                    if (hasVersion && hasCode)
                        return true;
                }
                return false;
            }
            catch (IOException)
            {
                return false;
            }
            catch (UnauthorizedAccessException)
            {
                return false;
            }
            catch (DecoderFallbackException)
            {
                return false;
            }
        }

        private static void ParseOptions(string[] args, int start, out List<string> inputs, out string? output, out string? encoding)
        {
            inputs = new List<string>();
            output = null;
            encoding = null;
            for (int i = start; i < args.Length; i++)
            {
                if (args[i] == "-o" || args[i] == "--output")
                {
                    if (++i >= args.Length)
                        throw new ArgumentException("-o 缺少路径。 ");
                    output = args[i];
                }
                else if (args[i] == "--encoding" || args[i] == "-e")
                {
                    if (++i >= args.Length)
                        throw new ArgumentException("--encoding 缺少编码名。 ");
                    encoding = args[i];
                }
                else if (IsHelp(args[i]))
                {
                    PrintUsage();
                    Environment.Exit(0);
                }
                else
                {
                    inputs.Add(args[i]);
                }
            }
        }

        private static string AssemblyOutputName(string input)
        {
            string name = Path.GetFileName(input);
            if (name.EndsWith(".asm.txt", StringComparison.OrdinalIgnoreCase))
                return name.Substring(0, name.Length - ".asm.txt".Length) + ".rebuild";
            return Path.GetFileNameWithoutExtension(name) + ".rebuild";
        }

        private static bool EndsWithSeparator(string path)
            => path.EndsWith(Path.DirectorySeparatorChar.ToString(), StringComparison.Ordinal)
                || path.EndsWith(Path.AltDirectorySeparatorChar.ToString(), StringComparison.Ordinal);

        private static void EnsureParent(string path)
        {
            string? parent = Path.GetDirectoryName(Path.GetFullPath(path));
            if (!string.IsNullOrEmpty(parent))
                Directory.CreateDirectory(parent);
        }

        private static bool IsHelp(string value)
            => value == "-h" || value == "--help" || value == "/?" || value.Equals("help", StringComparison.OrdinalIgnoreCase);

        private static void PrintUsage()
        {
            Console.WriteLine("YOX 反汇编/汇编工具");
            Console.WriteLine();
            Console.WriteLine("  yox_script_tool disasm <file|dir>... [-o output] [--encoding cp932]");
            Console.WriteLine("  yox_script_tool asm    <file|dir>... [-o output] [--encoding cp932]");
            Console.WriteLine("  yox_script_tool verify <file|dir>... [--encoding cp932]");
            Console.WriteLine("  yox_script_tool <拖放的 YOX 二进制或汇编文本>...");
            Console.WriteLine();
            Console.WriteLine("二进制按 YOX\\0 魔数识别，不检查扩展名。");
            Console.WriteLine("字符串直接内嵌；汇编时每次引用独立建池并自动补 NUL。");
            Console.WriteLine("单个 YOX 拖放生成同目录 asm.txt；汇编文本拖放生成 .rebuild。");
        }
    }
}
