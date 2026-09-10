#include <Windows.h>
#include <cstdio>

namespace
{
    static INIT_ONCE g_initOnce = INIT_ONCE_STATIC_INIT;
    static HMODULE g_realVersion = nullptr;

    using FnGetFileVersionInfoA = BOOL(WINAPI*)(LPCSTR, DWORD, DWORD, LPVOID);
    using FnGetFileVersionInfoByHandle = BOOL(WINAPI*)(DWORD, DWORD, DWORD, LPVOID);
    using FnGetFileVersionInfoExA = BOOL(WINAPI*)(DWORD, LPCSTR, DWORD, DWORD, LPVOID);
    using FnGetFileVersionInfoExW = BOOL(WINAPI*)(DWORD, LPCWSTR, DWORD, DWORD, LPVOID);
    using FnGetFileVersionInfoSizeA = DWORD(WINAPI*)(LPCSTR, LPDWORD);
    using FnGetFileVersionInfoSizeExA = DWORD(WINAPI*)(DWORD, LPCSTR, LPDWORD);
    using FnGetFileVersionInfoSizeExW = DWORD(WINAPI*)(DWORD, LPCWSTR, LPDWORD);
    using FnGetFileVersionInfoSizeW = DWORD(WINAPI*)(LPCWSTR, LPDWORD);
    using FnGetFileVersionInfoW = BOOL(WINAPI*)(LPCWSTR, DWORD, DWORD, LPVOID);
    using FnVerFindFileA = DWORD(WINAPI*)(DWORD, LPCSTR, LPCSTR, LPCSTR, LPSTR, PUINT, LPSTR, PUINT);
    using FnVerFindFileW = DWORD(WINAPI*)(DWORD, LPCWSTR, LPCWSTR, LPCWSTR, LPWSTR, PUINT, LPWSTR, PUINT);
    using FnVerInstallFileA = DWORD(WINAPI*)(DWORD, LPCSTR, LPCSTR, LPCSTR, LPCSTR, LPCSTR, LPSTR, PUINT);
    using FnVerInstallFileW = DWORD(WINAPI*)(DWORD, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, LPWSTR, PUINT);
    using FnVerLanguageNameA = DWORD(WINAPI*)(DWORD, LPSTR, DWORD);
    using FnVerLanguageNameW = DWORD(WINAPI*)(DWORD, LPWSTR, DWORD);
    using FnVerQueryValueA = BOOL(WINAPI*)(LPCVOID, LPCSTR, LPVOID*, PUINT);
    using FnVerQueryValueW = BOOL(WINAPI*)(LPCVOID, LPCWSTR, LPVOID*, PUINT);

    static FnGetFileVersionInfoA pGetFileVersionInfoA = nullptr;
    static FnGetFileVersionInfoByHandle pGetFileVersionInfoByHandle = nullptr;
    static FnGetFileVersionInfoExA pGetFileVersionInfoExA = nullptr;
    static FnGetFileVersionInfoExW pGetFileVersionInfoExW = nullptr;
    static FnGetFileVersionInfoSizeA pGetFileVersionInfoSizeA = nullptr;
    static FnGetFileVersionInfoSizeExA pGetFileVersionInfoSizeExA = nullptr;
    static FnGetFileVersionInfoSizeExW pGetFileVersionInfoSizeExW = nullptr;
    static FnGetFileVersionInfoSizeW pGetFileVersionInfoSizeW = nullptr;
    static FnGetFileVersionInfoW pGetFileVersionInfoW = nullptr;
    static FnVerFindFileA pVerFindFileA = nullptr;
    static FnVerFindFileW pVerFindFileW = nullptr;
    static FnVerInstallFileA pVerInstallFileA = nullptr;
    static FnVerInstallFileW pVerInstallFileW = nullptr;
    static FnVerLanguageNameA pVerLanguageNameA = nullptr;
    static FnVerLanguageNameW pVerLanguageNameW = nullptr;
    static FnVerQueryValueA pVerQueryValueA = nullptr;
    static FnVerQueryValueW pVerQueryValueW = nullptr;

    static FARPROC LoadExport(const char* name)
    {
        return g_realVersion ? GetProcAddress(g_realVersion, name) : nullptr;
    }

