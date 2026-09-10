@echo off
setlocal

set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars32.bat"
if not exist "%VCVARS%" (
  echo Visual Studio x86 build tools were not found.
  exit /b 1
)

call "%VCVARS%" >nul 2>nul
if errorlevel 1 exit /b 1

if not exist build mkdir build

powershell.exe -NoProfile -ExecutionPolicy Bypass -File generate_winmm_proxy.ps1
if errorlevel 1 exit /b 1

cl /nologo /std:c++17 /O2 /MT /EHsc /W4 /LD tac_name_hook.cpp ^
  /Fe:build\tac_name_hook.dll /link /INCREMENTAL:NO
if errorlevel 1 exit /b 1

ml /nologo /c /Fo"build\winmm_proxy_stubs.obj" generated\winmm_proxy_stubs.asm
if errorlevel 1 exit /b 1

cl /nologo /std:c++17 /O2 /MT /EHsc /W4 /c /DBUILD_WINMM_PROXY ^
  /Fo"build\tac_name_hook_proxy.obj" tac_name_hook.cpp
if errorlevel 1 exit /b 1

link /nologo /DLL /INCREMENTAL:NO /OUT:build\winmm.dll ^
  /DEF:generated\winmm_proxy.def build\tac_name_hook_proxy.obj ^
  build\winmm_proxy_stubs.obj
if errorlevel 1 exit /b 1

copy /Y tac_name_hook.ini build\tac_name_hook.ini >nul
copy /Y README.md build\README.md >nul

echo Built build\tac_name_hook.dll and build\winmm.dll
endlocal
