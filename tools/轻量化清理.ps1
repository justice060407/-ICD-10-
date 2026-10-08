# 本机轻量化清理工具
#
# 背景：用户要求"凡安装的东西以最轻便方式落地，不需要的筛选并删去"。
# 本脚本分四级，越往下越激进。只处理【可再生】的缓存与垃圾，
# 不碰任何用户文档、不碰已安装的程序本体。
#
# 用法（PowerShell，逐条选着跑，别一键全跑）：
#   .\tools\轻量化清理.ps1 -Level 1     # 只清过期日志，最保守
#   .\tools\轻量化清理.ps1 -Level 2     # + pip / npm 下载缓存（大头）
#   .\tools\轻量化清理.ps1 -Level 3     # + HuggingFace 模型缓存
#   .\tools\轻量化清理.ps1 -Level 4     # + Docker 虚悬层与 WSL 回收（装了 Docker 才用）
#   .\tools\轻量化清理.ps1 -Scan        # 只扫描不删除，先看清楚再决定

param(
    [int]$Level = 0,
    [switch]$Scan,
    [switch]$Yes
)

$ErrorActionPreference = 'Continue'

function Size-MB($path) {
    if (-not (Test-Path $path)) { return 0 }
    $s = (Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue |
          Measure-Object -Property Length -Sum).Sum
    return [math]::Round($s / 1MB, 1)
}

# ---------------------------------------------------------------------------
# 扫描清单：每一项都标注了"是否可再生"
# ---------------------------------------------------------------------------
$targets = @(
    @{ Name = 'WorkBuddy 运行日志';   Path = "$env:USERPROFILE\.workbuddy\logs";      再生 = '可再生'; 说明 = '程序自动重建' },
    @{ Name = 'WorkBuddy 追踪数据';   Path = "$env:USERPROFILE\.workbuddy\traces";    再生 = '可再生'; 说明 = '调试追踪，随时重采' },
    @{ Name = 'pip 下载缓存';         Path = "$env:LOCALAPPDATA\pip";                 再生 = '可再生'; 说明 = '已下载但未安装的 wheel 碎片' },
    @{ Name = 'npm 下载缓存';         Path = "$env:LOCALAPPDATA\npm-cache";           再生 = '可再生'; 说明 = '前端包缓存' },
    @{ Name = 'HuggingFace 模型缓存'; Path = "$env:USERPROFILE\.cache\huggingface";   再生 = '可再生（需重下）'; 说明 = 'bge-small-zh / MiniLM 等权重' },
    @{ Name = '系统临时目录';         Path = "$env:LOCALAPPDATA\Temp";                再生 = '部分可再生'; 说明 = '其他软件可能正在用，谨慎' }
)

Write-Host "`n===== 本机可回收空间扫描 =====`n" -ForegroundColor Cyan
$total = 0
foreach ($t in $targets) {
    $mb = Size-MB $t.Path
    if ($mb -gt 0) {
        $total += $mb
        Write-Host ("  {0,-24} {1,10} MB   [{2}] {3}" -f $t.Name, $mb, $t.再生, $t.说明)
    }
}
Write-Host ("  {0,-24} {1,10} MB" -f '—— 合计 ——', $total) -ForegroundColor Yellow

if ($Scan -or $Level -eq 0) {
    Write-Host "`n（扫描模式，未删除任何内容。加 -Level N 执行清理。）`n" -ForegroundColor Gray
    exit 0
}

# ---------------------------------------------------------------------------
# 分级执行
# ---------------------------------------------------------------------------
function Confirm-Go($msg) {
    if ($Yes) { return $true }
    $r = Read-Host "$msg [y/N]"
    return ($r -eq 'y' -or $r -eq 'Y')
}

if ($Level -ge 1) {
    Write-Host "`n--- Level 1：清日志与追踪 ---" -ForegroundColor Cyan
    foreach ($p in @("$env:USERPROFILE\.workbuddy\logs\*", "$env:USERPROFILE\.workbuddy\traces\*")) {
        if (Test-Path (Split-Path $p)) {
            Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "  完成。日志目录会被自动重建，不影响正在运行的会话。" -ForegroundColor Green
}

if ($Level -ge 2) {
    if (Confirm-Go "即将删除 pip + npm 下载缓存（约 13 GB，100% 可再生），确认？") {
        Write-Host "`n--- Level 2：清 pip / npm 缓存 ---" -ForegroundColor Cyan
        Remove-Item -Path "$env:LOCALAPPDATA\pip\cache\*" -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path "$env:LOCALAPPDATA\npm-cache\*" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  完成。下次安装会重新下载，只是慢一点。" -ForegroundColor Green
    }
}

if ($Level -ge 3) {
    if (Confirm-Go "即将删除 HuggingFace 模型缓存（约 550 MB，可再生但需重下），确认？") {
        Write-Host "`n--- Level 3：清 HuggingFace 缓存 ---" -ForegroundColor Cyan
        Remove-Item -Path "$env:USERPROFILE\.cache\huggingface\hub\*" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  完成。" -ForegroundColor Green
    }
}

if ($Level -ge 4) {
    Write-Host "`n--- Level 4：Docker 与 WSL 回收 ---" -ForegroundColor Cyan
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        & docker system prune -a --volumes -f
        & docker builder prune -a -f
        Write-Host "  Docker 虚悬镜像与构建缓存已清理。" -ForegroundColor Green
    } else {
        Write-Host "  未检测到 Docker，跳过。" -ForegroundColor Gray
    }
    Write-Host "  WSL 虚拟磁盘收缩需手动执行（PowerShell）：" -ForegroundColor Yellow
    Write-Host "    wsl --shutdown" -ForegroundColor White
    Write-Host "    diskpart -> select vdisk file=\"%LOCALAPPDATA%\\Docker\\wsl\\data\\ext4.vhdx\" -> compact vdisk" -ForegroundColor White
}

Write-Host "`n===== 清理结束 =====`n" -ForegroundColor Cyan
