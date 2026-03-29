#Requires -Version 5.1
# ============================================================
# Xoul - 완전 초기화 (리셋) 스크립트
# ============================================================
# VM, venv, LLM 모델 모두 삭제하고 처음부터 다시 설치할 수 있게 합니다.
#   .\reset.ps1            # 전체 리셋
#   .\reset.ps1 -debug     # venv만 리셋 (VM/LLM 유지)
#   .\setup_env.ps1        # 재설치
# ============================================================

param(
    [switch]$debug  # debug 모드: VM, Ollama, llama.cpp 유지, venv만 삭제
)

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# ── i18n 초기화 ──
. (Join-Path $ProjectDir "i18n.ps1")
$configPath = Join-Path $ProjectDir "config.json"
$lang = Get-LangFromConfig $configPath
Load-Locale $lang

Write-Host ""
Write-Host "========================================" -ForegroundColor Red
if ($debug) {
    Write-Host (T "reset.title_debug") -ForegroundColor Yellow
} else {
    Write-Host (T "reset.title_full") -ForegroundColor Red
}
Write-Host "========================================" -ForegroundColor Red
Write-Host ""

if (-not $debug) {
# 1. VM 중지 (모든 QEMU 프로세스 종료)
Write-Host (T "reset.step1") -ForegroundColor Yellow
$pidFile = Join-Path $ProjectDir "vm\qemu.pid"
if (Test-Path $pidFile) {
    $qemuPid = Get-Content $pidFile
    try {
        Stop-Process -Id $qemuPid -Force -ErrorAction SilentlyContinue
        Write-Host (T "reset.vm_pid_stopped" @{pid=$qemuPid}) -ForegroundColor Green
    } catch {
        Write-Host (T "reset.vm_already_stopped") -ForegroundColor Gray
    }
}
# 이름으로도 QEMU 프로세스 전부 종료
$qemuProcs = Get-Process -Name "qemu-system-*" -ErrorAction SilentlyContinue
if ($qemuProcs) {
    $qemuProcs | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host (T "reset.qemu_stopped" @{count="$($qemuProcs.Count)"}) -ForegroundColor Green
    Start-Sleep -Seconds 2
} else {
    Write-Host (T "reset.qemu_not_running") -ForegroundColor Gray
}

# llama-server 종료
Get-Process -Name "llama-server" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host (T "reset.llama_stopped") -ForegroundColor Green

# Ollama 종료
$ollamaProcs = Get-Process -Name "ollama*" -ErrorAction SilentlyContinue
if ($ollamaProcs) {
    $ollamaProcs | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host (T "reset.ollama_stopped" @{count="$($ollamaProcs.Count)"}) -ForegroundColor Green
} else {
    Write-Host (T "reset.ollama_not_running") -ForegroundColor Gray
}

# Ollama 서비스 중지 (Windows 서비스로 등록된 경우)
$ollamaSvc = Get-Service -Name "Ollama" -ErrorAction SilentlyContinue
if ($ollamaSvc -and $ollamaSvc.Status -eq "Running") {
    Stop-Service -Name "Ollama" -Force -ErrorAction SilentlyContinue
    Write-Host (T "reset.ollama_svc_stopped") -ForegroundColor Green
}
} else {
    Write-Host (T "reset.step1_skip") -ForegroundColor DarkGray
}

if (-not $debug) {
Write-Host ""
Write-Host (T "reset.step2") -ForegroundColor Yellow
$vmDir = Join-Path $ProjectDir "vm"
if (Test-Path $vmDir) {
    for ($i = 0; $i -lt 3; $i++) {
        Remove-Item -Recurse -Force $vmDir -ErrorAction SilentlyContinue
        if (!(Test-Path $vmDir)) { break }
        Write-Host (T "reset.file_lock_wait" @{attempt="$($i+1)"}) -ForegroundColor Gray
        Start-Sleep -Seconds 2
    }
    ssh-keygen -R "[127.0.0.1]:2222" 2>$null | Out-Null
    if (!(Test-Path $vmDir)) {
        Write-Host (T "reset.vm_deleted") -ForegroundColor Green
    } else {
        Write-Host (T "reset.vm_delete_fail") -ForegroundColor Red
    }
} else {
    Write-Host (T "reset.vm_not_found") -ForegroundColor Gray
}
} else {
    Write-Host (T "reset.step2_skip") -ForegroundColor DarkGray
}

# 3. Python venv 삭제
Write-Host ""
Write-Host (T "reset.step3") -ForegroundColor Yellow
$venvDir = Join-Path $ProjectDir ".venv"
if (Test-Path $venvDir) {
    # venv의 python 프로세스 종료
    Get-Process python*, pip* -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -like "$venvDir*"
    } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
    if (Test-Path $venvDir) {
        # 재시도
        Start-Sleep -Seconds 2
        Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
    }
    if (Test-Path $venvDir) {
        Write-Host (T "reset.venv_delete_fail") -ForegroundColor Red
    } else {
        Write-Host (T "reset.venv_deleted") -ForegroundColor Green
    }
} else {
    Write-Host (T "reset.venv_not_found") -ForegroundColor Gray
}

if (-not $debug) {
# 4. LLM 엔진 삭제 (모델은 보존)
Write-Host ""
Write-Host (T "reset.step4") -ForegroundColor Yellow

$llmBinDir = Join-Path $ProjectDir "llm"
if (Test-Path $llmBinDir) {
    Get-ChildItem -Path $llmBinDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -ne ".gguf" } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path $llmBinDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "models" } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host (T "reset.llm_deleted") -ForegroundColor Green
} else {
    Write-Host (T "reset.llm_not_found") -ForegroundColor Gray
}

$ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaExe) {
    Write-Host (T "reset.ollama_uninstalling") -ForegroundColor Yellow
    winget uninstall Ollama.Ollama --silent 2>$null
    Write-Host (T "reset.ollama_uninstalled") -ForegroundColor Green
} else {
    Write-Host (T "reset.ollama_not_installed") -ForegroundColor Gray
}

$ollamaDir = Join-Path $env:USERPROFILE ".ollama"
if (Test-Path $ollamaDir) {
    $size = (Get-ChildItem -Path $ollamaDir -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1GB
    Write-Host (T "reset.ollama_cache_kept" @{size="$([math]::Round($size, 1))"}) -ForegroundColor Gray
    Write-Host (T "reset.ollama_cache_hint") -ForegroundColor DarkGray
}
} else {
    Write-Host (T "reset.step4_skip") -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host (T "reset.done") -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host (T "reset.next_steps") -ForegroundColor Yellow
Write-Host (T "reset.next_hint") -ForegroundColor White
Write-Host ""
