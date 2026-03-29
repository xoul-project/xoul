#Requires -Version 5.1
# ============================================================
# Xoul - 완전 제거 (Uninstall) 스크립트
# ============================================================
# Xoul이 설치한 모든 구성요소를 제거합니다:
#   - QEMU 프로세스 종료 + VM 파일 삭제
#   - Ollama 프로세스 종료 + Ollama 앱 제거
#   - Ollama 모델 캐시 (~/.ollama) 삭제
#   - Python venv 삭제
#   - llm/ 폴더 삭제 (모델 포함)
#   - config.json, token.json, credentials.json 등 사용자 데이터
#
#   .\uninstall.ps1           # 전체 제거 (확인 프롬프트)
#   .\uninstall.ps1 -Force    # 확인 없이 제거
# ============================================================

param(
    [switch]$Force
)

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# ── i18n 초기화 ──
. (Join-Path $ProjectDir "i18n.ps1")
$configPath = Join-Path $ProjectDir "config.json"
$lang = Get-LangFromConfig $configPath
Load-Locale $lang

Write-Host ""
Write-Host "========================================" -ForegroundColor Red
Write-Host (T "uninstall.title") -ForegroundColor Red
Write-Host "========================================" -ForegroundColor Red
Write-Host ""
Write-Host (T "uninstall.warning") -ForegroundColor Yellow
Write-Host ""
Write-Host (T "uninstall.will_remove") -ForegroundColor White
Write-Host (T "uninstall.item_qemu") -ForegroundColor Gray
Write-Host (T "uninstall.item_ollama") -ForegroundColor Gray
Write-Host (T "uninstall.item_ollama_models") -ForegroundColor Gray
Write-Host (T "uninstall.item_venv") -ForegroundColor Gray
Write-Host (T "uninstall.item_vm") -ForegroundColor Gray
Write-Host (T "uninstall.item_llm") -ForegroundColor Gray
Write-Host (T "uninstall.item_userdata") -ForegroundColor Gray
Write-Host ""

if (-not $Force) {
    do {
        $confirm = Read-Host (T "uninstall.confirm_prompt")
        if ($confirm -notin @("y", "n", "Y", "N", "")) {
            Write-Host (T "uninstall.confirm_invalid") -ForegroundColor Yellow
        }
    } while ($confirm -notin @("y", "n", "Y", "N", ""))

    if ($confirm -ne "y" -and $confirm -ne "Y") {
        Write-Host ""
        Write-Host (T "uninstall.cancelled") -ForegroundColor Gray
        exit 0
    }
}

$totalSteps = 7
$step = 0

# ── 1. 모든 프로세스 종료 ──
$step++
Write-Host ""
Write-Host (T "uninstall.step_process" @{step="$step"; total="$totalSteps"}) -ForegroundColor Yellow

# QEMU
$qemuProcs = Get-Process -Name "qemu-system-*" -ErrorAction SilentlyContinue
if ($qemuProcs) {
    $qemuProcs | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host (T "uninstall.qemu_stopped" @{count="$($qemuProcs.Count)"}) -ForegroundColor Green
} else {
    Write-Host (T "uninstall.qemu_not_running") -ForegroundColor Gray
}

# llama-server
$llamaProc = Get-Process -Name "llama-server" -ErrorAction SilentlyContinue
if ($llamaProc) {
    $llamaProc | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host (T "uninstall.llama_stopped") -ForegroundColor Green
}

# Ollama
$ollamaProcs = Get-Process -Name "ollama*" -ErrorAction SilentlyContinue
if ($ollamaProcs) {
    # 서비스 먼저 중지
    $ollamaSvc = Get-Service -Name "Ollama" -ErrorAction SilentlyContinue
    if ($ollamaSvc -and $ollamaSvc.Status -eq "Running") {
        Stop-Service -Name "Ollama" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    Get-Process -Name "ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host (T "uninstall.ollama_stopped" @{count="$($ollamaProcs.Count)"}) -ForegroundColor Green
} else {
    Write-Host (T "uninstall.ollama_not_running") -ForegroundColor Gray
}

# host_sync_server (Python)
Get-Process python* -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*host_sync_server*"
} | Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 2

