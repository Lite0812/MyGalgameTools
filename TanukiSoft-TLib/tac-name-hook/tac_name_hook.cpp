#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <mmsystem.h>

#include <algorithm>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <new>
#include <string>
#include <unordered_set>
#include <vector>

#ifdef BUILD_WINMM_PROXY
#include "generated/winmm_exports.inc"
extern "C" FARPROC g_winmm_functions[WINMM_PROXY_EXPORT_COUNT];
#endif

#ifndef _M_IX86
#error This hook must be compiled for x86.
#endif

namespace {

using ArchiveOpenFn = int(__thiscall *)(void *, const char *, int *);
using MemStreamCtorFn = void *(__thiscall *)(void *, const void *, unsigned int);
using MemStreamDtorFn = void(__thiscall *)(void *);
using GameAllocFn = void *(__cdecl *)(size_t);
using GameFreeFn = void(__cdecl *)(void *);
using RefStreamFn = void *(__thiscall *)(void *, void *, int, char);
using RefAssignFn = int(__thiscall *)(void *, void **);
using RefReleaseFn = void(__thiscall *)(void *);

HMODULE g_dll_module = nullptr;
HANDLE g_name_file = INVALID_HANDLE_VALUE;
CRITICAL_SECTION g_log_lock;
bool g_log_lock_ready = false;
std::unordered_set<std::string> *g_seen_names = nullptr;
ArchiveOpenFn g_original_open = nullptr;
BYTE *g_hook_target = nullptr;
BYTE *g_trampoline = nullptr;
BYTE g_saved_code[5] = {};
volatile LONG g_install_state = 0;

struct HookConfig {
  bool log_names = true;
  bool loose_files = false;
  std::wstring patch_directory = L"patch";
};

HookConfig g_config;
MemStreamCtorFn g_mem_stream_ctor = nullptr;
MemStreamDtorFn g_mem_stream_dtor = nullptr;
GameAllocFn g_game_alloc = nullptr;
GameFreeFn g_game_free = nullptr;
RefStreamFn g_ref_stream = nullptr;
RefAssignFn g_ref_assign = nullptr;
RefReleaseFn g_ref_release = nullptr;

std::wstring ExecutableDirectory() {
  wchar_t path[MAX_PATH] = {};
  DWORD length = GetModuleFileNameW(nullptr, path, MAX_PATH);
  if (length == 0 || length >= MAX_PATH) {
    return L".";
  }
  wchar_t *slash = wcsrchr(path, L'\\');
  if (slash != nullptr) {
    *slash = L'\0';
  }
  return path;
}

std::wstring OutputPath(const wchar_t *name) {
  std::wstring result = ExecutableDirectory();
  result += L"\\";
  result += name;
  return result;
}

void AppendStatus(const char *format, ...);

bool IsTrueValue(const wchar_t *value) {
  return _wcsicmp(value, L"1") == 0 || _wcsicmp(value, L"true") == 0 ||
         _wcsicmp(value, L"yes") == 0 || _wcsicmp(value, L"on") == 0;
}

void LoadConfig() {
  const std::wstring path = OutputPath(L"tac_name_hook.ini");
  wchar_t value[64] = {};
  GetPrivateProfileStringW(L"TACHook", L"LogNames", L"1", value,
                           static_cast<DWORD>(std::size(value)), path.c_str());
  g_config.log_names = IsTrueValue(value);
  GetPrivateProfileStringW(L"TACHook", L"LooseFilePatch", L"0", value,
                           static_cast<DWORD>(std::size(value)), path.c_str());
  g_config.loose_files = IsTrueValue(value);
  wchar_t directory[512] = {};
  GetPrivateProfileStringW(L"TACHook", L"PatchDir", L"patch", directory,
                           static_cast<DWORD>(std::size(directory)), path.c_str());
  if (directory[0] != L'\0') {
    g_config.patch_directory = directory;
  }
  AppendStatus("Config: LogNames=%d LooseFilePatch=%d PatchDir=%ls",
               g_config.log_names ? 1 : 0, g_config.loose_files ? 1 : 0,
               g_config.patch_directory.c_str());
}

void AppendStatus(const char *format, ...) {
  char message[1024] = {};
  va_list args;
  va_start(args, format);
  int length = _vsnprintf_s(message, sizeof(message), _TRUNCATE, format, args);
  va_end(args);
  if (length <= 0) {
    return;
  }

  const std::wstring path = OutputPath(L"tac_hook_status.log");
  HANDLE file = CreateFileW(path.c_str(), FILE_APPEND_DATA,
                            FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                            OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) {
    return;
  }
  DWORD written = 0;
  WriteFile(file, message, static_cast<DWORD>(length), &written, nullptr);
  WriteFile(file, "\r\n", 2, &written, nullptr);
  CloseHandle(file);
}

bool OpenNameLog() {
  if (!g_config.log_names) {
    return true;
  }
  const std::wstring path = OutputPath(L"tac_names_utf8.txt");
  g_name_file = CreateFileW(path.c_str(), FILE_APPEND_DATA,
                            FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                            OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (g_name_file == INVALID_HANDLE_VALUE) {
    AppendStatus("CreateFile(tac_names_utf8.txt) failed: %lu", GetLastError());
    return false;
  }

  LARGE_INTEGER size = {};
  if (GetFileSizeEx(g_name_file, &size) && size.QuadPart == 0) {
    const BYTE bom[] = {0xEF, 0xBB, 0xBF};
    DWORD written = 0;
    WriteFile(g_name_file, bom, sizeof(bom), &written, nullptr);
  }
  return true;
}

bool SafeCopyPath(const char *source, char *target, size_t capacity,
                  size_t *length) {
  if (source == nullptr || target == nullptr || capacity < 2) {
    return false;
  }
  __try {
    size_t index = 0;
    for (; index + 1 < capacity; ++index) {
      target[index] = source[index];
      if (source[index] == '\0') {
        *length = index;
        return true;
      }
    }
    target[capacity - 1] = '\0';
  } __except (EXCEPTION_EXECUTE_HANDLER) {
    return false;
  }
  return false;
}

void LogArchiveName(const char *path) {
  char raw[4096] = {};
  size_t raw_length = 0;
  if (!SafeCopyPath(path, raw, sizeof(raw), &raw_length) || raw_length == 0 ||
      !g_config.log_names || !g_log_lock_ready ||
      g_name_file == INVALID_HANDLE_VALUE) {
    return;
  }

  EnterCriticalSection(&g_log_lock);
  bool is_new = true;
  if (g_seen_names != nullptr) {
    try {
      is_new = g_seen_names->insert(std::string(raw, raw_length)).second;
    } catch (...) {
      is_new = true;
    }
  }

  if (is_new) {
    wchar_t wide[4096] = {};
    char utf8[12288] = {};
    int wide_length = MultiByteToWideChar(932, 0, raw,
                                           static_cast<int>(raw_length), wide,
                                           static_cast<int>(std::size(wide)));
    if (wide_length <= 0) {
      wide_length = MultiByteToWideChar(
          CP_ACP, 0, raw, static_cast<int>(raw_length), wide,
          static_cast<int>(std::size(wide)));
    }
    if (wide_length > 0) {
      int utf8_length = WideCharToMultiByte(
          CP_UTF8, 0, wide, wide_length, utf8,
          static_cast<int>(std::size(utf8) - 2), nullptr, nullptr);
      if (utf8_length > 0) {
        utf8[utf8_length++] = '\r';
        utf8[utf8_length++] = '\n';
        DWORD written = 0;
        WriteFile(g_name_file, utf8, static_cast<DWORD>(utf8_length), &written,
                  nullptr);
        FlushFileBuffers(g_name_file);
      }
    }
  }
  LeaveCriticalSection(&g_log_lock);
}

bool PathToWide(const char *path, std::wstring *result) {
  if (path == nullptr || result == nullptr) {
    return false;
  }
  char raw[4096] = {};
  size_t length = 0;
  if (!SafeCopyPath(path, raw, sizeof(raw), &length) || length == 0) {
    return false;
  }
  int wide_length = MultiByteToWideChar(932, 0, raw, static_cast<int>(length),
                                        nullptr, 0);
  UINT code_page = 932;
  if (wide_length <= 0) {
    code_page = CP_ACP;
    wide_length = MultiByteToWideChar(code_page, 0, raw,
                                      static_cast<int>(length), nullptr, 0);
  }
  if (wide_length <= 0) {
    return false;
  }
  result->assign(static_cast<size_t>(wide_length), L'\0');
  return MultiByteToWideChar(code_page, 0, raw, static_cast<int>(length),
                             &(*result)[0], wide_length) == wide_length;
}

bool IsSafeRelativePath(std::wstring *path) {
  if (path == nullptr || path->empty()) {
    return false;
  }
  for (wchar_t &character : *path) {
    if (character == L'/') {
      character = L'\\';
    }
  }
  while (!path->empty() && ((*path)[0] == L'\\' || (*path)[0] == L'/')) {
    path->erase(path->begin());
  }
  if (path->empty() || (*path)[0] == L':' || path->find(L':') != std::wstring::npos) {
    return false;
  }
  size_t cursor = 0;
  while (cursor <= path->size()) {
    const size_t next = path->find(L'\\', cursor);
    const std::wstring component = path->substr(
        cursor, next == std::wstring::npos ? std::wstring::npos : next - cursor);
    if (component == L"..") {
      return false;
    }
    if (next == std::wstring::npos) {
      break;
    }
    cursor = next + 1;
  }
  return true;
}

std::wstring PatchRoot() {
  std::wstring root = g_config.patch_directory;
  const bool absolute = (root.size() >= 2 && root[1] == L':') ||
                        (root.size() >= 2 && root[0] == L'\\' && root[1] == L'\\');
  if (!absolute) {
    root = ExecutableDirectory() + L"\\" + root;
  }
  while (!root.empty() && (root.back() == L'\\' || root.back() == L'/')) {
    root.pop_back();
  }
  return root;
}

bool ArchivePathHash(void *archive, const char *path, unsigned long long *hash) {
  if (archive == nullptr || path == nullptr || hash == nullptr) {
    return false;
  }
  char raw[4096] = {};
  size_t length = 0;
  if (!SafeCopyPath(path, raw, sizeof(raw), &length) || length == 0) {
    return false;
  }
  unsigned int seed = 0;
  __try {
    seed = *reinterpret_cast<const unsigned int *>(
        static_cast<const BYTE *>(archive) + 0x30);
  } __except (EXCEPTION_EXECUTE_HANDLER) {
    return false;
  }

  unsigned long long value = 0;
  size_t index = 0;
  while (index < length) {
    unsigned char byte = static_cast<unsigned char>(raw[index]);
    if ((byte >= 0x81 && byte <= 0x9F) ||
        (byte >= 0xE0 && byte <= 0xFC)) {
      const signed char first = static_cast<signed char>(byte);
      value = value * 104729ULL + static_cast<long long>(first) + seed;
      ++index;
      if (index < length) {
        const signed char second = static_cast<signed char>(raw[index]);
        value = value * 104729ULL + static_cast<long long>(second) + seed;
        ++index;
      }
      continue;
    }
    if (byte >= 'a' && byte <= 'z') {
      byte = static_cast<unsigned char>(byte - ('a' - 'A'));
    } else if (byte == '\\') {
      byte = '/';
    }
    value = value * 104729ULL + static_cast<signed char>(byte) + seed;
    ++index;
  }
  *hash = value;
  return true;
}

void AddCandidate(std::vector<std::wstring> *candidates,
                  const std::wstring &candidate) {
  if (candidate.empty() ||
      std::find(candidates->begin(), candidates->end(), candidate) !=
          candidates->end()) {
    return;
  }
  candidates->push_back(candidate);
}

std::vector<std::wstring> PatchCandidates(void *archive,
                                          const char *archive_path) {
  std::vector<std::wstring> candidates;
  std::wstring relative;
  if (!PathToWide(archive_path, &relative) || !IsSafeRelativePath(&relative)) {
    return candidates;
  }
  const std::wstring root = PatchRoot();
  if (root.empty()) {
    return candidates;
  }

  // Keep directory-aware replacements most specific, then accept flat files.
  AddCandidate(&candidates, root + L"\\" + relative);
  const size_t separator = relative.find_last_of(L"\\/");
  const std::wstring base_name = separator == std::wstring::npos
                                     ? relative
                                     : relative.substr(separator + 1);
  AddCandidate(&candidates, root + L"\\" + base_name);

  unsigned long long hash = 0;
  if (ArchivePathHash(archive, archive_path, &hash)) {
    wchar_t hash_name[32] = {};
    _snwprintf_s(hash_name, std::size(hash_name), _TRUNCATE, L"%016llx", hash);
    const size_t dot = base_name.find_last_of(L'.');
    const std::wstring extension = dot == std::wstring::npos
                                       ? std::wstring()
                                       : base_name.substr(dot);
    AddCandidate(&candidates, root + L"\\" + hash_name + extension);
    AddCandidate(&candidates, root + L"\\" + hash_name);
  }
  return candidates;
}

std::wstring FindLooseFile(void *archive, const char *archive_path) {
  const std::vector<std::wstring> candidates =
      PatchCandidates(archive, archive_path);
  for (const std::wstring &candidate : candidates) {
    const DWORD attributes = GetFileAttributesW(candidate.c_str());
    if (attributes != INVALID_FILE_ATTRIBUTES &&
        (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
      return candidate;
    }
  }

  unsigned long long hash = 0;
  if (!ArchivePathHash(archive, archive_path, &hash)) {
    return std::wstring();
  }
  wchar_t hash_name[32] = {};
  _snwprintf_s(hash_name, std::size(hash_name), _TRUNCATE, L"%016llx", hash);
  const std::wstring wildcard = PatchRoot() + L"\\" + hash_name + L".*";
  WIN32_FIND_DATAW find_data = {};
  HANDLE find = FindFirstFileW(wildcard.c_str(), &find_data);
  if (find == INVALID_HANDLE_VALUE) {
    return std::wstring();
  }
  std::wstring result;
  do {
    if ((find_data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
      result = PatchRoot() + L"\\" + find_data.cFileName;
      break;
    }
  } while (FindNextFileW(find, &find_data));
  FindClose(find);
  return result;
}

bool ReadLooseFile(const std::wstring &path, std::vector<BYTE> *data) {
  if (data == nullptr) {
    return false;
  }
  HANDLE file = CreateFileW(path.c_str(), GENERIC_READ,
                            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                            nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) {
    return false;
  }
  LARGE_INTEGER size = {};
  bool ok = GetFileSizeEx(file, &size) && size.QuadPart >= 0 &&
            size.QuadPart <= 0xFFFFFFFFLL;
  if (ok) {
    data->resize(static_cast<size_t>(size.QuadPart));
    size_t total = 0;
    while (total < data->size()) {
      DWORD chunk = 0;
      const DWORD request = static_cast<DWORD>(std::min<size_t>(
          data->size() - total, 1u << 20));
      if (!ReadFile(file, data->data() + total, request, &chunk, nullptr) ||
          chunk == 0) {
        ok = false;
        break;
      }
      total += chunk;
    }
  }
  CloseHandle(file);
  if (!ok) {
    data->clear();
  }
  return ok;
}

bool ResolveStreamHelpers() {
  if (g_mem_stream_ctor != nullptr && g_mem_stream_dtor != nullptr &&
      g_game_alloc != nullptr && g_game_free != nullptr &&
      g_ref_stream != nullptr && g_ref_assign != nullptr &&
      g_ref_release != nullptr) {
    return true;
  }
  BYTE *module = reinterpret_cast<BYTE *>(GetModuleHandleW(nullptr));
  if (module == nullptr) {
    return false;
  }
  // IDA virtual addresses converted to PE RVAs (preferred base 0x00400000).
  g_mem_stream_ctor = reinterpret_cast<MemStreamCtorFn>(module + 0x177500);
  g_mem_stream_dtor = reinterpret_cast<MemStreamDtorFn>(module + 0x1776D0);
  g_game_alloc = reinterpret_cast<GameAllocFn>(module + 0x2E7D67);
  g_game_free = reinterpret_cast<GameFreeFn>(module + 0x2E7DAE);
  g_ref_stream = reinterpret_cast<RefStreamFn>(module + 0x1B4AB0);
  g_ref_assign = reinterpret_cast<RefAssignFn>(module + 0x08C290);
  g_ref_release = reinterpret_cast<RefReleaseFn>(module + 0x06D3E0);
  const BYTE ctor_prefix[] = {0x55, 0x8B, 0xEC, 0x6A, 0xFF};
  const BYTE dtor_prefix[] = {0x55, 0x8B, 0xEC, 0x51, 0x53};
  const BYTE alloc_prefix[] = {0x55, 0x8B, 0xEC, 0xEB, 0x0D};
  const BYTE assign_prefix[] = {0x55, 0x8B, 0xEC, 0x56, 0x8B};
  const BYTE release_prefix[] = {0x56, 0x57, 0x8B, 0xF9, 0x8B, 0x77, 0x08};
  if (memcmp(reinterpret_cast<const void *>(g_mem_stream_ctor), ctor_prefix,
             sizeof(ctor_prefix)) != 0 ||
      memcmp(reinterpret_cast<const void *>(g_mem_stream_dtor), dtor_prefix,
             sizeof(dtor_prefix)) != 0 ||
      memcmp(reinterpret_cast<const void *>(g_game_alloc), alloc_prefix,
             sizeof(alloc_prefix)) != 0 ||
      *reinterpret_cast<const BYTE *>(g_game_free) != 0xE9 ||
      memcmp(reinterpret_cast<const void *>(g_ref_stream), ctor_prefix,
             sizeof(ctor_prefix)) != 0 ||
      memcmp(reinterpret_cast<const void *>(g_ref_assign), assign_prefix,
             sizeof(assign_prefix)) != 0 ||
      memcmp(reinterpret_cast<const void *>(g_ref_release), release_prefix,
             sizeof(release_prefix)) != 0) {
    g_mem_stream_ctor = nullptr;
    g_mem_stream_dtor = nullptr;
    g_game_alloc = nullptr;
    g_game_free = nullptr;
    g_ref_stream = nullptr;
    g_ref_assign = nullptr;
    g_ref_release = nullptr;
    AppendStatus("Loose stream helper validation failed; loose patch disabled");
    return false;
  }
  return true;
}

void DestroyMemStream(void *stream) {
  if (stream == nullptr) {
    return;
  }
  g_mem_stream_dtor(stream);
  g_game_free(stream);
}

bool PopulateMemStream(void *stream, const std::vector<BYTE> &data) {
  if (data.empty()) {
    return true;
  }

  constexpr size_t kBlockSize = 0x2800;
  BYTE *module = reinterpret_cast<BYTE *>(GetModuleHandleW(nullptr));
  BYTE *descriptors = nullptr;
  __try {
    descriptors = *reinterpret_cast<BYTE **>(
        static_cast<BYTE *>(stream) + 0x0C);
  } __except (EXCEPTION_EXECUTE_HANDLER) {
    return false;
  }
  if (module == nullptr || descriptors == nullptr) {
    return false;
  }

  const size_t block_count = (data.size() + kBlockSize - 1) / kBlockSize;
  const DWORD expected_block_vtable =
      reinterpret_cast<DWORD>(module + 0x36962C);
  size_t offset = 0;
  for (size_t index = 0; index < block_count; ++index) {
    BYTE *holder = nullptr;
    __try {
      holder = *reinterpret_cast<BYTE **>(descriptors + index * 0x10 + 0x04);
      if (holder == nullptr ||
          *reinterpret_cast<const DWORD *>(holder) != expected_block_vtable) {
        return false;
      }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
      return false;
    }

    BYTE *block = static_cast<BYTE *>(g_game_alloc(kBlockSize));
    if (block == nullptr) {
      return false;
    }
    const size_t chunk = std::min(kBlockSize, data.size() - offset);
    memcpy(block, data.data() + offset, chunk);
    if (chunk < kBlockSize) {
      memset(block + chunk, 0, kBlockSize - chunk);
    }
    __try {
      *reinterpret_cast<BYTE **>(holder + 0x04) = block;
      holder[0x08] = 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
      g_game_free(block);
      return false;
    }
    offset += chunk;
  }
  return true;
}

bool TryOpenLooseFile(void *archive, const char *archive_path, int *result) {
  if (!g_config.loose_files || result == nullptr || !ResolveStreamHelpers()) {
    return false;
  }
  const std::wstring patch_path = FindLooseFile(archive, archive_path);
  if (patch_path.empty()) {
    return false;
  }
  std::vector<BYTE> data;
  if (!ReadLooseFile(patch_path, &data)) {
    AppendStatus("Loose file read failed: %ls", patch_path.c_str());
    return false;
  }
  void *stream = g_game_alloc(0x20);
  if (stream == nullptr) {
    return false;
  }
  bool constructed = false;
  try {
    constructed = g_mem_stream_ctor(
                      stream, nullptr,
                      static_cast<unsigned int>(data.size())) != nullptr;
    if (!constructed || !PopulateMemStream(stream, data)) {
      if (constructed) {
        DestroyMemStream(stream);
      } else {
        g_game_free(stream);
      }
      AppendStatus("Loose stream construction failed: %ls", patch_path.c_str());
      return false;
    }
  } catch (...) {
    if (constructed) {
      DestroyMemStream(stream);
    } else {
      g_game_free(stream);
    }
    AppendStatus("Loose stream construction failed: %ls", patch_path.c_str());
    return false;
  }

  DWORD ref_storage[4] = {};
  bool wrapped = false;
  try {
    g_ref_stream(ref_storage, stream, 1, 0);
    wrapped = true;
    if (g_ref_assign(ref_storage, reinterpret_cast<void **>(result)) < 0 ||
        *result == 0) {
      g_ref_release(ref_storage);
      AppendStatus("Loose stream reference assignment failed: %ls",
                   patch_path.c_str());
      return false;
    }
    g_ref_release(ref_storage);
  } catch (...) {
    if (wrapped) {
      g_ref_release(ref_storage);
    } else {
      DestroyMemStream(stream);
    }
    AppendStatus("Loose stream reference assignment failed: %ls", patch_path.c_str());
    return false;
  }
  AppendStatus("Loose override: %s -> %ls (%zu bytes)", archive_path,
               patch_path.c_str(), data.size());
  return true;
}

std::vector<int> ParsePattern(const char *text) {
  std::vector<int> result;
  const char *cursor = text;
  while (*cursor != '\0') {
    while (*cursor == ' ') {
      ++cursor;
    }
    if (*cursor == '\0') {
      break;
    }
    if (*cursor == '?') {
      result.push_back(-1);
      while (*cursor == '?') {
        ++cursor;
      }
    } else {
      char token[3] = {cursor[0], cursor[1], '\0'};
      result.push_back(static_cast<int>(strtoul(token, nullptr, 16)));
      cursor += 2;
    }
    while (*cursor != '\0' && *cursor != ' ') {
      ++cursor;
    }
  }
  return result;
}

bool PatternMatches(const BYTE *position, const BYTE *end,
                    const std::vector<int> &pattern) {
  if (position > end || static_cast<size_t>(end - position) < pattern.size()) {
    return false;
  }
  for (size_t index = 0; index < pattern.size(); ++index) {
    if (pattern[index] >= 0 && position[index] != pattern[index]) {
      return false;
    }
  }
  return true;
}

std::vector<BYTE *> ScanPattern(BYTE *begin, BYTE *end,
                                const std::vector<int> &pattern) {
  std::vector<BYTE *> result;
  if (pattern.empty() || end <= begin ||
      static_cast<size_t>(end - begin) < pattern.size()) {
    return result;
  }
  BYTE *last = end - pattern.size();
  for (BYTE *position = begin; position <= last; ++position) {
    if (PatternMatches(position, end, pattern)) {
      result.push_back(position);
    }
  }
  return result;
}

bool MainTextSection(BYTE **begin, BYTE **end) {
  BYTE *module = reinterpret_cast<BYTE *>(GetModuleHandleW(nullptr));
  if (module == nullptr) {
    return false;
  }
  auto *dos = reinterpret_cast<IMAGE_DOS_HEADER *>(module);
  if (dos->e_magic != IMAGE_DOS_SIGNATURE) {
    return false;
  }
  auto *nt = reinterpret_cast<IMAGE_NT_HEADERS32 *>(module + dos->e_lfanew);
  if (nt->Signature != IMAGE_NT_SIGNATURE ||
      nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC) {
    return false;
  }
  IMAGE_SECTION_HEADER *section = IMAGE_FIRST_SECTION(nt);
  for (WORD index = 0; index < nt->FileHeader.NumberOfSections;
       ++index, ++section) {
    char name[9] = {};
    memcpy(name, section->Name, 8);
    if (strcmp(name, ".text") == 0) {
      *begin = module + section->VirtualAddress;
      DWORD size = std::max(section->Misc.VirtualSize, section->SizeOfRawData);
      *end = *begin + size;
      return true;
    }
  }
  return false;
}

bool ContainsPattern(BYTE *begin, BYTE *end, const std::vector<int> &pattern) {
  return !ScanPattern(begin, end, pattern).empty();
}

BYTE *FindArchiveOpen() {
  BYTE *text_begin = nullptr;
  BYTE *text_end = nullptr;
  if (!MainTextSection(&text_begin, &text_end)) {
    AppendStatus("Unable to locate the main module .text section");
    return nullptr;
  }

  const std::vector<int> anchor = ParsePattern(
      "0F B6 C2 35 C5 9D 1C 81 69 C8 93 01 00 01 "
      "0F B6 C6 33 C8 69 C1 93 01 00 01");
  const std::vector<int> prologue = ParsePattern(
      "55 8B EC 6A FF 68 ?? ?? ?? ?? 64 A1 00 00 00 00 "
      "50 83 EC ?? A1 ?? ?? ?? ?? 33 C5 89 45 ?? 53 56 57");
  const std::vector<int> arguments =
      ParsePattern("8B 55 08 8B CA 8B 45 0C");
  const std::vector<int> known_entry = ParsePattern(
      "55 8B EC 6A FF 68 ?? ?? ?? ?? 64 A1 00 00 00 00 "
      "50 83 EC ?? A1 ?? ?? ?? ?? 33 C5 89 45 ?? 53 56 57 "
      "50 8D 45 ?? 64 A3 00 00 00 00 8B F1 89 75 ?? "
      "8B 55 08 8B CA 8B 45 0C");

  std::vector<BYTE *> candidates;
  const std::vector<BYTE *> anchors = ScanPattern(text_begin, text_end, anchor);
  for (BYTE *anchor_position : anchors) {
    BYTE *low = anchor_position -
                std::min<size_t>(0x600, anchor_position - text_begin);
    BYTE *candidate = nullptr;
    for (BYTE *position = anchor_position; position >= low; --position) {
      if (PatternMatches(position, text_end, prologue)) {
        candidate = position;
        break;
      }
      if (position == low) {
        break;
      }
    }
    if (candidate == nullptr) {
      continue;
    }
    BYTE *argument_end = std::min(candidate + 0x100, text_end);
    if (!ContainsPattern(candidate, argument_end, arguments)) {
      continue;
    }
    if (std::find(candidates.begin(), candidates.end(), candidate) ==
        candidates.end()) {
      candidates.push_back(candidate);
    }
  }

  if (candidates.empty()) {
    for (BYTE *candidate : ScanPattern(text_begin, text_end, known_entry)) {
      BYTE *search_end = std::min(candidate + 0x600, text_end);
      if (ContainsPattern(candidate, search_end, anchor)) {
        candidates.push_back(candidate);
      }
    }
  }

  if (candidates.size() != 1) {
    AppendStatus("Signature rejected: anchors=%zu candidates=%zu", anchors.size(),
                 candidates.size());
    return nullptr;
  }

  BYTE *module = reinterpret_cast<BYTE *>(GetModuleHandleW(nullptr));
  AppendStatus("Signature matched at RVA 0x%08lX (anchors=%zu)",
               static_cast<unsigned long>(candidates[0] - module),
               anchors.size());
  return candidates[0];
}

int __fastcall HookArchiveOpen(void *self, void *, const char *path,
                               int *result) {
  LogArchiveName(path);
  try {
    if (TryOpenLooseFile(self, path, result)) {
      return 0;
    }
  } catch (...) {
    AppendStatus("Loose override exception; falling back to TAC: %s",
                 path != nullptr ? path : "[null]");
  }
  return g_original_open(self, path, result);
}

bool InstallInlineHook(BYTE *target) {
  if (target == nullptr || target[0] != 0x55 || target[1] != 0x8B ||
      target[2] != 0xEC || target[3] != 0x6A || target[4] != 0xFF) {
    AppendStatus("Target prologue validation failed");
    return false;
  }

  BYTE *trampoline = static_cast<BYTE *>(
      VirtualAlloc(nullptr, 10, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
  if (trampoline == nullptr) {
    AppendStatus("VirtualAlloc(trampoline) failed: %lu", GetLastError());
    return false;
  }

  memcpy(g_saved_code, target, sizeof(g_saved_code));
  memcpy(trampoline, target, 5);
  trampoline[5] = 0xE9;
  *reinterpret_cast<DWORD *>(trampoline + 6) =
      static_cast<DWORD>((target + 5) - (trampoline + 10));

  BYTE patch[5] = {0xE9, 0, 0, 0, 0};
  *reinterpret_cast<DWORD *>(patch + 1) = static_cast<DWORD>(
      reinterpret_cast<BYTE *>(&HookArchiveOpen) - (target + 5));

  // Publish the callable trampoline before making the detour visible to any
  // other thread.
  g_trampoline = trampoline;
  g_original_open = reinterpret_cast<ArchiveOpenFn>(trampoline);

  DWORD old_protection = 0;
  if (!VirtualProtect(target, sizeof(patch), PAGE_EXECUTE_READWRITE,
                      &old_protection)) {
    AppendStatus("VirtualProtect(target) failed: %lu", GetLastError());
    g_original_open = nullptr;
    g_trampoline = nullptr;
    VirtualFree(trampoline, 0, MEM_RELEASE);
    return false;
  }
  memcpy(target, patch, sizeof(patch));
  FlushInstructionCache(GetCurrentProcess(), target, sizeof(patch));
  DWORD ignored = 0;
  VirtualProtect(target, sizeof(patch), old_protection, &ignored);

  g_hook_target = target;
  return true;
}

void RemoveInlineHook() {
  if (g_hook_target == nullptr) {
    return;
  }
  DWORD old_protection = 0;
  if (VirtualProtect(g_hook_target, sizeof(g_saved_code),
                     PAGE_EXECUTE_READWRITE, &old_protection)) {
    memcpy(g_hook_target, g_saved_code, sizeof(g_saved_code));
    FlushInstructionCache(GetCurrentProcess(), g_hook_target,
                          sizeof(g_saved_code));
    DWORD ignored = 0;
    VirtualProtect(g_hook_target, sizeof(g_saved_code), old_protection,
                   &ignored);
  }
  g_hook_target = nullptr;
  g_original_open = nullptr;
  if (g_trampoline != nullptr) {
    VirtualFree(g_trampoline, 0, MEM_RELEASE);
    g_trampoline = nullptr;
  }
}

bool InstallHook() {
  LONG previous = InterlockedCompareExchange(&g_install_state, 1, 0);
  if (previous == 2) {
    return true;
  }
  if (previous != 0) {
    return false;
  }

  InitializeCriticalSection(&g_log_lock);
  g_log_lock_ready = true;
  LoadConfig();
  g_seen_names = new (std::nothrow) std::unordered_set<std::string>();
  if (!OpenNameLog()) {
    InterlockedExchange(&g_install_state, -1);
    return false;
  }

  BYTE *target = FindArchiveOpen();
  if (target == nullptr || !InstallInlineHook(target)) {
    InterlockedExchange(&g_install_state, -1);
    return false;
  }
  AppendStatus("Hook installed successfully");
  InterlockedExchange(&g_install_state, 2);
  return true;
}

DWORD WINAPI InitializeThread(void *) {
  InstallHook();
  return 0;
}

#ifdef BUILD_WINMM_PROXY

HMODULE g_real_winmm = nullptr;
INIT_ONCE g_winmm_once = INIT_ONCE_STATIC_INIT;

BOOL CALLBACK LoadRealWinmm(PINIT_ONCE, PVOID, PVOID *) {
  wchar_t path[MAX_PATH] = {};
  UINT length = GetSystemDirectoryW(path, MAX_PATH);
  if (length == 0 || length + 11 >= MAX_PATH) {
    return FALSE;
  }
  wcscat_s(path, L"\\winmm.dll");
  g_real_winmm = LoadLibraryW(path);
  return g_real_winmm != nullptr && g_real_winmm != g_dll_module;
}

#endif

}  // namespace

extern "C" __declspec(dllexport) BOOL WINAPI InstallTacNameHook() {
  return InstallHook() ? TRUE : FALSE;
}

extern "C" __declspec(dllexport) DWORD WINAPI TacNameHookTargetRva() {
  if (g_hook_target == nullptr) {
    return 0;
  }
  return static_cast<DWORD>(
      g_hook_target - reinterpret_cast<BYTE *>(GetModuleHandleW(nullptr)));
}

#ifdef BUILD_WINMM_PROXY

extern "C" {
FARPROC g_winmm_functions[WINMM_PROXY_EXPORT_COUNT] = {};

FARPROC __cdecl ResolveWinmmExport(unsigned int index) {
  if (index >= WINMM_PROXY_EXPORT_COUNT ||
      !InitOnceExecuteOnce(&g_winmm_once, LoadRealWinmm, nullptr, nullptr)) {
    AppendStatus("Unable to load the real 32-bit system winmm.dll");
    ExitProcess(ERROR_MOD_NOT_FOUND);
  }

  const WinmmExportSpec &spec = kWinmmExportSpecs[index];
  FARPROC function =
      spec.name != nullptr
          ? GetProcAddress(g_real_winmm, spec.name)
          : GetProcAddress(g_real_winmm,
                           reinterpret_cast<LPCSTR>(
                               static_cast<ULONG_PTR>(spec.ordinal)));
  if (function == nullptr) {
    AppendStatus("System WinMM export resolution failed: index=%u ordinal=%u name=%s",
                 index, spec.ordinal,
                 spec.name != nullptr ? spec.name : "[NONAME]");
    ExitProcess(ERROR_PROC_NOT_FOUND);
  }

  PVOID previous = InterlockedCompareExchangePointer(
      reinterpret_cast<PVOID volatile *>(&g_winmm_functions[index]),
      reinterpret_cast<PVOID>(function), nullptr);
  return previous != nullptr ? reinterpret_cast<FARPROC>(previous) : function;
}
}  // extern "C"

#endif

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID reserved) {
  if (reason == DLL_PROCESS_ATTACH) {
    g_dll_module = module;
    DisableThreadLibraryCalls(module);
    HANDLE thread = CreateThread(nullptr, 0, InitializeThread, nullptr, 0, nullptr);
    if (thread != nullptr) {
      CloseHandle(thread);
    }
  } else if (reason == DLL_PROCESS_DETACH && reserved == nullptr) {
    RemoveInlineHook();
    if (g_name_file != INVALID_HANDLE_VALUE) {
      CloseHandle(g_name_file);
      g_name_file = INVALID_HANDLE_VALUE;
    }
  }
  return TRUE;
}
