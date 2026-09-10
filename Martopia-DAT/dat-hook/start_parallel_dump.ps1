param(
    [ValidateRange(1, 16)]
    [int]$进程数 = 8,

    [ValidateRange(1, 8)]
    [int]$每进程写入线程 = 2,

    [ValidateRange(0, 3)]
    [int]$失败重试次数 = 2,

    [string]$输出目录 = '',

    [switch]$保留运行进程
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5 使用 UTF8 即会写入 BOM；PowerShell 7 才支持 utf8BOM 名称。
$CSV编码 = if ($PSVersionTable.PSVersion.Major -ge 6) { 'utf8BOM' } else { 'UTF8' }
$脚本目录 = Split-Path -Parent $MyInvocation.MyCommand.Path
$游戏目录 = Split-Path -Parent $脚本目录
$游戏程序 = Join-Path $游戏目录 'Martopia.exe'
$代理文件 = Join-Path $脚本目录 'bin\Release\winmm.dll'
$数据目录 = Join-Path $游戏目录 'dat'

foreach ($路径 in @($游戏程序, $代理文件, $数据目录)) {
    if (-not (Test-Path -LiteralPath $路径)) {
        throw "缺少必需文件或目录：$路径"
    }
}

if ([string]::IsNullOrWhiteSpace($输出目录)) {
    $时间 = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $输出目录 = Join-Path $游戏目录 "martopia_parallel_dump\$时间"
}
$输出目录 = [IO.Path]::GetFullPath($输出目录)
$最终文件目录 = Join-Path $输出目录 'files'
$最终统计目录 = Join-Path $输出目录 'meta'
$分片统计目录 = Join-Path $最终统计目录 'shards'
$运行目录 = Join-Path $输出目录 'work'
New-Item -ItemType Directory -Path $最终文件目录, $分片统计目录, $运行目录 -Force | Out-Null

function New-运行副本 {
    param([Parameter(Mandatory)][string]$目录)

    New-Item -ItemType Directory -Path $目录 -Force | Out-Null
    foreach ($名称 in @('Martopia.exe', 'lua5.1.dll', 'lua51.dll', 'conf.dat')) {
        $来源 = Join-Path $游戏目录 $名称
        if (Test-Path -LiteralPath $来源) {
            Copy-Item -LiteralPath $来源 -Destination (Join-Path $目录 $名称) -Force
        }
    }
    Copy-Item -LiteralPath $代理文件 -Destination (Join-Path $目录 'winmm.dll') -Force
    $链接 = Join-Path $目录 'dat'
    if (-not (Test-Path -LiteralPath $链接)) {
        New-Item -ItemType Junction -Path $链接 -Target $数据目录 | Out-Null
    }
}

function Start-分片进程 {
    param(
        [Parameter(Mandatory)][int]$分片索引,
        [Parameter(Mandatory)][int]$分片总数,
        [Parameter(Mandatory)][string]$名称
    )

    $实例目录 = Join-Path $运行目录 $名称
    $实例文件目录 = $最终文件目录
    $实例统计目录 = Join-Path $分片统计目录 $名称
    New-运行副本 -目录 $实例目录
    New-Item -ItemType Directory -Path $实例文件目录, $实例统计目录 -Force | Out-Null

    $变量名 = @(
        'MARTOPIA_SHARD_INDEX', 'MARTOPIA_SHARD_COUNT', 'MARTOPIA_DUMP_ROOT',
        'MARTOPIA_EXTRACT_ROOT', 'MARTOPIA_META_ROOT', 'MARTOPIA_NO_CONSOLE',
        'MARTOPIA_DUMP_THREADS', 'MARTOPIA_VERBOSE_LOG', 'MARTOPIA_RELEASE_FEED_ARCHIVES'
    )
    $原值 = @{}
    foreach ($变量 in $变量名) {
        $原值[$变量] = [Environment]::GetEnvironmentVariable($变量, 'Process')
    }
    try {
        $env:MARTOPIA_SHARD_INDEX = [string]$分片索引
        $env:MARTOPIA_SHARD_COUNT = [string]$分片总数
        $env:MARTOPIA_DUMP_ROOT = $实例目录
        $env:MARTOPIA_EXTRACT_ROOT = $实例文件目录
        $env:MARTOPIA_META_ROOT = $实例统计目录
        $env:MARTOPIA_NO_CONSOLE = '1'
        $env:MARTOPIA_DUMP_THREADS = [string]$每进程写入线程
        Remove-Item Env:MARTOPIA_VERBOSE_LOG -ErrorAction SilentlyContinue
        # 单进程必须在每个 DAT 完成后释放归档对象，防止 32 位地址空间持续增长。
        if ($进程数 -eq 1) {
            $env:MARTOPIA_RELEASE_FEED_ARCHIVES = '1'
        }
        else {
            Remove-Item Env:MARTOPIA_RELEASE_FEED_ARCHIVES -ErrorAction SilentlyContinue
        }
        $进程 = Start-Process -FilePath (Join-Path $实例目录 'Martopia.exe') `
            -WorkingDirectory $实例目录 -PassThru -WindowStyle Hidden
    }
    finally {
        foreach ($变量 in $变量名) {
            [Environment]::SetEnvironmentVariable($变量, $原值[$变量], 'Process')
        }
    }

    [pscustomobject]@{
        名称 = $名称
        分片索引 = $分片索引
        分片总数 = $分片总数
        进程 = $进程
        文件目录 = $实例文件目录
        统计目录 = $实例统计目录
        完成文件 = Join-Path $实例统计目录 'completion.tsv'
        结果文件 = Join-Path $实例统计目录 'dat_results.tsv'
        已报告退出 = $false
    }
}

function Get-分片状态 {
    param([Parameter(Mandatory)]$任务)

    $结果数 = 0
    $成功数 = 0
    $失败数 = 0
    if (Test-Path -LiteralPath $任务.结果文件) {
        try {
            $结果 = @(Import-Csv -LiteralPath $任务.结果文件 -Delimiter "`t")
            $结果数 = $结果.Count
            $成功数 = @($结果 | Where-Object 成功 -eq '1').Count
            $失败数 = $结果数 - $成功数
        }
        catch {
            # 工作线程可能正好写到一半，下一轮再读即可。
        }
    }
    $完成 = Test-Path -LiteralPath $任务.完成文件
    $存活 = $null -ne (Get-Process -Id $任务.进程.Id -ErrorAction SilentlyContinue)
    [pscustomobject]@{
        结果数 = $结果数
        成功数 = $成功数
        失败数 = $失败数
        完成 = $完成
        存活 = $存活
    }
}

