#include <Windows.h>

#include <cstdio>

int main()
{
    HMODULE module = LoadLibraryW(L"winmm.dll");
    if (!module) {
        std::printf("LoadLibrary failed: %lu\n", GetLastError());
        return 1;
    }
    FARPROC ordinal1 = GetProcAddress(module, reinterpret_cast<const char*>(1));
    FARPROC ordinal2 = GetProcAddress(module, reinterpret_cast<const char*>(2));
    FARPROC time_get_time = GetProcAddress(module, "timeGetTime");
    if (!ordinal1 || !ordinal2 || !time_get_time) {
        std::printf("GetProcAddress failed: %lu\n", GetLastError());
        return 2;
    }
    const DWORD value = reinterpret_cast<DWORD(WINAPI*)()>(time_get_time)();
    std::printf("ordinal1=%p ordinal2=%p timeGetTime=%lu\n", ordinal1, ordinal2, value);
    return 0;
}