    static BOOL CALLBACK InitRealVersion(PINIT_ONCE, PVOID, PVOID*)
    {
        wchar_t systemDir[MAX_PATH] = {};
        GetSystemDirectoryW(systemDir, MAX_PATH);
        wchar_t versionPath[MAX_PATH] = {};
        swprintf_s(versionPath, L"%s\\version.dll", systemDir);
        g_realVersion = LoadLibraryW(versionPath);
        if (!g_realVersion)
        {
            return FALSE;
        }

        pGetFileVersionInfoA = reinterpret_cast<FnGetFileVersionInfoA>(LoadExport("GetFileVersionInfoA"));
        pGetFileVersionInfoByHandle = reinterpret_cast<FnGetFileVersionInfoByHandle>(LoadExport("GetFileVersionInfoByHandle"));
        pGetFileVersionInfoExA = reinterpret_cast<FnGetFileVersionInfoExA>(LoadExport("GetFileVersionInfoExA"));
        pGetFileVersionInfoExW = reinterpret_cast<FnGetFileVersionInfoExW>(LoadExport("GetFileVersionInfoExW"));
        pGetFileVersionInfoSizeA = reinterpret_cast<FnGetFileVersionInfoSizeA>(LoadExport("GetFileVersionInfoSizeA"));
        pGetFileVersionInfoSizeExA = reinterpret_cast<FnGetFileVersionInfoSizeExA>(LoadExport("GetFileVersionInfoSizeExA"));
        pGetFileVersionInfoSizeExW = reinterpret_cast<FnGetFileVersionInfoSizeExW>(LoadExport("GetFileVersionInfoSizeExW"));
        pGetFileVersionInfoSizeW = reinterpret_cast<FnGetFileVersionInfoSizeW>(LoadExport("GetFileVersionInfoSizeW"));
        pGetFileVersionInfoW = reinterpret_cast<FnGetFileVersionInfoW>(LoadExport("GetFileVersionInfoW"));
        pVerFindFileA = reinterpret_cast<FnVerFindFileA>(LoadExport("VerFindFileA"));
        pVerFindFileW = reinterpret_cast<FnVerFindFileW>(LoadExport("VerFindFileW"));
        pVerInstallFileA = reinterpret_cast<FnVerInstallFileA>(LoadExport("VerInstallFileA"));
        pVerInstallFileW = reinterpret_cast<FnVerInstallFileW>(LoadExport("VerInstallFileW"));
        pVerLanguageNameA = reinterpret_cast<FnVerLanguageNameA>(LoadExport("VerLanguageNameA"));
        pVerLanguageNameW = reinterpret_cast<FnVerLanguageNameW>(LoadExport("VerLanguageNameW"));
        pVerQueryValueA = reinterpret_cast<FnVerQueryValueA>(LoadExport("VerQueryValueA"));
        pVerQueryValueW = reinterpret_cast<FnVerQueryValueW>(LoadExport("VerQueryValueW"));
        return TRUE;
    }

    static bool EnsureRealVersion()
    {
        return InitOnceExecuteOnce(&g_initOnce, InitRealVersion, nullptr, nullptr) && g_realVersion != nullptr;
    }
}

extern "C" __declspec(dllexport) VOID CALLBACK DetourFinishHelperProcess(HWND, HINSTANCE, LPSTR, int)
{
}

extern "C" BOOL WINAPI ProxyGetFileVersionInfoA(LPCSTR a, DWORD b, DWORD c, LPVOID d)
{
    return EnsureRealVersion() && pGetFileVersionInfoA ? pGetFileVersionInfoA(a, b, c, d) : FALSE;
}

extern "C" BOOL WINAPI ProxyGetFileVersionInfoByHandle(DWORD a, DWORD b, DWORD c, LPVOID d)
{
    return EnsureRealVersion() && pGetFileVersionInfoByHandle ? pGetFileVersionInfoByHandle(a, b, c, d) : FALSE;
}

extern "C" BOOL WINAPI ProxyGetFileVersionInfoExA(DWORD a, LPCSTR b, DWORD c, DWORD d, LPVOID e)
{
    return EnsureRealVersion() && pGetFileVersionInfoExA ? pGetFileVersionInfoExA(a, b, c, d, e) : FALSE;
}

