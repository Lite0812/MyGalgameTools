#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>

#include <cstdio>

template <typename T>
T LoadExport(HMODULE module, const char *name) {
  FARPROC address = GetProcAddress(module, name);
  if (address == nullptr) {
    std::fprintf(stderr, "missing export: %s\n", name);
    ExitProcess(2);
  }
  return reinterpret_cast<T>(address);
}

int main(int argc, char **argv) {
  if (argc != 2) {
    std::fprintf(stderr, "usage: winmm_smoke.exe path-to-winmm.dll\n");
    return 2;
  }
  HMODULE proxy = LoadLibraryA(argv[1]);
  if (proxy == nullptr) {
    std::fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError());
    return 2;
  }

  using TimeGetTimeFn = DWORD(WINAPI *)();
  using PeriodFn = MMRESULT(WINAPI *)(UINT);
  using WaveOutGetNumDevsFn = UINT(WINAPI *)();
  using MmSystemGetVersionFn = UINT(WINAPI *)();
  using FourCcFn = FOURCC(WINAPI *)(LPCSTR, UINT);

  DWORD ticks = LoadExport<TimeGetTimeFn>(proxy, "timeGetTime")();
  MMRESULT begin = LoadExport<PeriodFn>(proxy, "timeBeginPeriod")(1);
  MMRESULT end = LoadExport<PeriodFn>(proxy, "timeEndPeriod")(1);
  UINT devices =
      LoadExport<WaveOutGetNumDevsFn>(proxy, "waveOutGetNumDevs")();
  UINT version =
      LoadExport<MmSystemGetVersionFn>(proxy, "mmsystemGetVersion")();
  FOURCC wave = LoadExport<FourCcFn>(proxy, "mmioStringToFOURCCA")("WAVE", 0);

  std::printf("ticks=%lu begin=%u end=%u devices=%u version=0x%08X "
              "fourcc=0x%08lX\n",
              ticks, begin, end, devices, version,
              static_cast<unsigned long>(wave));
  Sleep(100);
  FreeLibrary(proxy);
  return 0;
}
