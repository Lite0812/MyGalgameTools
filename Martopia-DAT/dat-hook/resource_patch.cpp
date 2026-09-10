#include "resource_patch.h"

#include <algorithm>
#include <cwctype>
#include <new>

namespace
{
constexpr unsigned int kMaximumPatchFolders = 64;
constexpr unsigned long long kMinimumPatchFileSize = 1;
constexpr unsigned long long kMaximumPatchFileSize = 0x7FFFFFFFull;

static std::wstring Trim(std::wstring value)
{
	while (!value.empty() && iswspace(value.front())) value.erase(value.begin());
	while (!value.empty() && iswspace(value.back())) value.pop_back();
	if (value.size() >= 2 && value.front() == L'"' && value.back() == L'"')
		value = value.substr(1, value.size() - 2);
	return value;
}

static std::wstring ReadIniString(const std::wstring& iniPath, const wchar_t* section,
	const wchar_t* key, const wchar_t* fallback)
{
	std::vector<wchar_t> buffer(32768);
	DWORD length = GetPrivateProfileStringW(section, key, fallback, buffer.data(),
		static_cast<DWORD>(buffer.size()), iniPath.c_str());
	return Trim(std::wstring(buffer.data(), length));
}

static bool ReadIniBool(const std::wstring& iniPath, const wchar_t* section,
	const wchar_t* key, bool fallback)
{
	std::wstring value = ReadIniString(iniPath, section, key, fallback ? L"true" : L"false");
	std::transform(value.begin(), value.end(), value.begin(),
		[](wchar_t c) { return static_cast<wchar_t>(towlower(c)); });
	if (value == L"1" || value == L"true" || value == L"yes" || value == L"on") return true;
	if (value == L"0" || value == L"false" || value == L"no" || value == L"off") return false;
	return fallback;
}

static unsigned long long ReadIniUnsigned64(const std::wstring& iniPath, const wchar_t* section,
	const wchar_t* key, unsigned long long fallback, unsigned long long minimum,
	unsigned long long maximum)
{
	std::wstring value = ReadIniString(iniPath, section, key, L"");
	if (value.empty()) return fallback;
	wchar_t* end = nullptr;
	unsigned long long parsed = _wcstoui64(value.c_str(), &end, 10);
	if (!end || *end != L'\0' || parsed < minimum || parsed > maximum) return fallback;
	return parsed;
}

static std::wstring ExpandPath(const std::wstring& path)
{
	DWORD required = ExpandEnvironmentStringsW(path.c_str(), nullptr, 0);
	if (required == 0) return path;
	std::vector<wchar_t> buffer(required);
	if (ExpandEnvironmentStringsW(path.c_str(), buffer.data(), required) == 0) return path;
	return std::wstring(buffer.data());
}

static bool IsAbsolutePath(const std::wstring& path)
{
	return (path.size() >= 3 && path[1] == L':' && (path[2] == L'\\' || path[2] == L'/')) ||
		(path.size() >= 2 && path[0] == L'\\' && path[1] == L'\\');
}

static std::wstring GetFullPath(const std::wstring& path)
{
	DWORD required = GetFullPathNameW(path.c_str(), 0, nullptr, nullptr);
	if (required == 0) return path;
	std::vector<wchar_t> buffer(static_cast<size_t>(required) + 1);
	DWORD length = GetFullPathNameW(path.c_str(), static_cast<DWORD>(buffer.size()), buffer.data(), nullptr);
	return (length == 0 || length >= buffer.size()) ? path : std::wstring(buffer.data(), length);
}

static std::wstring ResolvePatchFolder(const std::wstring& gameDirectory, const std::wstring& configured)
{
	std::wstring expanded = ExpandPath(configured);
	if (!IsAbsolutePath(expanded)) expanded = gameDirectory + L"\\" + expanded;
	return GetFullPath(expanded);
}

static bool DecodeInternalPath(const std::string& path, std::wstring& wide)
{
	wide.clear();
	if (path.empty()) return false;
	const UINT codePages[] = {932u, static_cast<UINT>(CP_UTF8), static_cast<UINT>(CP_ACP)};
	for (UINT codePage : codePages)
	{
		DWORD flags = codePage == CP_ACP ? 0 : MB_ERR_INVALID_CHARS;
		int length = MultiByteToWideChar(codePage, flags, path.data(), static_cast<int>(path.size()), nullptr, 0);
		if (length <= 0) continue;
		wide.resize(static_cast<size_t>(length));
		if (MultiByteToWideChar(codePage, flags, path.data(), static_cast<int>(path.size()),
			wide.data(), length) > 0) return true;
		wide.clear();
	}
	return false;
}

static bool IsSafeSegment(const std::wstring& segment)
{
	if (segment.empty() || segment == L"." || segment == L"..") return false;
	for (wchar_t c : segment)
	{
		if (c < 0x20 || c == L':' || c == L'"' || c == L'<' || c == L'>' ||
			c == L'|' || c == L'*' || c == L'?') return false;
	}
	return true;
}

static bool IsRegularFile(const std::wstring& path)
{
	DWORD attributes = GetFileAttributesW(path.c_str());
	return attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0;
}

static bool ReadPatchFile(const std::wstring& path, unsigned long long maximum,
	std::vector<BYTE>& bytes, std::wstring& error)
{
	bytes.clear();
	HANDLE file = CreateFileW(path.c_str(), GENERIC_READ,
		FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
		FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
	if (file == INVALID_HANDLE_VALUE)
	{
		error = L"无法打开补丁文件，错误码=" + std::to_wstring(GetLastError());
		return false;
	}
	LARGE_INTEGER size{};
	if (!GetFileSizeEx(file, &size) || size.QuadPart < 0 ||
		static_cast<unsigned long long>(size.QuadPart) > maximum)
	{
		error = L"补丁文件大小无效或超过 MaxFileSize";
		CloseHandle(file);
		return false;
	}
	try
	{
		bytes.resize(static_cast<size_t>(size.QuadPart));
	}
	catch (const std::bad_alloc&)
	{
		error = L"内存不足，无法读取补丁文件";
		CloseHandle(file);
		return false;
	}
	BYTE* cursor = bytes.data();
	DWORD remaining = static_cast<DWORD>(bytes.size());
	while (remaining != 0)
	{
		DWORD read = 0;
		if (!ReadFile(file, cursor, remaining, &read, nullptr) || read == 0)
		{
			error = L"读取补丁文件失败，错误码=" + std::to_wstring(GetLastError());
			bytes.clear();
			CloseHandle(file);
			return false;
		}
		cursor += read;
		remaining -= read;
	}
	CloseHandle(file);
	return true;
}
}

MartopiaConfig LoadMartopiaConfig(const std::wstring& gameDirectory)
{
	MartopiaConfig config;
	config.gameDirectory = gameDirectory;
	config.iniPath = gameDirectory + L"\\MartopiaWinmm.ini";
	config.enable = ReadIniBool(config.iniPath, L"Martopia", L"Enable", true);
	config.mode = static_cast<unsigned int>(ReadIniUnsigned64(config.iniPath, L"Martopia", L"Mode", 1, 0, 3));
	config.patchLog = ReadIniBool(config.iniPath, L"ResourcePatch", L"EnableLog", true);
	config.maxPatchFileSize = ReadIniUnsigned64(config.iniPath, L"ResourcePatch", L"MaxFileSize",
		268435456ull, kMinimumPatchFileSize, kMaximumPatchFileSize);
	unsigned int count = static_cast<unsigned int>(ReadIniUnsigned64(config.iniPath,
		L"ResourcePatch", L"PatchFolderCount", 1, 0, kMaximumPatchFolders));
	for (unsigned int index = 0; index < count; ++index)
	{
		std::wstring key = L"PatchFolderName_" + std::to_wstring(index);
		std::wstring fallback = index == 0 ? L"unencrypted" : L"";
		std::wstring folder = ReadIniString(config.iniPath, L"ResourcePatch", key.c_str(), fallback.c_str());
		if (!folder.empty()) config.patchFolders.push_back(ResolvePatchFolder(gameDirectory, folder));
	}
	return config;
}

bool NormalizeResourceRelativePath(const std::string& internalPath,
	std::wstring& relativePath, std::wstring& basename)
{
	relativePath.clear();
	basename.clear();
	std::wstring wide;
	if (!DecodeInternalPath(internalPath, wide) || wide.empty()) return false;
	if (wide.front() == L'\\' || wide.front() == L'/' ||
		(wide.size() >= 2 && wide[1] == L':')) return false;

	std::wstring segment;
	for (size_t index = 0; index <= wide.size(); ++index)
	{
		wchar_t c = index < wide.size() ? wide[index] : L'\\';
		if (c != L'\\' && c != L'/')
		{
			segment.push_back(c);
			continue;
		}
		if (!IsSafeSegment(segment)) return false;
		if (!relativePath.empty()) relativePath.push_back(L'\\');
		relativePath += segment;
		basename = segment;
		segment.clear();
	}
	return !relativePath.empty() && !basename.empty();
}

bool TryLoadResourcePatch(const MartopiaConfig& config, const std::string& internalPath,
	std::vector<BYTE>& bytes, ResourcePatchMatch& match, std::wstring& error)
{
	bytes.clear();
	match = {};
	error.clear();
	if (!config.PatchEnabled()) return false;
	std::wstring relative;
	std::wstring basename;
	if (!NormalizeResourceRelativePath(internalPath, relative, basename)) return false;

	// 后配置的目录优先；同一目录中保留结构的文件优先于平铺文件。
	for (auto root = config.patchFolders.rbegin(); root != config.patchFolders.rend(); ++root)
	{
		for (unsigned int kind = 0; kind < 2; ++kind)
		{
			const bool structured = kind == 0;
			const std::wstring& selected = structured ? relative : basename;
			std::wstring candidate = *root + L"\\" + selected;
			if (!IsRegularFile(candidate)) continue;
			if (!ReadPatchFile(candidate, config.maxPatchFileSize, bytes, error))
			{
				match.diskPath = candidate;
				match.relativePath = selected;
				match.structured = structured;
				return false;
			}
			match.diskPath = candidate;
			match.relativePath = selected;
			match.structured = structured;
			return true;
		}
	}
	return false;
}