extern "C" BOOL WINAPI ProxyGetFileVersionInfoExW(DWORD a, LPCWSTR b, DWORD c, DWORD d, LPVOID e)
{
    return EnsureRealVersion() && pGetFileVersionInfoExW ? pGetFileVersionInfoExW(a, b, c, d, e) : FALSE;
}

extern "C" DWORD WINAPI ProxyGetFileVersionInfoSizeA(LPCSTR a, LPDWORD b)
{
    return EnsureRealVersion() && pGetFileVersionInfoSizeA ? pGetFileVersionInfoSizeA(a, b) : 0;
}

extern "C" DWORD WINAPI ProxyGetFileVersionInfoSizeExA(DWORD a, LPCSTR b, LPDWORD c)
{
    return EnsureRealVersion() && pGetFileVersionInfoSizeExA ? pGetFileVersionInfoSizeExA(a, b, c) : 0;
}

extern "C" DWORD WINAPI ProxyGetFileVersionInfoSizeExW(DWORD a, LPCWSTR b, LPDWORD c)
{
    return EnsureRealVersion() && pGetFileVersionInfoSizeExW ? pGetFileVersionInfoSizeExW(a, b, c) : 0;
}

extern "C" DWORD WINAPI ProxyGetFileVersionInfoSizeW(LPCWSTR a, LPDWORD b)
{
    return EnsureRealVersion() && pGetFileVersionInfoSizeW ? pGetFileVersionInfoSizeW(a, b) : 0;
}

extern "C" BOOL WINAPI ProxyGetFileVersionInfoW(LPCWSTR a, DWORD b, DWORD c, LPVOID d)
{
    return EnsureRealVersion() && pGetFileVersionInfoW ? pGetFileVersionInfoW(a, b, c, d) : FALSE;
}

extern "C" DWORD WINAPI ProxyVerFindFileA(DWORD a, LPCSTR b, LPCSTR c, LPCSTR d, LPSTR e, PUINT f, LPSTR g, PUINT h)
{
    return EnsureRealVersion() && pVerFindFileA ? pVerFindFileA(a, b, c, d, e, f, g, h) : 0;
}

extern "C" DWORD WINAPI ProxyVerFindFileW(DWORD a, LPCWSTR b, LPCWSTR c, LPCWSTR d, LPWSTR e, PUINT f, LPWSTR g, PUINT h)
{
    return EnsureRealVersion() && pVerFindFileW ? pVerFindFileW(a, b, c, d, e, f, g, h) : 0;
}

extern "C" DWORD WINAPI ProxyVerInstallFileA(DWORD a, LPCSTR b, LPCSTR c, LPCSTR d, LPCSTR e, LPCSTR f, LPSTR g, PUINT h)
{
    return EnsureRealVersion() && pVerInstallFileA ? pVerInstallFileA(a, b, c, d, e, f, g, h) : 0;
}

extern "C" DWORD WINAPI ProxyVerInstallFileW(DWORD a, LPCWSTR b, LPCWSTR c, LPCWSTR d, LPCWSTR e, LPCWSTR f, LPWSTR g, PUINT h)
{
    return EnsureRealVersion() && pVerInstallFileW ? pVerInstallFileW(a, b, c, d, e, f, g, h) : 0;
}

extern "C" DWORD WINAPI ProxyVerLanguageNameA(DWORD a, LPSTR b, DWORD c)
{
    return EnsureRealVersion() && pVerLanguageNameA ? pVerLanguageNameA(a, b, c) : 0;
}

extern "C" DWORD WINAPI ProxyVerLanguageNameW(DWORD a, LPWSTR b, DWORD c)
{
    return EnsureRealVersion() && pVerLanguageNameW ? pVerLanguageNameW(a, b, c) : 0;
}

extern "C" BOOL WINAPI ProxyVerQueryValueA(LPCVOID a, LPCSTR b, LPVOID* c, PUINT d)
{
    return EnsureRealVersion() && pVerQueryValueA ? pVerQueryValueA(a, b, c, d) : FALSE;
}

extern "C" BOOL WINAPI ProxyVerQueryValueW(LPCVOID a, LPCWSTR b, LPVOID* c, PUINT d)
{
    return EnsureRealVersion() && pVerQueryValueW ? pVerQueryValueW(a, b, c, d) : FALSE;
}
