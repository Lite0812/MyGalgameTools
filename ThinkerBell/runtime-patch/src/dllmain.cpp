#include <Windows.h>

#include "patch_hook.h"

extern "C" FARPROC g_winmm_exports[193] = {};

namespace {

HMODULE g_real_winmm = nullptr;

bool LoadRealWinMM()
{
    wchar_t path[MAX_PATH] = {};
    const UINT length = GetSystemDirectoryW(path, MAX_PATH);
    if (length == 0 || length + 10 >= MAX_PATH) {
        return false;
    }
    lstrcatW(path, L"\\winmm.dll");
    g_real_winmm = LoadLibraryW(path);
    if (!g_real_winmm) {
        return false;
    }
    for (WORD ordinal = 2; ordinal <= 194; ++ordinal) {
        g_winmm_exports[ordinal - 2] = GetProcAddress(
            g_real_winmm, reinterpret_cast<const char*>(static_cast<ULONG_PTR>(ordinal)));
        if (!g_winmm_exports[ordinal - 2]) {
            return false;
        }
    }
    return true;
}

}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, void*)
{
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(instance);
        if (!LoadRealWinMM()) {
            return FALSE;
        }
        HANDLE thread = CreateThread(nullptr, 0, InitializePatchHook, instance, 0, nullptr);
        if (thread) {
            CloseHandle(thread);
        }
    }
    return TRUE;
}