function Wait-任务组 {
    param([Parameter(Mandatory)][array]$任务组)

    while ($true) {
        $总结果 = 0
        $总成功 = 0
        $总失败 = 0
        $已完成 = 0
        $异常退出 = @()
        foreach ($任务 in $任务组) {
            $状态 = Get-分片状态 -任务 $任务
            $总结果 += $状态.结果数
            $总成功 += $状态.成功数
            $总失败 += $状态.失败数
            if ($状态.完成) { $已完成++ }
            if (-not $状态.存活 -and -not $状态.完成) {
                $异常退出 += $任务
            }
        }

        $文本 = "`r[Martopia] 总进度 $总结果/759  成功=$总成功  失败=$总失败  分片完成=$已完成/$($任务组.Count)"
        Write-Host $文本.PadRight(110) -NoNewline

        if ($异常退出.Count -gt 0) {
            Write-Host
            $名称 = ($异常退出 | ForEach-Object 名称) -join '、'
            throw "以下分片进程在生成完成标记前退出：$名称"
        }
        if ($已完成 -eq $任务组.Count) {
            Write-Host
            return
        }
        Start-Sleep -Seconds 1
    }
}

function Stop-任务进程 {
    param([Parameter(Mandatory)][array]$任务组)

    if ($保留运行进程) { return }
    foreach ($任务 in $任务组) {
        $进程 = Get-Process -Id $任务.进程.Id -ErrorAction SilentlyContinue
        if ($进程) {
            Stop-Process -Id $进程.Id -Force
            $进程.WaitForExit()
        }
    }
}

function Merge-目录 {
    param(
        [Parameter(Mandatory)][string]$来源,
        [Parameter(Mandatory)][string]$目标
    )

    if (-not (Test-Path -LiteralPath $来源)) { return }
    Get-ChildItem -LiteralPath $来源 -Recurse -File | ForEach-Object {
        $相对路径 = [IO.Path]::GetRelativePath($来源, $_.FullName)
        $目标文件 = Join-Path $目标 $相对路径
        $目标父目录 = Split-Path -Parent $目标文件
        New-Item -ItemType Directory -Path $目标父目录 -Force | Out-Null
        if (-not (Test-Path -LiteralPath $目标文件)) {
            Copy-Item -LiteralPath $_.FullName -Destination $目标文件
            return
        }

        # 相同内部路径可能出现在多个 DAT 中，全部保留而不覆盖。
        for ($编号 = 1; ; $编号++) {
            $候选 = "$目标文件.__dup$编号"
            if (-not (Test-Path -LiteralPath $候选)) {
                Copy-Item -LiteralPath $_.FullName -Destination $候选
                break
            }
        }
    }
}

Write-Host "[Martopia] 开始多进程 DAT 解包"
Write-Host "[Martopia] 输出目录：$输出目录"
Write-Host "[Martopia] 引擎进程：$进程数；每进程写入线程：$每进程写入线程"
if ($进程数 -eq 1) {
    Write-Host '[Martopia] 单进程逐包释放归档缓存：已开启'
}

