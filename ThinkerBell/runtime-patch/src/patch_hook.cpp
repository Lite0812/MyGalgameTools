#include "patch_hook.h"

#include <detours.h>

#include <cstdarg>
#include <climits>
#include <cstdio>
#include <cwchar>
#include <string>
#include <vector>

namespace {

struct Config {
    bool enabled = true;
    bool logging = true;
    DWORD loader_rva = 0;
    DWORD max_file_size = 256u * 1024u * 1024u;
    std::wstring patch_directory = L"patch";
};

struct LoaderLayout {
    BYTE* address = nullptr;
    DWORD data_offset = 0;
    DWORD length_offset = 0;
    DWORD raw_offset = 0;
    BYTE* cleanup = nullptr;
    BYTE* allocate = nullptr;
    BYTE* release = nullptr;
    bool eax_this = false;
    bool single_character_extension = false;
    const wchar_t* signature_name = nullptr;
};

struct PatchEntry {
    DWORD entry_id = 0;
    BYTE extension_first = 0;
    BYTE extension_second = 0;
    std::wstring path;
};

using LoaderFunction = int(__thiscall*)(void*, void*, int, int);
using CleanupFunction = void(__thiscall*)(void*);
using AllocateFunction = void*(__cdecl*)(size_t);
using ReleaseFunction = void(__cdecl*)(void*);

Config g_config;
LoaderLayout g_layout;
LoaderFunction g_original_loader = nullptr;
void* g_original_loader_eax = nullptr;
std::wstring g_exe_directory;
std::wstring g_ini_path;
std::wstring g_log_path;
CRITICAL_SECTION g_log_lock;
bool g_log_lock_ready = false;
std::vector<PatchEntry> g_patch_entries;

DWORD ReadDword(const BYTE* address)
{
    DWORD value = 0;
    memcpy(&value, address, sizeof(value));
    return value;
}

BYTE* ResolveCall(BYTE* opcode)
{
    if (!opcode || opcode[0] != 0xE8) {
        return nullptr;
    }
    const LONG displacement = static_cast<LONG>(ReadDword(opcode + 1));
    return opcode + 5 + displacement;
}

std::wstring ModuleDirectory(HMODULE module)
{
    wchar_t path[MAX_PATH] = {};
    if (!GetModuleFileNameW(module, path, MAX_PATH)) {
        return {};
    }
    wchar_t* separator = wcsrchr(path, L'\\');
    if (separator) {
        *separator = L'\0';
    }
    return path;
}

std::wstring JoinPath(const std::wstring& left, const std::wstring& right)
{
    if (right.size() >= 2 && right[1] == L':') {
        return right;
    }
    if (right.size() >= 2 && right[0] == L'\\' && right[1] == L'\\') {
        return right;
    }
    return left + L"\\" + right;
}

void Log(const wchar_t* format, ...)
{
    if (!g_config.logging || !g_log_lock_ready) {
        return;
    }
    wchar_t message[1024] = {};
    va_list arguments;
    va_start(arguments, format);
    _vsnwprintf_s(message, _countof(message), _TRUNCATE, format, arguments);
    va_end(arguments);

    SYSTEMTIME time = {};
    GetLocalTime(&time);
    wchar_t line[1200] = {};
    _snwprintf_s(line, _countof(line), _TRUNCATE, L"[%02u:%02u:%02u.%03u] %s\r\n",
        time.wHour, time.wMinute, time.wSecond, time.wMilliseconds, message);

    EnterCriticalSection(&g_log_lock);
    HANDLE file = CreateFileW(g_log_path.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file != INVALID_HANDLE_VALUE) {
        const int utf8_size = WideCharToMultiByte(CP_UTF8, 0, line, -1, nullptr, 0, nullptr, nullptr);
        if (utf8_size > 1) {
            std::vector<char> utf8(static_cast<size_t>(utf8_size));
            WideCharToMultiByte(CP_UTF8, 0, line, -1, utf8.data(), utf8_size, nullptr, nullptr);
            DWORD written = 0;
            WriteFile(file, utf8.data(), static_cast<DWORD>(utf8.size() - 1), &written, nullptr);
        }
        CloseHandle(file);
    }
    LeaveCriticalSection(&g_log_lock);
}

bool ReadBool(const wchar_t* key, bool fallback)
{
    return GetPrivateProfileIntW(L"Patch", key, fallback ? 1 : 0, g_ini_path.c_str()) != 0;
}

void LoadConfig()
{
    g_config.enabled = ReadBool(L"Enable", true);
    g_config.logging = ReadBool(L"Log", true);
    g_config.max_file_size = GetPrivateProfileIntW(
        L"Patch", L"MaxFileSize", 256u * 1024u * 1024u, g_ini_path.c_str());
    wchar_t directory[MAX_PATH] = {};
    GetPrivateProfileStringW(L"Patch", L"Directory", L"patch", directory, MAX_PATH, g_ini_path.c_str());
    g_config.patch_directory = directory;
    g_config.loader_rva = GetPrivateProfileIntW(L"Hook", L"LoaderRva", 0, g_ini_path.c_str());
}

struct Pattern {
    const BYTE* bytes;
    const char* mask;
    size_t size;
    size_t cleanup_call;
    size_t release_call;
    size_t allocate_call;
    size_t length_immediate;
    size_t data_immediate;
    size_t raw_immediate;
    bool eax_this;
    bool single_character_extension;
    const wchar_t* name;
};

const BYTE kLegacyPattern[] = {
    0x53, 0x55, 0x56, 0x57, 0x8B, 0xF1, 0xE8, 0, 0, 0, 0,
    0x8B, 0x7C, 0x24, 0x14, 0x8B, 0x87, 0x0C, 0x01, 0, 0,
    0x89, 0x86, 0, 0, 0, 0, 0x40, 0x50, 0xE8
};
const char kLegacyMask[] = "xxxxxxx????xxxxxxxxxxxx????xxx";

const BYTE kModernPattern[] = {
    0x55, 0x8B, 0xEC, 0x53, 0x56, 0x57, 0x8B, 0xF9, 0xE8, 0, 0, 0, 0,
    0x8B, 0x5D, 0x08, 0x8B, 0x83, 0x0C, 0x01, 0, 0,
    0x89, 0x87, 0, 0, 0, 0, 0x40, 0x50, 0xE8
};
const char kModernMask[] = "xxxxxxxxx????xxxxxxxxxxx????xxx";

const BYTE kEarlyPattern[] = {
    0x53, 0x55, 0x56, 0x57, 0x8B, 0xF1, 0xE8, 0, 0, 0, 0,
    0x8B, 0x5C, 0x24, 0x14, 0x8B, 0x83, 0x0C, 0x01, 0, 0,
    0x89, 0x86, 0, 0, 0, 0, 0x40, 0x50, 0xE8
};
const char kEarlyMask[] = "xxxxxxx????xxxxxxxxxxxx????xxx";

const BYTE kFramePattern[] = {
    0x55, 0x8B, 0xEC, 0x53, 0x56, 0x57, 0x8B, 0xF1, 0xE8, 0, 0, 0, 0,
    0x8B, 0x5D, 0x08, 0x8B, 0x83, 0x0C, 0x01, 0, 0,
    0x89, 0x86, 0, 0, 0, 0, 0x40, 0x50, 0xE8
};
const char kFrameMask[] = "xxxxxxxxx????xxxxxxxxxxx????xxx";

const BYTE kEaxPattern[] = {
    0x53, 0x55, 0x8B, 0x6C, 0x24, 0x0C, 0x56, 0x57, 0x8B, 0xF8,
    0x8B, 0x87, 0, 0, 0, 0, 0x33, 0xDB, 0x3B, 0xC3, 0x74, 0x0F,
    0x50, 0xE8, 0, 0, 0, 0, 0x83, 0xC4, 0x04, 0x89, 0x9F
};
const char kEaxMask[] = "xxxxxxxxxxxx????xxxxxxxx????xxxxx";

static_assert(sizeof(kLegacyPattern) == sizeof(kLegacyMask) - 1);
static_assert(sizeof(kModernPattern) == sizeof(kModernMask) - 1);
static_assert(sizeof(kEarlyPattern) == sizeof(kEarlyMask) - 1);
static_assert(sizeof(kFramePattern) == sizeof(kFrameMask) - 1);
static_assert(sizeof(kEaxPattern) == sizeof(kEaxMask) - 1);

bool Matches(const BYTE* data, const Pattern& pattern)
{
    for (size_t index = 0; index < pattern.size; ++index) {
        if (pattern.mask[index] == 'x' && data[index] != pattern.bytes[index]) {
            return false;
        }
    }
    return true;
}

bool IsImageAddress(BYTE* module, BYTE* address)
{
    const auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(module);
    const auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(module + dos->e_lfanew);
    const auto section = IMAGE_FIRST_SECTION(nt);
    for (WORD index = 0; index < nt->FileHeader.NumberOfSections; ++index) {
        BYTE* begin = module + section[index].VirtualAddress;
        BYTE* end = begin + section[index].Misc.VirtualSize;
        if (address >= begin && address < end) {
            return true;
        }
    }
    return false;
}

bool DecodeLayout(BYTE* module, BYTE* address, const Pattern& pattern, LoaderLayout& layout)
{
    if (address[pattern.allocate_call] != 0xE8 ||
        (pattern.cleanup_call && address[pattern.cleanup_call] != 0xE8) ||
        (pattern.release_call && address[pattern.release_call] != 0xE8)) {
        return false;
    }
    const DWORD data_offset = ReadDword(address + pattern.data_immediate);
    const DWORD raw_offset = pattern.raw_immediate
        ? ReadDword(address + pattern.raw_immediate)
        : data_offset - 8;
    const DWORD length_offset = pattern.length_immediate
        ? ReadDword(address + pattern.length_immediate)
        : raw_offset + 4;
    BYTE* cleanup = pattern.cleanup_call ? ResolveCall(address + pattern.cleanup_call) : nullptr;
    BYTE* release = pattern.release_call ? ResolveCall(address + pattern.release_call) : nullptr;
    BYTE* allocate = ResolveCall(address + pattern.allocate_call);
    if (data_offset != length_offset + 4 || raw_offset + 4 != length_offset ||
        data_offset < 0x100 || data_offset > 0x100000 ||
        (cleanup && !IsImageAddress(module, cleanup)) ||
        (release && !IsImageAddress(module, release)) ||
        !IsImageAddress(module, allocate)) {
        return false;
    }
    layout.address = address;
    layout.data_offset = data_offset;
    layout.length_offset = length_offset;
    layout.raw_offset = raw_offset;
    layout.cleanup = cleanup;
    layout.allocate = allocate;
    layout.release = release;
    layout.eax_this = pattern.eax_this;
    layout.single_character_extension = pattern.single_character_extension;
    layout.signature_name = pattern.name;
    return true;
}

bool FindLoader(LoaderLayout& layout)
{
    BYTE* module = reinterpret_cast<BYTE*>(GetModuleHandleW(nullptr));
    const auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(module);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) {
        return false;
    }
    const auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(module + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386) {
        return false;
    }

