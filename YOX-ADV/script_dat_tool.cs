using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;

internal static class ScriptDatTool
{
    private const string ManifestFileName = "script_dat_manifest.json";
    private const string ManifestFormat = "YOX_DAT_V2";
    private const string LegacyManifestFormat = "YOX_DAT_LAYOUT_V1";
    private const string IndexSuffixFileName = "index_suffix.bin";
    private const int ContainerHeaderSize = 0x20;
    private const int IndexRecordSize = 0x10;
    private const int Alignment = 0x800;

    private sealed class Manifest
    {
        public string Format { get; set; } = ManifestFormat;
        public int EntryCount { get; set; }
        public string IndexSuffix { get; set; } = IndexSuffixFileName;
    }

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length == 0)
            {
                PrintHelp();
                return 1;
            }

            string first = args[0];
            if (IsCommand(first, "unpack"))
                return RunUnpack(args.Skip(1).ToArray());
            if (IsCommand(first, "pack"))
                return RunPack(args.Skip(1).ToArray());
            if (IsCommand(first, "verify"))
                return RunVerify(args.Skip(1).ToArray());
            if (IsCommand(first, "compact"))
                return RunCompact(args.Skip(1).ToArray());
            if (IsCommand(first, "help") || first is "-h" or "--help" or "/?")
            {
                PrintHelp();
                return 0;
            }

            int result = 0;
            foreach (string path in args)
            {
                if (Directory.Exists(path))
                {
                    Pack(path, DefaultPackOutput(path));
                }
                else if (File.Exists(path) &&
                         string.Equals(Path.GetFileName(path), ManifestFileName, StringComparison.OrdinalIgnoreCase))
                {
                    string directory = Path.GetDirectoryName(Path.GetFullPath(path))!;
                    Pack(directory, DefaultPackOutput(directory));
                }
                else if (File.Exists(path))
                {
                    Unpack(path, DefaultUnpackOutput(path));
                }
                else
                {
                    Console.Error.WriteLine($"找不到输入：{path}");
                    result = 1;
                }
            }
            return result;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"错误：{ex.Message}");
            return 1;
        }
    }

    private static bool IsCommand(string value, string command) =>
        string.Equals(value, command, StringComparison.OrdinalIgnoreCase);

    private static int RunUnpack(string[] args)
    {
        ParseInputOutput(args, out string input, out string? output);
        Unpack(input, output ?? DefaultUnpackOutput(input));
        return 0;
    }

    private static int RunPack(string[] args)
    {
        ParseInputOutput(args, out string input, out string? output);
        string directory = File.Exists(input) ? Path.GetDirectoryName(Path.GetFullPath(input))! : input;
        Pack(directory, output ?? DefaultPackOutput(directory));
        return 0;
    }

    private static int RunCompact(string[] args)
    {
        if (args.Length != 1)
            throw new ArgumentException("compact 用法：script_dat_tool compact <unpacked_directory>");
        CompactLegacyManifest(args[0]);
        return 0;
    }

    private static int RunVerify(string[] args)
    {
        if (args.Length != 1)
            throw new ArgumentException("verify 用法：script_dat_tool verify <input.dat>");

        string input = Path.GetFullPath(args[0]);
        string tempRoot = Path.Combine(Path.GetTempPath(), "script_dat_verify_" + Guid.NewGuid().ToString("N"));
        string rebuilt = Path.Combine(tempRoot, "roundtrip.dat");
        try
        {
            Unpack(input, tempRoot, quiet: true);
            Pack(tempRoot, rebuilt, quiet: true);
            string originalHash = Sha256File(input);
            string rebuiltHash = Sha256File(rebuilt);
            bool same = FilesEqual(input, rebuilt);
            Console.WriteLine($"原始 SHA-256：{originalHash}");
            Console.WriteLine($"回包 SHA-256：{rebuiltHash}");
            Console.WriteLine(same ? "验证通过：逐字节完全一致。" : "验证失败：二进制存在差异。");
            return same ? 0 : 2;
        }
        finally
        {
            if (Directory.Exists(tempRoot))
                Directory.Delete(tempRoot, recursive: true);
        }
    }

    private static void ParseInputOutput(string[] args, out string input, out string? output)
    {
        input = string.Empty;
        output = null;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] is "-o" or "--output")
            {
                if (++i >= args.Length)
                    throw new ArgumentException("-o/--output 缺少路径。");
                output = args[i];
            }
            else if (input.Length == 0)
            {
                input = args[i];
            }
            else
            {
                throw new ArgumentException($"无法识别的参数：{args[i]}");
            }
        }
        if (input.Length == 0)
            throw new ArgumentException("缺少输入路径。");
    }

    private static void Unpack(string inputPath, string outputDirectory, bool quiet = false)
    {
        inputPath = Path.GetFullPath(inputPath);
        outputDirectory = Path.GetFullPath(outputDirectory);
        byte[] data = File.ReadAllBytes(inputPath);
        ValidateMagicAndHeader(data);

        uint indexOffset = ReadUInt32(data, 0x08);
        uint entryCount = ReadUInt32(data, 0x0C);
        int indexBytes = checked((int)entryCount * IndexRecordSize);
        if (indexOffset < ContainerHeaderSize || (long)indexOffset + indexBytes > data.LongLength)
            throw new InvalidDataException("DAT 索引偏移或数量越界。");

        var entries = new List<(uint Offset, uint Size)>((int)entryCount);
        for (int i = 0; i < entryCount; i++)
        {
            int record = checked((int)indexOffset + i * IndexRecordSize);
            uint offset = ReadUInt32(data, record);
            uint size = ReadUInt32(data, record + 4);
            uint metadata1 = ReadUInt32(data, record + 8);
            uint metadata2 = ReadUInt32(data, record + 12);
            if (metadata1 != uint.MaxValue || metadata2 != 0)
                throw new InvalidDataException($"条目 {i:D5} 使用了非标准索引元数据，紧凑清单无法无损表示。");
            if (offset < ContainerHeaderSize || (long)offset + size > indexOffset)
                throw new InvalidDataException($"条目 {i:D5} 的范围越界或覆盖索引表。");
            entries.Add((offset, size));
        }

        ValidateCanonicalLayout(data, indexOffset, entries);
        EnsureEmptyOutputDirectory(outputDirectory);
        string entriesDirectory = Path.Combine(outputDirectory, "entries");
        Directory.CreateDirectory(entriesDirectory);

        for (int i = 0; i < entries.Count; i++)
        {
            var entry = entries[i];
            File.WriteAllBytes(
                Path.Combine(entriesDirectory, $"{i:D5}.yox"),
                Slice(data, entry.Offset, entry.Size));
        }

        byte[] suffix = Slice(data, (long)indexOffset + indexBytes, data.LongLength - indexOffset - indexBytes);
        File.WriteAllBytes(Path.Combine(outputDirectory, IndexSuffixFileName), suffix);
        WriteManifest(outputDirectory, new Manifest { EntryCount = entries.Count });

        if (!quiet)
        {
            Console.WriteLine($"已解包：{inputPath}");
            Console.WriteLine($"条目数：{entryCount}");
            Console.WriteLine($"输出目录：{outputDirectory}");
            Console.WriteLine($"紧凑清单：{Path.Combine(outputDirectory, ManifestFileName)}");
        }
    }

    private static void Pack(string inputDirectory, string outputPath, bool quiet = false)
    {
        inputDirectory = Path.GetFullPath(inputDirectory);
        outputPath = Path.GetFullPath(outputPath);
        Manifest manifest = ReadManifest(inputDirectory);
        List<string> entryFiles = EnumerateEntryFiles(inputDirectory, manifest.EntryCount);
        byte[] suffix = File.ReadAllBytes(ResolveInside(inputDirectory, manifest.IndexSuffix));

        using var stream = new MemoryStream();
        stream.Write(new byte[ContainerHeaderSize]);
        PadTo(stream, Alignment);

        var offsets = new uint[entryFiles.Count];
        var sizes = new uint[entryFiles.Count];
        for (int i = 0; i < entryFiles.Count; i++)
        {
            PadTo(stream, Alignment);
            offsets[i] = checked((uint)stream.Position);
            byte[] entry = File.ReadAllBytes(entryFiles[i]);
            sizes[i] = checked((uint)entry.Length);
            stream.Write(entry);
        }

        PadTo(stream, Alignment);
        uint indexOffset = checked((uint)stream.Position);
        for (int i = 0; i < entryFiles.Count; i++)
        {
            WriteUInt32(stream, offsets[i]);
            WriteUInt32(stream, sizes[i]);
            WriteUInt32(stream, uint.MaxValue);
            WriteUInt32(stream, 0);
        }
        stream.Write(suffix);

        byte[] output = stream.ToArray();
        output[0] = (byte)'Y';
        output[1] = (byte)'O';
        output[2] = (byte)'X';
        output[3] = 0;
        WriteUInt32(output, 0x08, indexOffset);
        WriteUInt32(output, 0x0C, checked((uint)entryFiles.Count));

        string? parent = Path.GetDirectoryName(outputPath);
        if (!string.IsNullOrEmpty(parent))
            Directory.CreateDirectory(parent);
        File.WriteAllBytes(outputPath, output);

        if (!quiet)
        {
            Console.WriteLine($"已封包：{outputPath}");
            Console.WriteLine($"条目数：{entryFiles.Count}");
            Console.WriteLine($"SHA-256：{Sha256(output)}");
        }
    }

    private static void CompactLegacyManifest(string directory)
    {
        directory = Path.GetFullPath(directory);
        string manifestPath = Path.Combine(directory, ManifestFileName);
        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(manifestPath));
        JsonElement root = document.RootElement;
        string format = root.GetProperty("format").GetString() ?? string.Empty;
        if (format == ManifestFormat)
        {
            Console.WriteLine("清单已经是紧凑 V2 格式。");
            return;
        }
        if (format != LegacyManifestFormat)
            throw new InvalidDataException($"无法迁移的清单格式：{format}");

        int entryCount = root.GetProperty("entryCount").GetInt32();
        byte[] indexAndSuffix = Convert.FromBase64String(root.GetProperty("indexAndSuffixBase64").GetString()!);
        int indexBytes = checked(entryCount * IndexRecordSize);
        if (indexAndSuffix.Length < indexBytes)
            throw new InvalidDataException("旧清单中的索引数据长度不足。");
        byte[] suffix = indexAndSuffix[indexBytes..];
        File.WriteAllBytes(Path.Combine(directory, IndexSuffixFileName), suffix);
        WriteManifest(directory, new Manifest { EntryCount = entryCount });
        Console.WriteLine($"已迁移为紧凑清单：{manifestPath}");
        Console.WriteLine($"清单外必要尾部数据：{IndexSuffixFileName} ({suffix.Length} 字节)");
    }

    private static void ValidateMagicAndHeader(byte[] data)
    {
        if (data.Length < ContainerHeaderSize)
            throw new InvalidDataException("文件小于 32 字节，不是有效的 YOX DAT。");
        if (data[0] != (byte)'Y' || data[1] != (byte)'O' || data[2] != (byte)'X' || data[3] != 0)
            throw new InvalidDataException("文件头不是 YOX\\0。");
        for (int i = 4; i < ContainerHeaderSize; i++)
        {
            if (i is >= 8 and < 16)
                continue;
            if (data[i] != 0)
                throw new InvalidDataException("DAT 头包含非标准保留字段，紧凑清单无法无损表示。");
        }
    }

    private static void ValidateCanonicalLayout(byte[] data, uint indexOffset, List<(uint Offset, uint Size)> entries)
    {
        long expected = Alignment;
        if (entries.Count == 0)
            expected = indexOffset;
        for (int i = 0; i < entries.Count; i++)
        {
            expected = Align(expected, Alignment);
            if (entries[i].Offset != expected)
                throw new InvalidDataException($"条目 {i:D5} 不符合 0x{Alignment:X} 规范布局。");
            expected += entries[i].Size;
            long next = i + 1 < entries.Count ? entries[i + 1].Offset : indexOffset;
            EnsureZeroRange(data, expected, next - expected, $"条目 {i:D5} 后填充");
        }
        if (Align(expected, Alignment) != indexOffset)
            throw new InvalidDataException("索引表未按标准规则放置。");
        EnsureZeroRange(data, ContainerHeaderSize, Alignment - ContainerHeaderSize, "容器头后填充");
    }

    private static void EnsureZeroRange(byte[] data, long offset, long length, string name)
    {
        if (length < 0 || offset < 0 || offset + length > data.LongLength)
            throw new InvalidDataException($"{name}范围越界。");
        for (long i = offset; i < offset + length; i++)
        {
            if (data[i] != 0)
                throw new InvalidDataException($"{name}包含非零数据，紧凑清单无法无损表示。");
        }
    }

    private static Manifest ReadManifest(string directory)
    {
        string path = Path.Combine(directory, ManifestFileName);
        Manifest manifest = JsonSerializer.Deserialize<Manifest>(File.ReadAllText(path), JsonOptions)
            ?? throw new InvalidDataException("清单解析失败。");
        if (manifest.Format != ManifestFormat)
            throw new InvalidDataException($"清单不是 V2 格式；请先运行 compact：{manifest.Format}");
        if (manifest.EntryCount < 0)
            throw new InvalidDataException("entryCount 不能为负数。");
        return manifest;
    }

    private static void WriteManifest(string directory, Manifest manifest) =>
        File.WriteAllText(Path.Combine(directory, ManifestFileName), JsonSerializer.Serialize(manifest, JsonOptions));

    private static List<string> EnumerateEntryFiles(string directory, int count)
    {
        string entriesDirectory = Path.Combine(directory, "entries");
        var files = new List<string>(count);
        for (int i = 0; i < count; i++)
        {
            string path = Path.Combine(entriesDirectory, $"{i:D5}.yox");
            if (!File.Exists(path))
                throw new FileNotFoundException($"缺少连续条目 {i:D5}.yox。", path);
            files.Add(path);
        }
        string[] extra = Directory.GetFiles(entriesDirectory, "*.yox")
            .Where(path => !files.Contains(path, StringComparer.OrdinalIgnoreCase)).ToArray();
        if (extra.Length != 0)
            throw new InvalidDataException($"entries 中存在清单范围外的 YOX 文件：{Path.GetFileName(extra[0])}");
        return files;
    }

    private static void EnsureEmptyOutputDirectory(string directory)
    {
        if (Directory.Exists(directory))
        {
            if (Directory.EnumerateFileSystemEntries(directory).Any())
                throw new IOException($"输出目录非空：{directory}");
        }
        else
        {
            Directory.CreateDirectory(directory);
        }
    }

    private static string ResolveInside(string directory, string relative)
    {
        string root = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            + Path.DirectorySeparatorChar;
        string path = Path.GetFullPath(Path.Combine(directory, relative));
        if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException($"清单路径逃逸输入目录：{relative}");
        return path;
    }

    private static long Align(long value, int alignment) => (value + alignment - 1) / alignment * alignment;

    private static void PadTo(Stream stream, int alignment)
    {
        int count = checked((int)(Align(stream.Position, alignment) - stream.Position));
        if (count > 0)
            stream.Write(new byte[count]);
    }

    private static uint ReadUInt32(byte[] data, int offset) =>
        BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(offset, 4));

    private static void WriteUInt32(byte[] data, int offset, uint value) =>
        BinaryPrimitives.WriteUInt32LittleEndian(data.AsSpan(offset, 4), value);

    private static void WriteUInt32(Stream stream, uint value)
    {
        Span<byte> bytes = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(bytes, value);
        stream.Write(bytes);
    }

    private static byte[] Slice(byte[] data, long offset, long length)
    {
        if (offset < 0 || length < 0 || offset + length > data.LongLength || length > int.MaxValue)
            throw new InvalidDataException("切片范围越界。");
        byte[] result = new byte[(int)length];
        Buffer.BlockCopy(data, (int)offset, result, 0, (int)length);
        return result;
    }

    private static string Sha256(byte[] data) => Convert.ToHexString(SHA256.HashData(data));

    private static string Sha256File(string path)
    {
        using FileStream stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream));
    }

    private static bool FilesEqual(string left, string right)
    {
        var leftInfo = new FileInfo(left);
        var rightInfo = new FileInfo(right);
        if (leftInfo.Length != rightInfo.Length)
            return false;
        const int bufferSize = 1024 * 1024;
        byte[] a = new byte[bufferSize];
        byte[] b = new byte[bufferSize];
        using FileStream fa = File.OpenRead(left);
        using FileStream fb = File.OpenRead(right);
        while (true)
        {
            int ra = fa.Read(a, 0, a.Length);
            int rb = fb.Read(b, 0, b.Length);
            if (ra != rb)
                return false;
            if (ra == 0)
                return true;
            if (!a.AsSpan(0, ra).SequenceEqual(b.AsSpan(0, rb)))
                return false;
        }
    }

    private static string DefaultUnpackOutput(string input) =>
        Path.Combine(Path.GetDirectoryName(Path.GetFullPath(input))!, Path.GetFileNameWithoutExtension(input) + "__");

    private static string DefaultPackOutput(string directory)
    {
        string full = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string parent = Path.GetDirectoryName(full)!;
        string name = Path.GetFileName(full);
        if (name.EndsWith("__", StringComparison.Ordinal))
            name = name[..^2];
        return Path.Combine(parent, name + ".rebuild.dat");
    }

    private static void PrintHelp()
    {
        Console.WriteLine("YOX script.dat 无损解封包工具");
        Console.WriteLine();
        Console.WriteLine("用法：");
        Console.WriteLine("  script_dat_tool unpack <input.dat> [-o <directory>]");
        Console.WriteLine("  script_dat_tool pack <directory|manifest.json> [-o <output.dat>]");
        Console.WriteLine("  script_dat_tool verify <input.dat>");
        Console.WriteLine("  script_dat_tool compact <legacy_unpacked_directory>");
        Console.WriteLine();
        Console.WriteLine("拖放 DAT 文件到 exe：解包到同目录的 <文件名>__。");
        Console.WriteLine("拖放解包目录或 manifest 到 exe：生成 <文件名>.rebuild.dat。");
    }
}
