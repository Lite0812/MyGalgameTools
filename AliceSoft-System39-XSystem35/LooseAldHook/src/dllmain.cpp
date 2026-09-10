#include "config.h"
#include "log.h"
#include "loose_ald_hook.h"
#include "version_proxy.h"

#include <Windows.h>

namespace
{
    DWORD WINAPI WorkerThread(LPVOID)
    {
        LooseAldHook::InitVersionProxy();
        LooseAldHook::LoadConfig();
        LooseAldHook::InstallLooseAldHooks();
        return 0;
    }
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID)
{
    switch (reason)
    {
    case DLL_PROCESS_ATTACH:
    {
        DisableThreadLibraryCalls(module);
        LooseAldHook::InitLogger();
        HANDLE thread = CreateThread(nullptr, 0, WorkerThread, nullptr, 0, nullptr);
        if (thread)
        {
            CloseHandle(thread);
        }
        else
        {
            LooseAldHook::Log(L"ERROR", L"CreateThread failed: %lu", GetLastError());
        }
        break;
    }
    case DLL_PROCESS_DETACH:
        LooseAldHook::ShutdownVersionProxy();
        LooseAldHook::ShutdownLogger();
        break;
    }
    return TRUE;
}
