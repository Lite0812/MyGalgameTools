param(
    [string]$SystemWinmm = "$env:WINDIR\SysWOW64\winmm.dll",
    [string]$OutputDirectory = "$PSScriptRoot\generated"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $SystemWinmm)) {
    throw "32-bit system winmm.dll was not found: $SystemWinmm"
}

$dump = & dumpbin.exe /nologo /exports $SystemWinmm
if ($LASTEXITCODE -ne 0) {
    throw "dumpbin failed for $SystemWinmm"
}

$exports = @{}
foreach ($line in $dump) {
    if ($line -match '^\s*(\d+)\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s+(\S+)') {
        $ordinal = [int]$Matches[1]
        $name = $Matches[2]
        if ($name -ne '[NONAME]') {
            $exports[$ordinal] = $name
        }
    }
    elseif ($line -match '^\s*(\d+)\s+[0-9A-Fa-f]{8}\s+\[NONAME\]') {
        $exports[[int]$Matches[1]] = $null
    }
}

if ($exports.Count -eq 0) {
    throw 'No WinMM exports were parsed from dumpbin output.'
}

$ordinals = @($exports.Keys | Sort-Object)
$firstOrdinal = $ordinals[0]
$lastOrdinal = $ordinals[-1]
for ($ordinal = $firstOrdinal; $ordinal -le $lastOrdinal; ++$ordinal) {
    if (-not $exports.ContainsKey($ordinal)) {
        throw "Export ordinal $ordinal is missing; sparse tables are not supported."
    }
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$newline = "`r`n"
$utf8 = [System.Text.UTF8Encoding]::new($false)

$asm = [System.Collections.Generic.List[string]]::new()
$asm.Add('.386')
$asm.Add('.model flat')
$asm.Add('EXTERN _g_winmm_functions:DWORD')
$asm.Add('EXTERN _ResolveWinmmExport:PROC')
$asm.Add('.code')

$def = [System.Collections.Generic.List[string]]::new()
$def.Add('LIBRARY winmm')
$def.Add('EXPORTS')

$inc = [System.Collections.Generic.List[string]]::new()
$inc.Add('#pragma once')
$inc.Add(('#define WINMM_PROXY_EXPORT_COUNT {0}' -f $ordinals.Count))
$inc.Add('struct WinmmExportSpec { unsigned short ordinal; const char *name; };')
$inc.Add('static const WinmmExportSpec kWinmmExportSpecs[WINMM_PROXY_EXPORT_COUNT] = {')

for ($index = 0; $index -lt $ordinals.Count; ++$index) {
    $ordinal = $ordinals[$index]
    $name = $exports[$ordinal]
    $stub = "_winmm_stub_$index"
    $linkStub = "winmm_stub_$index"
    $ready = "winmm_ready_$index"

    $asm.Add("$stub PROC")
    $asm.Add("    mov eax, DWORD PTR [_g_winmm_functions + $($index * 4)]")
    $asm.Add('    test eax, eax')
    $asm.Add("    jne $ready")
    $asm.Add("    push $index")
    $asm.Add('    call _ResolveWinmmExport')
    $asm.Add('    add esp, 4')
    $asm.Add("${ready}:")
    $asm.Add('    jmp eax')
    $asm.Add("$stub ENDP")

    if ($null -eq $name) {
        $def.Add("    winmm_ordinal_$ordinal=$linkStub @$ordinal NONAME")
        $inc.Add("    {$ordinal, nullptr},")
    }
    else {
        $def.Add("    $name=$linkStub @$ordinal")
        $inc.Add("    {$ordinal, `"$name`"},")
    }
}

$asm.Add('END')
$inc.Add('};')

[System.IO.File]::WriteAllText(
    (Join-Path $OutputDirectory 'winmm_proxy_stubs.asm'),
    [string]::Join($newline, $asm) + $newline,
    $utf8)
[System.IO.File]::WriteAllText(
    (Join-Path $OutputDirectory 'winmm_proxy.def'),
    [string]::Join($newline, $def) + $newline,
    $utf8)
[System.IO.File]::WriteAllText(
    (Join-Path $OutputDirectory 'winmm_exports.inc'),
    [string]::Join($newline, $inc) + $newline,
    $utf8)

Write-Host "Generated $($ordinals.Count) WinMM exports (ordinals $firstOrdinal..$lastOrdinal)."
