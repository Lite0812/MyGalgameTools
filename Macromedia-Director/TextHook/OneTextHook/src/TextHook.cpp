#include "TextHook.h"
#include "JsonTrans.h"

#include <Windows.h>
#include <detours.h>

#include <cstdarg>
#include <cstdio>
#include <memory>
#include <mutex>

namespace OneTextHook
{
    namespace
    {
        using ExtTextOutAFn = BOOL(WINAPI*)(HDC, int, int, UINT, const RECT*, LPCSTR, UINT, const INT*);
        using GetTextExtentExPointAFn = BOOL(WINAPI*)(HDC, LPCSTR, int, int, LPINT, LPINT, LPSIZE);
        using GetTextExtentPoint32AFn = BOOL(WINAPI*)(HDC, LPCSTR, int, LPSIZE);
        using GetTextExtentPointAFn = BOOL(WINAPI*)(HDC, LPCSTR, int, LPSIZE);
        using MultiByteToWideCharFn = int(WINAPI*)(UINT, DWORD, LPCCH, int, LPWSTR, int);

        Settings g_settings;
        TranslationStore g_store;
        std::mutex g_lock;
        FILE* g_log = nullptr;
        std::wstring g_extractPath;
        bool g_initialized = false;
        bool g_shuttingDown = false;

        ExtTextOutAFn RealExtTextOutA = nullptr;
        GetTextExtentExPointAFn RealGetTextExtentExPointA = nullptr;
        GetTextExtentPoint32AFn RealGetTextExtentPoint32A = nullptr;
        GetTextExtentPointAFn RealGetTextExtentPointA = nullptr;
        MultiByteToWideCharFn RealMultiByteToWideChar = nullptr;

        std::wstring GetModuleDir(HMODULE module)
        {
            wchar_t path[MAX_PATH] = {};
            GetModuleFileNameW(module, path, MAX_PATH);
            std::wstring result = path;
            size_t pos = result.find_last_of(L"\\/");
            if (pos == std::wstring::npos) return L".";
            return result.substr(0, pos);
        }

        std::wstring GetExeDir()
        {
            wchar_t path[MAX_PATH] = {};
            GetModuleFileNameW(nullptr, path, MAX_PATH);
            std::wstring result = path;
            size_t pos = result.find_last_of(L"\\/");
            if (pos == std::wstring::npos) return L".";
            return result.substr(0, pos);
        }

        bool ReadBoolIni(const wchar_t* section, const wchar_t* key, bool def, const std::wstring& ini)
        {
            wchar_t buf[32] = {};
            GetPrivateProfileStringW(section, key, def ? L"true" : L"false", buf, _countof(buf), ini.c_str());
            return _wcsicmp(buf, L"1") == 0 || _wcsicmp(buf, L"true") == 0 || _wcsicmp(buf, L"yes") == 0 || _wcsicmp(buf, L"on") == 0;
        }

        UINT ReadUIntIni(const wchar_t* section, const wchar_t* key, UINT def, const std::wstring& ini)
        {
            return static_cast<UINT>(GetPrivateProfileIntW(section, key, static_cast<INT>(def), ini.c_str()));
        }

        std::wstring ReadStringIni(const wchar_t* section, const wchar_t* key, const wchar_t* def, const std::wstring& ini)
        {
            wchar_t buf[MAX_PATH] = {};
            GetPrivateProfileStringW(section, key, def, buf, _countof(buf), ini.c_str());
            return buf;
        }

