#include "version_proxy.h"
#include "log.h"

#include <Windows.h>
#include <strsafe.h>

namespace LooseAldHook
{
    static HMODULE g_version = nullptr;
    static INIT_ONCE g_initOnce = INIT_ONCE_STATIC_INIT;

    template <typename T>
    static T Resolve(const char* name)
    {
        if (!g_version)
        {
            return nullptr;
        }
        return reinterpret_cast<T>(GetProcAddress(g_version, name));
    }

    static BOOL CALLBACK InitVersionOnce(PINIT_ONCE, PVOID, PVOID*)
    {
        wchar_t systemDir[MAX_PATH] = {};
        if (!GetSystemDirectoryW(systemDir, MAX_PATH))
        {
            Log(L"ERROR", L"GetSystemDirectoryW failed: %lu", GetLastError());
            return TRUE;
        }

        wchar_t path[MAX_PATH] = {};
        StringCchCopyW(path, MAX_PATH, systemDir);
        StringCchCatW(path, MAX_PATH, L"\\version.dll");

        g_version = LoadLibraryW(path);
        if (!g_version)
        {
            Log(L"ERROR", L"LoadLibraryW(%s) failed: %lu", path, GetLastError());
        }
        else
        {
            Log(L"INFO", L"Loaded system version.dll: %s", path);
        }
        return TRUE;
    }

    bool InitVersionProxy()
    {
        InitOnceExecuteOnce(&g_initOnce, InitVersionOnce, nullptr, nullptr);
        return g_version != nullptr;
    }

    void ShutdownVersionProxy()
    {
        if (g_version)
        {
            FreeLibrary(g_version);
            g_version = nullptr;
        }
    }
}

static bool EnsureVersion()
{
    return LooseAldHook::InitVersionProxy();
}

extern "C" BOOL WINAPI GetFileVersionInfoA(LPCSTR lptstrFilename, DWORD dwHandle, DWORD dwLen, LPVOID lpData)
{
    using Fn = BOOL (WINAPI*)(LPCSTR, DWORD, DWORD, LPVOID);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return FALSE;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("GetFileVersionInfoA");
    return fn ? fn(lptstrFilename, dwHandle, dwLen, lpData) : FALSE;
}

extern "C" BOOL WINAPI GetFileVersionInfoW(LPCWSTR lptstrFilename, DWORD dwHandle, DWORD dwLen, LPVOID lpData)
{
    using Fn = BOOL (WINAPI*)(LPCWSTR, DWORD, DWORD, LPVOID);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return FALSE;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("GetFileVersionInfoW");
    return fn ? fn(lptstrFilename, dwHandle, dwLen, lpData) : FALSE;
}

extern "C" DWORD WINAPI GetFileVersionInfoSizeA(LPCSTR lptstrFilename, LPDWORD lpdwHandle)
{
    using Fn = DWORD (WINAPI*)(LPCSTR, LPDWORD);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("GetFileVersionInfoSizeA");
    return fn ? fn(lptstrFilename, lpdwHandle) : 0;
}

extern "C" DWORD WINAPI GetFileVersionInfoSizeW(LPCWSTR lptstrFilename, LPDWORD lpdwHandle)
{
    using Fn = DWORD (WINAPI*)(LPCWSTR, LPDWORD);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("GetFileVersionInfoSizeW");
    return fn ? fn(lptstrFilename, lpdwHandle) : 0;
}

extern "C" BOOL WINAPI VerQueryValueA(LPCVOID pBlock, LPCSTR lpSubBlock, LPVOID* lplpBuffer, PUINT puLen)
{
    using Fn = BOOL (WINAPI*)(LPCVOID, LPCSTR, LPVOID*, PUINT);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return FALSE;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("VerQueryValueA");
    return fn ? fn(pBlock, lpSubBlock, lplpBuffer, puLen) : FALSE;
}

extern "C" BOOL WINAPI VerQueryValueW(LPCVOID pBlock, LPCWSTR lpSubBlock, LPVOID* lplpBuffer, PUINT puLen)
{
    using Fn = BOOL (WINAPI*)(LPCVOID, LPCWSTR, LPVOID*, PUINT);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return FALSE;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("VerQueryValueW");
    return fn ? fn(pBlock, lpSubBlock, lplpBuffer, puLen) : FALSE;
}

extern "C" BOOL WINAPI GetFileVersionInfoExA(DWORD dwFlags, LPCSTR lpwstrFilename, DWORD dwHandle, DWORD dwLen, LPVOID lpData)
{
    using Fn = BOOL (WINAPI*)(DWORD, LPCSTR, DWORD, DWORD, LPVOID);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return FALSE;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("GetFileVersionInfoExA");
    return fn ? fn(dwFlags, lpwstrFilename, dwHandle, dwLen, lpData) : FALSE;
}