    if (g_config.loader_rva) {
        BYTE* address = module + g_config.loader_rva;
        const Pattern patterns[] = {
            {kLegacyPattern, kLegacyMask, sizeof(kLegacyPattern), 6, 0, 29, 23, 53, 0, false, false, L"vc9/manual"},
            {kModernPattern, kModernMask, sizeof(kModernPattern), 8, 0, 30, 24, 44, 0, false, false, L"vc14/manual"},
            {kEarlyPattern, kEarlyMask, sizeof(kEarlyPattern), 6, 0, 29, 23, 55, 0, false, true, L"early/manual"},
            {kFramePattern, kFrameMask, sizeof(kFramePattern), 8, 0, 30, 24, 56, 0, false, false, L"frame/manual"},
            {kEaxPattern, kEaxMask, sizeof(kEaxPattern), 0, 23, 84, 64, 12, 39, true, false, L"eax/manual"}
        };
        for (const Pattern& pattern : patterns) {
            if (Matches(address, pattern) && DecodeLayout(module, address, pattern, layout)) {
                return true;
            }
        }
        return false;
    }

    const Pattern patterns[] = {
        {kLegacyPattern, kLegacyMask, sizeof(kLegacyPattern), 6, 0, 29, 23, 53, 0, false, false, L"vc9"},
        {kModernPattern, kModernMask, sizeof(kModernPattern), 8, 0, 30, 24, 44, 0, false, false, L"vc14"},
        {kEarlyPattern, kEarlyMask, sizeof(kEarlyPattern), 6, 0, 29, 23, 55, 0, false, true, L"early-vc7"},
        {kFramePattern, kFrameMask, sizeof(kFramePattern), 8, 0, 30, 24, 56, 0, false, false, L"frame-vc7"},
        {kEaxPattern, kEaxMask, sizeof(kEaxPattern), 0, 23, 84, 64, 12, 39, true, false, L"eax-vc8"}
    };
    std::vector<LoaderLayout> candidates;
    const auto section = IMAGE_FIRST_SECTION(nt);
    for (WORD section_index = 0; section_index < nt->FileHeader.NumberOfSections; ++section_index) {
        BYTE* begin = module + section[section_index].VirtualAddress;
        const size_t size = section[section_index].Misc.VirtualSize;
        for (const Pattern& pattern : patterns) {
            if (size < pattern.size) {
                continue;
            }
            for (size_t offset = 0; offset <= size - pattern.size; ++offset) {
                if (!Matches(begin + offset, pattern)) {
                    continue;
                }
                LoaderLayout candidate;
                if (DecodeLayout(module, begin + offset, pattern, candidate)) {
                    candidates.push_back(candidate);
                }
            }
        }
    }
    if (candidates.size() != 1) {
        Log(L"特征扫描得到 %u 个有效候选，拒绝挂钩", static_cast<unsigned>(candidates.size()));
        return false;
    }
    layout = candidates.front();
    return true;
}

