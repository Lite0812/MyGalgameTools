param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [ValidateSet("x86")]
    [string]$Platform = "x86",
    [ValidateSet("build", "clean")]
    [string]$Action = "build",
    [switch]$CopyToGameRoot
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Remove-IfExists {
    param([string]$Path)
    if (Test-Path $Path) {
        try {
            Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Host "[Warn] Cleanup failed, skipped: $Path"
        }
    }
}

function Find-MSBuild {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $found = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find "MSBuild\**\Bin\MSBuild.exe" | Select-Object -First 1
        if ($found -and (Test-Path $found)) { return $found }
    }

    $fallback = @(
        "E:\VisualStudio\VisualStudio\MSBuild\Current\Bin\MSBuild.exe",
        "E:\VisualStudio\VisualStudio\MSBuild\Current\Bin\amd64\MSBuild.exe",
        "C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe",
        "C:\Program Files\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe",
        "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\MSBuild.exe"
    )

    foreach ($path in $fallback) {
        if (Test-Path $path) { return $path }
    }

    throw "MSBuild.exe not found"
}

function Sync-RuntimeFiles {
    param(
        [string]$Root,
        [string]$Configuration,
        [switch]$CopyToGameRoot
    )

    $outDir = Join-Path $Root ("out\bin\x86\" + $Configuration)
    $iniSource = Join-Path $Root "src\OneTextHook.ini"
    if (Test-Path $outDir) {
        if (Test-Path $iniSource) {
            Copy-Item -Path $iniSource -Destination (Join-Path $outDir "OneTextHook.ini") -Force
            Write-Host "[Info] Output config: $outDir\OneTextHook.ini"
        }
        Write-Host "[Info] Output proxy: $outDir\version.dll"
    }

    if ($CopyToGameRoot) {
        $gameRoot = Split-Path -Parent $Root
        $dll = Join-Path $outDir "version.dll"
        $ini = Join-Path $outDir "OneTextHook.ini"
        if (Test-Path $dll) {
            Copy-Item -Path $dll -Destination (Join-Path $gameRoot "version.dll") -Force
            Write-Host "[Info] Copied proxy: $gameRoot\version.dll"
        }
        if (Test-Path $ini) {
            Copy-Item -Path $ini -Destination (Join-Path $gameRoot "OneTextHook.ini") -Force
            Write-Host "[Info] Copied config: $gameRoot\OneTextHook.ini"
        }
    }
}

function Cleanup-Intermediate {
    param([string]$Root)
    Remove-IfExists (Join-Path $Root "Release")
    Remove-IfExists (Join-Path $Root "Debug")
    Remove-IfExists (Join-Path $Root "x64")
    Remove-IfExists (Join-Path $Root "Win32")
    Remove-IfExists (Join-Path $Root "src\Release")
    Remove-IfExists (Join-Path $Root "src\Debug")
    Remove-IfExists (Join-Path $Root "src\x64")
    Remove-IfExists (Join-Path $Root "src\Win32")
}

$msbuild = Find-MSBuild
$msTarget = if ($Action -eq "clean") { "Clean;Build" } else { "Build" }
$solution = Join-Path $root "OneTextHook.sln"

Write-Host "[Info] MSBuild: $msbuild"
Write-Host "[Info] Solution: $solution"
Write-Host "[Info] Configuration: $Configuration"
Write-Host "[Info] Platform: $Platform"
Write-Host "[Info] Action: $msTarget"

& $msbuild $solution "/m:1" "/t:$msTarget" "/p:Configuration=$Configuration" "/p:Platform=$Platform" | Out-Host
$exitCode = [int]$LASTEXITCODE

if ($exitCode -ne 0 -and $Action -eq "build") {
    Write-Host "[Warn] Incremental build failed, retrying Clean;Build ..."
    & $msbuild $solution "/m:1" "/t:Clean;Build" "/p:Configuration=$Configuration" "/p:Platform=$Platform" | Out-Host
    $exitCode = [int]$LASTEXITCODE
}

if ($exitCode -ne 0) {
    throw "Build failed, exit code=$exitCode"
}

Sync-RuntimeFiles -Root $root -Configuration $Configuration -CopyToGameRoot:$CopyToGameRoot
Cleanup-Intermediate -Root $root

Write-Host "[Done] Build succeeded"
exit 0
