#pragma once

#include <Windows.h>

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace OneTextHook
{
    struct Settings
    {
        std::wstring baseDir;
        std::wstring translationJson = L"trans1.json";
        std::wstring extractJson = L"trans1.json";
        std::wstring logFile = L"OneTextHook.log";
        UINT readCodePage = 932;
        UINT writeCodePage = 936;
        bool keepAscii = false;
        bool enableExtract = true;
        bool enableReplace = true;
        bool verboseLog = false;
        bool hookExtTextOutA = true;
        bool hookGetTextExtentExPointA = true;
        bool hookGetTextExtentPoint32A = true;
        bool hookGetTextExtentPointA = true;
        bool hookMultiByteToWideChar = false;
    };

    bool Initialize(HMODULE module);
    void Shutdown();

    void Log(const wchar_t* format, ...);
    std::wstring GetBaseDir();
    std::wstring JoinPath(const std::wstring& dir, const std::wstring& name);
    std::wstring ResolvePath(const std::wstring& baseDir, const std::wstring& path);
}