extern "C" BOOL WINAPI GetFileVersionInfoExW(DWORD dwFlags, LPCWSTR lpwstrFilename, DWORD dwHandle, DWORD dwLen, LPVOID lpData)
{
    using Fn = BOOL (WINAPI*)(DWORD, LPCWSTR, DWORD, DWORD, LPVOID);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return FALSE;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("GetFileVersionInfoExW");
    return fn ? fn(dwFlags, lpwstrFilename, dwHandle, dwLen, lpData) : FALSE;
}

extern "C" DWORD WINAPI GetFileVersionInfoSizeExA(DWORD dwFlags, LPCSTR lpwstrFilename, LPDWORD lpdwHandle)
{
    using Fn = DWORD (WINAPI*)(DWORD, LPCSTR, LPDWORD);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("GetFileVersionInfoSizeExA");
    return fn ? fn(dwFlags, lpwstrFilename, lpdwHandle) : 0;
}

extern "C" DWORD WINAPI GetFileVersionInfoSizeExW(DWORD dwFlags, LPCWSTR lpwstrFilename, LPDWORD lpdwHandle)
{
    using Fn = DWORD (WINAPI*)(DWORD, LPCWSTR, LPDWORD);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("GetFileVersionInfoSizeExW");
    return fn ? fn(dwFlags, lpwstrFilename, lpdwHandle) : 0;
}

extern "C" DWORD WINAPI VerLanguageNameA(DWORD wLang, LPSTR szLang, DWORD cchLang)
{
    using Fn = DWORD (WINAPI*)(DWORD, LPSTR, DWORD);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("VerLanguageNameA");
    return fn ? fn(wLang, szLang, cchLang) : 0;
}

extern "C" DWORD WINAPI VerLanguageNameW(DWORD wLang, LPWSTR szLang, DWORD cchLang)
{
    using Fn = DWORD (WINAPI*)(DWORD, LPWSTR, DWORD);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("VerLanguageNameW");
    return fn ? fn(wLang, szLang, cchLang) : 0;
}

extern "C" DWORD WINAPI VerFindFileA(DWORD uFlags, LPCSTR szFileName, LPCSTR szWinDir, LPCSTR szAppDir, LPSTR szCurDir, PUINT puCurDirLen, LPSTR szDestDir, PUINT puDestDirLen)
{
    using Fn = DWORD (WINAPI*)(DWORD, LPCSTR, LPCSTR, LPCSTR, LPSTR, PUINT, LPSTR, PUINT);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("VerFindFileA");
    return fn ? fn(uFlags, szFileName, szWinDir, szAppDir, szCurDir, puCurDirLen, szDestDir, puDestDirLen) : 0;
}

extern "C" DWORD WINAPI VerFindFileW(DWORD uFlags, LPCWSTR szFileName, LPCWSTR szWinDir, LPCWSTR szAppDir, LPWSTR szCurDir, PUINT puCurDirLen, LPWSTR szDestDir, PUINT puDestDirLen)
{
    using Fn = DWORD (WINAPI*)(DWORD, LPCWSTR, LPCWSTR, LPCWSTR, LPWSTR, PUINT, LPWSTR, PUINT);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("VerFindFileW");
    return fn ? fn(uFlags, szFileName, szWinDir, szAppDir, szCurDir, puCurDirLen, szDestDir, puDestDirLen) : 0;
}

extern "C" DWORD WINAPI VerInstallFileA(DWORD uFlags, LPCSTR szSrcFileName, LPCSTR szDestFileName, LPCSTR szSrcDir, LPCSTR szDestDir, LPCSTR szCurDir, LPSTR szTmpFile, PUINT puTmpFileLen)
{
    using Fn = DWORD (WINAPI*)(DWORD, LPCSTR, LPCSTR, LPCSTR, LPCSTR, LPCSTR, LPSTR, PUINT);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("VerInstallFileA");
    return fn ? fn(uFlags, szSrcFileName, szDestFileName, szSrcDir, szDestDir, szCurDir, szTmpFile, puTmpFileLen) : 0;
}

extern "C" DWORD WINAPI VerInstallFileW(DWORD uFlags, LPCWSTR szSrcFileName, LPCWSTR szDestFileName, LPCWSTR szSrcDir, LPCWSTR szDestDir, LPCWSTR szCurDir, LPWSTR szTmpFile, PUINT puTmpFileLen)
{
    using Fn = DWORD (WINAPI*)(DWORD, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, LPWSTR, PUINT);
    static Fn fn = nullptr;
    if (!EnsureVersion()) return 0;
    if (!fn) fn = LooseAldHook::Resolve<Fn>("VerInstallFileW");
    return fn ? fn(uFlags, szSrcFileName, szDestFileName, szSrcDir, szDestDir, szCurDir, szTmpFile, puTmpFileLen) : 0;
}
