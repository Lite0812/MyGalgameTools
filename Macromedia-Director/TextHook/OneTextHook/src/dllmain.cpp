#include <Windows.h>

#include "TextHook.h"

namespace
{
    HMODULE g_module = nullptr;
    HANDLE g_thread = nullptr;

    DWORD WINAPI InitThread(LPVOID)
    {
        OneTextHook::Initialize(g_module);
        return 0;
    }
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH)
    {
        g_module = instance;
        DisableThreadLibraryCalls(instance);
        g_thread = CreateThread(nullptr, 0, InitThread, nullptr, 0, nullptr);
        if (g_thread)
        {
            CloseHandle(g_thread);
            g_thread = nullptr;
        }
    }
    else if (reason == DLL_PROCESS_DETACH)
    {
        OneTextHook::Shutdown();
    }
    return TRUE;
}