$任务组 = @()
for ($索引 = 0; $索引 -lt $进程数; $索引++) {
    $名称 = 'shard_{0:D2}' -f ($索引 + 1)
    $任务组 += Start-分片进程 -分片索引 $索引 -分片总数 $进程数 -名称 $名称
}

try {
    Wait-任务组 -任务组 $任务组
}
finally {
    Stop-任务进程 -任务组 $任务组
}

$所有结果 = @()
foreach ($任务 in $任务组) {
    if (Test-Path -LiteralPath $任务.结果文件) {
        $所有结果 += Import-Csv -LiteralPath $任务.结果文件 -Delimiter "`t"
    }
}
$结果映射 = @{}
foreach ($结果 in $所有结果) {
    $结果映射[[int]$结果.资源表索引] = $结果
}
for ($资源索引 = 1; $资源索引 -le 759; $资源索引++) {
    if (-not $结果映射.ContainsKey($资源索引)) {
        $结果映射[$资源索引] = [pscustomobject]@{
            资源表索引 = [string]$资源索引
            成功 = '0'
            状态 = '分片未返回结果'
            物理归档 = 'dat\{0:D4}.dat' -f $资源索引
            逻辑资源 = ''
        }
    }
}
$所有结果 = @(1..759 | ForEach-Object { $结果映射[$_] })
$失败项 = @($所有结果 | Where-Object 成功 -ne '1')

for ($轮次 = 1; $轮次 -le $失败重试次数 -and $失败项.Count -gt 0; $轮次++) {
    Write-Host "[Martopia] 第 $轮次 轮重试：$($失败项.Count) 个 DAT"
    $重试任务 = @()
    foreach ($失败 in $失败项) {
        $资源索引 = [int]$失败.资源表索引
        $名称 = 'retry_{0:D2}_{1:D4}' -f $轮次, $资源索引
        $重试任务 += Start-分片进程 -分片索引 ($资源索引 - 1) -分片总数 759 -名称 $名称
    }
    try {
        Wait-任务组 -任务组 $重试任务
    }
    finally {
        Stop-任务进程 -任务组 $重试任务
    }

    foreach ($任务 in $重试任务) {
        if (Test-Path -LiteralPath $任务.结果文件) {
            $重试结果 = @(Import-Csv -LiteralPath $任务.结果文件 -Delimiter "`t")
            foreach ($结果 in $重试结果) {
                $位置 = -1
                for ($i = 0; $i -lt $所有结果.Count; $i++) {
                    if ($所有结果[$i].资源表索引 -eq $结果.资源表索引) { $位置 = $i; break }
                }
                if ($位置 -ge 0 -and $结果.成功 -eq '1') { $所有结果[$位置] = $结果 }
            }
        }
    }
    $失败项 = @($所有结果 | Where-Object 成功 -ne '1')
    $任务组 += $重试任务
}

$所有结果 = @($所有结果 | Sort-Object { [int]$_.资源表索引 })
$所有结果 | Export-Csv -LiteralPath (Join-Path $最终统计目录 'all_dat_results.tsv') `
    -Delimiter "`t" -NoTypeInformation -Encoding $CSV编码
$成功总数 = @($所有结果 | Where-Object 成功 -eq '1').Count
$失败项 = @($所有结果 | Where-Object 成功 -ne '1')
$文件统计 = Get-ChildItem -LiteralPath $最终文件目录 -Recurse -File | Measure-Object -Property Length -Sum
$汇总 = @(
    [pscustomobject]@{
        状态 = if ($成功总数 -eq 759) { '成功' } else { '不完整' }
        DAT成功 = $成功总数
        DAT总数 = 759
        DAT失败 = $失败项.Count
        文件数 = $文件统计.Count
        文件字节 = [long]$文件统计.Sum
        进程数 = $进程数
    }
)
$汇总 | Export-Csv -LiteralPath (Join-Path $最终统计目录 'summary.tsv') `
    -Delimiter "`t" -NoTypeInformation -Encoding $CSV编码

if ($失败项.Count -gt 0) {
    $失败项 | Export-Csv -LiteralPath (Join-Path $最终统计目录 'failed_dat.tsv') `
        -Delimiter "`t" -NoTypeInformation -Encoding $CSV编码
    throw "解包未完整：成功 $成功总数/759，仍有 $($失败项.Count) 个 DAT 失败。详见 meta\failed_dat.tsv"
}

Write-Host "[Martopia] 全部完成：759/759"
Write-Host "[Martopia] 文件数：$($文件统计.Count)，总字节：$([long]$文件统计.Sum)"
Write-Host "[Martopia] 文件目录：$最终文件目录"
Write-Host "[Martopia] 统计目录：$最终统计目录"
