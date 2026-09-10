#include "resource_patch.h"

#include <Windows.h>

#include <cstdio>
#include <string>
#include <vector>

namespace
{
static bool EnsureDirectory(const std::wstring& path)
{
	if (CreateDirectoryW(path.c_str(), nullptr)) return true;
	return GetLastError() == ERROR_ALREADY_EXISTS;
}

static bool WriteFileBytes(const std::wstring& path, const char* text)
{
	HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
		FILE_ATTRIBUTE_NORMAL, nullptr);
	if (file == INVALID_HANDLE_VALUE) return false;
	DWORD length = text ? static_cast<DWORD>(strlen(text)) : 0;
	DWORD written = 0;
	bool ok = length == 0 || (WriteFile(file, text, length, &written, nullptr) && written == length);
	CloseHandle(file);
	return ok;
}

static std::string AsText(const std::vector<BYTE>& bytes)
{
	return std::string(bytes.begin(), bytes.end());
}

static bool Check(bool condition, const char* message)
{
	if (condition) return true;
	std::printf("失败：%s\n", message);
	return false;
}
}

int wmain()
{
	wchar_t modulePath[MAX_PATH * 4]{};
	GetModuleFileNameW(nullptr, modulePath, _countof(modulePath));
	std::wstring root(modulePath);
	root.resize(root.find_last_of(L"\\/"));
	root += L"\\resource_patch_test_data";
	std::wstring first = root + L"\\first";
	std::wstring second = root + L"\\second";
	std::wstring firstStructured = first + L"\\SS";
	std::wstring secondStructured = second + L"\\SS";
	if (!EnsureDirectory(root) || !EnsureDirectory(first) || !EnsureDirectory(second) ||
		!EnsureDirectory(firstStructured) || !EnsureDirectory(secondStructured))
	{
		std::printf("失败：无法创建测试目录\n");
		return 1;
	}

	bool ok = true;
	ok &= WriteFileBytes(firstStructured + L"\\script.lua", "first-structured");
	ok &= WriteFileBytes(second + L"\\script.lua", "second-flat");
	ok &= WriteFileBytes(secondStructured + L"\\script.lua", "second-structured");
	ok &= WriteFileBytes(second + L"\\empty.bin", nullptr);
	if (!ok)
	{
		std::printf("失败：无法创建测试文件\n");
		return 1;
	}

	std::wstring executableDirectory = root.substr(0, root.find_last_of(L"\\/"));
	std::wstring iniPath = executableDirectory + L"\\MartopiaWinmm.ini";
	ok &= WriteFileBytes(iniPath,
		"[Martopia]\r\n"
		"Enable = true\r\n"
		"Mode = 3\r\n"
		"[ResourcePatch]\r\n"
		"PatchFolderCount = 2\r\n"
		"PatchFolderName_0 = resource_patch_test_data\\first\r\n"
		"PatchFolderName_1 = resource_patch_test_data\\second\r\n"
		"EnableLog = false\r\n"
		"MaxFileSize = 1024\r\n");
	MartopiaConfig config = LoadMartopiaConfig(executableDirectory);
	ok &= Check(config.enable && config.mode == 3 && config.PatchEnabled() && config.DumpEnabled(),
		"INI 模式解析错误");
	ok &= Check(!config.patchLog && config.maxPatchFileSize == 1024,
		"INI 日志或大小配置解析错误");
	ok &= Check(config.patchFolders.size() == 2 &&
		_wcsicmp(config.patchFolders[0].c_str(), first.c_str()) == 0 &&
		_wcsicmp(config.patchFolders[1].c_str(), second.c_str()) == 0,
		"INI 多目录解析或相对路径解析错误");
	config.mode = 1;
	std::vector<BYTE> bytes;
	ResourcePatchMatch match;
	std::wstring error;

	ok &= Check(TryLoadResourcePatch(config, "SS\\script.lua", bytes, match, error),
		"未命中后配置目录的结构文件");
	ok &= Check(AsText(bytes) == "second-structured", "结构文件内容或目录优先级错误");
	ok &= Check(match.structured, "结构匹配标记错误");

	DeleteFileW((secondStructured + L"\\script.lua").c_str());
	ok &= Check(TryLoadResourcePatch(config, "SS\\script.lua", bytes, match, error),
		"未命中后配置目录的平铺文件");
	ok &= Check(AsText(bytes) == "second-flat", "平铺文件内容或目录优先级错误");
	ok &= Check(!match.structured, "平铺匹配标记错误");

	ok &= Check(TryLoadResourcePatch(config, "folder\\empty.bin", bytes, match, error),
		"空补丁文件未被识别");
	ok &= Check(bytes.empty(), "空补丁文件读出了额外数据");

	std::wstring relative;
	std::wstring basename;
	ok &= Check(NormalizeResourceRelativePath("SS\\script.lua", relative, basename),
		"合法相对路径被拒绝");
	ok &= Check(relative == L"SS\\script.lua" && basename == L"script.lua",
		"合法相对路径规范化错误");
	ok &= Check(!NormalizeResourceRelativePath("..\\script.lua", relative, basename),
		"上级目录路径未被拒绝");
	ok &= Check(!NormalizeResourceRelativePath("C:\\script.lua", relative, basename),
		"绝对路径未被拒绝");
	ok &= Check(!NormalizeResourceRelativePath("\\\\server\\share\\script.lua", relative, basename),
		"UNC 路径未被拒绝");

	config.maxPatchFileSize = 4;
	error.clear();
	ok &= Check(!TryLoadResourcePatch(config, "SS\\script.lua", bytes, match, error),
		"超出大小限制的补丁文件未被拒绝");
	ok &= Check(!error.empty(), "大小限制失败没有返回中文原因");

	std::printf(ok ? "全部资源覆盖单元测试通过\n" : "资源覆盖单元测试存在失败\n");
	return ok ? 0 : 1;
}
