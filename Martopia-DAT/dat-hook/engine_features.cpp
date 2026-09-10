#include "engine_features.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <vector>

namespace
{
struct SectionView
{
	BYTE* begin = nullptr;
	SIZE_T size = 0;
	DWORD characteristics = 0;
};

struct ImageView
{
	BYTE* base = nullptr;
	SIZE_T size = 0;
	std::vector<SectionView> sections;
};

static bool BuildImageView(HMODULE module, ImageView& image)
{
	image = {};
	BYTE* base = reinterpret_cast<BYTE*>(module);
	if (!base) return false;
	PIMAGE_DOS_HEADER dos = reinterpret_cast<PIMAGE_DOS_HEADER>(base);
	if (dos->e_magic != IMAGE_DOS_SIGNATURE) return false;
	PIMAGE_NT_HEADERS32 nt = reinterpret_cast<PIMAGE_NT_HEADERS32>(base + dos->e_lfanew);
	if (nt->Signature != IMAGE_NT_SIGNATURE || nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC)
		return false;
	image.base = base;
	image.size = nt->OptionalHeader.SizeOfImage;
	PIMAGE_SECTION_HEADER section = IMAGE_FIRST_SECTION(nt);
	for (WORD i = 0; i < nt->FileHeader.NumberOfSections; ++i)
	{
		SIZE_T offset = section[i].VirtualAddress;
		SIZE_T size = (std::max)(static_cast<SIZE_T>(section[i].Misc.VirtualSize),
			static_cast<SIZE_T>(section[i].SizeOfRawData));
		if (offset >= image.size) continue;
		size = (std::min)(size, image.size - offset);
		image.sections.push_back({base + offset, size, section[i].Characteristics});
	}
	return !image.sections.empty();
}

static bool InImage(const ImageView& image, const void* address, SIZE_T size = 1)
{
	if (!address || size > image.size) return false;
	const BYTE* value = static_cast<const BYTE*>(address);
	return value >= image.base && value <= image.base + image.size - size;
}

static bool InSection(const ImageView& image, const void* address, SIZE_T size, DWORD required)
{
	if (!InImage(image, address, size)) return false;
	const BYTE* value = static_cast<const BYTE*>(address);
	for (const SectionView& section : image.sections)
	{
		if ((section.characteristics & required) != required) continue;
		if (size > section.size) continue;
		if (value >= section.begin && value <= section.begin + section.size - size) return true;
	}
	return false;
}

static std::vector<int> ParsePattern(const char* text)
{
	std::vector<int> pattern;
	const char* cursor = text ? text : "";
	auto hexValue = [](char value) -> int {
		if (value >= '0' && value <= '9') return value - '0';
		if (value >= 'A' && value <= 'F') return value - 'A' + 10;
		if (value >= 'a' && value <= 'f') return value - 'a' + 10;
		return -1;
	};
	while (*cursor)
	{
		while (*cursor == ' ' || *cursor == '\t') ++cursor;
		if (!*cursor) break;
		if (*cursor == '?')
		{
			pattern.push_back(-1);
			while (*cursor == '?') ++cursor;
			continue;
		}
		int high = hexValue(cursor[0]);
		int low = hexValue(cursor[1]);
		if (high < 0 || low < 0) return {};
		pattern.push_back((high << 4) | low);
		cursor += 2;
	}
	return pattern;
}

static bool Matches(const BYTE* data, const std::vector<int>& pattern)
{
	for (SIZE_T i = 0; i < pattern.size(); ++i)
		if (pattern[i] >= 0 && data[i] != static_cast<BYTE>(pattern[i])) return false;
	return true;
}

static BYTE* FindUniquePattern(const ImageView& image, const char* text, DWORD sectionFlags)
{
	std::vector<int> pattern = ParsePattern(text);
	if (pattern.empty()) return nullptr;
	BYTE* found = nullptr;
	for (const SectionView& section : image.sections)
	{
		if ((section.characteristics & sectionFlags) != sectionFlags || section.size < pattern.size()) continue;
		for (SIZE_T i = 0; i <= section.size - pattern.size(); ++i)
		{
			BYTE* candidate = section.begin + i;
			if (!Matches(candidate, pattern)) continue;
			if (found) return nullptr;
			found = candidate;
		}
	}
	return found;
}

static BYTE* FindPatternNear(const BYTE* begin, SIZE_T size, const char* text)
{
	std::vector<int> pattern = ParsePattern(text);
	if (!begin || pattern.empty() || size < pattern.size()) return nullptr;
	for (SIZE_T i = 0; i <= size - pattern.size(); ++i)
		if (Matches(begin + i, pattern)) return const_cast<BYTE*>(begin + i);
	return nullptr;
}

static BYTE* ResolveCallTarget(const ImageView& image, BYTE* opcode)
{
	if (!InSection(image, opcode, 5, IMAGE_SCN_MEM_EXECUTE) || opcode[0] != 0xE8) return nullptr;
	LONG displacement = *reinterpret_cast<const LONG*>(opcode + 1);
	BYTE* target = opcode + 5 + displacement;
	return InSection(image, target, 1, IMAGE_SCN_MEM_EXECUTE) ? target : nullptr;
}

static BYTE* FindFunctionStart(const ImageView& image, BYTE* marker, SIZE_T maximumDistance)
{
	if (!InSection(image, marker, 1, IMAGE_SCN_MEM_EXECUTE)) return nullptr;
	static const std::vector<int> prologue = ParsePattern("55 8B EC 6A FF 68");
	for (SIZE_T distance = 0; distance <= maximumDistance; ++distance)
	{
		BYTE* candidate = marker - distance;
		if (!InSection(image, candidate, prologue.size(), IMAGE_SCN_MEM_EXECUTE)) break;
		if (Matches(candidate, prologue)) return candidate;
	}
	return nullptr;
}

static bool ReadImageDword(const ImageView& image, const BYTE* address, DWORD& value)
{
	if (!InImage(image, address, sizeof(value))) return false;
	value = *reinterpret_cast<const DWORD*>(address);
	return true;
}

static bool IsWritableAddress(const ImageView& image, const void* address, SIZE_T size = sizeof(DWORD))
{
	return InSection(image, address, size, IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE);
}

static bool ReadImageString(const ImageView& image, DWORD pointerValue, std::string& result)
{
	result.clear();
	const char* text = reinterpret_cast<const char*>(static_cast<ULONG_PTR>(pointerValue));
	if (!InSection(image, text, 1, IMAGE_SCN_MEM_READ)) return false;
	for (SIZE_T i = 0; i < 2048; ++i)
	{
		if (!InSection(image, text + i, 1, IMAGE_SCN_MEM_READ)) return false;
		unsigned char value = static_cast<unsigned char>(text[i]);
		if (value == 0) return !result.empty();
		if (value < 0x20 || value == 0x7F) return false;
		result.push_back(static_cast<char>(value));
	}
	return false;
}

static bool StartsWithPath(const std::string& value, const char* prefix)
{
	SIZE_T length = strlen(prefix);
	return value.size() >= length && _strnicmp(value.c_str(), prefix, length) == 0;
}

static bool EndsWithDat(const std::string& value)
{
	return value.size() >= 4 && _stricmp(value.c_str() + value.size() - 4, ".dat") == 0;
}

static bool IsResourcePair(const ImageView& image, const DWORD* pair)
{
	if (!InSection(image, pair, sizeof(DWORD) * 2, IMAGE_SCN_MEM_READ)) return false;
	std::string resource;
	std::string archive;
	if (!ReadImageString(image, pair[0], resource) || !ReadImageString(image, pair[1], archive)) return false;
	bool logical = StartsWithPath(resource, "Resource\\") || StartsWithPath(resource, "Resource/");
	bool physical = StartsWithPath(archive, "dat\\") || StartsWithPath(archive, "dat/");
	return logical && physical && EndsWithDat(archive);
}

static bool FindResourceTable(const ImageView& image, const DWORD*& table, unsigned int& count)
{
	table = nullptr;
	count = 0;
	for (const SectionView& section : image.sections)
	{
		if ((section.characteristics & IMAGE_SCN_MEM_READ) == 0 ||
			(section.characteristics & IMAGE_SCN_MEM_EXECUTE) != 0) continue;
		for (SIZE_T offset = 0; offset + sizeof(DWORD) * 2 <= section.size; offset += sizeof(DWORD))
		{
			const DWORD* candidate = reinterpret_cast<const DWORD*>(section.begin + offset);
			if (!IsResourcePair(image, candidate)) continue;
			unsigned int current = 0;
			while (current < 100000 &&
				offset + (static_cast<SIZE_T>(current) + 1) * sizeof(DWORD) * 2 <= section.size &&
				IsResourcePair(image, candidate + static_cast<SIZE_T>(current) * 2))
				++current;
			if (current > count)
			{
				table = candidate;
				count = current;
			}
			offset += static_cast<SIZE_T>(current) * sizeof(DWORD) * 2 - sizeof(DWORD);
		}
	}
	return table != nullptr && count >= 8;
}

static std::string RvaText(const EngineFeatures& features, const void* address)
{
	if (!address || !features.imageBase) return "未识别";
	char text[32]{};
	sprintf_s(text, "0x%zX", static_cast<const BYTE*>(address) - features.imageBase);
	return text;
}
}

