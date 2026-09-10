#include "config.h"
#include "log.h"

#include <cwctype>
#include <cstdlib>
#include <strsafe.h>

namespace LooseAldHook
{
    static Config g_config;

    static std::wstring Trim(const std::wstring& s)
    {
        size_t first = 0;
        while (first < s.size() && iswspace(s[first]))
        {
            ++first;
        }
        size_t last = s.size();
        while (last > first && iswspace(s[last - 1]))
        {
            --last;
        }
        return s.substr(first, last - first);
    }

    static bool ParseBool(const wchar_t* value, bool fallback)
    {
        if (!value || !*value)
        {
            return fallback;
        }
        if (_wcsicmp(value, L"1") == 0 || _wcsicmp(value, L"true") == 0 || _wcsicmp(value, L"yes") == 0 || _wcsicmp(value, L"on") == 0)
        {
            return true;
        }
        if (_wcsicmp(value, L"0") == 0 || _wcsicmp(value, L"false") == 0 || _wcsicmp(value, L"no") == 0 || _wcsicmp(value, L"off") == 0)
        {
            return false;
        }
        return fallback;
    }

    static DWORD ParseUInt(const wchar_t* value, DWORD fallback)
    {
        if (!value || !*value)
        {
            return fallback;
        }
        wchar_t* end = nullptr;
        unsigned long parsed = wcstoul(value, &end, 0);
        if (end == value)
        {
            return fallback;
        }
        return static_cast<DWORD>(parsed);
    }

    static ResolveMode ParseResolveMode(const wchar_t* value, ResolveMode fallback)
    {
        if (!value || !*value)
        {
            return fallback;
        }
        if (_wcsicmp(value, L"Auto") == 0)
        {
            return ResolveMode::Auto;
        }
        if (_wcsicmp(value, L"Heuristic") == 0 || _wcsicmp(value, L"Pattern") == 0)
        {
            return ResolveMode::Heuristic;
        }
        if (_wcsicmp(value, L"FixedRva") == 0 || _wcsicmp(value, L"Fixed") == 0)
        {
            return ResolveMode::FixedRva;
        }
        if (_wcsicmp(value, L"Disabled") == 0 || _wcsicmp(value, L"Off") == 0)
        {
            return ResolveMode::Disabled;
        }
        return fallback;
    }

    static const wchar_t* ResolveModeName(ResolveMode mode)
    {
        switch (mode)
        {
        case ResolveMode::Auto: return L"Auto";
        case ResolveMode::Heuristic: return L"Heuristic";
        case ResolveMode::FixedRva: return L"FixedRva";
        case ResolveMode::Disabled: return L"Disabled";
        default: return L"Unknown";
        }
    }

    static std::wstring BuildIniPath()
    {
        wchar_t path[MAX_PATH] = {};
        StringCchCopyW(path, MAX_PATH, GetGameDir());
        size_t len = wcslen(path);
        if (len && path[len - 1] != L'\\' && path[len - 1] != L'/')
        {
            StringCchCatW(path, MAX_PATH, L"\\");
        }
        StringCchCatW(path, MAX_PATH, L"version.ini");
        return path;
    }

    void LoadConfig()
    {
        g_config = Config{};
        const std::wstring iniPath = BuildIniPath();

        wchar_t buffer[512] = {};
        GetPrivateProfileStringW(L"LooseALD", L"Enable", L"true", buffer, 512, iniPath.c_str());
        g_config.enable = ParseBool(buffer, true);

        GetPrivateProfileStringW(L"LooseALD", L"PatchDir", L"patch", buffer, 512, iniPath.c_str());
        g_config.patchDir = Trim(buffer);
        if (g_config.patchDir.empty())
        {
            g_config.patchDir = L"patch";
        }

        GetPrivateProfileStringW(L"LooseALD", L"EnableLog", L"true", buffer, 512, iniPath.c_str());
        g_config.enableLog = ParseBool(buffer, true);

        GetPrivateProfileStringW(L"LooseALD", L"HookExistsCheck", L"false", buffer, 512, iniPath.c_str());
        g_config.hookExistsCheck = ParseBool(buffer, false);

        const UINT maxFileSize = GetPrivateProfileIntW(L"LooseALD", L"MaxFileSize", 268435456, iniPath.c_str());
        g_config.maxFileSize = maxFileSize ? maxFileSize : 268435456;

        GetPrivateProfileStringW(L"LooseALD", L"ResolveMode", L"Auto", buffer, 512, iniPath.c_str());
        g_config.resolveMode = ParseResolveMode(buffer, ResolveMode::Auto);

        GetPrivateProfileStringW(L"LooseALD", L"StrictHeuristic", L"false", buffer, 512, iniPath.c_str());
        g_config.strictHeuristic = ParseBool(buffer, false);

        GetPrivateProfileStringW(L"LooseALD", L"FixedGetSizeRva", L"0x31E0", buffer, 512, iniPath.c_str());
        g_config.fixedGetSizeRva = ParseUInt(buffer, 0x31E0);

        GetPrivateProfileStringW(L"LooseALD", L"FixedReadRva", L"0x2E40", buffer, 512, iniPath.c_str());
        g_config.fixedReadRva = ParseUInt(buffer, 0x2E40);

        GetPrivateProfileStringW(L"LooseALD", L"FixedExistsRva", L"0x3380", buffer, 512, iniPath.c_str());
        g_config.fixedExistsRva = ParseUInt(buffer, 0x3380);

        GetPrivateProfileStringW(L"LooseALD", L"FixedResizeRva", L"0x57C20", buffer, 512, iniPath.c_str());
        g_config.fixedResizeRva = ParseUInt(buffer, 0x57C20);

        GetPrivateProfileStringW(L"LooseALD", L"FixedX35OpenRva", L"0x4E00", buffer, 512, iniPath.c_str());
        g_config.fixedX35OpenRva = ParseUInt(buffer, 0x4E00);

        GetPrivateProfileStringW(L"LooseALD", L"FixedX35ReleaseRva", L"0x4ED0", buffer, 512, iniPath.c_str());
        g_config.fixedX35ReleaseRva = ParseUInt(buffer, 0x4ED0);

        Log(L"INFO", L"Config loaded: ini=%s enable=%d patchDir=%s enableLog=%d hookExists=%d maxFileSize=%u resolveMode=%s strictHeuristic=%d fixedRvas=%04X/%04X/%04X/%04X fixedX35=%04X/%04X",
            iniPath.c_str(),
            g_config.enable ? 1 : 0,
            g_config.patchDir.c_str(),
            g_config.enableLog ? 1 : 0,
            g_config.hookExistsCheck ? 1 : 0,
            g_config.maxFileSize,
            ResolveModeName(g_config.resolveMode),
            g_config.strictHeuristic ? 1 : 0,
            g_config.fixedGetSizeRva,
            g_config.fixedReadRva,
            g_config.fixedExistsRva,
            g_config.fixedResizeRva,
            g_config.fixedX35OpenRva,
            g_config.fixedX35ReleaseRva);
    }

    const Config& GetConfig()
    {
        return g_config;
    }
}
