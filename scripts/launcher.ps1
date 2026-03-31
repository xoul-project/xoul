#Requires -Version 5.1
# launcher.ps1 — Xoul Agent Launcher
# Starts VM + LLM server together.
#
# Usage: .\launcher.ps1


$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# 이전 VM 호스트 키 충돌 방지
try { ssh-keygen -R "[127.0.0.1]:2222" 2>&1 | Out-Null } catch {}

# ── i18n 초기화 ──
. (Join-Path $ProjectDir "i18n.ps1")
$configPath = Join-Path $ProjectDir "config.json"
$lang = Get-LangFromConfig $configPath
Load-Locale $lang

Write-Host ""
Write-Host (T "launcher.title") -ForegroundColor Cyan
Write-Host (T "launcher.separator") -ForegroundColor DarkGray

# ── venv Python 감지 ──
$venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $venvPython = "python"
    Write-Host (T "launcher.no_venv") -ForegroundColor Yellow
}

# ── 1. config 로드 ──
if (-not (Test-Path $configPath)) {
    Write-Host (T "launcher.no_config") -ForegroundColor Red
    exit 1
}
$config = Get-Content -Path $configPath -Raw -Encoding UTF8 | ConvertFrom-Json

# ── 2. VM 재시작 (항상 클린 상태 보장) ──
Write-Host (T "launcher.vm_starting") -ForegroundColor Yellow
& $venvPython (Join-Path $ProjectDir "vm_manager.py") restart 2>$null
if ($LASTEXITCODE -ne 0) {
    # restart 실패 시 start fallback
    & $venvPython (Join-Path $ProjectDir "vm_manager.py") start 2>$null
}
Write-Host (T "launcher.vm_running") -ForegroundColor Green

# ── 3. Ollama 서버 시작 ──
Write-Host (T "launcher.ollama_starting") -ForegroundColor Yellow

# 기존 Ollama 프로세스 종료 후 재시작
$ollamaProc = Get-Process -Name "ollama*" -ErrorAction SilentlyContinue
if ($ollamaProc) {
    Write-Host (T "launcher.ollama_restart") -ForegroundColor Yellow
    $svc = Get-Service -Name "Ollama" -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Stop-Service -Name "Ollama" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    Get-Process -Name "ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# Ollama 시작 (병렬 처리 + 멀티 모델 설정)
$env:OLLAMA_NUM_PARALLEL = "4"
$env:OLLAMA_MAX_LOADED_MODELS = "3"
$env:OLLAMA_KEEP_ALIVE = "-1"
$env:OLLAMA_FLASH_ATTENTION = "1"
$ollamaRunning = $false
$ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaExe) {
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Minimized
    Write-Host (T "launcher.ollama_waiting") -NoNewline -ForegroundColor Gray
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 -ErrorAction Stop
            $ollamaRunning = $true
            Write-Host ""
            Write-Host (T "launcher.ollama_ready" @{seconds="$($($i+1)*2)"}) -ForegroundColor Green
            break
        } catch {
            Write-Host "." -NoNewline -ForegroundColor Gray
        }
    }
} else {
    Write-Host (T "launcher.ollama_not_installed") -ForegroundColor Red
}

if (-not $ollamaRunning) {
    Write-Host ""
    Write-Host (T "launcher.ollama_fail") -ForegroundColor Red
} else {
    # Ollama 모델 num_ctx 설정
    $ollamaModel = if ($config.llm -and $config.llm.ollama_model) { $config.llm.ollama_model } else { "qwen3:8b" }
    Write-Host (T "launcher.model_ctx_check" @{model=$ollamaModel}) -ForegroundColor Yellow
    try {
        $tempModelfile = Join-Path $env:TEMP "xoul_modelfile"
        "FROM $ollamaModel`nPARAMETER num_ctx 32768" | Set-Content -Path $tempModelfile -Encoding UTF8
        $createResult = & ollama create $ollamaModel -f $tempModelfile 2>&1
        Remove-Item $tempModelfile -ErrorAction SilentlyContinue
        Write-Host (T "launcher.model_ctx_set") -ForegroundColor Green
    } catch {
        Write-Host (T "launcher.model_ctx_fail") -ForegroundColor Yellow
    }

    # GPU 예열 — 모델을 VRAM에 미리 로드
    $isLocal = ($config.llm.engine -eq "ollama")
    if ($isLocal) {
        Write-Host "  🔥 Warming up GPU ($ollamaModel)..." -NoNewline -ForegroundColor Yellow
        try {
            $body = @{ model = $ollamaModel; prompt = "hi"; stream = $false; options = @{ num_predict = 1 } } | ConvertTo-Json
            $warmup = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/generate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 120
            Write-Host " ✅ Model loaded in VRAM" -ForegroundColor Green
        } catch {
            Write-Host " ⚠ warmup skipped" -ForegroundColor Yellow
        }
    }
}

