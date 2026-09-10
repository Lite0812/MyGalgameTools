param(
    [string]$ExePath = ".\\LoveDere.exe",
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"

$entryVa = 0x424F80
$entryOffset = 0x24F80
$caveVa = 0x498E80
$caveOffset = 0x98E80

$createFileAIat = 0x499128
$closeHandleIat = 0x4991E8
$sub424100 = 0x424100
$returnVa = 0x424F88

function Add-Bytes {
    param(
        [System.Collections.Generic.List[byte]]$Buffer,
        [byte[]]$Bytes
    )

    foreach ($byte in $Bytes) {
        $Buffer.Add($byte)
    }
}

function Add-UInt32LE {
    param(
        [System.Collections.Generic.List[byte]]$Buffer,
        [UInt32]$Value
    )

    $Buffer.Add([byte]($Value -band 0xFF))
    $Buffer.Add([byte](($Value -shr 8) -band 0xFF))
    $Buffer.Add([byte](($Value -shr 16) -band 0xFF))
    $Buffer.Add([byte](($Value -shr 24) -band 0xFF))
}

function Add-Rel32 {
    param(
        [System.Collections.Generic.List[byte]]$Buffer,
        [UInt32]$InstructionVa,
        [UInt32]$TargetVa
    )

    $nextVa = [Int64]$InstructionVa + 5
    $rel = [Int64]$TargetVa - $nextVa
    if ($rel -lt 0) {
        $rel += 0x100000000L
    }
    Add-UInt32LE $Buffer ([UInt32]$rel)
}

function Add-IndirectCall {
    param(
        [System.Collections.Generic.List[byte]]$Buffer,
        [UInt32]$IatVa
    )

    Add-Bytes $Buffer ([byte[]](0xFF, 0x15))
    Add-UInt32LE $Buffer $IatVa
}

function Add-Call {
    param(
        [System.Collections.Generic.List[byte]]$Buffer,
        [UInt32]$BaseVa,
        [UInt32]$TargetVa
    )

    $instructionVa = [UInt32]($BaseVa + $Buffer.Count)
    $Buffer.Add(0xE8)
    Add-Rel32 $Buffer $instructionVa $TargetVa
}

function Add-Jump {
    param(
        [System.Collections.Generic.List[byte]]$Buffer,
        [UInt32]$BaseVa,
        [UInt32]$TargetVa
    )

    $instructionVa = [UInt32]($BaseVa + $Buffer.Count)
    $Buffer.Add(0xE9)
    Add-Rel32 $Buffer $instructionVa $TargetVa
}

function Set-ShortJumpTarget {
    param(
        [System.Collections.Generic.List[byte]]$Buffer,
        [int]$JumpPos,
        [int]$TargetPos
    )

    $rel = $TargetPos - ($JumpPos + 2)
    if ($rel -lt -128 -or $rel -gt 127) {
        throw "short jump out of range: $rel"
    }

    $Buffer[$JumpPos + 1] = [byte][sbyte]$rel
}

function Test-Bytes {
    param(
        [byte[]]$Data,
        [int]$Offset,
        [byte[]]$Expected
    )

    if ($Offset + $Expected.Length -gt $Data.Length) {
        return $false
    }

    for ($i = 0; $i -lt $Expected.Length; $i++) {
        if ($Data[$Offset + $i] -ne $Expected[$i]) {
            return $false
        }
    }

    return $true
}

function Write-Bytes {
    param(
        [byte[]]$Data,
        [int]$Offset,
        [byte[]]$Patch
    )

    for ($i = 0; $i -lt $Patch.Length; $i++) {
        $Data[$Offset + $i] = $Patch[$i]
    }
}

$entryOriginal = [byte[]](0x55, 0x32, 0xD2, 0xE8, 0x78, 0xF1, 0xFF, 0xFF)

$caveOriginal = [System.Collections.Generic.List[byte]]::new()
for ($i = 0; $i -lt 4; $i++) {
    $caveOriginal.Add(0xC3)
    for ($j = 0; $j -lt 15; $j++) {
        $caveOriginal.Add(0xCC)
    }
}
$caveOriginal = $caveOriginal.ToArray()

$stub = [System.Collections.Generic.List[byte]]::new()
Add-Bytes $stub ([byte[]](0x8B, 0x44, 0x24, 0x04))
Add-Bytes $stub ([byte[]](0x85, 0xC0))
$jumpIfNull = $stub.Count
Add-Bytes $stub ([byte[]](0x74, 0x00))
Add-Bytes $stub ([byte[]](0x6A, 0x00, 0x6A, 0x80, 0x6A, 0x03, 0x6A, 0x00, 0x6A, 0x01))
Add-Bytes $stub ([byte[]](0x68, 0x00, 0x00, 0x00, 0x80))
Add-Bytes $stub ([byte[]](0x50))
Add-IndirectCall $stub $createFileAIat
Add-Bytes $stub ([byte[]](0x83, 0xC4, 0x1C, 0x83, 0xF8, 0xFF))
$jumpIfMissing = $stub.Count
Add-Bytes $stub ([byte[]](0x74, 0x00))
Add-Bytes $stub ([byte[]](0x50))
Add-IndirectCall $stub $closeHandleIat
Add-Bytes $stub ([byte[]](0x83, 0xC4, 0x04, 0x33, 0xC0, 0xC3))

$origPath = $stub.Count
Add-Bytes $stub ([byte[]](0x55, 0x32, 0xD2))
Add-Call $stub $caveVa $sub424100
Add-Jump $stub $caveVa $returnVa

Set-ShortJumpTarget $stub $jumpIfNull $origPath
Set-ShortJumpTarget $stub $jumpIfMissing $origPath

if ($stub.Count -ne 64) {
    throw "stub length mismatch: $($stub.Count)"
}

$entryPatch = [System.Collections.Generic.List[byte]]::new()
Add-Jump $entryPatch $entryVa $caveVa
Add-Bytes $entryPatch ([byte[]](0x90, 0x90, 0x90))
$entryPatch = $entryPatch.ToArray()
$stubPatch = $stub.ToArray()

$resolvedExe = (Resolve-Path $ExePath).Path
$fileBytes = [System.IO.File]::ReadAllBytes($resolvedExe)

$entryAlreadyPatched = Test-Bytes $fileBytes $entryOffset $entryPatch
$caveAlreadyPatched = Test-Bytes $fileBytes $caveOffset $stubPatch
if ($entryAlreadyPatched -and $caveAlreadyPatched) {
    Write-Host "Already patched: $resolvedExe"
    exit 0
}

if (-not (Test-Bytes $fileBytes $entryOffset $entryOriginal)) {
    throw "entry bytes do not match expected original at 0x24F80"
}

if (-not (Test-Bytes $fileBytes $caveOffset $caveOriginal)) {
    throw "code cave bytes do not match expected nullsub padding at 0x98E80"
}

if (-not $SkipBackup) {
    $backupPath = "$resolvedExe.loose-first.bak"
    if (-not (Test-Path $backupPath)) {
        Copy-Item $resolvedExe $backupPath
        Write-Host "Backup created: $backupPath"
    }
    else {
        Write-Host "Backup exists: $backupPath"
    }
}

Write-Bytes $fileBytes $entryOffset $entryPatch
Write-Bytes $fileBytes $caveOffset $stubPatch
[System.IO.File]::WriteAllBytes($resolvedExe, $fileBytes)

Write-Host "Patched loose-file-first lookup into $resolvedExe"
Write-Host ("entry: {0}" -f (($entryPatch | ForEach-Object { '{0:X2}' -f $_ }) -join ' '))
Write-Host ("stub : {0}" -f (($stubPatch | ForEach-Object { '{0:X2}' -f $_ }) -join ' '))