bool ValidExtensionCharacter(BYTE character)
{
    return (character >= '0' && character <= '9') ||
        (character >= 'A' && character <= 'Z') ||
        (character >= 'a' && character <= 'z');
}

BYTE LowerAscii(BYTE character)
{
    return character >= 'A' && character <= 'Z' ? character + ('a' - 'A') : character;
}

void IndexPatchDirectory()
{
    g_patch_entries.clear();
    const std::wstring directory = JoinPath(g_exe_directory, g_config.patch_directory);
    WIN32_FIND_DATAW data = {};
    HANDLE search = FindFirstFileW(JoinPath(directory, L"*").c_str(), &data);
    if (search == INVALID_HANDLE_VALUE) {
        Log(L"补丁目录不存在: %s", directory.c_str());
        return;
    }
    do {
        if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
            continue;
        }
        const wchar_t* name = data.cFileName;
        const wchar_t* dot = wcsrchr(name, L'.');
        if (!dot || dot == name || dot[1] == L'\0' || dot[2] != L'\0' && dot[3] != L'\0') {
            continue;
        }
        ULONGLONG value = 0;
        bool numeric = true;
        for (const wchar_t* cursor = name; cursor < dot; ++cursor) {
            if (*cursor < L'0' || *cursor > L'9') {
                numeric = false;
                break;
            }
            value = value * 10 + static_cast<unsigned>(*cursor - L'0');
            if (value > MAXDWORD) {
                numeric = false;
                break;
            }
        }
        if (!numeric || dot == name) {
            continue;
        }
        const BYTE first = static_cast<BYTE>(dot[1]);
        const BYTE second = dot[2] ? static_cast<BYTE>(dot[2]) : 0;
        if (dot[1] > 0x7F || dot[2] > 0x7F || !ValidExtensionCharacter(first) ||
            (second && !ValidExtensionCharacter(second))) {
            continue;
        }
        PatchEntry entry;
        entry.entry_id = static_cast<DWORD>(value);
        entry.extension_first = LowerAscii(first);
        entry.extension_second = LowerAscii(second);
        entry.path = JoinPath(directory, name);
        g_patch_entries.push_back(entry);
    } while (FindNextFileW(search, &data));
    FindClose(search);
    Log(L"已索引 %u 个平铺替换文件", static_cast<unsigned>(g_patch_entries.size()));
}

