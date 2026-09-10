#include <Windows.h>
#include <detours.h>
#include <intrin.h>

#include "engine_features.h"
#include "resource_patch.h"

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cctype>
#include <cstring>
#include <cstdio>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

using ArchiveReadFn = int (__thiscall *)(void*, void*, void*, unsigned int);
// sub_4EEAC0 是成员函数：ECX 是归档密码对象，两个参数仍在栈上传递，
// 所以自由函数钩子需要使用 __fastcall 的 ECX/EDX 适配器。
using DecryptFn = void* (__thiscall *)(void*, const void*, char*);
using OstreamWriteFn = void* (__thiscall *)(void*, const char*, long long);
// sub_4B1280 虽然反编译显示三个普通参数，实际是 thiscall：ECX 是资源路径，
// 后面才是密钥和辅助参数。
using ArchiveQueueFn = char (__thiscall *)(const char*, const void*, const void*, int);
using ArchiveQueueAltFn = char (__thiscall *)(const char*, const void*);
using ArchivePumpFn = int (__cdecl *)();
using ArchiveDestroyFn = void* (__stdcall *)(void*);
using ArchiveFinalizeFn = bool (__thiscall *)(void*);
using ArchiveMapInsertFn = void* (__fastcall *)(void*, void*);
using ArchiveVerifyFn = bool (__thiscall *)(void*, const char*, const void*);
using ArchiveBuildFn = char (__thiscall *)(void*);
using ArchiveWorkerFn = unsigned int (__stdcall *)(void*);

static ArchiveReadFn g_originalArchiveRead = nullptr;
static DecryptFn g_originalDecrypt = nullptr;
static OstreamWriteFn g_originalOstreamWrite = nullptr;
static ArchiveQueueFn g_originalArchiveQueue = nullptr;
static ArchiveQueueAltFn g_originalArchiveQueueAlt = nullptr;
static ArchivePumpFn g_originalArchivePump = nullptr;
static ArchiveDestroyFn g_originalArchiveDestroy = nullptr;
static ArchiveFinalizeFn g_originalArchiveFinalize = nullptr;
static ArchiveMapInsertFn g_originalArchiveMapInsert = nullptr;
static ArchiveVerifyFn g_originalArchiveVerify = nullptr;
static ArchiveBuildFn g_originalArchiveBuild = nullptr;
static ArchiveWorkerFn g_originalArchiveWorker = nullptr;
static HMODULE g_martopia = nullptr;
static BYTE* g_imageBase = nullptr;
static EngineFeatures g_engine;
static HANDLE g_manifest = INVALID_HANDLE_VALUE;
static HANDLE g_blocks = INVALID_HANDLE_VALUE;
static HANDLE g_queueLog = INVALID_HANDLE_VALUE;
static HANDLE g_entriesLog = INVALID_HANDLE_VALUE;
static HANDLE g_runtimeLog = INVALID_HANDLE_VALUE;
static HANDLE g_feedResultsLog = INVALID_HANDLE_VALUE;
static HANDLE g_patchLog = INVALID_HANDLE_VALUE;
static CRITICAL_SECTION g_fileLock;
static std::wstring g_dumpRoot;
static std::wstring g_extractRoot;
static std::wstring g_metaRoot;
static std::atomic<unsigned long> g_sequence{0};
static DWORD g_writePatched = 0;
static bool g_decryptDryRun = false;
static bool g_decryptNoWrite = false;
static bool g_decryptNoRead = false;
static bool g_verboseRuntimeLog = false;
static bool g_releaseFeedArchives = false;
static bool g_dumpEnabled = false;
static MartopiaConfig g_config;
static thread_local struct EntryCapture* g_capture = nullptr;
static std::atomic<bool> g_feedStarted{false};
static std::atomic<bool> g_feedRunning{false};
static std::atomic<bool> g_feedDeferred{false};
static bool g_feedAll = false;
static BYTE g_feedKey[32]{};
static const void* g_feedArg2 = nullptr;
static int g_feedArg3 = 0;
static unsigned int g_shardIndex = 0;
static unsigned int g_shardCount = 1;
static unsigned int g_shardBegin = 0;
static unsigned int g_shardEnd = 0;
static unsigned int g_resourceCount = 0;
static unsigned int g_workerStartTimeoutMs = 5000;
static unsigned int g_completionQuietMs = 3000;
static bool g_closeAfterDump = true;

// 资源登记计划只在游戏主线程逐项提交。引擎的归档缓存、密码对象和
// 工作线程状态都是进程全局，不能像普通文件写入一样并发推进。
struct FeedItem
{
	DWORD tableIndex = 0;
	DWORD resourcePointer = 0;
	std::string resource;
	std::string archive;
	bool video = false;
	bool skipped = false;
};

static std::mutex g_feedMutex;
static std::vector<FeedItem> g_feedPlan;
static size_t g_feedCursor = 0;
static bool g_feedWaiting = false;
static bool g_feedWorkerStarted = false;
static ULONGLONG g_feedSubmittedTick = 0;
static DWORD g_feedInFlightIndex = 0;
static std::string g_feedInFlightResource;
static std::string g_feedInFlightArchive;
static std::atomic<unsigned long> g_feedRejected{0};
static std::atomic<unsigned long> g_feedCompleted{0};
static std::atomic<unsigned long> g_feedFailed{0};
static std::atomic<bool> g_feedPlanReady{false};
static std::atomic<bool> g_completionWritten{false};
static std::atomic<ULONGLONG> g_feedDoneObservedTick{0};
static std::atomic<ULONGLONG> g_lastRelevantActivityTick{0};

static std::mutex g_successfulArchivesMutex;
static std::unordered_set<std::string> g_successfulArchives;
static std::unordered_set<std::string> g_shardArchives;

// 归档解码必须留在游戏线程，因为缓存和密码状态是进程全局的；
// 文件创建与写入相互独立，因此交给小型线程池处理。
struct ExtractJob
{
	std::string path;
	std::vector<BYTE> bytes;
	unsigned int requested = 0;
};

struct EntryLogJob
{
	std::string archive;
	std::string entry;
	DWORD field56 = 0;
	DWORD field60 = 0;
};

static std::mutex g_outputMutex;
static std::condition_variable g_outputCv;
static std::deque<ExtractJob> g_outputQueue;
static std::deque<EntryLogJob> g_entryQueue;
static std::vector<std::thread> g_outputWorkers;
static std::thread g_progressThread;
static std::atomic<bool> g_outputReady{false};
static std::atomic<bool> g_progressStop{false};
static std::atomic<unsigned long> g_filesQueued{0};
static std::atomic<unsigned long> g_filesWritten{0};
static std::atomic<unsigned long> g_filesFailed{0};
static std::atomic<unsigned long> g_outputActive{0};
static std::atomic<unsigned long long> g_bytesCaptured{0};
static std::atomic<unsigned long long> g_bytesWritten{0};
static std::atomic<unsigned long> g_archivesParsed{0};
static std::atomic<unsigned long> g_entriesSeen{0};
static std::atomic<unsigned long> g_feedAttempted{0};
static std::atomic<unsigned long> g_feedQueued{0};
static std::mutex g_progressMutex;
static std::string g_lastArchive;
static std::string g_lastQueueResource;
static std::string g_lastReadPath;
static unsigned int g_outputThreadCount = 0;

// 这些计数器用于判断卡住时究竟是没有新调用，还是调用停在游戏内部。
static std::atomic<unsigned long> g_archiveReadCalls{0};
static std::atomic<unsigned long> g_archiveReadSuccess{0};
static std::atomic<unsigned long> g_archiveBuildCalls{0};
static std::atomic<unsigned long> g_archiveBuildSuccess{0};
static std::atomic<unsigned long> g_nativeQueueCalls{0};
static std::atomic<unsigned long> g_nativeQueueSuccess{0};
static std::atomic<unsigned long> g_pumpCalls{0};
static std::atomic<unsigned long> g_finalizeCalls{0};
static std::atomic<unsigned long> g_finalizeCompleted{0};
static std::atomic<unsigned long> g_cacheInsertSkipped{0};
static std::atomic<unsigned long> g_feedArchivesReleased{0};
static std::atomic<unsigned long> g_streamWriteCalls{0};
static std::atomic<unsigned long long> g_streamWriteBytes{0};
static std::atomic<unsigned long> g_patchHits{0};
static std::atomic<unsigned long> g_patchFailures{0};
static std::atomic<ULONGLONG> g_lastArchiveReadTick{0};
static std::atomic<ULONGLONG> g_lastArchiveBuildTick{0};
static std::atomic<ULONGLONG> g_lastQueueTick{0};
static std::atomic<ULONGLONG> g_lastPumpTick{0};
static std::atomic<ULONGLONG> g_lastFinalizeTick{0};
static thread_local bool g_bypassNextLoadedMapInsert = false;
static thread_local bool g_feedArchiveUsedTemporaryNode = false;
static thread_local std::string g_workerArchive;
static thread_local bool g_workerIsFeedItem = false;
static thread_local bool g_workerIsScheduledFeedItem = false;
static thread_local bool g_workerVerifySeen = false;
static thread_local bool g_workerVerifySucceeded = false;
static thread_local bool g_workerBuildSeen = false;
static thread_local bool g_workerBuildSucceeded = false;

struct EntryCapture
{
	void* stream = nullptr;
	std::string path;
	unsigned int requested = 0;
	bool captureForDump = false;
	bool patchEnabled = false;
	bool patchWritten = false;
	std::vector<BYTE> bytes;
	std::vector<BYTE> patchBytes;
	ResourcePatchMatch patchMatch;
};

static char __fastcall HookArchiveQueue(const char* resource, void* edx,
	const void* key, const void* arg2, int arg3);
static int __cdecl HookArchivePump();
static bool __fastcall HookArchiveFinalize(void* self, void* /*edx*/);
static void* __fastcall HookArchiveMapInsert(void* map, void* key);
static bool __fastcall HookArchiveVerify(void* self, void* /*edx*/, const char* path, const void* key);
static char __fastcall HookArchiveBuild(void* decoder, void* edx);
static unsigned int __stdcall HookArchiveWorker(void* argument);
static void FlushDecryptBlocks();
static void WriteEntryRecordSync(const std::string& archive, const std::string& entry,
	DWORD field56, DWORD field60);
static void WriteRuntimeLog(const char* event, const std::string& detail = {}, bool flush = false);
static void WritePatchLog(const char* status, const std::string& internalPath,
	const ResourcePatchMatch& match, SIZE_T patchSize, unsigned int originalSize,
	const std::wstring& error = {});
static std::string RuntimePathText(const std::string& value);
static void WriteRuntimeHeartbeat();
static bool TryReadDword(const void* address, DWORD& value);
static void FeedNextArchive();
static void MarkFeedArchiveComplete(const std::string& archive, const char* status, bool succeeded);
static void TryWriteCompletionMarker();

static std::string ArchiveKey(std::string archive)
{
	std::replace(archive.begin(), archive.end(), '/', '\\');
	std::transform(archive.begin(), archive.end(), archive.begin(), [](unsigned char c) {
		return static_cast<char>(std::tolower(c));
	});
	return archive;
}

static bool WasArchiveCaptured(const std::string& archive)
{
	std::lock_guard<std::mutex> lock(g_successfulArchivesMutex);
	return g_successfulArchives.find(ArchiveKey(archive)) != g_successfulArchives.end();
}

static bool ArchiveBelongsToShard(const std::string& archive)
{
	return g_shardArchives.find(ArchiveKey(archive)) != g_shardArchives.end();
}

#pragma pack(push, 1)
struct BlockRecord
{
	DWORD magic;
	DWORD sequence;
	DWORD threadId;
	DWORD callerRva;
	BYTE ciphertext[32];
	BYTE plaintext[32];
	BYTE iv[32];
};
#pragma pack(pop)

static std::mutex g_blockBufferMutex;
static std::vector<BlockRecord> g_blockBuffer;

static bool IsReadable(const void* address, SIZE_T size)
{
	if (!address || size == 0) return false;
	const BYTE* p = static_cast<const BYTE*>(address);
	SIZE_T left = size;
	while (left != 0)
	{
		MEMORY_BASIC_INFORMATION mbi{};
		if (VirtualQuery(p, &mbi, sizeof(mbi)) != sizeof(mbi)) return false;
		if (mbi.State != MEM_COMMIT || (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) != 0)
			return false;
		SIZE_T available = reinterpret_cast<SIZE_T>(mbi.BaseAddress) + mbi.RegionSize - reinterpret_cast<SIZE_T>(p);
		if (available == 0) return false;
		SIZE_T step = (left < available) ? left : available;
		p += step;
		left -= step;
	}
	return true;
}

