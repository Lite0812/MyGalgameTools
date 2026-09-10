@echo off
setlocal

set "ROOT=%~dp0"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"

if exist "%VSWHERE%" (
  for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.Component.MSBuild -property installationPath`) do set "VSINSTALL=%%i"
)

if defined VSINSTALL (
  set "VCVARS=%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat"
  if exist "%VCVARS%" call "%VCVARS%"
)

where msbuild >nul 2>nul
if errorlevel 1 (
  echo MSBuild not found. Please run this from a VS Developer Command Prompt.
  exit /b 1
)

msbuild "%ROOT%LooseAldHook.sln" /m /p:Configuration=Release /p:Platform=x64
exit /b %errorlevel%