        void LoadSettings(HMODULE module)
        {
            std::wstring moduleDir = GetModuleDir(module);
            std::wstring exeDir = GetExeDir();
            std::wstring ini = JoinPath(exeDir, L"OneTextHook.ini");
            if (GetFileAttributesW(ini.c_str()) == INVALID_FILE_ATTRIBUTES)
            {
                ini = JoinPath(moduleDir, L"OneTextHook.ini");
            }

            g_settings.baseDir = exeDir;
            g_settings.translationJson = ReadStringIni(L"General", L"TranslationJson", g_settings.translationJson.c_str(), ini);
            g_settings.extractJson = ReadStringIni(L"General", L"ExtractJson", g_settings.extractJson.c_str(), ini);
            g_settings.logFile = ReadStringIni(L"General", L"LogFile", g_settings.logFile.c_str(), ini);
            g_settings.readCodePage = ReadUIntIni(L"General", L"ReadCodePage", g_settings.readCodePage, ini);
            g_settings.writeCodePage = ReadUIntIni(L"General", L"WriteCodePage", g_settings.writeCodePage, ini);
            g_settings.keepAscii = ReadBoolIni(L"General", L"KeepAscii", g_settings.keepAscii, ini);
            g_settings.enableExtract = ReadBoolIni(L"General", L"EnableExtract", g_settings.enableExtract, ini);
            g_settings.enableReplace = ReadBoolIni(L"General", L"EnableReplace", g_settings.enableReplace, ini);
            g_settings.verboseLog = ReadBoolIni(L"General", L"VerboseLog", g_settings.verboseLog, ini);
            g_settings.hookExtTextOutA = ReadBoolIni(L"Hooks", L"ExtTextOutA", g_settings.hookExtTextOutA, ini);
            g_settings.hookGetTextExtentExPointA = ReadBoolIni(L"Hooks", L"GetTextExtentExPointA", g_settings.hookGetTextExtentExPointA, ini);
            g_settings.hookGetTextExtentPoint32A = ReadBoolIni(L"Hooks", L"GetTextExtentPoint32A", g_settings.hookGetTextExtentPoint32A, ini);
            g_settings.hookGetTextExtentPointA = ReadBoolIni(L"Hooks", L"GetTextExtentPointA", g_settings.hookGetTextExtentPointA, ini);
            g_settings.hookMultiByteToWideChar = ReadBoolIni(L"Hooks", L"MultiByteToWideChar", g_settings.hookMultiByteToWideChar, ini);
        }

        void OpenLog()
        {
            std::wstring logPath = ResolvePath(g_settings.baseDir, g_settings.logFile);
            _wfopen_s(&g_log, logPath.c_str(), L"ab+");
            if (g_log)
            {
                fseek(g_log, 0, SEEK_END);
                if (ftell(g_log) <= 0)
                {
                    const unsigned char bom[3] = { 0xEF, 0xBB, 0xBF };
                    fwrite(bom, 1, sizeof(bom), g_log);
                }
            }
        }

        void SaveExtractLocked()
        {
            if (!g_settings.enableExtract || g_extractPath.empty()) return;
            g_store.SaveExtractAtomic(g_extractPath);
        }

        bool ProcessAnsiText(const char* text, int length, std::wstring& original, std::string& replacement, const wchar_t* apiName)
        {
            original = NormalizeText(AnsiToWide(text, length, g_settings.readCodePage));
            if (original.empty()) return false;

            std::wstring translation;
            bool hasTranslation = g_settings.enableReplace && g_store.FindTranslation(original, translation);
            if (!LooksLikeUsefulText(original, g_settings.keepAscii, hasTranslation))
            {
                return false;
            }

            {
                std::lock_guard<std::mutex> guard(g_lock);
                if (g_settings.enableExtract)
                {
                    g_store.RecordSeen(original);
                    SaveExtractLocked();
                }
            }

            if (g_settings.verboseLog)
            {
                Log(L"%s: %s%s%s", apiName, original.c_str(), hasTranslation ? L" -> " : L"", hasTranslation ? translation.c_str() : L"");
            }

            if (!hasTranslation || translation.empty() || translation == original)
            {
                return false;
            }

            replacement = WideToAnsi(translation, g_settings.writeCodePage);
            return !replacement.empty();
        }

        BOOL WINAPI HookExtTextOutA(HDC hdc, int x, int y, UINT options, const RECT* rect, LPCSTR text, UINT c, const INT* dx)
        {
            std::wstring original;
            std::string replacement;
            if (ProcessAnsiText(text, static_cast<int>(c), original, replacement, L"ExtTextOutA"))
            {
                const INT* useDx = (dx && replacement.size() != c) ? nullptr : dx;
                return RealExtTextOutA(hdc, x, y, options, rect, replacement.c_str(), static_cast<UINT>(replacement.size()), useDx);
            }
            return RealExtTextOutA(hdc, x, y, options, rect, text, c, dx);
        }

        BOOL WINAPI HookGetTextExtentExPointA(HDC hdc, LPCSTR text, int c, int maxExtent, LPINT fit, LPINT dx, LPSIZE size)
        {
            std::wstring original;
            std::string replacement;
            if (ProcessAnsiText(text, c, original, replacement, L"GetTextExtentExPointA"))
            {
                return RealGetTextExtentExPointA(hdc, replacement.c_str(), static_cast<int>(replacement.size()), maxExtent, fit, dx, size);
            }
            return RealGetTextExtentExPointA(hdc, text, c, maxExtent, fit, dx, size);
        }