# ── 4. Xoul API 상태 확인 ──
$port = if ($config.PSObject.Properties["server"] -and $config.server.port) { $config.server.port } else { 3000 }
$sshPort = if ($config.PSObject.Properties["vm"] -and $config.vm.ssh_port) { $config.vm.ssh_port } else { 2222 }
$sshKey = Join-Path $ProjectDir "vm\xoul_key"
if (-not (Test-Path $sshKey) -and (Test-Path "C:\xoul\vm\xoul_key")) {
    $sshKey = "C:\xoul\vm\xoul_key"
}

Write-Host (T "launcher.api_checking") -ForegroundColor Yellow
Start-Sleep -Seconds 2

# 서비스 존재 여부 확인 (신규 setup 시 deploy 필요)
$svcExists = ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no -o LogLevel=ERROR root@127.0.0.1 "systemctl list-unit-files xoul.service 2>/dev/null | grep -c xoul" 2>$null
if (-not $svcExists -or $svcExists -eq "0") {
    Write-Host "  📦 First launch detected — running deploy..." -ForegroundColor Yellow
    & (Join-Path $ProjectDir "scripts\deploy.ps1")
    Start-Sleep -Seconds 3
}

try {
    $response = Invoke-RestMethod -Uri "http://127.0.0.1:$port/status" -TimeoutSec 5
    Write-Host (T "launcher.api_running") -ForegroundColor Green
} catch {
    Write-Host (T "launcher.api_fail") -ForegroundColor Yellow
    Write-Host (T "launcher.api_check_vm") -ForegroundColor Yellow
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no -o LogLevel=ERROR root@127.0.0.1 "systemctl status xoul --no-pager" 2>&1
}

# ── 4.5. 브라우저 데몬 확인 (필수 인프라) ──
$browserStatus = ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no -o LogLevel=ERROR root@127.0.0.1 "systemctl is-active xoul-browser 2>/dev/null" 2>$null
if ($browserStatus -ne "active") {
    Write-Host "  🌐 Starting browser daemon..." -ForegroundColor Yellow
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no -o LogLevel=ERROR root@127.0.0.1 "systemctl enable xoul-browser 2>/dev/null; systemctl start xoul-browser" 2>$null
    Start-Sleep -Seconds 3
    $browserCheck = ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no -o LogLevel=ERROR root@127.0.0.1 "systemctl is-active xoul-browser 2>/dev/null" 2>$null
    if ($browserCheck -eq "active") {
        Write-Host "  🌐 Browser daemon started" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ Browser daemon failed to start" -ForegroundColor Red
    }
} else {
    Write-Host "  🌐 Browser daemon running" -ForegroundColor Green
}

# ── 4.6. 클라이언트 서비스 확인 (Telegram/Discord/Slack) ──
Write-Host "  📨 Checking client services..." -ForegroundColor Yellow
$clientScript = @'
import json, subprocess
c = json.load(open('/root/xoul/config.json'))
started = []
for k, v in c.get('clients', {}).items():
    svc = f'xoul-{k}'
    if v.get('enabled'):
        r = subprocess.run(['systemctl', 'is-active', svc], capture_output=True, text=True)
        if r.stdout.strip() != 'active':
            subprocess.run(['systemctl', 'enable', '--now', svc], capture_output=True)
            started.append(svc)
        else:
            started.append(f'{svc}(ok)')
if started:
    print(','.join(started))
else:
    print('none')
'@ -replace "`r", ""
$clientResult = $clientScript | ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no -o LogLevel=ERROR root@127.0.0.1 "python3 -" 2>$null
if ($clientResult -and $clientResult -ne "none") {
    Write-Host "  📨 Client services: $clientResult" -ForegroundColor Green
} else {
    Write-Host "  📨 No client services enabled" -ForegroundColor Gray
}

# ── 5. 파일 동기화 서버 (VM → Host) ──
Write-Host "  🔄 Starting file sync server..." -ForegroundColor Yellow
try {
    $syncScript = Join-Path $ProjectDir "desktop\host_sync_server.py"
    if (Test-Path $syncScript) {
        Start-Process -FilePath $venvPython -ArgumentList "`"$syncScript`"" -WindowStyle Hidden
        Write-Host "  🔄 File sync server started (port 3100)" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ host_sync_server.py not found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  ⚠ Sync server start failed: $_" -ForegroundColor Yellow
}

# ── 6. 완료 ──
$apiKey = if ($config.PSObject.Properties["server"]) { $config.server.api_key } else { $null }
Write-Host ""
Write-Host "  ═══════════════════════════════" -ForegroundColor DarkGray
Write-Host (T "launcher.ready") -ForegroundColor Green
Write-Host "  ─────────────────────────────" -ForegroundColor DarkGray
Write-Host (T "launcher.api_url" @{port="$port"}) -ForegroundColor White
if ($apiKey) {
    Write-Host "  API Key: $($apiKey.Substring(0, [Math]::Min(8, $apiKey.Length)))..." -ForegroundColor White
}
Write-Host (T "launcher.docs_url" @{port="$port"}) -ForegroundColor White
Write-Host "  ═══════════════════════════════" -ForegroundColor DarkGray
Write-Host ""
