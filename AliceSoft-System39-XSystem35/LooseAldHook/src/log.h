#pragma once

#include <Windows.h>
#include <cstdarg>

namespace LooseAldHook
{
    void InitLogger();
    void ShutdownLogger();
    void Log(const wchar_t* level, const wchar_t* fmt, ...);
    void LogV(const wchar_t* level, const wchar_t* fmt, va_list args);
    const wchar_t* GetGameDir();
}