// SEH 保护区域内故意不创建 C++ 对象；资源记录采用游戏 MSVC ABI 的 std::string。
static bool TryReadMsvcString(const BYTE* object, char* output, SIZE_T outputSize)
{
	if (!object || !output || outputSize < 2) return false;
	__try
	{
		DWORD length = *reinterpret_cast<const DWORD*>(object + 0x10);
		DWORD capacity = *reinterpret_cast<const DWORD*>(object + 0x14);
		if (length == 0 || length >= 0x1000) return false;
		const char* source = (capacity < 0x10)
			? reinterpret_cast<const char*>(object)
			: *reinterpret_cast<const char* const*>(object);
		if (!source || !IsReadable(source, length)) return false;
		SIZE_T copyLength = (length < outputSize - 1) ? length : outputSize - 1;
		memcpy(output, source, copyLength);
		output[copyLength] = '\0';
		return copyLength != 0;
	}
	__except (EXCEPTION_EXECUTE_HANDLER)
	{
		return false;
	}
}

static std::wstring AnsiPathToWide(const std::string& path)
{
	if (path.empty()) return {};
	int count = MultiByteToWideChar(932, MB_ERR_INVALID_CHARS, path.data(), static_cast<int>(path.size()), nullptr, 0);
	UINT codePage = 932;
	if (count <= 0)
	{
		codePage = CP_UTF8;
		count = MultiByteToWideChar(codePage, MB_ERR_INVALID_CHARS, path.data(), static_cast<int>(path.size()), nullptr, 0);
	}
	if (count <= 0)
	{
		codePage = CP_ACP;
		count = MultiByteToWideChar(codePage, 0, path.data(), static_cast<int>(path.size()), nullptr, 0);
	}
	if (count <= 0) return {};
	std::wstring result(static_cast<size_t>(count), L'\0');
	MultiByteToWideChar(codePage, 0, path.data(), static_cast<int>(path.size()), result.data(), count);
	return result;
}

static std::wstring Utf8ToWide(const std::string& text)
{
	if (text.empty()) return {};
	int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(),
		static_cast<int>(text.size()), nullptr, 0);
	if (count <= 0) return {};
	std::wstring result(static_cast<size_t>(count), L'\0');
	MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(),
		static_cast<int>(text.size()), result.data(), count);
	return result;
}

static std::wstring MakeSafeRelativePath(const std::string& path, unsigned long sequence)
{
	std::wstring wide = AnsiPathToWide(path);
	std::wstring result;
	std::wstring segment;
	const auto flush = [&]() {
		if (segment.empty() || segment == L".")
		{
			segment.clear();
			return;
		}
		if (segment == L"..") segment = L"_up_";
		for (wchar_t& c : segment)
		{
			if (c == L':' || c == L'"' || c == L'<' || c == L'>' || c == L'|' || c == L'*' || c == L'?') c = L'_';
		}
		if (!result.empty()) result.push_back(L'\\');
		result += segment;
		segment.clear();
	};
	for (wchar_t c : wide)
	{
		if (c == L'/' || c == L'\\') flush();
		else segment.push_back(c);
	}
	flush();
	if (result.empty())
	{
		wchar_t fallback[64]{};
		wsprintfW(fallback, L"_unnamed_%lu.bin", sequence);
		result = fallback;
	}
	return result;
}

static bool EnsureDirectory(const std::wstring& path)
{
	if (path.empty()) return false;
	std::wstring current;
	SIZE_T start = 0;
	if (path.size() >= 3 && path[1] == L':' && (path[2] == L'\\' || path[2] == L'/'))
	{
		current = path.substr(0, 3);
		start = 3;
	}
	else if (path.size() >= 2 && path[0] == L'\\' && path[1] == L'\\')
	{
		current = L"\\\\";
		start = 2;
	}
	for (SIZE_T i = start; i <= path.size(); ++i)
	{
		if (i < path.size() && path[i] != L'\\' && path[i] != L'/') continue;
		if (i > start)
		{
			current += path.substr(start, i - start);
			CreateDirectoryW(current.c_str(), nullptr);
		}
		if (i < path.size()) current.push_back(L'\\');
		start = i + 1;
	}
	DWORD attributes = GetFileAttributesW(path.c_str());
	return attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
}

static std::wstring GetExeDirectory()
{
	wchar_t buffer[MAX_PATH * 4]{};
	DWORD length = GetModuleFileNameW(nullptr, buffer, static_cast<DWORD>(_countof(buffer)));
	if (length == 0 || length >= _countof(buffer)) return L".";
	std::wstring path(buffer, length);
	SIZE_T slash = path.find_last_of(L"\\/");
	return slash == std::wstring::npos ? L"." : path.substr(0, slash);
}

static std::wstring ReadEnvPath(const wchar_t* name)
{
	wchar_t value[MAX_PATH * 8]{};
	DWORD length = GetEnvironmentVariableW(name, value, _countof(value));
	if (length == 0 || length >= _countof(value)) return {};
	wchar_t full[MAX_PATH * 8]{};
	DWORD fullLength = GetFullPathNameW(value, _countof(full), full, nullptr);
	if (fullLength == 0 || fullLength >= _countof(full)) return std::wstring(value, length);
	return std::wstring(full, fullLength);
}

static std::wstring MakeRunDirectory()
{
	std::wstring configured = ReadEnvPath(L"MARTOPIA_DUMP_ROOT");
	if (!configured.empty()) return configured;
	SYSTEMTIME now{};
	GetLocalTime(&now);
	wchar_t name[96]{};
	wsprintfW(name, L"martopia_dump\\%04u%02u%02u_%02u%02u%02u_%03u",
		now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond, now.wMilliseconds);
	return GetExeDirectory() + L"\\" + name;
}

static void WriteHandle(HANDLE handle, const void* data, DWORD size)
{
	if (handle == INVALID_HANDLE_VALUE || !data || size == 0) return;
	const BYTE* cursor = static_cast<const BYTE*>(data);
	while (size != 0)
	{
		DWORD written = 0;
		if (!WriteFile(handle, cursor, size, &written, nullptr) || written == 0) break;
		cursor += written;
		size -= written;
	}
}

static void FlushOutputHandles()
{
	for (HANDLE handle : {g_manifest, g_blocks, g_queueLog, g_entriesLog, g_runtimeLog, g_feedResultsLog, g_patchLog})
		if (handle != INVALID_HANDLE_VALUE) FlushFileBuffers(handle);
}

static std::string WideToUtf8(const std::wstring& text)
{
	if (text.empty()) return {};
	int count = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
	if (count <= 0) return {};
	std::string result(static_cast<size_t>(count), '\0');
	WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), result.data(), count, nullptr, nullptr);
	return result;
}

static void WritePatchLog(const char* status, const std::string& internalPath,
	const ResourcePatchMatch& match, SIZE_T patchSize, unsigned int originalSize,
	const std::wstring& error)
{
	if (g_patchLog == INVALID_HANDLE_VALUE || !status) return;
	auto clean = [](std::string value) {
		for (char& c : value) if (c == '\t' || c == '\r' || c == '\n') c = ' ';
		return value;
	};
	SYSTEMTIME now{};
	GetLocalTime(&now);
	char prefix[192]{};
	int length = sprintf_s(prefix, sizeof(prefix),
		"%04u-%02u-%02u %02u:%02u:%02u.%03u\t%s\t%lu\t%u\t%s\t",
		now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond,
		now.wMilliseconds, status, static_cast<unsigned long>(patchSize), originalSize,
		match.diskPath.empty() ? "-" : (match.relativePath.empty()
			? "目录" : (match.structured ? "保留结构" : "平铺")));
	if (length < 0) return;
	std::string internalUtf8 = clean(RuntimePathText(internalPath));
	std::string diskUtf8 = clean(WideToUtf8(match.diskPath));
	std::string errorUtf8 = clean(WideToUtf8(error));
	EnterCriticalSection(&g_fileLock);
	WriteHandle(g_patchLog, prefix, static_cast<DWORD>(length));
	WriteHandle(g_patchLog, internalUtf8.data(), static_cast<DWORD>(internalUtf8.size()));
	static const char tab = '\t';
	WriteHandle(g_patchLog, &tab, 1);
	WriteHandle(g_patchLog, diskUtf8.data(), static_cast<DWORD>(diskUtf8.size()));
	WriteHandle(g_patchLog, &tab, 1);
	WriteHandle(g_patchLog, errorUtf8.data(), static_cast<DWORD>(errorUtf8.size()));
	static const char newline[] = "\r\n";
	WriteHandle(g_patchLog, newline, 2);
	FlushFileBuffers(g_patchLog);
	LeaveCriticalSection(&g_fileLock);
}

static void WriteManifest(const std::wstring& relative, SIZE_T size, DWORD requested, unsigned long sequence)
{
	std::string utf8 = WideToUtf8(relative);
	char line[128]{};
	int prefix = sprintf_s(line, sizeof(line), "%lu\t%lu\t%u\t", sequence,
		static_cast<unsigned long>(size), requested);
	if (prefix < 0) return;
	EnterCriticalSection(&g_fileLock);
	WriteHandle(g_manifest, line, static_cast<DWORD>(prefix));
	if (!utf8.empty()) WriteHandle(g_manifest, utf8.data(), static_cast<DWORD>(utf8.size()));
	static const char newline[] = "\r\n";
	WriteHandle(g_manifest, newline, 2);
	LeaveCriticalSection(&g_fileLock);
}

