# Docker 验收自检脚本
#
# 用途：在【已安装 Docker 的机器】上，一条命令完成
#   1) 检测 Docker 是否可用
#   2) 构建 base 镜像
#   3) 测量镜像体积（开题报告 4.2 硬指标 <500MB）
#   4) 容器内跑一遍全链路，确认容器里也能出结果
#   5) 生成 out/docker_evidence.txt —— 直接交给导师的凭据
#
# 用法：
#   .\tools\docker-check.ps1
#   .\tools\docker-check.ps1 -SkipRun     # 只构建和测体积，不跑链路

param([switch]$SkipRun)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
$tag  = 'clinical-llm-course:base'
$out  = Join-Path $project 'out'
if (-not (Test-Path $out)) { New-Item -ItemType Directory -Force -Path $out | Out-Null }

function Line($s) { Write-Host $s -ForegroundColor Cyan }
function Ok($s)   { Write-Host ("  [OK] " + $s) -ForegroundColor Green }
function Bad($s)  { Write-Host ("  [!!] " + $s) -ForegroundColor Yellow }

Line "`n===== Docker 验收自检 =====`n"

# ---------- 1. 检测 Docker ----------
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Bad "本机没有 Docker。"
    Write-Host ""
    Write-Host "  这台机器不适合做镜像验收。请二选一：" -ForegroundColor White
    Write-Host "   a) 换一台装了 Docker 的机器跑这个脚本" -ForegroundColor White
    Write-Host "   b) 日常开发根本不需要 Docker，直接跑：" -ForegroundColor White
    Write-Host "      python src\run_pipeline.py --key `"本课程密钥`"" -ForegroundColor White
    Write-Host "  详见 docs\Docker使用路径.md 的路径 A / 路径 B`n" -ForegroundColor Gray
    exit 0
}
Ok "Docker 客户端已找到"

try {
    $ver = & docker version --format '{{.Server.Version}}' 2>&1
    if ($LASTEXITCODE -ne 0) { throw "daemon" }
    Ok ("Docker 守护进程运行中，版本 " + $ver)
} catch {
    Bad "Docker 守护进程没起来。请先启动 Docker Desktop，等托盘图标停止动画后再跑本脚本。"
    exit 1
}

# ---------- 2. 构建 ----------
Line "`n--- 构建 base 镜像（首次较慢，需联网拉 python:3.11-slim）---"
$buildStart = Get-Date
& docker build --target base -t $tag . 2>&1 | Select-Object -Last 12
if ($LASTEXITCODE -ne 0) {
    Bad "构建失败。看上面的报错；常见原因是网络不通或 .dockerignore 缺失导致上下文过大。"
    exit 1
}
$buildSec = [math]::Round(((Get-Date) - $buildStart).TotalSeconds, 1)
Ok ("构建完成，耗时 " + $buildSec + " 秒")

# ---------- 3. 测体积 ----------
Line "`n--- 镜像体积（开题报告指标 <500MB）---"
$sizeStr = (& docker images $tag --format '{{.Size}}').Trim()
Write-Host ("  原始值: " + $sizeStr) -ForegroundColor White

$mb = 0.0
if ($sizeStr -match '^([\d.]+)\s*(MB|GB|kB|B)$') {
    $num = [double]$Matches[1]
    switch ($Matches[2]) {
        'GB' { $mb = $num * 1024 }
        'MB' { $mb = $num }
        'kB' { $mb = $num / 1024 }
        'B'  { $mb = $num / 1048576 }
    }
} elseif ($sizeStr -match '^([\d.]+)MB$') { $mb = [double]$Matches[1] }

if ($mb -le 0) {
    $bytes = (& docker images $tag --format '{{.Size}}' | ForEach-Object { $_ })
    Bad ("无法解析体积字符串：" + $sizeStr + "，请手动看 docker images 输出")
} else {
    $mb = [math]::Round($mb, 1)
    Write-Host ("  = " + $mb + " MB") -ForegroundColor White
    if ($mb -lt 500) {
        Ok ("满足 <500MB 指标，余量 " + [math]::Round(500 - $mb, 1) + " MB")
    } else {
        Bad ("超出 500MB 指标 " + [math]::Round($mb - 500, 1) + " MB，需要瘦身")
    }
}

Write-Host "`n  各层占用：" -ForegroundColor Gray
& docker history $tag --format '{{.Size}}  {{.CreatedBy}}' 2>&1 | Select-Object -First 8

# ---------- 4. 容器内跑链路 ----------
$runResult = '未执行'
if (-not $SkipRun) {
    Line "`n--- 容器内跑一遍全链路 ---"
    & docker run --rm -v "${out}:/course/out" -v "${project}\data:/course/data" $tag 2>&1 | Select-Object -Last 20
    if ($LASTEXITCODE -eq 0) { Ok "容器内全链路通过"; $runResult = '通过' }
    else { Bad "容器内运行失败"; $runResult = '失败' }
} else {
    $runResult = '已跳过（-SkipRun）'
}

# ---------- 5. 写凭据 ----------
$ev = @()
$ev += "=============================================="
$ev += " 临床大模型与病历挖掘 · Docker 化微课 验收凭据"
$ev += "=============================================="
$ev += "生成时间     : " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
$ev += "镜像名       : " + $tag
$ev += "镜像体积     : " + $mb + " MB"
$ev += "指标要求     : < 500 MB"
$ev += "指标结论     : " + $(if ($mb -lt 500 -and $mb -gt 0) { "通过（余量 " + [math]::Round(500 - $mb, 1) + " MB）" } else { "未通过" })
$ev += "构建耗时     : " + $buildSec + " 秒"
$ev += "容器内全链路 : " + $runResult
$ev += "Docker 版本  : " + $ver
$ev += "说明         : gpu 层（约 6GB）为运行态训练工具，不计入课程镜像体积"
$ev += "=============================================="
$evPath = Join-Path $out 'docker_evidence.txt'
[System.IO.File]::WriteAllText($evPath, ($ev -join "`r`n"), (New-Object System.Text.UTF8Encoding($true)))

Line "`n===== 完成 ====="
Write-Host ("  凭据已写入: " + $evPath) -ForegroundColor Green
Write-Host "  把这个文件直接交给导师即可。`n" -ForegroundColor Gray
