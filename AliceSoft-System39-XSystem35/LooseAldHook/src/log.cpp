#include "log.h"

#include <strsafe.h>

namespace LooseAldHook
{
    static CRITICAL_SECTION g_logLock;
    static bool g_logLockReady = false;
    static wchar_t g_gameDir[MAX_PATH] = {};
    static wchar_t g_logPath[MAX_PATH] = {};

    static void AppendPath(wchar_t* out, size_t outCount, const wchar_t* base, const wchar_t* name)
    {
        StringCchCopyW(out, outCount, base ? base : L"");
        size_t len = wcslen(out);
        if (len && out[len - 1] != L'\\' && out[len - 1] != L'/')
        {
            StringCchCatW(out, outCount, L"\\");
        }
        StringCchCatW(out, outCount, name ? name : L"");
    }

    static void WriteLine(const wchar_t* line)
    {
        if (!g_logPath[0] || !line)
        {
            return;
        }

        HANDLE file = CreateFileW(g_logPath,
            FILE_APPEND_DATA,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            nullptr,
            OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
        if (file == INVALID_HANDLE_VALUE)
        {
            return;
        }

        int utf8Len = WideCharToMultiByte(CP_UTF8, 0, line, -1, nullptr, 0, nullptr, nullptr);
        if (utf8Len > 1)
        {
            char* utf8 = new char[utf8Len + 4];
            WideCharToMultiByte(CP_UTF8, 0, line, -1, utf8, utf8Len, nullptr, nullptr);
            DWORD written = 0;
            WriteFile(file, utf8, utf8Len - 1, &written, nullptr);
            WriteFile(file, "\r\n", 2, &written, nullptr);
            delete[] utf8;
        }
        CloseHandle(file);
    }

    void InitLogger()
    {
        if (!g_logLockReady)
        {
            InitializeCriticalSection(&g_logLock);
            g_logLockReady = true;
        }

        wchar_t exePath[MAX_PATH] = {};
        GetModuleFileNameW(nullptr, exePath, MAX_PATH);
        StringCchCopyW(g_gameDir, MAX_PATH, exePath);
        wchar_t* slash = wcsrchr(g_gameDir, L'\\');
        wchar_t* slash2 = wcsrchr(g_gameDir, L'/');
        if (!slash || (slash2 && slash2 > slash))
        {
            slash = slash2;
        }
        if (slash)
        {
            *slash = 0;
        }
        else
        {
            GetCurrentDirectoryW(MAX_PATH, g_gameDir);
        }

        AppendPath(g_logPath, MAX_PATH, g_gameDir, L"LooseAldHook.log");
        Log(L"INFO", L"Logger initialized: %s", g_logPath);
    }

    void ShutdownLogger()
    {
        Log(L"INFO", L"Logger shutdown");
        if (g_logLockReady)
        {
            DeleteCriticalSection(&g_logLock);
            g_logLockReady = false;
        }
    }

    void LogV(const wchar_t* level, const wchar_t* fmt, va_list args)
    {
        wchar_t message[2048] = {};
        StringCchVPrintfW(message, 2048, fmt, args);

        SYSTEMTIME st = {};
        GetLocalTime(&st);

        wchar_t line[2300] = {};
        StringCchPrintfW(line, 2300,
            L"[%02u:%02u:%02u.%03u][P%lu:T%lu][LooseALD][%s] %s",
            st.wHour, st.wMinute, st.wSecond, st.wMilliseconds,
            GetCurrentProcessId(), GetCurrentThreadId(),
            level ? level : L"INFO", message);

        OutputDebugStringW(line);
        OutputDebugStringW(L"\n");

        if (g_logLockReady)
        {
            EnterCriticalSection(&g_logLock);
            WriteLine(line);
            LeaveCriticalSection(&g_logLock);
        }
        else
        {
            WriteLine(line);
        }
    }

    void Log(const wchar_t* level, const wchar_t* fmt, ...)
    {
        va_list args;
        va_start(args, fmt);
        LogV(level, fmt, args);
        va_end(args);
    }

    const wchar_t* GetGameDir()
    {
        return g_gameDir;
    }
}