bool ResolveEngineFeatures(HMODULE module, EngineFeatures& result, std::string& error)
{
	result = {};
	error.clear();
	ImageView image;
	if (!BuildImageView(module, image))
	{
		error = "主程序不是可识别的 32 位 PE 镜像";
		return false;
	}
	result.imageBase = image.base;
	result.imageSize = image.size;

	BYTE* worker = FindUniquePattern(image,
		"55 8B EC 56 8B 75 08 85 F6 74 ?? 83 7E 14 10 72 ?? 8B 06 EB ?? 8B C6 57 "
		"8D 4E 1C 51 50 8D 4E 40 E8 ?? ?? ?? ?? 84 C0 74 ?? 8D 4E 40 E8 ?? ?? ?? ?? "
		"8D 4E 40 E8 ?? ?? ?? ??",
		IMAGE_SCN_MEM_EXECUTE);
	if (!worker)
	{
		error = "归档工作线程特征缺失或不唯一";
		return false;
	}
	result.archiveWorker = worker;

	std::vector<BYTE*> workerCalls;
	for (SIZE_T offset = 0; offset + 8 < 0x60; ++offset)
	{
		BYTE* instruction = worker + offset;
		if (instruction[0] == 0x8D && instruction[1] == 0x4E && instruction[2] == 0x40 &&
			instruction[3] == 0xE8)
		{
			BYTE* target = ResolveCallTarget(image, instruction + 3);
			if (target) workerCalls.push_back(target);
			offset += 7;
		}
	}
	if (workerCalls.size() < 3)
	{
		error = "归档工作线程调用关系不完整";
		return false;
	}
	result.archiveVerify = workerCalls[0];
	result.archiveBuild = workerCalls[1];
	result.archiveFinalize = workerCalls[2];

	BYTE* mapSite = FindPatternNear(worker, 0x90, "8B D6 B9 ?? ?? ?? ?? E8 ?? ?? ?? ?? 89 70 1C");
	BYTE* destroySite = FindPatternNear(worker, 0xA0, "56 E8 ?? ?? ?? ?? 68 ?? ?? ?? ?? FF 15");
	DWORD loadedMap = 0;
	if (!mapSite || !destroySite || !ReadImageDword(image, mapSite + 3, loadedMap))
	{
		error = "归档缓存插入与析构关系不完整";
		return false;
	}
	result.loadedArchiveMap = reinterpret_cast<BYTE*>(static_cast<ULONG_PTR>(loadedMap));
	result.archiveMapInsert = ResolveCallTarget(image, mapSite + 7);
	result.archiveDestroy = ResolveCallTarget(image, destroySite + 1);
	if (!IsWritableAddress(image, result.loadedArchiveMap) || !result.archiveMapInsert || !result.archiveDestroy)
	{
		error = "归档缓存特征指向了无效节";
		return false;
	}

	BYTE* pump = FindUniquePattern(image,
		"55 8B EC 83 EC 08 8B 0D ?? ?? ?? ?? 53 56 8D 45 ?? 50 51 FF 15 ?? ?? ?? ?? "
		"81 7D ?? 03 01 00 00 0F 84 ?? ?? ?? ?? 83 3D ?? ?? ?? ?? 00",
		IMAGE_SCN_MEM_EXECUTE);
	if (!pump)
	{
		error = "归档调度函数特征缺失或不唯一";
		return false;
	}
	result.archivePump = pump;
	DWORD threadHandle = 0;
	if (!ReadImageDword(image, pump + 8, threadHandle))
	{
		error = "无法读取归档线程句柄特征";
		return false;
	}
	result.archiveThreadHandle = reinterpret_cast<DWORD*>(static_cast<ULONG_PTR>(threadHandle));
	std::vector<DWORD*> queueGlobals;
	for (SIZE_T offset = 0; offset + 7 <= 0xE0; ++offset)
	{
		BYTE* instruction = pump + offset;
		if (instruction[0] != 0x83 || instruction[1] != 0x3D || instruction[6] != 0) continue;
		DWORD value = *reinterpret_cast<const DWORD*>(instruction + 2);
		DWORD* address = reinterpret_cast<DWORD*>(static_cast<ULONG_PTR>(value));
		if (IsWritableAddress(image, address)) queueGlobals.push_back(address);
		offset += 6;
	}
	if (!IsWritableAddress(image, result.archiveThreadHandle) || queueGlobals.size() < 2)
	{
		error = "归档调度全局状态特征不完整";
		return false;
	}
	result.alternateQueueMode = queueGlobals[0];
	result.primaryQueueCount = queueGlobals[1];

	BYTE* altMarker = FindUniquePattern(image,
		"84 C0 0F 85 ?? ?? ?? ?? 83 7D ?? 02 0F 85 ?? ?? ?? ?? 68 10 01 00 00",
		IMAGE_SCN_MEM_EXECUTE);
	BYTE* primaryMarker = FindUniquePattern(image,
		"84 C0 0F 84 ?? ?? ?? ?? 68 20 01 00 00",
		IMAGE_SCN_MEM_EXECUTE);
	result.archiveQueueAlt = FindFunctionStart(image, altMarker, 0xA0);
	result.archiveQueue = FindFunctionStart(image, primaryMarker, 0xA0);
	if (!result.archiveQueueAlt || !result.archiveQueue)
	{
		error = "归档队列入口特征缺失或不唯一";
		return false;
	}

	BYTE* readSite = FindPatternNear(result.archiveBuild, 0x500,
		"8D 46 58 85 C0 74 ?? 8D 46 68 EB ?? 33 C0 8B 4E 38 51 8B D7 83 C2 10 52 50 8D 4F 08 E8");
	result.archiveRead = readSite ? ResolveCallTarget(image, readSite + 28) : nullptr;
	if (!result.archiveRead)
	{
		error = "归档读取入口调用特征缺失";
		return false;
	}

	result.decrypt = FindUniquePattern(image,
		"55 8B EC 83 EC 20 53 8B 5D 08 56 57 8B C1 B9 08 00 00 00 8B F3 8D 7D ?? "
		"53 53 8D 48 40 E8 ?? ?? ?? ??",
		IMAGE_SCN_MEM_EXECUTE);
	if (!FindResourceTable(image, result.resourceTable, result.resourceCount))
	{
		error = "未找到连续的 Resource 与 dat 路径对资源表";
		return false;
	}
	return true;
}

std::string DescribeEngineFeatures(const EngineFeatures& features)
{
	return "工作线程=" + RvaText(features, features.archiveWorker) +
		"；调度=" + RvaText(features, features.archivePump) +
		"；队列=" + RvaText(features, features.archiveQueue) +
		"；备用队列=" + RvaText(features, features.archiveQueueAlt) +
		"；验证=" + RvaText(features, features.archiveVerify) +
		"；构建=" + RvaText(features, features.archiveBuild) +
		"；读取=" + RvaText(features, features.archiveRead) +
		"；资源表=" + RvaText(features, features.resourceTable) +
		"；资源数=" + std::to_string(features.resourceCount);
}
