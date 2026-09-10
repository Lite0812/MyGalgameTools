@echo off
setlocal
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "E:\VisualStudio\VisualStudio\VC\Auxiliary\Build\vcvarsall.bat" set "VSROOT=E:\VisualStudio\VisualStudio"
if defined VSROOT goto vs_found
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
:vs_found
if not defined VSROOT exit /b 1
call "%VSROOT%\VC\Auxiliary\Build\vcvarsall.bat" x86
if errorlevel 1 exit /b 1
if not exist build mkdir build
python tools\generate_winmm_proxy.py
if errorlevel 1 exit /b 1
ml /nologo /c /Fo build\winmm_proxy.obj src\winmm_proxy.asm
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /utf-8 /O2 /EHsc /MT /W4 /DWIN32_LEAN_AND_MEAN /I "..\CialloHook\third\detours\include" /c src\dllmain.cpp src\patch_hook.cpp /Fo"build\\"
if errorlevel 1 exit /b 1
link /nologo /DLL /OUT:build\winmm.dll /DEF:src\winmm.def build\dllmain.obj build\patch_hook.obj build\winmm_proxy.obj "..\CialloHook\third\detours\lib.X86\detours.lib" kernel32.lib user32.lib
if errorlevel 1 exit /b 1
copy /y ThinkerBellPatch.ini build\ThinkerBellPatch.ini >nul
if not exist build\patch mkdir build\patch
echo Built build\winmm.dll
exit /b 0
