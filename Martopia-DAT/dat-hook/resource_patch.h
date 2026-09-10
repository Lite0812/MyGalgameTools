#pragma once

#include <Windows.h>

#include <string>
#include <vector>

struct MartopiaConfig
{
	bool enable = true;
	unsigned int mode = 1;
	bool patchLog = true;
	unsigned long long maxPatchFileSize = 268435456ull;
	std::wstring iniPath;
	std::wstring gameDirectory;
	std::vector<std::wstring> patchFolders;

	bool PatchEnabled() const { return enable && (mode == 1 || mode == 3); }
	bool DumpEnabled() const { return enable && (mode == 2 || mode == 3); }
};

struct ResourcePatchMatch
{
	std::wstring diskPath;
	std::wstring relativePath;
	bool structured = false;
};

// INI 不存在时也使用结构体中的安全默认值，便于直接部署 DLL。
MartopiaConfig LoadMartopiaConfig(const std::wstring& gameDirectory);

// 返回 false 代表未命中或读取失败；error 仅在候选存在但无法读取时有内容。
bool TryLoadResourcePatch(const MartopiaConfig& config, const std::string& internalPath,
	std::vector<BYTE>& bytes, ResourcePatchMatch& match, std::wstring& error);

// 暴露给测试程序，确保绝对路径与上级目录不会逃出补丁根目录。
bool NormalizeResourceRelativePath(const std::string& internalPath,
	std::wstring& relativePath, std::wstring& basename);
