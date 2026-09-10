#pragma once

#include <Windows.h>
#include <string>

namespace LooseAldHook
{
    enum class ResolveMode
    {
        Auto,
        Heuristic,
        FixedRva,
        Disabled
    };

    struct Config
    {
        bool enable = true;
        std::wstring patchDir = L"patch";
        bool enableLog = true;
        bool hookExistsCheck = false;
        DWORD maxFileSize = 268435456;
        ResolveMode resolveMode = ResolveMode::Auto;
        bool strictHeuristic = false;
        DWORD fixedGetSizeRva = 0x31E0;
        DWORD fixedReadRva = 0x2E40;
        DWORD fixedExistsRva = 0x3380;
        DWORD fixedResizeRva = 0x57C20;
        DWORD fixedX35OpenRva = 0x4E00;
        DWORD fixedX35ReleaseRva = 0x4ED0;
    };

    const Config& GetConfig();
    void LoadConfig();
}
