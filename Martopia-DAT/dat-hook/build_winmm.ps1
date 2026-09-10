param(
    [ValidateSet('Release', 'Debug')]
    [string]$Configuration = 'Release'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$msbuildCandidates = @(
    'E:\VisualStudio\VisualStudio\MSBuild\Current\Bin\MSBuild.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\MSBuild.exe'
)
$msbuild = $msbuildCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $msbuild) { throw 'MSBuild.exe not found' }

& $msbuild (Join-Path $root 'MartopiaWinmm.vcxproj') /p:Configuration=$Configuration /p:Platform=Win32 /m
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Built: $(Join-Path $root "bin\$Configuration\winmm.dll")"