        BOOL WINAPI HookGetTextExtentPoint32A(HDC hdc, LPCSTR text, int c, LPSIZE size)
        {
            std::wstring original;
            std::string replacement;
            if (ProcessAnsiText(text, c, original, replacement, L"GetTextExtentPoint32A"))
            {
                return RealGetTextExtentPoint32A(hdc, replacement.c_str(), static_cast<int>(replacement.size()), size);
            }
            return RealGetTextExtentPoint32A(hdc, text, c, size);
        }

        BOOL WINAPI HookGetTextExtentPointA(HDC hdc, LPCSTR text, int c, LPSIZE size)
        {
            std::wstring original;
            std::string replacement;
            if (ProcessAnsiText(text, c, original, replacement, L"GetTextExtentPointA"))
            {
                return RealGetTextExtentPointA(hdc, replacement.c_str(), static_cast<int>(replacement.size()), size);
            }
            return RealGetTextExtentPointA(hdc, text, c, size);
        }

        int WINAPI HookMultiByteToWideChar(UINT cp, DWORD flags, LPCCH mb, int cb, LPWSTR wide, int cch)
        {
            if (mb && (cp == g_settings.readCodePage || cp == 932))
            {
                std::wstring original = NormalizeText(AnsiToWide(mb, cb, cp));
                std::wstring dummy;
                if (!original.empty() && LooksLikeUsefulText(original, g_settings.keepAscii, g_store.FindTranslation(original, dummy)))
                {
                    std::lock_guard<std::mutex> guard(g_lock);
                    if (g_settings.enableExtract)
                    {
                        g_store.RecordSeen(original);
                        SaveExtractLocked();
                    }
                }
            }
            return RealMultiByteToWideChar(cp, flags, mb, cb, wide, cch);
        }

        bool AttachOne(PVOID* realFunc, PVOID hookFunc, const wchar_t* name)
        {
            if (!realFunc || !*realFunc) return false;
            LONG result = DetourAttach(realFunc, hookFunc);
            Log(L"Attach %s result=%ld", name, static_cast<long>(result));
            return result == NO_ERROR;
        }

        FARPROC GetProc(const wchar_t* dll, const char* name)
        {
            HMODULE mod = GetModuleHandleW(dll);
            if (!mod) mod = LoadLibraryW(dll);
            return mod ? GetProcAddress(mod, name) : nullptr;
        }

        bool InstallHooks()
        {
            RealExtTextOutA = reinterpret_cast<ExtTextOutAFn>(GetProc(L"gdi32.dll", "ExtTextOutA"));
            RealGetTextExtentExPointA = reinterpret_cast<GetTextExtentExPointAFn>(GetProc(L"gdi32.dll", "GetTextExtentExPointA"));
            RealGetTextExtentPoint32A = reinterpret_cast<GetTextExtentPoint32AFn>(GetProc(L"gdi32.dll", "GetTextExtentPoint32A"));
            RealGetTextExtentPointA = reinterpret_cast<GetTextExtentPointAFn>(GetProc(L"gdi32.dll", "GetTextExtentPointA"));
            RealMultiByteToWideChar = reinterpret_cast<MultiByteToWideCharFn>(GetProc(L"kernel32.dll", "MultiByteToWideChar"));

            DetourRestoreAfterWith();
            DetourTransactionBegin();
            DetourUpdateThread(GetCurrentThread());
            if (g_settings.hookExtTextOutA) AttachOne(reinterpret_cast<PVOID*>(&RealExtTextOutA), HookExtTextOutA, L"ExtTextOutA");
            if (g_settings.hookGetTextExtentExPointA) AttachOne(reinterpret_cast<PVOID*>(&RealGetTextExtentExPointA), HookGetTextExtentExPointA, L"GetTextExtentExPointA");
            if (g_settings.hookGetTextExtentPoint32A) AttachOne(reinterpret_cast<PVOID*>(&RealGetTextExtentPoint32A), HookGetTextExtentPoint32A, L"GetTextExtentPoint32A");
            if (g_settings.hookGetTextExtentPointA) AttachOne(reinterpret_cast<PVOID*>(&RealGetTextExtentPointA), HookGetTextExtentPointA, L"GetTextExtentPointA");
            if (g_settings.hookMultiByteToWideChar) AttachOne(reinterpret_cast<PVOID*>(&RealMultiByteToWideChar), HookMultiByteToWideChar, L"MultiByteToWideChar");
            LONG commit = DetourTransactionCommit();
            Log(L"DetourTransactionCommit result=%ld", static_cast<long>(commit));
            return commit == NO_ERROR;
        }

