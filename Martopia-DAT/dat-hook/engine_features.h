#pragma once

#include <Windows.h>

#include <string>

// 同一引擎不同游戏中的函数地址和全局变量位置可能不同。
// 此结构只保存运行时特征解析结果，不包含任何固定镜像偏移。
struct EngineFeatures
{
	BYTE* imageBase = nullptr;
	SIZE_T imageSize = 0;

	BYTE* archiveRead = nullptr;
	BYTE* decrypt = nullptr;
	BYTE* archiveQueue = nullptr;
	BYTE* archiveQueueAlt = nullptr;
	BYTE* archivePump = nullptr;
	BYTE* archiveDestroy = nullptr;
	BYTE* archiveFinalize = nullptr;
	BYTE* archiveMapInsert = nullptr;
	BYTE* archiveVerify = nullptr;
	BYTE* archiveBuild = nullptr;
	BYTE* archiveWorker = nullptr;

	DWORD* archiveThreadHandle = nullptr;
	BYTE* loadedArchiveMap = nullptr;
	DWORD* primaryQueueCount = nullptr;
	DWORD* alternateQueueMode = nullptr;
	const DWORD* resourceTable = nullptr;
	unsigned int resourceCount = 0;
};

bool ResolveEngineFeatures(HMODULE module, EngineFeatures& result, std::string& error);
std::string DescribeEngineFeatures(const EngineFeatures& features);