static void WriteExtractedFileSync(const std::string& originalPath, const std::vector<BYTE>& bytes, unsigned int requested)
{
	unsigned long sequence = ++g_sequence;
	std::wstring relative = MakeSafeRelativePath(originalPath, sequence);
	std::wstring output = g_extractRoot + L"\\" + relative;
	SIZE_T slash = output.find_last_of(L"\\/");
	if (slash != std::wstring::npos) EnsureDirectory(output.substr(0, slash));
	HANDLE file = CreateFileW(output.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
	if (file == INVALID_HANDLE_VALUE)
	{
		wchar_t suffix[32]{};
		for (unsigned int i = 1; i < 10000 && file == INVALID_HANDLE_VALUE; ++i)
		{
			wsprintfW(suffix, L".__dup%u", i);
			std::wstring candidate = output + suffix;
			file = CreateFileW(candidate.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
			if (file != INVALID_HANDLE_VALUE) relative += suffix;
		}
	}
	if (file != INVALID_HANDLE_VALUE)
	{
		if (!bytes.empty())
		{
			WriteHandle(file, bytes.data(), static_cast<DWORD>(std::min<SIZE_T>(bytes.size(), 0xFFFFFFFFu)));
			g_bytesWritten.fetch_add(static_cast<unsigned long long>(bytes.size()), std::memory_order_relaxed);
		}
		CloseHandle(file);
		g_filesWritten.fetch_add(1, std::memory_order_relaxed);
	}
	else
	{
		g_filesFailed.fetch_add(1, std::memory_order_relaxed);
	}
	WriteManifest(relative, bytes.size(), requested, sequence);
}

static unsigned int ReadEnvUnsigned(const wchar_t* name, unsigned int fallback,
	unsigned int minimum, unsigned int maximum)
{
	wchar_t value[32]{};
	DWORD length = GetEnvironmentVariableW(name, value, _countof(value));
	if (length == 0 || length >= _countof(value)) return fallback;
	wchar_t* end = nullptr;
	unsigned long parsed = wcstoul(value, &end, 10);
	if (!end || *end != L'\0' || parsed < minimum || parsed > maximum) return fallback;
	return static_cast<unsigned int>(parsed);
}

static void ExtractWorker()
{
	for (;;)
	{
		ExtractJob job;
		EntryLogJob entryJob;
		bool hasFileJob = false;
		bool hasEntryJob = false;
		{
			std::unique_lock<std::mutex> lock(g_outputMutex);
			g_outputCv.wait(lock, [] {
				return !g_outputQueue.empty() || !g_entryQueue.empty() || !g_outputReady.load();
			});
			if (g_outputQueue.empty() && g_entryQueue.empty())
			{
				if (!g_outputReady.load()) return;
				continue;
			}
			if (!g_outputQueue.empty())
			{
				job = std::move(g_outputQueue.front());
				g_outputQueue.pop_front();
				hasFileJob = true;
				g_outputActive.fetch_add(1, std::memory_order_relaxed);
			}
			else
			{
				entryJob = std::move(g_entryQueue.front());
				g_entryQueue.pop_front();
				hasEntryJob = true;
				g_outputActive.fetch_add(1, std::memory_order_relaxed);
			}
		}
		if (hasFileJob)
			WriteExtractedFileSync(job.path, job.bytes, job.requested);
		else if (hasEntryJob)
			WriteEntryRecordSync(entryJob.archive, entryJob.entry, entryJob.field56, entryJob.field60);
		g_outputActive.fetch_sub(1, std::memory_order_relaxed);
	}
}

static void QueueExtractedFile(const std::string& originalPath,
	std::vector<BYTE>&& bytes, unsigned int requested)
{
	if (!g_outputReady.load(std::memory_order_acquire) || g_outputWorkers.empty())
	{
		WriteExtractedFileSync(originalPath, bytes, requested);
		return;
	}
	ExtractJob job;
	job.path = originalPath;
	job.requested = requested;
	g_filesQueued.fetch_add(1, std::memory_order_relaxed);
	bool useSynchronousFallback = false;
	{
		std::lock_guard<std::mutex> lock(g_outputMutex);
		// 磁盘速度跟不上大型归档时限制队列长度，避免游戏堆无限增长。
		if (g_outputQueue.size() >= 2048)
		{
			// 队列满时同步写入，形成回压但不丢数据。
			g_filesQueued.fetch_sub(1, std::memory_order_relaxed);
			useSynchronousFallback = true;
		}
		else
		{
			job.bytes = std::move(bytes);
			g_outputQueue.emplace_back(std::move(job));
		}
	}
	if (useSynchronousFallback)
	{
		WriteExtractedFileSync(originalPath, bytes, requested);
		return;
	}
	g_outputCv.notify_one();
}

static void ProgressPrint(const char* reason)
{
	FlushDecryptBlocks();
	std::string archive;
	{
		std::lock_guard<std::mutex> lock(g_progressMutex);
		archive = g_lastArchive;
	}
	unsigned long attempted = g_feedAttempted.load(std::memory_order_relaxed);
	unsigned long queued = g_feedQueued.load(std::memory_order_relaxed);
	unsigned long completed = g_feedCompleted.load(std::memory_order_relaxed);
	unsigned long entries = g_entriesSeen.load(std::memory_order_relaxed);
	unsigned long files = g_filesWritten.load(std::memory_order_relaxed);
	unsigned long pending = 0;
	{
		std::lock_guard<std::mutex> lock(g_outputMutex);
		pending = static_cast<unsigned long>(g_outputQueue.size() + g_entryQueue.size());
	}
	size_t feedCursor = 0;
	size_t feedPlanSize = 0;
	bool feedWaiting = false;
	std::string feedInFlight;
	{
		std::lock_guard<std::mutex> lock(g_feedMutex);
		feedCursor = g_feedCursor;
		feedPlanSize = g_feedPlan.size();
		feedWaiting = g_feedWaiting;
		feedInFlight = g_feedInFlightArchive;
		if (feedWaiting) ++pending;
	}
	if (attempted == 0) attempted = g_shardEnd - g_shardBegin;
	const char* current = feedWaiting && !feedInFlight.empty() ? feedInFlight.c_str() :
		(archive.empty() ? reason : archive.c_str());
	std::printf("\r[Martopia] 分片 %u/%u DAT %lu/%lu 已登记=%lu 已释放=%lu 条目=%lu 文件=%lu 等待=%lu 归档失败=%lu 写入失败=%lu %s       ",
		g_shardIndex + 1, g_shardCount, completed, attempted, queued,
		g_feedArchivesReleased.load(std::memory_order_relaxed), entries, files, pending,
		g_feedFailed.load(std::memory_order_relaxed), g_filesFailed.load(std::memory_order_relaxed), current);
	std::fflush(stdout);
}

static void ProgressWorker()
{
	ULONGLONG nextRuntimeLog = GetTickCount64();
	while (!g_progressStop.load(std::memory_order_acquire))
	{
		ProgressPrint("进行中");
		ULONGLONG now = GetTickCount64();
		if (now >= nextRuntimeLog)
		{
			WriteRuntimeHeartbeat();
			TryWriteCompletionMarker();
			nextRuntimeLog = now + 1000;
		}
		Sleep(500);
	}
	ProgressPrint("完成");
	WriteRuntimeHeartbeat();
	std::printf("\n[Martopia] 导出线程已停止，已抓取=%llu 字节，已写入=%llu 字节\n",
		static_cast<unsigned long long>(g_bytesCaptured.load()),
		static_cast<unsigned long long>(g_bytesWritten.load()));
	std::fflush(stdout);
}

static void StartProgressConsole()
{
	wchar_t disabled[8]{};
	if (GetEnvironmentVariableW(L"MARTOPIA_NO_CONSOLE", disabled, _countof(disabled)) != 0)
		return;
	if (!GetConsoleWindow()) AllocConsole();
	SetConsoleOutputCP(CP_UTF8);
	FILE* output = nullptr;
	freopen_s(&output, "CONOUT$", "w", stdout);
	freopen_s(&output, "CONOUT$", "w", stderr);
	SetConsoleTitleW(L"Martopia DAT 解包进度");
	std::string rootUtf8 = WideToUtf8(g_dumpRoot);
	std::printf("[Martopia] 已启动异步 DAT 导出\n");
	std::printf("[Martopia] 分片：%u/%u（资源表索引 %u-%u）\n",
		g_shardIndex + 1, g_shardCount, g_shardBegin + 1, g_shardEnd);
	std::printf("[Martopia] 解包目录：%s   统计目录：%s\n",
		WideToUtf8(g_extractRoot).c_str(), WideToUtf8(g_metaRoot).c_str());
	std::printf("[Martopia] 文件写入线程：%u（可用 MARTOPIA_DUMP_THREADS 调整）\n", g_outputThreadCount);
	std::fflush(stdout);
}

static void StartOutputWorkers()
{
	unsigned int defaultCount = std::thread::hardware_concurrency();
	if (defaultCount == 0) defaultCount = 4;
	defaultCount = (std::max)(2u, (std::min)(defaultCount, 8u));
	g_outputThreadCount = ReadEnvUnsigned(L"MARTOPIA_DUMP_THREADS", defaultCount, 1, 32);
	g_outputReady.store(true, std::memory_order_release);
	for (unsigned int i = 0; i < g_outputThreadCount; ++i)
		g_outputWorkers.emplace_back(ExtractWorker);
	StartProgressConsole();
	g_progressThread = std::thread(ProgressWorker);
}

static void DumpDecryptBlock(const BYTE* cipher, const BYTE* plain, const BYTE* iv)
{
	if (g_blocks == INVALID_HANDLE_VALUE || !cipher || !plain || !iv) return;
	BlockRecord record{};
	record.magic = 0x4B4C424D; // "MBLK"
	record.sequence = ++g_sequence;
	record.threadId = GetCurrentThreadId();
	void* caller = _ReturnAddress();
	record.callerRva = (g_imageBase && reinterpret_cast<BYTE*>(caller) >= g_imageBase)
		? static_cast<DWORD>(reinterpret_cast<BYTE*>(caller) - g_imageBase) : 0;
	memcpy(record.ciphertext, cipher, 32);
	memcpy(record.plaintext, plain, 32);
	memcpy(record.iv, iv, 32);
	std::vector<BlockRecord> batch;
	{
		std::lock_guard<std::mutex> lock(g_blockBufferMutex);
		g_blockBuffer.push_back(record);
		if (g_blockBuffer.size() >= 128) batch.swap(g_blockBuffer);
	}
	if (!batch.empty())
	{
		EnterCriticalSection(&g_fileLock);
		WriteHandle(g_blocks, batch.data(), static_cast<DWORD>(batch.size() * sizeof(BlockRecord)));
		LeaveCriticalSection(&g_fileLock);
	}
}

static void FlushDecryptBlocks()
{
	std::vector<BlockRecord> batch;
	{
		std::lock_guard<std::mutex> lock(g_blockBufferMutex);
		if (g_blockBuffer.empty()) return;
		batch.swap(g_blockBuffer);
	}
	EnterCriticalSection(&g_fileLock);
	WriteHandle(g_blocks, batch.data(), static_cast<DWORD>(batch.size() * sizeof(BlockRecord)));
	LeaveCriticalSection(&g_fileLock);
}

static int HookArchiveReadImpl(void* context, void* output, void* input, unsigned int size)
{
	EntryCapture capture;
	capture.stream = output;
	capture.requested = size;
	char path[1024]{};
	if (output && TryReadMsvcString(reinterpret_cast<const BYTE*>(output) - 0x68, path, sizeof(path)))
		capture.path.assign(path);

	// 全量解包只记录主动登记的归档；资源覆盖则也检查游戏自身正常读取的资源。
	capture.captureForDump = g_dumpEnabled && (!g_feedAll || g_workerIsFeedItem);
	std::wstring patchError;
	if (g_config.PatchEnabled() && !capture.path.empty())
	{
		capture.patchEnabled = TryLoadResourcePatch(g_config, capture.path,
			capture.patchBytes, capture.patchMatch, patchError);
		if (!capture.patchEnabled && !patchError.empty())
		{
			g_patchFailures.fetch_add(1, std::memory_order_relaxed);
			if (g_config.patchLog)
				WritePatchLog("读取失败", capture.path, capture.patchMatch, 0, size, patchError);
		}
	}
	if (!capture.captureForDump && !capture.patchEnabled)
		return g_originalArchiveRead ? g_originalArchiveRead(context, output, input, size) : -1;

	g_lastRelevantActivityTick.store(GetTickCount64(), std::memory_order_relaxed);
	if (capture.captureForDump && !capture.patchEnabled && size <= 0x40000000u)
		capture.bytes.reserve(size);
	g_archiveReadCalls.fetch_add(1, std::memory_order_relaxed);
	g_lastArchiveReadTick.store(GetTickCount64(), std::memory_order_relaxed);
	{
		std::lock_guard<std::mutex> lock(g_progressMutex);
		g_lastReadPath = capture.path;
	}
	if (g_verboseRuntimeLog)
	{
		WriteRuntimeLog("归档读取开始", "路径=" + RuntimePathText(capture.path) +
			"；请求字节=" + std::to_string(size));
	}
	EntryCapture* previous = g_capture;
	g_capture = &capture;
	int result = g_originalArchiveRead ? g_originalArchiveRead(context, output, input, size) : -1;
	g_capture = previous;
	if (result == 0) g_archiveReadSuccess.fetch_add(1, std::memory_order_relaxed);
	if (capture.patchEnabled)
	{
		if (capture.patchWritten)
		{
			g_patchHits.fetch_add(1, std::memory_order_relaxed);
			if (g_config.patchLog)
				WritePatchLog("覆盖成功", capture.path, capture.patchMatch,
					capture.patchBytes.size(), size);
			if (capture.captureForDump) capture.bytes = std::move(capture.patchBytes);
		}
		else
		{
			g_patchFailures.fetch_add(1, std::memory_order_relaxed);
			if (g_config.patchLog)
				WritePatchLog("未进入输出流", capture.path, capture.patchMatch,
					capture.patchBytes.size(), size, L"已回退为归档原始内容");
		}
	}
	SIZE_T capturedSize = capture.bytes.size();
	if (capture.captureForDump && !capture.path.empty() && (result == 0 || !capture.bytes.empty()))
	{
		g_bytesCaptured.fetch_add(static_cast<unsigned long long>(capturedSize), std::memory_order_relaxed);
		QueueExtractedFile(capture.path, std::move(capture.bytes), size);
	}
	if (g_verboseRuntimeLog)
	{
		WriteRuntimeLog("归档读取结束", "路径=" + RuntimePathText(capture.path) +
			"；结果=" + std::to_string(result) +
			"；捕获字节=" + std::to_string(capturedSize));
	}
	return result;
}

static void* HookOstreamWriteImpl(void* stream, const char* data, DWORD countLow, DWORD countHigh)
{
	long long count = (static_cast<long long>(countHigh) << 32) | countLow;
	g_streamWriteCalls.fetch_add(1, std::memory_order_relaxed);
	if (count > 0) g_streamWriteBytes.fetch_add(static_cast<unsigned long long>(count), std::memory_order_relaxed);
	EntryCapture* capture = g_capture;
	if (capture && capture->stream == stream)
	{
		if (capture->patchEnabled)
		{
			// 原 DAT 仍由引擎完整读取和解码；只把第一次输出替换为补丁内容，
			// 后续原始分块写入全部吞掉，因此补丁大小可以与原文件不同。
			if (capture->patchWritten) return stream;
			capture->patchWritten = true;
			if (capture->patchBytes.empty()) return stream;
			return g_originalOstreamWrite
				? g_originalOstreamWrite(stream,
					reinterpret_cast<const char*>(capture->patchBytes.data()),
					static_cast<long long>(capture->patchBytes.size()))
				: stream;
		}
		if (capture->captureForDump && data && count > 0 && count <= 0x40000000LL &&
			IsReadable(data, static_cast<SIZE_T>(count)))
		{
			capture->bytes.insert(capture->bytes.end(), reinterpret_cast<const BYTE*>(data),
				reinterpret_cast<const BYTE*>(data) + static_cast<SIZE_T>(count));
		}
	}
	return g_originalOstreamWrite ? g_originalOstreamWrite(stream, data, count) : stream;
}

// MSVC 只允许成员函数使用 __thiscall；以下 naked 适配器保留游戏的
// ECX/栈 ABI，再调用普通 C 辅助函数。
extern "C" __declspec(naked) int HookArchiveRead()
{
	__asm
	{
		push dword ptr [esp+0Ch]
		push dword ptr [esp+0Ch]
		push dword ptr [esp+0Ch]
		push ecx
		call HookArchiveReadImpl
		add esp, 10h
		ret 0Ch
	}
}

extern "C" __declspec(naked) void* HookOstreamWrite()
{
	__asm
	{
		push dword ptr [esp+0Ch]
		push dword ptr [esp+0Ch]
		push dword ptr [esp+0Ch]
		push ecx
		call HookOstreamWriteImpl
		add esp, 10h
		ret 0Ch
	}
}

static void* __fastcall HookDecrypt(void* self, void* /*edx*/, const void* input, char* iv)
{
	if (g_decryptDryRun)
		return g_originalDecrypt ? g_originalDecrypt(self, input, iv) : nullptr;
	BYTE cipher[32]{};
	BYTE ivBefore[32]{};
	if (input && IsReadable(input, 32)) memcpy(cipher, input, 32);
	if (iv && IsReadable(iv, 32)) memcpy(ivBefore, iv, 32);
	void* result = g_originalDecrypt ? g_originalDecrypt(self, input, iv) : nullptr;
	if (g_decryptNoRead) return result;
	if (input && IsReadable(input, 32))
	{
		BYTE plain[32]{};
		memcpy(plain, input, 32);
		if (!g_decryptNoWrite) DumpDecryptBlock(cipher, plain, ivBefore);
	}
	return result;
}

static bool PatchOstreamWriteIat(HMODULE module)
{
	BYTE* base = reinterpret_cast<BYTE*>(module);
	PIMAGE_DOS_HEADER dos = reinterpret_cast<PIMAGE_DOS_HEADER>(base);
	if (!dos || dos->e_magic != IMAGE_DOS_SIGNATURE) return false;
	PIMAGE_NT_HEADERS32 nt = reinterpret_cast<PIMAGE_NT_HEADERS32>(base + dos->e_lfanew);
	if (nt->Signature != IMAGE_NT_SIGNATURE) return false;
	const IMAGE_DATA_DIRECTORY& directory = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
	if (!directory.VirtualAddress) return false;
	PIMAGE_IMPORT_DESCRIPTOR imports = reinterpret_cast<PIMAGE_IMPORT_DESCRIPTOR>(base + directory.VirtualAddress);
	static const char kName[] = "?write@?$basic_ostream@DU?$char_traits@D@std@@@std@@QAEAAV12@PBD_J@Z";
	for (PIMAGE_IMPORT_DESCRIPTOR item = imports; item->Name != 0; ++item)
	{
		PIMAGE_THUNK_DATA32 original = item->OriginalFirstThunk
			? reinterpret_cast<PIMAGE_THUNK_DATA32>(base + item->OriginalFirstThunk) : nullptr;
		PIMAGE_THUNK_DATA32 first = reinterpret_cast<PIMAGE_THUNK_DATA32>(base + item->FirstThunk);
		if (!first) continue;
		for (SIZE_T index = 0; first[index].u1.Function != 0; ++index)
		{
			if (!original || (original[index].u1.Ordinal & IMAGE_ORDINAL_FLAG32)) continue;
			PIMAGE_IMPORT_BY_NAME name = reinterpret_cast<PIMAGE_IMPORT_BY_NAME>(base + original[index].u1.AddressOfData);
			if (!name || strcmp(reinterpret_cast<const char*>(name->Name), kName) != 0) continue;
			DWORD oldProtect = 0;
			if (!VirtualProtect(&first[index].u1.Function, sizeof(DWORD), PAGE_READWRITE, &oldProtect)) continue;
			if (!g_originalOstreamWrite)
				g_originalOstreamWrite = reinterpret_cast<OstreamWriteFn>(first[index].u1.Function);
			first[index].u1.Function = reinterpret_cast<ULONG>(reinterpret_cast<void*>(&HookOstreamWrite));
			DWORD ignored = 0;
			VirtualProtect(&first[index].u1.Function, sizeof(DWORD), oldProtect, &ignored);
			FlushInstructionCache(GetCurrentProcess(), &first[index].u1.Function, sizeof(DWORD));
			++g_writePatched;
		}
	}
	return g_writePatched != 0;
}

static bool InstallDetours(bool installArchive, bool installDecrypt, bool installFeed)
{
	if (!g_martopia || !g_engine.archiveRead) return false;
	g_imageBase = g_engine.imageBase;
	g_originalArchiveRead = reinterpret_cast<ArchiveReadFn>(g_engine.archiveRead);
	if (installDecrypt)
		g_originalDecrypt = reinterpret_cast<DecryptFn>(g_engine.decrypt);
	if (installFeed)
	{
		g_originalArchiveQueue = reinterpret_cast<ArchiveQueueFn>(g_engine.archiveQueue);
		g_originalArchiveQueueAlt = reinterpret_cast<ArchiveQueueAltFn>(g_engine.archiveQueueAlt);
		g_originalArchivePump = reinterpret_cast<ArchivePumpFn>(g_engine.archivePump);
		g_originalArchiveDestroy = reinterpret_cast<ArchiveDestroyFn>(g_engine.archiveDestroy);
		g_originalArchiveFinalize = reinterpret_cast<ArchiveFinalizeFn>(g_engine.archiveFinalize);
		g_originalArchiveMapInsert = reinterpret_cast<ArchiveMapInsertFn>(g_engine.archiveMapInsert);
		g_originalArchiveVerify = reinterpret_cast<ArchiveVerifyFn>(g_engine.archiveVerify);
		g_originalArchiveBuild = reinterpret_cast<ArchiveBuildFn>(g_engine.archiveBuild);
		g_originalArchiveWorker = reinterpret_cast<ArchiveWorkerFn>(g_engine.archiveWorker);
	}
	LONG error = DetourTransactionBegin();
	if (error != NO_ERROR) return false;
	error = DetourUpdateThread(GetCurrentThread());
	if (error == NO_ERROR && installArchive)
		error = DetourAttach(reinterpret_cast<PVOID*>(&g_originalArchiveRead), reinterpret_cast<PVOID>(&HookArchiveRead));
	if (error == NO_ERROR && installDecrypt)
		error = DetourAttach(reinterpret_cast<PVOID*>(&g_originalDecrypt), reinterpret_cast<PVOID>(&HookDecrypt));
	if (error == NO_ERROR && installFeed)
		error = DetourAttach(reinterpret_cast<PVOID*>(&g_originalArchiveQueue), reinterpret_cast<PVOID>(&HookArchiveQueue));
	if (error == NO_ERROR && installFeed)
		error = DetourAttach(reinterpret_cast<PVOID*>(&g_originalArchivePump), reinterpret_cast<PVOID>(&HookArchivePump));
	if (error == NO_ERROR && installFeed)
		error = DetourAttach(reinterpret_cast<PVOID*>(&g_originalArchiveFinalize), reinterpret_cast<PVOID>(&HookArchiveFinalize));
	if (error == NO_ERROR && installFeed)
		error = DetourAttach(reinterpret_cast<PVOID*>(&g_originalArchiveMapInsert), reinterpret_cast<PVOID>(&HookArchiveMapInsert));
	if (error == NO_ERROR && installFeed)
		error = DetourAttach(reinterpret_cast<PVOID*>(&g_originalArchiveVerify), reinterpret_cast<PVOID>(&HookArchiveVerify));
	// 备用队列没有单独的钩子函数，保留 trampoline 指针供全量登记直接调用。
	if (error == NO_ERROR && installFeed)
		error = DetourAttach(reinterpret_cast<PVOID*>(&g_originalArchiveBuild), reinterpret_cast<PVOID>(&HookArchiveBuild));
	if (error == NO_ERROR && installFeed)
		error = DetourAttach(reinterpret_cast<PVOID*>(&g_originalArchiveWorker), reinterpret_cast<PVOID>(&HookArchiveWorker));
	if (error == NO_ERROR) error = DetourTransactionCommit();
	else DetourTransactionAbort();
	return error == NO_ERROR;
}

static void WriteStartupLog(const char* text)
{
	if (g_manifest == INVALID_HANDLE_VALUE || !text) return;
	EnterCriticalSection(&g_fileLock);
	WriteHandle(g_manifest, text, static_cast<DWORD>(strlen(text)));
	static const char newline[] = "\r\n";
	WriteHandle(g_manifest, newline, 2);
	LeaveCriticalSection(&g_fileLock);
}

static void WriteFeedResult(DWORD tableIndex, const std::string& resource,
	const std::string& archive, const char* status, bool succeeded)
{
	if (g_feedResultsLog == INVALID_HANDLE_VALUE) return;
	std::string resourceUtf8 = RuntimePathText(resource);
	std::string archiveUtf8 = RuntimePathText(archive);
	char prefix[96]{};
	int length = sprintf_s(prefix, sizeof(prefix), "%lu\t%d\t%s\t",
		static_cast<unsigned long>(tableIndex + 1), succeeded ? 1 : 0,
		status ? status : "未知");
	if (length < 0) return;
	EnterCriticalSection(&g_fileLock);
	WriteHandle(g_feedResultsLog, prefix, static_cast<DWORD>(length));
	WriteHandle(g_feedResultsLog, archiveUtf8.data(), static_cast<DWORD>(archiveUtf8.size()));
	static const char tab = '\t';
	WriteHandle(g_feedResultsLog, &tab, 1);
	WriteHandle(g_feedResultsLog, resourceUtf8.data(), static_cast<DWORD>(resourceUtf8.size()));
	static const char newline[] = "\r\n";
	WriteHandle(g_feedResultsLog, newline, 2);
	FlushFileBuffers(g_feedResultsLog);
	LeaveCriticalSection(&g_fileLock);
}

static void WriteRuntimeLog(const char* event, const std::string& detail, bool flush)
{
	if (g_runtimeLog == INVALID_HANDLE_VALUE || !event) return;
	std::string safe = detail;
	for (char& c : safe)
	{
		if (c == '\t' || c == '\r' || c == '\n') c = ' ';
	}
	if (safe.size() > 4096) safe.resize(4096);
	SYSTEMTIME now{};
	GetLocalTime(&now);
	char prefix[256]{};
	int length = sprintf_s(prefix, sizeof(prefix),
		"%04u-%02u-%02u %02u:%02u:%02u.%03u\t%llu\t%lu\t%s\t",
		now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond,
		now.wMilliseconds, static_cast<unsigned long long>(GetTickCount64()),
		static_cast<unsigned long>(GetCurrentThreadId()), event);
	if (length < 0) return;
	EnterCriticalSection(&g_fileLock);
	WriteHandle(g_runtimeLog, prefix, static_cast<DWORD>(length));
	if (!safe.empty()) WriteHandle(g_runtimeLog, safe.data(), static_cast<DWORD>(safe.size()));
	static const char newline[] = "\r\n";
	WriteHandle(g_runtimeLog, newline, 2);
	if (flush) FlushFileBuffers(g_runtimeLog);
	LeaveCriticalSection(&g_fileLock);
}

static std::string RuntimePathText(const std::string& value)
{
	if (value.empty()) return "未发生";
	std::string utf8 = WideToUtf8(AnsiPathToWide(value));
	return utf8.empty() ? value : utf8;
}

static void WriteRuntimeHeartbeat()
{
	if (g_runtimeLog == INVALID_HANDLE_VALUE) return;
	ULONGLONG now = GetTickCount64();
	ULONGLONG readTick = g_lastArchiveReadTick.load(std::memory_order_relaxed);
	ULONGLONG buildTick = g_lastArchiveBuildTick.load(std::memory_order_relaxed);
	ULONGLONG queueTick = g_lastQueueTick.load(std::memory_order_relaxed);
	std::string archive;
	std::string queueResource;
	std::string readPath;
	{
		std::lock_guard<std::mutex> lock(g_progressMutex);
		archive = g_lastArchive;
		queueResource = g_lastQueueResource;
		readPath = g_lastReadPath;
	}
	unsigned long pending = 0;
	{
		std::lock_guard<std::mutex> lock(g_outputMutex);
		pending = static_cast<unsigned long>(g_outputQueue.size() + g_entryQueue.size());
	}
	size_t feedCursor = 0;
	size_t feedPlanSize = 0;
	bool feedWaiting = false;
	std::string feedInFlight;
	{
		std::lock_guard<std::mutex> lock(g_feedMutex);
		feedCursor = g_feedCursor;
		feedPlanSize = g_feedPlan.size();
		feedWaiting = g_feedWaiting;
		feedInFlight = g_feedInFlightArchive;
		if (feedWaiting) ++pending;
	}
	DWORD primaryQueue = 0;
	DWORD alternateMode = 0;
	if (g_imageBase)
	{
		TryReadDword(g_engine.primaryQueueCount, primaryQueue);
		TryReadDword(g_engine.alternateQueueMode, alternateMode);
	}
	const auto idleText = [now](ULONGLONG tick) -> std::string {
		return tick == 0 ? "未发生" : std::to_string(static_cast<unsigned long long>(now - tick)) + "毫秒";
	};
	std::string detail =
		"分片=" + std::to_string(g_shardIndex + 1) + "/" + std::to_string(g_shardCount) +
		"（索引=" + std::to_string(g_shardBegin + 1) + "-" + std::to_string(g_shardEnd) + "）" +
		"；归档构建=" + std::to_string(g_archiveBuildCalls.load()) +
		"（成功=" + std::to_string(g_archiveBuildSuccess.load()) +
		"，已解析=" + std::to_string(g_archivesParsed.load()) +
		"）；归档读取=" + std::to_string(g_archiveReadCalls.load()) +
		"（成功=" + std::to_string(g_archiveReadSuccess.load()) +
		"）；原生登记=" + std::to_string(g_nativeQueueCalls.load()) +
		"（成功=" + std::to_string(g_nativeQueueSuccess.load()) +
		"）；全量登记=" + std::to_string(g_feedQueued.load()) + "/" +
		std::to_string(g_feedAttempted.load()) +
		"（计划=" + std::to_string(feedCursor) + "/" +
		std::to_string(feedPlanSize) + "；完成=" +
		std::to_string(g_feedCompleted.load()) + "；拒绝=" +
		std::to_string(g_feedRejected.load()) + "；等待归档=" +
		(feedWaiting ? RuntimePathText(feedInFlight) : "无") + "）" +
		"；流写入调用=" + std::to_string(g_streamWriteCalls.load()) +
		"（字节=" + std::to_string(g_streamWriteBytes.load()) +
		"）；文件已写=" + std::to_string(g_filesWritten.load()) +
		"；等待=" + std::to_string(pending) +
		"；写入中=" + std::to_string(g_outputActive.load()) +
		"；归档失败=" + std::to_string(g_feedFailed.load()) +
		"；写入失败=" + std::to_string(g_filesFailed.load()) +
		"；补丁命中=" + std::to_string(g_patchHits.load()) +
		"；补丁失败=" + std::to_string(g_patchFailures.load()) +
		"；调度调用=" + std::to_string(g_pumpCalls.load()) +
		"；归档收尾=" + std::to_string(g_finalizeCompleted.load()) + "/" +
		std::to_string(g_finalizeCalls.load()) +
		"；跳过缓存=" + std::to_string(g_cacheInsertSkipped.load()) +
		"；归档释放=" + std::to_string(g_feedArchivesReleased.load()) +
		"；引擎主队列=" + std::to_string(primaryQueue) +
		"；备用模式=" + std::to_string(alternateMode) +
		"；最后归档=" + RuntimePathText(archive) +
		"；最后读取=" + RuntimePathText(readPath) +
		"；最后登记=" + RuntimePathText(queueResource) +
		"；读取空闲=" + idleText(readTick) +
		"；构建空闲=" + idleText(buildTick) +
		"；登记空闲=" + idleText(queueTick) +
		"；调度空闲=" + idleText(g_lastPumpTick.load(std::memory_order_relaxed));
	WriteRuntimeLog("心跳", detail, true);
	static bool idleReported = false;
	ULONGLONG lastActivity = (std::max)({ readTick, buildTick, queueTick });
	if (lastActivity != 0 && now - lastActivity >= 5000)
	{
		if (!idleReported)
		{
			WriteRuntimeLog("引擎调用停滞", "连续超过 5 秒没有新的归档登记、构建或读取；" + detail, true);
			idleReported = true;
		}
	}
	else
	{
		idleReported = false;
	}
}

static void TryWriteCompletionMarker()
{
	if (!g_feedPlanReady.load(std::memory_order_acquire) ||
		g_completionWritten.load(std::memory_order_acquire)) return;
	bool feedDone = false;
	{
		std::lock_guard<std::mutex> lock(g_feedMutex);
		feedDone = g_feedCursor >= g_feedPlan.size() && !g_feedWaiting;
	}
	if (!feedDone)
	{
		g_feedDoneObservedTick.store(0, std::memory_order_release);
		return;
	}
	ULONGLONG now = GetTickCount64();
	ULONGLONG observed = g_feedDoneObservedTick.load(std::memory_order_acquire);
	if (observed == 0)
	{
		g_feedDoneObservedTick.compare_exchange_strong(observed, now, std::memory_order_acq_rel);
		return;
	}
	ULONGLONG lastRelevant = g_lastRelevantActivityTick.load(std::memory_order_relaxed);
	ULONGLONG quietSince = (std::max)(observed, lastRelevant);
	if (now - quietSince < g_completionQuietMs ||
		g_outputActive.load(std::memory_order_acquire) != 0) return;
	{
		std::lock_guard<std::mutex> lock(g_outputMutex);
		if (!g_outputQueue.empty() || !g_entryQueue.empty()) return;
	}
	bool expected = false;
	if (!g_completionWritten.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) return;
	unsigned long failed = g_feedFailed.load(std::memory_order_relaxed);
	unsigned long writeFailed = g_filesFailed.load(std::memory_order_relaxed);
	unsigned long patchFailed = g_patchFailures.load(std::memory_order_relaxed);
	std::string status = failed == 0 && writeFailed == 0 && patchFailed == 0 ? "成功" : "存在失败";
	std::string content =
		"状态\t分片\t分片数\t起始索引\t结束索引\t计划\t完成\t登记成功\t归档失败\t登记拒绝\t文件\t写入失败\t归档释放\t补丁命中\t补丁失败\r\n" +
		status + "\t" + std::to_string(g_shardIndex + 1) + "\t" + std::to_string(g_shardCount) + "\t" +
		std::to_string(g_shardBegin + 1) + "\t" + std::to_string(g_shardEnd) + "\t" +
		std::to_string(g_feedPlan.size()) + "\t" + std::to_string(g_feedCompleted.load()) + "\t" +
		std::to_string(g_feedQueued.load()) + "\t" + std::to_string(failed) + "\t" +
		std::to_string(g_feedRejected.load()) + "\t" + std::to_string(g_filesWritten.load()) + "\t" +
		std::to_string(writeFailed) + "\t" + std::to_string(g_feedArchivesReleased.load()) + "\t" +
		std::to_string(g_patchHits.load()) + "\t" + std::to_string(patchFailed) + "\r\n";
	HANDLE marker = CreateFileW((g_metaRoot + L"\\completion.tsv").c_str(), GENERIC_WRITE,
		FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
	if (marker != INVALID_HANDLE_VALUE)
	{
		WriteHandle(marker, content.data(), static_cast<DWORD>(content.size()));
		FlushFileBuffers(marker);
		CloseHandle(marker);
	}
	WriteRuntimeLog("分片处理完成", "状态=" + status + "；完成=" +
		std::to_string(g_feedCompleted.load()) + "/" + std::to_string(g_feedPlan.size()) +
		"；归档失败=" + std::to_string(failed) + "；写入失败=" + std::to_string(writeFailed) +
		"；补丁失败=" + std::to_string(patchFailed), true);
	if (g_closeAfterDump)
	{
		WriteRuntimeLog("自动关闭游戏", "全部资源和统计文件已经写入磁盘", true);
		FlushOutputHandles();
		Sleep(50);
		ExitProcess(0);
	}
}

static void WriteQueueRecord(const char* kind, const char* resource, int result,
	const void* key, const void* arg2, int arg3)
{
	if (g_queueLog == INVALID_HANDLE_VALUE) return;
	char line[1024]{};
	char keyHex[65]{};
	if (key && IsReadable(key, 32))
	{
		const BYTE* bytes = static_cast<const BYTE*>(key);
		for (int i = 0; i < 32; ++i) sprintf_s(keyHex + i * 2, 3, "%02X", bytes[i]);
	}
	else
	{
		strcpy_s(keyHex, "<unreadable>");
	}
	sprintf_s(line, sizeof(line), "%s\t%d\t%d\t%p\t%d\t%s\t%s\r\n",
		kind ? kind : "queue", result, arg3, arg2, g_feedStarted.load() ? 1 : 0,
		keyHex, resource ? resource : "");
	EnterCriticalSection(&g_fileLock);
	WriteHandle(g_queueLog, line, static_cast<DWORD>(strlen(line)));
	LeaveCriticalSection(&g_fileLock);
}

static bool TryReadDword(const void* address, DWORD& value)
{
	if (!address || !IsReadable(address, sizeof(DWORD))) return false;
	__try
	{
		value = *static_cast<const DWORD*>(address);
		return true;
	}
	__except (EXCEPTION_EXECUTE_HANDLER)
	{
		return false;
	}
}

static bool TryReadCString(const char* source, std::string& output, SIZE_T limit = 2048)
{
	output.clear();
	if (!source || limit == 0) return false;
	__try
	{
		for (SIZE_T i = 0; i < limit; ++i)
		{
			if (!IsReadable(source + i, 1)) return false;
			char c = source[i];
			if (c == '\0') return !output.empty();
			output.push_back(c);
		}
	}
	__except (EXCEPTION_EXECUTE_HANDLER)
	{
		output.clear();
		return false;
	}
	return false;
}

static void WriteEntryRecordSync(const std::string& archive, const std::string& entry,
	DWORD field56, DWORD field60)
{
	if (g_entriesLog == INVALID_HANDLE_VALUE) return;
	std::string archiveUtf8 = WideToUtf8(AnsiPathToWide(archive));
	std::string entryUtf8 = WideToUtf8(AnsiPathToWide(entry));
	char prefix[96]{};
	sprintf_s(prefix, sizeof(prefix), "%lu\t%lu\t", static_cast<unsigned long>(field56),
		static_cast<unsigned long>(field60));
	EnterCriticalSection(&g_fileLock);
	WriteHandle(g_entriesLog, prefix, static_cast<DWORD>(strlen(prefix)));
	if (!archiveUtf8.empty()) WriteHandle(g_entriesLog, archiveUtf8.data(), static_cast<DWORD>(archiveUtf8.size()));
	static const char tab = '\t';
	WriteHandle(g_entriesLog, &tab, 1);
	if (!entryUtf8.empty()) WriteHandle(g_entriesLog, entryUtf8.data(), static_cast<DWORD>(entryUtf8.size()));
	static const char newline[] = "\r\n";
	WriteHandle(g_entriesLog, newline, 2);
	LeaveCriticalSection(&g_fileLock);
}

static void WriteEntryRecord(const std::string& archive, const std::string& entry,
	DWORD field56, DWORD field60)
{
	if (g_entriesLog == INVALID_HANDLE_VALUE) return;
	if (!g_outputReady.load(std::memory_order_acquire) || g_outputWorkers.empty())
	{
		WriteEntryRecordSync(archive, entry, field56, field60);
		return;
	}
	EntryLogJob job;
	job.archive = archive;
	job.entry = entry;
	job.field56 = field56;
	job.field60 = field60;
	bool useSynchronousFallback = false;
	{
		std::lock_guard<std::mutex> lock(g_outputMutex);
		if (g_entryQueue.size() >= 8192)
			useSynchronousFallback = true;
		else
			g_entryQueue.emplace_back(std::move(job));
	}
	if (useSynchronousFallback)
		WriteEntryRecordSync(archive, entry, field56, field60);
	else
		g_outputCv.notify_one();
}

static void CaptureArchiveEntries(void* decoder)
{
	if (g_entriesLog == INVALID_HANDLE_VALUE || !decoder) return;
	BYTE* base = static_cast<BYTE*>(decoder) - 0x40;
	char archiveBuffer[1024]{};
	std::string archive;
	if (!TryReadMsvcString(base, archiveBuffer, sizeof(archiveBuffer))) archive = "<unknown>";
	else archive.assign(archiveBuffer);
	{
		std::lock_guard<std::mutex> lock(g_progressMutex);
		g_lastArchive = archive;
	}
	DWORD bucketCount = 0;
	DWORD bucketArray = 0;
	DWORD itemCount = 0;
	if (!TryReadDword(static_cast<BYTE*>(decoder) + 188, bucketCount) ||
		!TryReadDword(static_cast<BYTE*>(decoder) + 192, itemCount) ||
		!TryReadDword(static_cast<BYTE*>(decoder) + 204, bucketArray)) return;
	if (bucketCount == 0 || bucketCount > 0x100000 || itemCount > 0x100000 ||
		!IsReadable(reinterpret_cast<const void*>(bucketArray), static_cast<SIZE_T>(bucketCount) * 4)) return;
	DWORD seen = 0;
	for (DWORD bucket = 0; bucket < bucketCount && seen < itemCount + 1024; ++bucket)
	{
		DWORD link = 0;
		if (!TryReadDword(reinterpret_cast<const BYTE*>(bucketArray) + bucket * 4, link)) break;
		for (DWORD hops = 0; link && hops < 0x10000 && seen < itemCount + 1024; ++hops)
		{
			if (link < 0x1000) break;
			BYTE* node = reinterpret_cast<BYTE*>(static_cast<SIZE_T>(link) - 32);
			DWORD entryPointer = 0;
			DWORD next = 0;
			if (!TryReadDword(node + 28, entryPointer) || !TryReadDword(node + 32, next)) break;
			if (entryPointer >= 0x1000)
			{
				char entryBuffer[2048]{};
				std::string entry;
				if (TryReadMsvcString(reinterpret_cast<const BYTE*>(entryPointer), entryBuffer, sizeof(entryBuffer)))
				{
					TryReadCString(entryBuffer, entry);
					DWORD field56 = 0;
					DWORD field60 = 0;
					TryReadDword(reinterpret_cast<const BYTE*>(entryPointer) + 56, field56);
					TryReadDword(reinterpret_cast<const BYTE*>(entryPointer) + 60, field60);
					WriteEntryRecord(archive, entry, field56, field60);
					g_entriesSeen.fetch_add(1, std::memory_order_relaxed);
					++seen;
				}
			}
			link = next;
		}
	}
}

// 资源表的每一项是“逻辑资源路径、物理归档路径”两个指针。
// 物理路径以 0x3026B275 开头的是 ASF/视频包，不属于 AttacheCase
// 目录包，按引擎的文件读取结果原样保存，不送入 DAT 解码队列。
static bool IsVideoArchivePath(const std::string& archive)
{
	if (archive.empty()) return false;
	std::wstring wide = AnsiPathToWide(archive);
	HANDLE file = CreateFileW(wide.c_str(), GENERIC_READ,
		FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
		OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
	if (file == INVALID_HANDLE_VALUE)
	{
		std::wstring full = GetExeDirectory() + L"\\" + wide;
		file = CreateFileW(full.c_str(), GENERIC_READ,
			FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
			OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
	}
	if (file == INVALID_HANDLE_VALUE) return false;
	BYTE header[4]{};
	DWORD read = 0;
	BOOL ok = ReadFile(file, header, sizeof(header), &read, nullptr);
	CloseHandle(file);
	return ok && read == sizeof(header) && header[0] == 0x30 &&
		header[1] == 0x26 && header[2] == 0xB2 && header[3] == 0x75;
}

// ASF 不是 AttacheCase 目录包，不能交给 DAT 解码线程；直接保留原始包，
// 输出仍放在 files 目录，统计信息只写入 meta 目录。
static bool CopyRawArchive(const std::string& archive)
{
	std::wstring source = AnsiPathToWide(archive);
	if (GetFileAttributesW(source.c_str()) == INVALID_FILE_ATTRIBUTES)
		source = GetExeDirectory() + L"\\" + source;
	WIN32_FILE_ATTRIBUTE_DATA attributes{};
	if (!GetFileAttributesExW(source.c_str(), GetFileExInfoStandard, &attributes) ||
		(attributes.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0)
		return false;
	unsigned long sequence = ++g_sequence;
	std::wstring relative = MakeSafeRelativePath(archive, sequence);
	std::wstring output = g_extractRoot + L"\\" + relative;
	SIZE_T slash = output.find_last_of(L"\\/");
	if (slash != std::wstring::npos && !EnsureDirectory(output.substr(0, slash))) return false;
	if (!CopyFileW(source.c_str(), output.c_str(), FALSE)) return false;
	ULARGE_INTEGER size{};
	size.HighPart = attributes.nFileSizeHigh;
	size.LowPart = attributes.nFileSizeLow;
	WriteManifest(relative, static_cast<SIZE_T>(size.QuadPart),
		static_cast<DWORD>(size.QuadPart > 0xFFFFFFFFULL ? 0xFFFFFFFFUL : size.QuadPart), sequence);
	return true;
}

static bool IsConfiguredPrimaryArchive(const std::string& archive)
{
	wchar_t configured[260]{};
	DWORD length = GetEnvironmentVariableW(L"MARTOPIA_PRIMARY_ARCHIVE", configured, _countof(configured));
	if (length == 0 || length >= _countof(configured)) return false;
	std::wstring value(configured, length);
	std::wstring actual = AnsiPathToWide(archive);
	return _wcsicmp(value.c_str(), actual.c_str()) == 0;
}

static bool IsConfiguredSkippedArchive(const std::string& archive)
{
	wchar_t configured[260]{};
	DWORD length = GetEnvironmentVariableW(L"MARTOPIA_SKIP_ARCHIVE", configured, _countof(configured));
	if (length == 0 || length >= _countof(configured)) return false;
	std::wstring value(configured, length);
	std::wstring actual = AnsiPathToWide(archive);
	return _wcsicmp(value.c_str(), actual.c_str()) == 0;
}


// 按动态识别出的资源表建立计划，每次只在主线程提交一个归档。
// 这样完全遵循引擎自己的缓存生命周期，避免把备用队列一次性灌满。
static void FeedAllArchives(const void* key, const void* arg2, int arg3)
{
	if (!g_feedAll || !g_imageBase || g_feedRunning.exchange(true)) return;
	if (!key || !IsReadable(key, sizeof(g_feedKey)))
	{
		WriteStartupLog("# 全量登记失败：首个队列密钥不可读");
		g_feedRunning = false;
		return;
	}
	if (!g_originalArchiveQueueAlt && !g_originalArchiveQueue)
	{
		WriteStartupLog("# 全量登记失败：没有可用的归档队列入口");
		g_feedRunning = false;
		return;
	}
	memcpy(g_feedKey, key, sizeof(g_feedKey));
	g_feedArg2 = arg2;
	g_feedArg3 = arg3;
	std::vector<FeedItem> plan;
	const DWORD* table = g_engine.resourceTable;
	for (DWORD i = g_shardBegin; i < g_shardEnd; ++i)
	{
		DWORD resourcePointer = 0;
		if (!TryReadDword(table + static_cast<SIZE_T>(i) * 2, resourcePointer) || resourcePointer < 0x1000)
		{
			g_feedRejected.fetch_add(1, std::memory_order_relaxed);
			continue;
		}
		FeedItem item;
		item.tableIndex = i;
		item.resourcePointer = resourcePointer;
		if (!TryReadCString(reinterpret_cast<const char*>(static_cast<SIZE_T>(resourcePointer)), item.resource))
		{
			g_feedRejected.fetch_add(1, std::memory_order_relaxed);
			continue;
		}
		DWORD archivePointer = 0;
		if (TryReadDword(table + static_cast<SIZE_T>(i) * 2 + 1, archivePointer) && archivePointer >= 0x1000)
			TryReadCString(reinterpret_cast<const char*>(static_cast<SIZE_T>(archivePointer)), item.archive);
		item.video = IsVideoArchivePath(item.archive);
		item.skipped = IsConfiguredSkippedArchive(item.archive);
		plan.emplace_back(std::move(item));
	}
	{
		std::lock_guard<std::mutex> lock(g_feedMutex);
		g_feedPlan = std::move(plan);
		g_feedCursor = 0;
		g_feedWaiting = false;
		g_feedWorkerStarted = false;
		g_feedSubmittedTick = 0;
		g_feedInFlightIndex = 0;
		g_feedInFlightResource.clear();
		g_feedInFlightArchive.clear();
	}
	g_feedAttempted.store(g_shardEnd - g_shardBegin, std::memory_order_release);
	g_feedQueued.store(0, std::memory_order_release);
	g_feedCompleted.store(0, std::memory_order_release);
	g_feedFailed.store(0, std::memory_order_release);
	g_feedPlanReady.store(true, std::memory_order_release);
	WriteRuntimeLog("分片登记计划完成", "分片=" + std::to_string(g_shardIndex + 1) + "/" +
		std::to_string(g_shardCount) + "；资源表索引=" + std::to_string(g_shardBegin + 1) + "-" +
		std::to_string(g_shardEnd) + "；有效=" + std::to_string(g_feedPlan.size()) +
		"；主线程逐项提交", true);
	g_feedRunning = false;
	FeedNextArchive();
}

static bool BuildShardArchiveSet()
{
	g_shardArchives.clear();
	for (DWORD i = g_shardBegin; i < g_shardEnd; ++i)
	{
		DWORD archivePointer = 0;
		std::string archive;
		if (!TryReadDword(g_engine.resourceTable + static_cast<SIZE_T>(i) * 2 + 1, archivePointer) ||
			archivePointer < 0x1000 ||
			!TryReadCString(reinterpret_cast<const char*>(static_cast<SIZE_T>(archivePointer)), archive))
			continue;
		g_shardArchives.insert(ArchiveKey(archive));
	}
	return !g_shardArchives.empty();
}

static void MarkFeedArchiveComplete(const std::string& archive, const char* status, bool succeeded)
{
	bool completed = false;
	DWORD tableIndex = 0;
	std::string resource;
	{
		std::lock_guard<std::mutex> lock(g_feedMutex);
		if (g_feedWaiting && _stricmp(g_feedInFlightArchive.c_str(), archive.c_str()) == 0)
		{
			tableIndex = g_feedInFlightIndex;
			resource = g_feedInFlightResource;
			g_feedWaiting = false;
			g_feedWorkerStarted = false;
			g_feedSubmittedTick = 0;
			g_feedInFlightResource.clear();
			g_feedInFlightArchive.clear();
			completed = true;
		}
	}
	if (completed)
	{
		g_feedCompleted.fetch_add(1, std::memory_order_relaxed);
		if (!succeeded) g_feedFailed.fetch_add(1, std::memory_order_relaxed);
		WriteFeedResult(tableIndex, resource, archive, status, succeeded);
		WriteRuntimeLog(succeeded ? "归档登记完成" : "归档处理失败",
			"索引=" + std::to_string(tableIndex + 1) + "；物理=" + RuntimePathText(archive) +
			"；状态=" + (status ? status : "未知"), !succeeded);
	}
}

static void FeedNextArchive()
{
	if (!g_feedPlanReady.load(std::memory_order_acquire)) return;
	FeedItem item;
	{
		std::lock_guard<std::mutex> lock(g_feedMutex);
		if (g_feedWaiting || g_feedCursor >= g_feedPlan.size()) return;
		item = g_feedPlan[g_feedCursor++];
		g_feedInFlightIndex = item.tableIndex;
		g_feedInFlightResource = item.resource;
		g_feedInFlightArchive = item.archive;
		g_feedWaiting = true;
		g_feedWorkerStarted = false;
		g_feedSubmittedTick = GetTickCount64();
	}
	char result = 0;
	if (item.skipped || item.video)
	{
		result = CopyRawArchive(item.archive) ? 1 : 0;
		WriteQueueRecord(item.skipped ? "feed-skip-copy" : "feed-video-copy", item.resource.c_str(), result, g_feedKey, nullptr, 0);
		WriteRuntimeLog(item.skipped ? "诊断包原始复制" : "视频包原始复制",
			"逻辑=" + RuntimePathText(item.resource) + "；物理=" + RuntimePathText(item.archive) +
			"；结果=" + std::to_string(static_cast<int>(result)), true);
	}
	else if (!IsConfiguredPrimaryArchive(item.archive) && g_originalArchiveQueueAlt)
	{
		result = g_originalArchiveQueueAlt(reinterpret_cast<const char*>(static_cast<SIZE_T>(item.resourcePointer)), g_feedKey);
		WriteQueueRecord("feed-alt", item.resource.c_str(), result, g_feedKey, nullptr, 0);
	}
	else if (g_originalArchiveQueue)
	{
		result = g_originalArchiveQueue(reinterpret_cast<const char*>(static_cast<SIZE_T>(item.resourcePointer)),
			g_feedKey, g_feedArg2, g_feedArg3);
		WriteQueueRecord("feed-primary", item.resource.c_str(), result, g_feedKey, g_feedArg2, g_feedArg3);
	}
	if (result) g_feedQueued.fetch_add(1, std::memory_order_relaxed);
	if (!result || item.skipped || item.video)
	{
		bool alreadyCaptured = !result && WasArchiveCaptured(item.archive);
		if (!result)
		{
			bool physicalDat = item.archive.size() >= 4 &&
				(_strnicmp(item.archive.c_str(), "dat\\", 4) == 0 ||
				 _strnicmp(item.archive.c_str(), "dat/", 4) == 0);
			if (!physicalDat)
				WriteRuntimeLog("动态资源未登记", "逻辑=" + RuntimePathText(item.resource) +
					"；物理=" + RuntimePathText(item.archive) +
					"；资源表不是物理 DAT，按引擎请求结果跳过", true);
			else
				g_feedRejected.fetch_add(1, std::memory_order_relaxed);
		}
		MarkFeedArchiveComplete(item.archive,
			result ? (item.video ? "视频原始复制" : "诊断包原始复制") :
				(alreadyCaptured ? "已由引擎原生预加载" : "登记被拒绝"),
			result != 0 || alreadyCaptured);
	}
}

static bool __fastcall HookArchiveFinalize(void* self, void* /*edx*/)
{
	g_finalizeCalls.fetch_add(1, std::memory_order_relaxed);
	g_lastFinalizeTick.store(GetTickCount64(), std::memory_order_relaxed);
	std::string archive;
	{
		std::lock_guard<std::mutex> lock(g_progressMutex);
		archive = g_lastArchive;
	}
	WriteRuntimeLog("归档收尾开始", "归档=" + RuntimePathText(archive) +
		"；对象=" + std::to_string(reinterpret_cast<ULONG_PTR>(self)));
	bool result = g_originalArchiveFinalize ? g_originalArchiveFinalize(self) : false;
	g_finalizeCompleted.fetch_add(1, std::memory_order_relaxed);
	g_lastFinalizeTick.store(GetTickCount64(), std::memory_order_relaxed);
	// 只旁路 DLL 主动灌入的归档。游戏启动阶段原生加载的资源仍进入缓存，
	// 避免解包逻辑影响引擎后续初始化和正常资源访问。
	g_bypassNextLoadedMapInsert = g_releaseFeedArchives && g_workerIsScheduledFeedItem;
	WriteRuntimeLog("归档收尾结束", "归档=" + RuntimePathText(archive) +
		"；结果=" + std::to_string(result ? 1 : 0));
	return result;
}

static void* __fastcall HookArchiveMapInsert(void* map, void* key)
{
	BYTE* expectedMap = g_engine.loadedArchiveMap;
	if (g_bypassNextLoadedMapInsert && map == expectedMap)
	{
		g_bypassNextLoadedMapInsert = false;
		g_feedArchiveUsedTemporaryNode = true;
		g_cacheInsertSkipped.fetch_add(1, std::memory_order_relaxed);
		char archiveBuffer[1024]{};
		std::string archive;
		if (key && TryReadMsvcString(static_cast<const BYTE*>(key), archiveBuffer, sizeof(archiveBuffer)))
			archive.assign(archiveBuffer);
		WriteRuntimeLog("跳过归档缓存登记", "归档=" + RuntimePathText(archive));
		// 调用方只会在返回节点的 +28 写入对象指针；线程局部临时节点
		// 保证该写入有效，同时不修改引擎的全局哈希表。
		static thread_local BYTE temporaryNode[64]{};
		memset(temporaryNode, 0, sizeof(temporaryNode));
		return temporaryNode;
	}
	return g_originalArchiveMapInsert ? g_originalArchiveMapInsert(map, key) : nullptr;
}

static bool __fastcall HookArchiveVerify(void* self, void* /*edx*/, const char* path, const void* key)
{
	g_workerVerifySeen = true;
	WriteRuntimeLog("归档验证开始", "路径=" + RuntimePathText(path ? path : ""));
	bool result = g_originalArchiveVerify ? g_originalArchiveVerify(self, path, key) : false;
	g_workerVerifySucceeded = result;
	WriteRuntimeLog("归档验证结束", "路径=" + RuntimePathText(path ? path : "") +
		"；结果=" + std::to_string(result ? 1 : 0));
	return result;
}

static unsigned int __stdcall HookArchiveWorker(void* argument)
{
	char archiveBuffer[1024]{};
	std::string archive;
	if (argument && TryReadMsvcString(static_cast<const BYTE*>(argument), archiveBuffer, sizeof(archiveBuffer)))
		archive.assign(archiveBuffer);
	std::string previous = g_workerArchive;
	bool previousFeedItem = g_workerIsFeedItem;
	bool previousScheduledFeedItem = g_workerIsScheduledFeedItem;
	bool previousBypass = g_bypassNextLoadedMapInsert;
	bool previousTemporaryNode = g_feedArchiveUsedTemporaryNode;
	g_workerArchive = archive;
	g_workerIsFeedItem = ArchiveBelongsToShard(archive) && !WasArchiveCaptured(archive);
	g_workerIsScheduledFeedItem = false;
	g_bypassNextLoadedMapInsert = false;
	g_feedArchiveUsedTemporaryNode = false;
	if (g_workerIsFeedItem)
		g_lastRelevantActivityTick.store(GetTickCount64(), std::memory_order_relaxed);
	g_workerVerifySeen = false;
	g_workerVerifySucceeded = false;
	g_workerBuildSeen = false;
	g_workerBuildSucceeded = false;
	{
		std::lock_guard<std::mutex> lock(g_feedMutex);
		if (g_feedWaiting && _stricmp(g_feedInFlightArchive.c_str(), archive.c_str()) == 0)
		{
			g_feedWorkerStarted = true;
			g_workerIsFeedItem = true;
			g_workerIsScheduledFeedItem = true;
			g_lastRelevantActivityTick.store(GetTickCount64(), std::memory_order_relaxed);
		}
	}
	WriteRuntimeLog("归档工作线程开始", "路径=" + RuntimePathText(archive) +
		"；参数=" + std::to_string(reinterpret_cast<ULONG_PTR>(argument)), false);
	unsigned int result = g_originalArchiveWorker ? g_originalArchiveWorker(argument) : 0;
	WriteRuntimeLog("归档工作线程结束", "路径=" + RuntimePathText(archive) +
		"；结果=" + std::to_string(result), false);
	bool succeeded = g_workerVerifySeen && g_workerVerifySucceeded &&
		g_workerBuildSeen && g_workerBuildSucceeded;
	if (succeeded && g_workerIsFeedItem)
	{
		std::lock_guard<std::mutex> lock(g_successfulArchivesMutex);
		g_successfulArchives.insert(ArchiveKey(archive));
	}
	if (!archive.empty())
		MarkFeedArchiveComplete(archive, succeeded ? "验证并解包成功" :
			(g_workerVerifySeen && !g_workerVerifySucceeded ? "DAT 验证失败" : "DAT 构建失败"), succeeded);
	// 原生工作线程在成功后把 argument 留在全局缓存，正常只在游戏退出时释放。
	// 单进程全量解包模式已旁路该缓存，因此必须走引擎自己的完整析构函数；
	// 文件内容在读取钩子中已移动到独立写入队列，不再引用这个归档对象。
	if (succeeded && g_workerIsScheduledFeedItem && g_feedArchiveUsedTemporaryNode && argument && g_originalArchiveDestroy)
	{
		g_originalArchiveDestroy(argument);
		unsigned long released = g_feedArchivesReleased.fetch_add(1, std::memory_order_relaxed) + 1;
		if (released <= 3 || released % 25 == 0)
		{
			WriteRuntimeLog("归档对象已释放", "路径=" + RuntimePathText(archive) +
				"；累计=" + std::to_string(released), true);
		}
	}
	g_workerArchive = previous;
	g_workerIsFeedItem = previousFeedItem;
	g_workerIsScheduledFeedItem = previousScheduledFeedItem;
	g_bypassNextLoadedMapInsert = previousBypass;
	g_feedArchiveUsedTemporaryNode = previousTemporaryNode;
	if (succeeded)
		g_lastRelevantActivityTick.store(GetTickCount64(), std::memory_order_relaxed);
	return result;
}

static int __cdecl HookArchivePump()
{
	g_pumpCalls.fetch_add(1, std::memory_order_relaxed);
	g_lastPumpTick.store(GetTickCount64(), std::memory_order_relaxed);
	// 归档构建发生在引擎工作线程，备用队列的哈希表只能由调度器线程
	// 修改。构建钩子只登记请求，真正写入放在每帧调度入口的主线程执行。
	if (g_feedDeferred.exchange(false, std::memory_order_acq_rel))
	{
		WriteRuntimeLog("主线程登记全量资源", "在归档调度前执行");
		FeedAllArchives(g_feedKey, g_feedArg2, g_feedArg3);
	}
	DWORD primaryBefore = 0;
	DWORD alternateBefore = 0;
	DWORD threadHandleValue = 0;
	if (g_imageBase)
	{
		TryReadDword(g_engine.primaryQueueCount, primaryBefore);
		TryReadDword(g_engine.alternateQueueMode, alternateBefore);
		TryReadDword(g_engine.archiveThreadHandle, threadHandleValue);
	}
	DWORD threadExitCode = 0xFFFFFFFF;
	BOOL threadQueryOk = FALSE;
	if (threadHandleValue != 0)
		threadQueryOk = GetExitCodeThread(reinterpret_cast<HANDLE>(static_cast<SIZE_T>(threadHandleValue)), &threadExitCode);
	int result = g_originalArchivePump ? g_originalArchivePump() : 0;
	DWORD primaryAfter = 0;
	DWORD alternateAfter = 0;
	DWORD threadHandleAfter = 0;
	if (g_imageBase)
	{
		TryReadDword(g_engine.primaryQueueCount, primaryAfter);
		TryReadDword(g_engine.alternateQueueMode, alternateAfter);
		TryReadDword(g_engine.archiveThreadHandle, threadHandleAfter);
	}
	DWORD threadExitCodeAfter = 0xFFFFFFFF;
	BOOL threadQueryAfterOk = FALSE;
	if (threadHandleAfter != 0)
		threadQueryAfterOk = GetExitCodeThread(
			reinterpret_cast<HANDLE>(static_cast<SIZE_T>(threadHandleAfter)), &threadExitCodeAfter);
	bool workerRunningAfter = threadQueryAfterOk && threadExitCodeAfter == STILL_ACTIVE;
	// 32 位进程接近地址空间上限时，_beginthreadex 可能返回 0。此时引擎已把
	// 请求从备用队列删除，却不会启动工作线程；若继续等待就会永久卡住。
	// 只有确认调度后的线程句柄也不处于运行状态、两个队列都已清空后才失败。
	// 工作线程可能刚由本次 Pump 创建，尚未来得及取得 g_feedMutex，不能只看标志。
	bool workerStartTimedOut = false;
	std::string timedOutArchive;
	{
		std::lock_guard<std::mutex> lock(g_feedMutex);
		ULONGLONG now = GetTickCount64();
		if (g_feedWaiting && !g_feedWorkerStarted && g_feedSubmittedTick != 0 &&
			now - g_feedSubmittedTick >= g_workerStartTimeoutMs &&
			primaryAfter == 0 && alternateAfter == 0 && !workerRunningAfter)
		{
			timedOutArchive = g_feedInFlightArchive;
			workerStartTimedOut = true;
		}
	}
	if (workerStartTimedOut)
	{
		WriteRuntimeLog("归档工作线程未启动", "路径=" + RuntimePathText(timedOutArchive) +
			"；等待=" + std::to_string(g_workerStartTimeoutMs) +
			"毫秒；队列已空且调度后没有活动线程，已加入失败清单", true);
		MarkFeedArchiveComplete(timedOutArchive, "工作线程启动失败", false);
	}
	// 原生调度完成后再提交下一项，确保当前归档对象已经离开工作线程。
	FeedNextArchive();
	// 只有队列数量变化或显式详细日志时才写单次调度事件，避免日志本身拖慢主线程。
	if (g_verboseRuntimeLog || primaryBefore != primaryAfter || alternateBefore != alternateAfter ||
		(primaryBefore != 0 || alternateBefore != 0) && (g_pumpCalls.load(std::memory_order_relaxed) % 100 == 0))
	{
		WriteRuntimeLog("归档调度", "返回=" + std::to_string(result) +
			"；主队列=" + std::to_string(primaryBefore) + "->" + std::to_string(primaryAfter) +
			"；备用队列=" + std::to_string(alternateBefore) + "->" + std::to_string(alternateAfter) +
			"；线程句柄=" + std::to_string(threadHandleValue) + "->" + std::to_string(threadHandleAfter) +
			"；句柄查询=" + std::to_string(threadQueryOk ? 1 : 0) + "->" +
				std::to_string(threadQueryAfterOk ? 1 : 0) +
			"；线程状态=" + std::to_string(threadExitCode) + "->" + std::to_string(threadExitCodeAfter));
	}
	return result;
}

static char __fastcall HookArchiveQueue(const char* resource, void* /*edx*/,
	const void* key, const void* arg2, int arg3)
{
	g_nativeQueueCalls.fetch_add(1, std::memory_order_relaxed);
	g_lastQueueTick.store(GetTickCount64(), std::memory_order_relaxed);
	{
		std::lock_guard<std::mutex> lock(g_progressMutex);
		g_lastQueueResource = resource ? resource : "";
	}
	char result = g_originalArchiveQueue ? g_originalArchiveQueue(resource, key, arg2, arg3) : 0;
	if (result) g_nativeQueueSuccess.fetch_add(1, std::memory_order_relaxed);
	WriteQueueRecord("native", resource, result, key, arg2, arg3);
	if (g_verboseRuntimeLog)
	{
		WriteRuntimeLog("原生归档登记", "路径=" + RuntimePathText(resource ? resource : "") +
			"；结果=" + std::to_string(static_cast<int>(result)));
	}
	if (result && g_feedAll && !g_feedStarted.exchange(true))
	{
		// 只复制参数并交给主线程调度；工作线程不能直接改备用队列哈希表。
		if (key && IsReadable(key, sizeof(g_feedKey)))
		{
			memcpy(g_feedKey, key, sizeof(g_feedKey));
			g_feedArg2 = arg2;
			g_feedArg3 = arg3;
			g_feedDeferred.store(true, std::memory_order_release);
			WriteRuntimeLog("全量登记已排队", "等待主线程调度", true);
		}
	}
	return result;
}

static char __fastcall HookArchiveBuild(void* decoder, void* /*edx*/)
{
	g_workerBuildSeen = true;
	g_archiveBuildCalls.fetch_add(1, std::memory_order_relaxed);
	g_lastArchiveBuildTick.store(GetTickCount64(), std::memory_order_relaxed);
	WriteRuntimeLog("归档构建开始", "对象=" + std::to_string(reinterpret_cast<ULONG_PTR>(decoder)) +
		"；工作路径=" + RuntimePathText(g_workerArchive), false);
	char result = g_originalArchiveBuild ? g_originalArchiveBuild(decoder) : 0;
	g_workerBuildSucceeded = result != 0;
	if (result) g_archiveBuildSuccess.fetch_add(1, std::memory_order_relaxed);
	if (result && g_workerIsFeedItem)
	{
		g_archivesParsed.fetch_add(1, std::memory_order_relaxed);
		CaptureArchiveEntries(decoder);
	}
	if (result && g_feedAll && !g_feedStarted.load(std::memory_order_acquire))
	{
		BYTE* base = static_cast<BYTE*>(decoder) - 0x40;
		DWORD arg3 = 0;
		DWORD arg2Pointer = 0;
		if (IsReadable(base + 28, 32) &&
			TryReadDword(base + 60, arg2Pointer) && TryReadDword(base + 64, arg3))
		{
			bool expected = false;
			if (g_feedStarted.compare_exchange_strong(expected, true, std::memory_order_acq_rel))
			{
				// 密钥和参数在工作对象释放前复制，待主线程的调度钩子消费。
				memcpy(g_feedKey, base + 28, sizeof(g_feedKey));
				g_feedArg2 = reinterpret_cast<const void*>(static_cast<SIZE_T>(arg2Pointer));
				g_feedArg3 = static_cast<int>(arg3);
				g_feedDeferred.store(true, std::memory_order_release);
				WriteRuntimeLog("全量登记已排队", "等待主线程调度；参数=" +
					std::to_string(static_cast<unsigned long>(arg3)), true);
			}
		}
		else
		{
			WriteStartupLog("# 全量登记失败：归档对象参数不可读");
		}
	}
	WriteRuntimeLog("归档构建结束", "对象=" + std::to_string(reinterpret_cast<ULONG_PTR>(decoder)) +
		"；工作路径=" + RuntimePathText(g_workerArchive) +
		"；结果=" + std::to_string(static_cast<int>(result)), false);
	return result;
}

static DWORD WINAPI InitThread(LPVOID)
{
	g_martopia = GetModuleHandleW(nullptr);
	InitializeCriticalSection(&g_fileLock);
	g_config = LoadMartopiaConfig(GetExeDirectory());
	g_dumpEnabled = g_config.DumpEnabled();
	if (!g_config.enable || g_config.mode == 0)
		return 0;

	g_workerStartTimeoutMs = ReadEnvUnsigned(L"MARTOPIA_WORKER_START_TIMEOUT_MS", 5000, 1000, 60000);
	g_completionQuietMs = ReadEnvUnsigned(L"MARTOPIA_COMPLETION_QUIET_MS", 3000, 1000, 30000);
	if (g_dumpEnabled)
	{
		g_dumpRoot = MakeRunDirectory();
		EnsureDirectory(g_dumpRoot);
		g_extractRoot = ReadEnvPath(L"MARTOPIA_EXTRACT_ROOT");
		if (g_extractRoot.empty()) g_extractRoot = g_dumpRoot + L"\\files";
		g_metaRoot = ReadEnvPath(L"MARTOPIA_META_ROOT");
		if (g_metaRoot.empty()) g_metaRoot = g_dumpRoot + L"\\meta";
		EnsureDirectory(g_extractRoot);
		EnsureDirectory(g_metaRoot);
		g_manifest = CreateFileW((g_metaRoot + L"\\manifest.tsv").c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
			CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
		g_blocks = CreateFileW((g_metaRoot + L"\\decrypt_blocks.bin").c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
			CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
		g_queueLog = CreateFileW((g_metaRoot + L"\\archive_queue.tsv").c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
			CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
		g_entriesLog = CreateFileW((g_metaRoot + L"\\archive_entries.tsv").c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
			CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
		g_runtimeLog = CreateFileW((g_metaRoot + L"\\runtime.tsv").c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
			CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
		g_feedResultsLog = CreateFileW((g_metaRoot + L"\\dat_results.tsv").c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
			CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
		WriteStartupLog("# sequence\tsize\trequested\tpath");
		if (g_queueLog != INVALID_HANDLE_VALUE)
		{
			static const char header[] = "kind\tresult\targ3\targ2\tfeed_started\tkey32\tresource\r\n";
			WriteHandle(g_queueLog, header, static_cast<DWORD>(sizeof(header) - 1));
		}
		if (g_entriesLog != INVALID_HANDLE_VALUE)
		{
			static const char header[] = "field56\tfield60\tarchive\tentry\r\n";
			WriteHandle(g_entriesLog, header, static_cast<DWORD>(sizeof(header) - 1));
		}
		if (g_runtimeLog != INVALID_HANDLE_VALUE)
		{
			static const char header[] = "时间\t系统毫秒\t线程\t事件\t详情\r\n";
			WriteHandle(g_runtimeLog, header, static_cast<DWORD>(sizeof(header) - 1));
		}
		if (g_feedResultsLog != INVALID_HANDLE_VALUE)
		{
			static const char header[] = "资源表索引\t成功\t状态\t物理归档\t逻辑资源\r\n";
			WriteHandle(g_feedResultsLog, header, static_cast<DWORD>(sizeof(header) - 1));
		}
	}
	if (g_config.PatchEnabled() && g_config.patchLog)
	{
		std::wstring path = g_dumpEnabled
			? g_metaRoot + L"\\resource_patch.tsv"
			: g_config.gameDirectory + L"\\MartopiaWinmm_patch.tsv";
		g_patchLog = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
			CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
		if (g_patchLog != INVALID_HANDLE_VALUE)
		{
			static const char header[] =
				"时间\t状态\t补丁字节\t原始字节\t匹配方式\t内部路径\t补丁文件\t备注\r\n";
			WriteHandle(g_patchLog, header, static_cast<DWORD>(sizeof(header) - 1));
			for (const std::wstring& folder : g_config.patchFolders)
			{
				ResourcePatchMatch folderMatch;
				folderMatch.diskPath = folder;
				WritePatchLog("加载目录", "", folderMatch, 0, 0);
			}
		}
	}

	std::string featureError;
	if (!ResolveEngineFeatures(g_martopia, g_engine, featureError))
	{
		WriteStartupLog(("# 引擎特征识别失败：" + featureError).c_str());
		WriteRuntimeLog("引擎特征识别失败", featureError, true);
		ResourcePatchMatch emptyMatch;
		WritePatchLog("初始化失败", "", emptyMatch, 0, 0, Utf8ToWide(featureError));
		FlushOutputHandles();
		return 0;
	}
	g_imageBase = g_engine.imageBase;
	g_resourceCount = g_engine.resourceCount;
	g_shardCount = g_dumpEnabled ? ReadEnvUnsigned(L"MARTOPIA_SHARD_COUNT", 1, 1, 1024) : 1;
	g_shardIndex = ReadEnvUnsigned(L"MARTOPIA_SHARD_INDEX", 0, 0, g_shardCount - 1);
	g_shardBegin = static_cast<unsigned int>(
		(static_cast<unsigned long long>(g_resourceCount) * g_shardIndex) / g_shardCount);
	g_shardEnd = static_cast<unsigned int>(
		(static_cast<unsigned long long>(g_resourceCount) * (g_shardIndex + 1)) / g_shardCount);
	if (g_dumpEnabled && !BuildShardArchiveSet())
	{
		WriteRuntimeLog("分片资源索引失败", "动态资源表中没有可用的归档路径", true);
		FlushOutputHandles();
		return 0;
	}

	wchar_t option[8]{};
	bool skipAll = GetEnvironmentVariableW(L"MARTOPIA_NO_HOOK", option, _countof(option)) != 0;
	bool skipArchive = GetEnvironmentVariableW(L"MARTOPIA_NO_ARCHIVE_HOOK", option, _countof(option)) != 0;
	bool skipDecrypt = GetEnvironmentVariableW(L"MARTOPIA_NO_DECRYPT_HOOK", option, _countof(option)) != 0;
	bool skipStream = GetEnvironmentVariableW(L"MARTOPIA_NO_STREAM_HOOK", option, _countof(option)) != 0;
	bool enableDecrypt = g_dumpEnabled &&
		GetEnvironmentVariableW(L"MARTOPIA_ENABLE_DECRYPT_BLOCKS", option, _countof(option)) != 0;
	bool keepFeedArchives =
		GetEnvironmentVariableW(L"MARTOPIA_KEEP_FEED_ARCHIVES", option, _countof(option)) != 0;
	g_releaseFeedArchives = g_dumpEnabled && !keepFeedArchives &&
		(g_shardCount == 1 ||
		GetEnvironmentVariableW(L"MARTOPIA_RELEASE_FEED_ARCHIVES", option, _countof(option)) != 0 ||
		GetEnvironmentVariableW(L"MARTOPIA_SKIP_ARCHIVE_CACHE", option, _countof(option)) != 0);
	g_feedAll = g_dumpEnabled &&
		GetEnvironmentVariableW(L"MARTOPIA_NO_FEED_ALL", option, _countof(option)) == 0;
	g_closeAfterDump = g_dumpEnabled &&
		GetEnvironmentVariableW(L"MARTOPIA_KEEP_OPEN", option, _countof(option)) == 0;
	g_decryptDryRun = GetEnvironmentVariableW(L"MARTOPIA_DECRYPT_DRY", option, _countof(option)) != 0;
	g_decryptNoWrite = GetEnvironmentVariableW(L"MARTOPIA_DECRYPT_NO_WRITE", option, _countof(option)) != 0;
	g_decryptNoRead = GetEnvironmentVariableW(L"MARTOPIA_DECRYPT_NO_READ", option, _countof(option)) != 0;
	g_verboseRuntimeLog = GetEnvironmentVariableW(L"MARTOPIA_VERBOSE_LOG", option, _countof(option)) != 0;

	WriteRuntimeLog("引擎特征识别完成", DescribeEngineFeatures(g_engine), true);
	WriteRuntimeLog("初始化", "模式=" + std::to_string(g_config.mode) +
		"；资源覆盖=" + (g_config.PatchEnabled() ? "开启" : "关闭") +
		"；详细调用日志=" + (g_verboseRuntimeLog ? "开启" : "关闭") +
		"；分片=" + std::to_string(g_shardIndex + 1) + "/" + std::to_string(g_shardCount) +
		"；资源表索引=" + std::to_string(g_shardBegin + 1) + "-" + std::to_string(g_shardEnd) +
		"；逐包释放=" + (g_releaseFeedArchives ? "开启" : "关闭") +
		"；完成后关闭=" + (g_closeAfterDump ? "开启" : "关闭") +
		"；工作线程启动超时=" + std::to_string(g_workerStartTimeoutMs) + "毫秒" +
		"；完成静默期=" + std::to_string(g_completionQuietMs) + "毫秒", true);
	if (g_dumpEnabled) StartOutputWorkers();

	bool installDecrypt = enableDecrypt && !skipDecrypt && g_engine.decrypt != nullptr;
	if (enableDecrypt && !skipDecrypt && !g_engine.decrypt)
		WriteRuntimeLog("解密块钩子未启用", "当前程序中没有唯一匹配的解密函数特征", true);
	bool installArchive = !skipArchive && (g_dumpEnabled || g_config.PatchEnabled());
	bool detoured = false;
	if (!skipAll && (installArchive || installDecrypt || g_feedAll))
		detoured = InstallDetours(installArchive, installDecrypt, g_feedAll);
	bool writeHooked = !skipAll && !skipStream && (g_dumpEnabled || g_config.PatchEnabled()) &&
		PatchOstreamWriteIat(g_martopia);
	char status[160]{};
	sprintf_s(status, sizeof(status), "# 钩子状态：归档=%d 解密=%d 登记=%d 输出流=%d 槽位=%lu",
		detoured ? 1 : 0, installDecrypt ? 1 : 0, g_feedAll ? 1 : 0, writeHooked ? 1 : 0,
		static_cast<unsigned long>(g_writePatched));
	WriteStartupLog(status);
	WriteRuntimeLog("钩子状态", std::string("归档=") + (detoured ? "1" : "0") +
		"；解密=" + (installDecrypt ? "1" : "0") +
		"；登记=" + (g_feedAll ? "1" : "0") +
		"；输出流=" + (writeHooked ? "1" : "0") +
		"；槽位=" + std::to_string(g_writePatched), true);
	if (g_config.PatchEnabled() && (!detoured || !writeHooked))
	{
		ResourcePatchMatch emptyMatch;
		WritePatchLog("初始化失败", "", emptyMatch, 0, 0,
			!detoured ? L"归档读取钩子安装失败" : L"输出流钩子安装失败");
	}
	else if (g_config.PatchEnabled())
	{
		ResourcePatchMatch emptyMatch;
		WritePatchLog("初始化成功", "", emptyMatch, 0, 0,
			L"资源覆盖钩子已安装；模式=" + std::to_wstring(g_config.mode));
	}
	return 0;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID)
{
	if (reason == DLL_PROCESS_ATTACH)
	{
		DisableThreadLibraryCalls(module);
		HMODULE pinned = nullptr;
		GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
			reinterpret_cast<LPCWSTR>(&DllMain), &pinned);
		HANDLE thread = CreateThread(nullptr, 0, InitThread, nullptr, 0, nullptr);
		if (thread) CloseHandle(thread);
	}
	return TRUE;
}