bool OpenPatchFile(const BYTE* archive_map, HANDLE& file, std::wstring& path)
{
    const DWORD entry_id = ReadDword(archive_map + 0x110);
    const BYTE extension_first = LowerAscii(archive_map[0x04]);
    const BYTE extension_second = g_layout.single_character_extension
        ? 0
        : LowerAscii(archive_map[0x114]);
    if (!ValidExtensionCharacter(extension_first) ||
        (extension_second != 0 && !ValidExtensionCharacter(extension_second))) {
        return false;
    }
    for (const PatchEntry& entry : g_patch_entries) {
        if (entry.entry_id != entry_id || entry.extension_first != extension_first ||
            entry.extension_second != extension_second) {
            continue;
        }
        path = entry.path;
        file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
        if (file != INVALID_HANDLE_VALUE) {
            return true;
        }
    }
    return false;
}

void ClearResourceBuffers(void* self)
{
    if (g_layout.cleanup) {
        reinterpret_cast<CleanupFunction>(g_layout.cleanup)(self);
        return;
    }
    auto release = reinterpret_cast<ReleaseFunction>(g_layout.release);
    BYTE* object = static_cast<BYTE*>(self);
    void*& data = *reinterpret_cast<void**>(object + g_layout.data_offset);
    void*& raw = *reinterpret_cast<void**>(object + g_layout.raw_offset);
    if (data) {
        release(data);
        data = nullptr;
    }
    if (raw) {
        release(raw);
        raw = nullptr;
    }
    *reinterpret_cast<DWORD*>(object + g_layout.length_offset) = 0;
}