# ── 2. QEMU 제거 (winget) ──
$step++
Write-Host ""
Write-Host (T "uninstall.step_qemu" @{step="$step"; total="$totalSteps"}) -ForegroundColor Yellow

$qemuExe = $null
foreach ($path in @("qemu-system-x86_64", "$env:ProgramFiles\qemu\qemu-system-x86_64.exe")) {
    try {
        if (Test-Path $path) { $qemuExe = $path; break }
        $found = Get-Command $path -ErrorAction SilentlyContinue
        if ($found) { $qemuExe = $found.Source; break }
    } catch {}
}

if ($qemuExe) {
    Write-Host (T "uninstall.qemu_uninstalling") -ForegroundColor Yellow
    winget uninstall SoftwareFreedomConservancy.QEMU --silent 2>$null
    Write-Host (T "uninstall.qemu_uninstalled") -ForegroundColor Green
} else {
    Write-Host (T "uninstall.qemu_not_installed") -ForegroundColor Gray
}

# ── 3. Ollama 제거 + 모델 캐시 삭제 ──
$step++
Write-Host ""
Write-Host (T "uninstall.step_ollama" @{step="$step"; total="$totalSteps"}) -ForegroundColor Yellow

$ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaExe) {
    Write-Host (T "uninstall.ollama_uninstalling") -ForegroundColor Yellow
    winget uninstall Ollama.Ollama --silent 2>$null
    Write-Host (T "uninstall.ollama_uninstalled") -ForegroundColor Green
} else {
    Write-Host (T "uninstall.ollama_not_installed") -ForegroundColor Gray
}

# Ollama 모델 캐시 삭제
$ollamaDir = Join-Path $env:USERPROFILE ".ollama"
if (Test-Path $ollamaDir) {
    $size = (Get-ChildItem -Path $ollamaDir -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1GB
    Write-Host (T "uninstall.ollama_cache_deleting" @{size="$([math]::Round($size, 1))"}) -ForegroundColor Yellow
    Remove-Item -Recurse -Force $ollamaDir -ErrorAction SilentlyContinue
    if (Test-Path $ollamaDir) {
        Write-Host (T "uninstall.ollama_cache_fail") -ForegroundColor Red
    } else {
        Write-Host (T "uninstall.ollama_cache_deleted") -ForegroundColor Green
    }
} else {
    Write-Host (T "uninstall.ollama_cache_not_found") -ForegroundColor Gray
}

# ── 4. VM 파일 삭제 ──
$step++
Write-Host ""
Write-Host (T "uninstall.step_vm" @{step="$step"; total="$totalSteps"}) -ForegroundColor Yellow

$vmDir = Join-Path $ProjectDir "vm"
if (Test-Path $vmDir) {
    # 백업 파일은 건너뛰기
    $backupFile = Join-Path $vmDir "xoul.qcow2.backup"
    $hasBackup = Test-Path $backupFile

    for ($i = 0; $i -lt 3; $i++) {
        if ($hasBackup) {
            # 백업 제외하고 삭제
            Get-ChildItem $vmDir -Exclude "xoul.qcow2.backup" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            Remove-Item -Recurse -Force $vmDir -ErrorAction SilentlyContinue
        }
        $remaining = Get-ChildItem $vmDir -Exclude "xoul.qcow2.backup" -ErrorAction SilentlyContinue
        if (-not $remaining -or $remaining.Count -eq 0) { break }
        Write-Host (T "uninstall.file_lock_wait" @{attempt="$($i+1)"}) -ForegroundColor Gray
        Start-Sleep -Seconds 2
    }
    ssh-keygen -R "[127.0.0.1]:2222" 2>$null | Out-Null

    if ($hasBackup) {
        Write-Host (T "uninstall.vm_deleted_backup_kept") -ForegroundColor Green
    } else {
        if (Test-Path $vmDir) {
            Write-Host (T "uninstall.vm_delete_fail") -ForegroundColor Red
        } else {
            Write-Host (T "uninstall.vm_deleted") -ForegroundColor Green
        }
    }
} else {
    Write-Host (T "uninstall.vm_not_found") -ForegroundColor Gray
}

# ── 5. Python venv 삭제 ──
$step++
Write-Host ""
Write-Host (T "uninstall.step_venv" @{step="$step"; total="$totalSteps"}) -ForegroundColor Yellow

$venvDir = Join-Path $ProjectDir ".venv"
if (Test-Path $venvDir) {
    Get-Process python*, pip* -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -like "$venvDir*"
    } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
    if (Test-Path $venvDir) {
        Start-Sleep -Seconds 2
        Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
    }
    if (Test-Path $venvDir) {
        Write-Host (T "uninstall.venv_delete_fail") -ForegroundColor Red
    } else {
        Write-Host (T "uninstall.venv_deleted") -ForegroundColor Green
    }
} else {
    Write-Host (T "uninstall.venv_not_found") -ForegroundColor Gray
}