        void RemoveHooks()
        {
            if (!g_initialized) return;
            DetourTransactionBegin();
            DetourUpdateThread(GetCurrentThread());
            if (g_settings.hookExtTextOutA && RealExtTextOutA) DetourDetach(reinterpret_cast<PVOID*>(&RealExtTextOutA), HookExtTextOutA);
            if (g_settings.hookGetTextExtentExPointA && RealGetTextExtentExPointA) DetourDetach(reinterpret_cast<PVOID*>(&RealGetTextExtentExPointA), HookGetTextExtentExPointA);
            if (g_settings.hookGetTextExtentPoint32A && RealGetTextExtentPoint32A) DetourDetach(reinterpret_cast<PVOID*>(&RealGetTextExtentPoint32A), HookGetTextExtentPoint32A);
            if (g_settings.hookGetTextExtentPointA && RealGetTextExtentPointA) DetourDetach(reinterpret_cast<PVOID*>(&RealGetTextExtentPointA), HookGetTextExtentPointA);
            if (g_settings.hookMultiByteToWideChar && RealMultiByteToWideChar) DetourDetach(reinterpret_cast<PVOID*>(&RealMultiByteToWideChar), HookMultiByteToWideChar);
            DetourTransactionCommit();
        }
    }

    void Log(const wchar_t* format, ...)
    {
        if (!g_log) return;
        SYSTEMTIME st = {};
        GetLocalTime(&st);
        wchar_t msg[2048] = {};
        va_list args;
        va_start(args, format);
        _vsnwprintf_s(msg, _countof(msg), _TRUNCATE, format, args);
        va_end(args);
        wchar_t line[2300] = {};
        swprintf_s(line, L"[%02u:%02u:%02u.%03u] %s\r\n", st.wHour, st.wMinute, st.wSecond, st.wMilliseconds, msg);
        std::string utf8 = WideToUtf8(line);
        fwrite(utf8.data(), 1, utf8.size(), g_log);
        fflush(g_log);
    }

    std::wstring GetBaseDir()
    {
        return g_settings.baseDir;
    }

    std::wstring JoinPath(const std::wstring& dir, const std::wstring& name)
    {
        if (dir.empty()) return name;
        if (name.empty()) return dir;
        wchar_t last = dir.back();
        if (last == L'\\' || last == L'/') return dir + name;
        return dir + L"\\" + name;
    }

    std::wstring ResolvePath(const std::wstring& baseDir, const std::wstring& path)
    {
        if (path.size() >= 2 && path[1] == L':') return path;
        if (!path.empty() && (path[0] == L'\\' || path[0] == L'/')) return path;
        return JoinPath(baseDir, path);
    }

    bool Initialize(HMODULE module)
    {
        LoadSettings(module);
        OpenLog();
        Log(L"OneTextHook initializing");

        std::wstring transPath = ResolvePath(g_settings.baseDir, g_settings.translationJson);
        g_extractPath = ResolvePath(g_settings.baseDir, g_settings.extractJson);

        bool transLoaded = g_store.LoadTranslations(transPath);
        bool extractLoaded = g_store.LoadExtract(g_extractPath);
        Log(L"Load trans=%d count=%u path=%s", transLoaded ? 1 : 0, static_cast<unsigned>(g_store.TranslationCount()), transPath.c_str());
        Log(L"Load extract=%d count=%u path=%s", extractLoaded ? 1 : 0, static_cast<unsigned>(g_store.ExtractCount()), g_extractPath.c_str());

        bool ok = InstallHooks();
        g_initialized = ok;
        Log(L"OneTextHook initialized ok=%d", ok ? 1 : 0);
        return ok;
    }

    void Shutdown()
    {
        g_shuttingDown = true;
        RemoveHooks();
        {
            std::lock_guard<std::mutex> guard(g_lock);
            SaveExtractLocked();
        }
        Log(L"OneTextHook shutdown");
        if (g_log)
        {
            fclose(g_log);
            g_log = nullptr;
        }
    }
}