int TryPatchResource(void* self, void* archive_map, int archive_group, bool& handled)
{
    handled = false;
    HANDLE file = INVALID_HANDLE_VALUE;
    std::wstring path;
    if (!archive_map || !OpenPatchFile(static_cast<const BYTE*>(archive_map), file, path)) {
        return 0;
    }
    handled = true;

    LARGE_INTEGER size = {};
    if (!GetFileSizeEx(file, &size) || size.QuadPart < 0 ||
        static_cast<ULONGLONG>(size.QuadPart) > g_config.max_file_size || size.HighPart != 0) {
        Log(L"忽略大小无效的替换文件: %s", path.c_str());
        CloseHandle(file);
        handled = false;
        return 0;
    }

    const DWORD file_size = size.LowPart;
    auto allocate = reinterpret_cast<AllocateFunction>(g_layout.allocate);
    ClearResourceBuffers(self);
    BYTE* buffer = static_cast<BYTE*>(allocate(static_cast<size_t>(file_size) + 1));
    if (!buffer) {
        Log(L"替换文件分配失败: %s", path.c_str());
        CloseHandle(file);
        return -1;
    }
    DWORD bytes_read = 0;
    const BOOL read_ok = ReadFile(file, buffer, file_size, &bytes_read, nullptr);
    CloseHandle(file);
    if (!read_ok || bytes_read != file_size) {
        *reinterpret_cast<void**>(static_cast<BYTE*>(self) + g_layout.data_offset) = buffer;
        ClearResourceBuffers(self);
        Log(L"替换文件读取失败: %s", path.c_str());
        return -1;
    }
    buffer[file_size] = 0;
    *reinterpret_cast<DWORD*>(static_cast<BYTE*>(self) + g_layout.length_offset) = file_size;
    if (archive_group == 3) {
        *reinterpret_cast<void**>(static_cast<BYTE*>(self) + g_layout.raw_offset) = buffer;
        *reinterpret_cast<void**>(static_cast<BYTE*>(self) + g_layout.data_offset) = nullptr;
    } else {
        *reinterpret_cast<void**>(static_cast<BYTE*>(self) + g_layout.raw_offset) = nullptr;
        *reinterpret_cast<void**>(static_cast<BYTE*>(self) + g_layout.data_offset) = buffer;
    }
    Log(L"替换命中: %s (%u bytes)", path.c_str(), file_size);
    return static_cast<int>(file_size);
}