# ── 6. LLM 폴더 삭제 (모델 포함) ──
$step++
Write-Host ""
Write-Host (T "uninstall.step_llm" @{step="$step"; total="$totalSteps"}) -ForegroundColor Yellow

$llmDir = Join-Path $ProjectDir "llm"
if (Test-Path $llmDir) {
    $modelsDir = Join-Path $llmDir "models"
    if (Test-Path $modelsDir) {
        $modelSize = (Get-ChildItem -Path $modelsDir -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1GB
        if ($modelSize -gt 0) {
            Write-Host (T "uninstall.llm_models_deleting" @{size="$([math]::Round($modelSize, 1))"}) -ForegroundColor Yellow
        }
    }
    Remove-Item -Recurse -Force $llmDir -ErrorAction SilentlyContinue
    if (Test-Path $llmDir) {
        Write-Host (T "uninstall.llm_delete_fail") -ForegroundColor Red
    } else {
        Write-Host (T "uninstall.llm_deleted") -ForegroundColor Green
    }
} else {
    Write-Host (T "uninstall.llm_not_found") -ForegroundColor Gray
}

# ── 7. 사용자 데이터 정리 ──
$step++
Write-Host ""
Write-Host (T "uninstall.step_userdata" @{step="$step"; total="$totalSteps"}) -ForegroundColor Yellow

$userFiles = @("config.json", "token.json", "credentials.json")
foreach ($f in $userFiles) {
    $fPath = Join-Path $ProjectDir $f
    if (Test-Path $fPath) {
        Remove-Item $fPath -Force -ErrorAction SilentlyContinue
        Write-Host "  ✅ $f" -ForegroundColor Green
    }
}

# __pycache__ 정리
Get-ChildItem -Path $ProjectDir -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host (T "uninstall.cache_cleaned") -ForegroundColor Green

# share/ 내용 정리 (폴더는 유지)
$shareDir = Join-Path $ProjectDir "share"
if (Test-Path $shareDir) {
    Get-ChildItem $shareDir -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host (T "uninstall.share_cleaned") -ForegroundColor Green
}

# workspace/ 내용 정리
$wsDir = Join-Path $ProjectDir "workspace"
if (Test-Path $wsDir) {
    Get-ChildItem $wsDir -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host (T "uninstall.workspace_cleaned") -ForegroundColor Green
}

# ── 완료 ──
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host (T "uninstall.done") -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host (T "uninstall.reinstall_hint") -ForegroundColor Yellow
Write-Host (T "uninstall.reinstall_cmd") -ForegroundColor White
Write-Host ""
