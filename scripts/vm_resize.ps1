#Requires -Version 5.1
# ============================================================
# Xoul VM 리소스 변경 (QEMU)
# ============================================================
# config.json의 VM 설정(memory, cpus)을 변경하고 VM을 재시작합니다.
#   .\vm_resize.ps1                 # 현재 설정 확인
#   .\vm_resize.ps1 -Memory 4096   # 메모리 4GB
#   .\vm_resize.ps1 -CPUs 4        # CPU 4코어
#   .\vm_resize.ps1 -Memory 4096 -CPUs 4  # 둘 다
# ============================================================

param(
    [int]$Memory = 0,    # MB 단위 (예: 2048, 4096)
    [int]$CPUs   = 0     # 코어 수 (예: 2, 4)
)

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# ── i18n 초기화 ──
. (Join-Path $ProjectDir "i18n.ps1")
$configPath = Join-Path $ProjectDir "config.json"
$lang = Get-LangFromConfig $configPath
Load-Locale $lang

if (-not (Test-Path $configPath)) {
    Write-Host "  ❌ config.json not found" -ForegroundColor Red
    exit 1
}

$config = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

$currentMem  = if ($config.vm.memory) { $config.vm.memory } else { "1024" }
$currentCpus = if ($config.vm.cpus)   { $config.vm.cpus }   else { 2 }

# 인자 없으면 현재 설정 표시
if ($Memory -eq 0 -and $CPUs -eq 0) {
    Write-Host ""
    Write-Host "  📊 Xoul VM Configuration (QEMU)" -ForegroundColor Cyan
    Write-Host "  ────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "  Memory : ${currentMem}MB" -ForegroundColor White
    Write-Host "  CPUs   : $currentCpus" -ForegroundColor White
    Write-Host ""
    Write-Host "  Usage:" -ForegroundColor Yellow
    Write-Host "    .\vm_resize.ps1 -Memory 4096 -CPUs 4" -ForegroundColor Gray
    Write-Host ""
    exit 0
}

# 설정 변경
$changed = $false
if ($Memory -gt 0) {
    $config.vm | Add-Member -NotePropertyName "memory" -NotePropertyValue "$Memory" -Force
    Write-Host "  ✅ Memory: ${currentMem}MB → ${Memory}MB" -ForegroundColor Green
    $changed = $true
}
if ($CPUs -gt 0) {
    $config.vm | Add-Member -NotePropertyName "cpus" -NotePropertyValue $CPUs -Force
    Write-Host "  ✅ CPUs: $currentCpus → $CPUs" -ForegroundColor Green
    $changed = $true
}

if ($changed) {
    $jsonOut = $config | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($configPath, $jsonOut, (New-Object System.Text.UTF8Encoding $false))
    Write-Host ""
    Write-Host "  ⚠ VM을 재시작해야 적용됩니다." -ForegroundColor Yellow
    Write-Host "    .\launcher.ps1" -ForegroundColor Gray
    Write-Host ""
}