int __fastcall HookedLoader(void* self, void*, void* archive_map, int archive_group, int compressed)
{
    bool handled = false;
    const int result = TryPatchResource(self, archive_map, archive_group, handled);
    return handled ? result : g_original_loader(self, archive_map, archive_group, compressed);
}

int __cdecl HandleEaxLoader(void* self, void* archive_map, int archive_group, int compressed);

__declspec(naked) int CallOriginalEaxLoader(void*, void*, void*, int, int)
{
    __asm {
        push ebp
        mov ebp, esp
        push dword ptr [ebp + 24]
        push dword ptr [ebp + 20]
        push dword ptr [ebp + 16]
        mov eax, dword ptr [ebp + 12]
        call dword ptr [ebp + 8]
        pop ebp
        ret
    }
}

int __cdecl HandleEaxLoader(void* self, void* archive_map, int archive_group, int compressed)
{
    bool handled = false;
    const int result = TryPatchResource(self, archive_map, archive_group, handled);
    return handled ? result : CallOriginalEaxLoader(
        g_original_loader_eax, self, archive_map, archive_group, compressed);
}

__declspec(naked) void HookedLoaderEax()
{
    __asm {
        push ebp
        mov ebp, esp
        push dword ptr [ebp + 16]
        push dword ptr [ebp + 12]
        push dword ptr [ebp + 8]
        push eax
        call HandleEaxLoader
        add esp, 16
        pop ebp
        ret 12
    }
}

bool InstallHook()
{
    if (!FindLoader(g_layout)) {
        Log(L"未识别到受支持且唯一的 ThinkerBell 资源加载器");
        return false;
    }
    BYTE* module = reinterpret_cast<BYTE*>(GetModuleHandleW(nullptr));
    Log(L"加载器=%s RVA=0x%X data=0x%X raw=0x%X length=0x%X",
        g_layout.signature_name, static_cast<unsigned>(g_layout.address - module),
        g_layout.data_offset, g_layout.raw_offset, g_layout.length_offset);

    DetourTransactionBegin();
    DetourUpdateThread(GetCurrentThread());
    if (g_layout.eax_this) {
        g_original_loader_eax = g_layout.address;
        DetourAttach(&g_original_loader_eax, HookedLoaderEax);
    } else {
        g_original_loader = reinterpret_cast<LoaderFunction>(g_layout.address);
        DetourAttach(reinterpret_cast<void**>(&g_original_loader), HookedLoader);
    }
    const LONG result = DetourTransactionCommit();
    if (result != NO_ERROR) {
        Log(L"Detours 挂钩失败: %ld", result);
        return false;
    }
    Log(L"散文件替换已启用，目录: %s", JoinPath(g_exe_directory, g_config.patch_directory).c_str());
    return true;
}

}

DWORD WINAPI InitializePatchHook(void* module)
{
    InitializeCriticalSection(&g_log_lock);
    g_log_lock_ready = true;
    const std::wstring dll_directory = ModuleDirectory(static_cast<HMODULE>(module));
    g_exe_directory = ModuleDirectory(GetModuleHandleW(nullptr));
    g_ini_path = JoinPath(dll_directory, L"ThinkerBellPatch.ini");
    g_log_path = JoinPath(dll_directory, L"ThinkerBellPatch.log");
    LoadConfig();
    Log(L"ThinkerBellPatch 初始化");
    if (!g_config.enabled) {
        Log(L"INI 已禁用散文件替换");
        return 0;
    }
    IndexPatchDirectory();
    InstallHook();
    return 0;
}
