#Requires -Version 5.1
# ============================================================
# Xoul 개인비서 - 환경 설정 스크립트
# ============================================================
# 사용법:
#   .\setup_env.ps1              # 전체 설정 (GPU 모드)
#   .\setup_env.ps1 -CPU         # CPU 전용 모드
# ============================================================

param(
    [switch]$CPU
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

try {

# 안전한 Safe-ReadHost 래퍼: 키보드 버퍼를 비운 뒤 입력 받음
# (이전 단계에서 Enter 키가 버퍼에 남아 다음 프롬프트를 건너뛰는 문제 방지)
function Safe-ReadHost {
    param([string]$Prompt)
    try { $host.UI.RawUI.FlushInputBuffer() } catch {}
    if ($Prompt) { return Read-Host $Prompt } else { return Read-Host }
}

# 이전 VM 호스트 키 충돌 방지
try { ssh-keygen -R "[127.0.0.1]:2222" 2>&1 | Out-Null } catch {}

# ─────────────────────────────────────────────
# Step 0. Language Selection / 언어 선택
# ─────────────────────────────────────────────
. (Join-Path $ProjectDir "i18n.ps1")
$configPath = Join-Path $ProjectDir "config.json"
$existingLang = "ko"
if (Test-Path $configPath) {
    $existingLang = Get-LangFromConfig $configPath
}

Write-Host ""
Write-Host "  ┌──────────────────────────────────────────────┐" -ForegroundColor Cyan
Write-Host "  │  Select Language / 언어를 선택하세요            │" -ForegroundColor Cyan
Write-Host "  └──────────────────────────────────────────────┘" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. English             — All guides and messages will be in English" -ForegroundColor White
Write-Host "  2. 한국어 (Korean)     — 모든 안내와 메시지가 한국어로 표시됩니다" -ForegroundColor White
Write-Host ""
$defaultNum = if ($existingLang -eq "ko") { "2" } else { "1" }
do {
    $langChoice = Safe-ReadHost "  선택 / Select (1-2, default: $defaultNum)"
    if (-not $langChoice) { $langChoice = $defaultNum }
    if ($langChoice -notin @("1","2")) {
        Write-Host "  ⚠ 1 또는 2를 입력해주세요 / Please enter 1 or 2." -ForegroundColor Yellow
    }
} while ($langChoice -notin @("1","2"))

if ($langChoice -eq "2") {
    Load-Locale "ko"
    $selectedLang = "ko"
    Write-Host "  → 한국어 선택됨" -ForegroundColor Green
} else {
    Load-Locale "en"
    $selectedLang = "en"
    Write-Host "  → English selected" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host (T "setup.title") -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ─────────────────────────────────────────────
# 1. Python 3.12 설치 확인
# ─────────────────────────────────────────────
Write-Host (T "setup.step1_title") -ForegroundColor Yellow

$python312 = $null

# py launcher로 3.12 찾기
try {
    $pyCheck = & py -3.12 --version 2>&1
    if ($pyCheck -match "Python 3\.12") {
        $python312 = { py -3.12 @args }
        Write-Host (T "setup.python_found_py" @{version=$pyCheck}) -ForegroundColor Green
    }
} catch {}

# 직접 경로 확인
if (-not $python312) {
    $directPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "C:\Python312\python.exe",
        "$env:ProgramFiles\Python312\python.exe"
    )
    foreach ($p in $directPaths) {
        if (Test-Path $p) {
            $python312 = { & $p @args }.GetNewClosure()
            Write-Host (T "setup.python_found_direct" @{path=$p}) -ForegroundColor Green
            break
        }
    }
}

# 없으면 설치
if (-not $python312) {
    Write-Host (T "setup.python_installing") -ForegroundColor Yellow
    winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python312 = { py -3.12 @args }
    Write-Host (T "setup.python_installed") -ForegroundColor Green
}

# venv 생성용 python 경로 확정
$venvPath = Join-Path $ProjectDir ".venv"

# ─────────────────────────────────────────────
# 2. LLM 추론 엔진 설정
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.step2_title") -ForegroundColor Yellow

$llmDir = Join-Path $ProjectDir "llm"
$modelsDir = Join-Path $llmDir "models"
New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null

$engineType = "ollama"

# Ollama 설치 확인 및 자동 업데이트
$ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaExe) {
    $rawVer = (ollama --version 2>$null) | Out-String
    if ($rawVer -match '(\d+\.\d+\.\d+)') { $currentVer = $Matches[1] } else { $currentVer = "unknown" }
    Write-Host (T "setup.ollama_found" @{path=$ollamaExe.Source}) -ForegroundColor Green
    Write-Host "  Version: $currentVer" -ForegroundColor Gray

    # 최신 버전 확인 (백그라운드 Job + 5초 타임아웃으로 블로킹 방지)
    Write-Host "  🔍 Checking for Ollama updates..." -ForegroundColor Gray
    try {
        $job = Start-Job -ScriptBlock {
            winget list --id Ollama.Ollama --accept-source-agreements --disable-interactivity 2>&1 | Out-String
        }
        # 최대 5초 대기
        $done = Wait-Job $job -Timeout 5
        if ($done) {
            $listOutput = Receive-Job $job
            Remove-Job $job -Force
            $installedVer = ""
            $availableVer = ""
            if ($listOutput -match "Ollama\.Ollama\s+(\d+[\d.]+\d)\s+(\d+[\d.]+\d)\s+winget") {
                $installedVer = $Matches[1]
                $availableVer = $Matches[2]
            } elseif ($listOutput -match "Ollama\.Ollama\s+(\d+[\d.]+\d)\s+winget") {
                $installedVer = $Matches[1]
            }

            if ($availableVer -and $availableVer -ne $installedVer) {
                # 업데이트 가능
                Write-Host "  ⬆️  New version available: $installedVer → $availableVer" -ForegroundColor Yellow
                do {
                    $updateChoice = Safe-ReadHost "  Update Ollama now? (y/n, default: n)"
                    if (-not $updateChoice) { $updateChoice = "n" }
                    if ($updateChoice -notin @("y", "n")) {
                        Write-Host "  ⚠ Please enter y or n." -ForegroundColor Yellow
                    }
                } while ($updateChoice -notin @("y", "n"))

                if ($updateChoice -eq "y") {
                    Write-Host "  ⬆️  Updating Ollama..." -ForegroundColor Yellow
                    winget upgrade --id Ollama.Ollama --accept-source-agreements --accept-package-agreements --silent
                    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
                    Start-Sleep -Seconds 3
                    Get-Process -Name "Ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
                    $rawNewVer = (ollama --version 2>$null) | Out-String
                    if ($rawNewVer -match '(\d+\.\d+\.\d+)') { $newVer = $Matches[1] } else { $newVer = "unknown" }
                    Write-Host "  ✅ Ollama updated: $installedVer → $newVer" -ForegroundColor Green
                } else {
                    Write-Host "  ⏭️  Skipped update, continuing with $currentVer" -ForegroundColor Gray
                }
            } else {
                Write-Host "  ✅ Ollama is up to date ($currentVer)" -ForegroundColor Green
            }
        } else {
            # 5초 타임아웃 — 업데이트 체크를 건너뜀
            Remove-Job $job -Force
            Write-Host "  ⏭️  Update check timed out, skipping (current: $currentVer)" -ForegroundColor Gray
        }
    } catch {
        Write-Host "  ⚠ Update check failed, continuing with current version" -ForegroundColor Yellow
    }
} else {
    Write-Host (T "setup.ollama_installing") -ForegroundColor Yellow
    winget install Ollama.Ollama --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    # 설치 후 자동 실행되는 GUI 종료 (사용자 혼동 방지)
    Start-Sleep -Seconds 3
    Get-Process -Name "Ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host (T "setup.ollama_installed") -ForegroundColor Green
}

# Ollama 모델 메모리 영구 상주 설정 (KEEP_ALIVE=-1)
$currentKeepAlive = [System.Environment]::GetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "User")
if ($currentKeepAlive -ne "-1") {
    [System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "-1", "User")
    $env:OLLAMA_KEEP_ALIVE = "-1"
    Write-Host "  ✅ OLLAMA_KEEP_ALIVE=-1 set (models stay in memory permanently)" -ForegroundColor Green
} else {
    Write-Host "  ✅ OLLAMA_KEEP_ALIVE already set (-1)" -ForegroundColor Green
}

# 동시 모델 로딩 설정 (메인 GPU + CPU 요약 + 임베딩 = 3개)
$currentMaxModels = [System.Environment]::GetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "User")
if ($currentMaxModels -ne "3") {
    [System.Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "3", "User")
    $env:OLLAMA_MAX_LOADED_MODELS = "3"
}
$currentNumParallel = [System.Environment]::GetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "User")
if ($currentNumParallel -ne "1") {
    [System.Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "1", "User")
    $env:OLLAMA_NUM_PARALLEL = "1"
}
Write-Host "  ✅ OLLAMA_MAX_LOADED_MODELS=3, OLLAMA_NUM_PARALLEL=1 set" -ForegroundColor Green

# 임베딩 모델 자동 설치 (시맨틱 메모리/유사도 검색용 - BGE-M3)
Write-Host (T "setup.embed_installing") -ForegroundColor Yellow
ollama pull bge-m3
Write-Host (T "setup.embed_installed") -ForegroundColor Green
# 요약용 경량 모델 설치
Write-Host (T "setup.summary_installing") -ForegroundColor Yellow
ollama pull qwen2.5:3b
# CPU 전용 커스텀 모델 생성 (GPU 점유 방지)
$modelfilePath = Join-Path $env:TEMP "Modelfile_cpu"
Set-Content -Path $modelfilePath -Value "FROM qwen2.5:3b`nPARAMETER num_gpu 0" -NoNewline
ollama create qwen2.5:3b-cpu -f $modelfilePath
Remove-Item $modelfilePath -ErrorAction SilentlyContinue
Write-Host (T "setup.summary_installed") -ForegroundColor Green

# GPU/CPU 모드 감지
$gpuMode = $false
$ngl = 0
if (-not $CPU) {
    try {
        $nv = nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null
        if ($nv) {
            $gpuMode = $true
            $ngl = 99
            Write-Host (T "setup.gpu_detected" @{gpu=$nv}) -ForegroundColor Magenta
        }
        else { Write-Host (T "setup.gpu_not_detected") -ForegroundColor Gray }
    } catch { Write-Host (T "setup.gpu_not_detected") -ForegroundColor Gray }
} else {
    Write-Host (T "setup.cpu_mode_selected") -ForegroundColor Gray
}

# ── 기존 설정 감지 ──
$existingEngine = $null
$existingProvider = $null
$existingModel = $null
$existingApiKey = $null
$existingConfigPath = Join-Path $ProjectDir "config.json"
if (Test-Path $existingConfigPath) {
    try {
        $existingCfg = [System.IO.File]::ReadAllText($existingConfigPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        $existingEngine = $existingCfg.llm.engine
        $existingProvider = $existingCfg.llm.provider
        if ($existingProvider -and $existingCfg.llm.providers.PSObject.Properties[$existingProvider]) {
            $existingModel = $existingCfg.llm.providers.$existingProvider.model_name
            $existingApiKey = $existingCfg.llm.providers.$existingProvider.api_key
        }
    } catch {}
}
$existingIsCommercial = ($existingEngine -eq "commercial")

# ── Local vs Commercial vs External 선택 ──
Write-Host ""
Write-Host "  ┌──────────────────────────────────────────────┐" -ForegroundColor Cyan
Write-Host "  │  $(T 'setup.provider_select_title')           │" -ForegroundColor Cyan
Write-Host "  └──────────────────────────────────────────────┘" -ForegroundColor Cyan
Write-Host ""
$localMark = if (-not $existingIsCommercial -and $existingEngine -and $existingEngine -ne "external") { T "setup.current_mark" } else { "" }
$commMark  = if ($existingIsCommercial -and $existingProvider -ne "external") { T "setup.current_mark_model" @{model=$existingModel} } else { "" }
$extMark   = if ($existingProvider -eq "external") { T "setup.current_mark_model" @{model=$existingModel} } else { "" }
Write-Host "  1. $(T 'setup.provider_local')  ⭐$localMark" -ForegroundColor White
Write-Host "  2. $(T 'setup.provider_commercial')$commMark" -ForegroundColor White
Write-Host "  3. External Model (OpenAI-compatible API)$extMark" -ForegroundColor White
Write-Host ""
$defaultProviderChoice = if ($existingProvider -eq "external") { "3" } elseif ($existingIsCommercial) { "2" } else { "1" }
do {
    $providerChoice = Safe-ReadHost (T "setup.provider_prompt" @{default=$defaultProviderChoice})
    if (-not $providerChoice) { $providerChoice = $defaultProviderChoice }
    if ($providerChoice -notin @("1","2","3")) {
        Write-Host (T "setup.provider_invalid") -ForegroundColor Yellow
    }
} while ($providerChoice -notin @("1","2","3"))

$useCommercial = ($providerChoice -eq "2")
$useExternal   = ($providerChoice -eq "3")

if ($useCommercial) {
    # ── Commercial Model Selection ──
    Write-Host ""
    Write-Host "  ┌──────────────────────────────────────────────┐" -ForegroundColor Magenta
    Write-Host "  │  $(T 'setup.commercial_title')                │" -ForegroundColor Magenta
    Write-Host "  └──────────────────────────────────────────────┘" -ForegroundColor Magenta
    # Commercial 모델 목록 (models.json에서 로드)
    $cmModelsJsonPath = Join-Path $ProjectDir "models.json"
    $cmModelsData = [System.IO.File]::ReadAllText($cmModelsJsonPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $cmEntries = @()
    foreach ($entry in $cmModelsData.commercial) {
        $cmEntries += $entry
    }

    # 모델 목록 표시
    $cmModelItems = @()
    foreach ($entry in $cmEntries) {
        if ($entry.group) {
            Write-Host "  ── $($entry.group) ──" -ForegroundColor Yellow
        } else {
            $num = "$($entry.num).".PadRight(4)
            $label = "$($entry.label)".PadRight(22)
            Write-Host "  $num$label($($entry.model))" -ForegroundColor White
            $cmModelItems += $entry
        }
    }
    Write-Host ""

    # 기존 모델 번호 찾기
    $defaultCmChoice = "2"
    $modelToNum = @{}
    foreach ($item in $cmModelItems) {
        $modelToNum[$item.model] = $item.num
    }
    $cmMaxNum = ($cmModelItems | Measure-Object -Property num -Maximum).Maximum
    if ($existingModel -and $modelToNum.ContainsKey($existingModel)) {
        $defaultCmChoice = $modelToNum[$existingModel]
        Write-Host (T "setup.current_setting" @{model=$existingModel}) -ForegroundColor DarkCyan
    }

    do {
        $cmChoice = Safe-ReadHost (T "setup.commercial_prompt")
        if (-not $cmChoice) { $cmChoice = $defaultCmChoice }
        $cmValid = $cmChoice -match '^\d+$' -and [int]$cmChoice -ge 1 -and [int]$cmChoice -le [int]$cmMaxNum
        if (-not $cmValid) {
            Write-Host (T "setup.commercial_invalid") -ForegroundColor Yellow
        }
    } while (-not $cmValid)

    # 선택한 모델 찾기 (models.json에서 로드)
    $selectedCm = $cmModelItems | Where-Object { $_.num -eq $cmChoice }
    if (-not $selectedCm) { $selectedCm = $cmModelItems[0] }
    $cmProvider = $selectedCm.provider
    $cmBaseUrl  = $selectedCm.base_url
    $cmModel    = $selectedCm.model
    $cmLabel    = $selectedCm.label

    Write-Host (T "setup.commercial_selected" @{model=$cmLabel; detail=$cmModel}) -ForegroundColor Cyan
    Write-Host ""
    # API key — 기존 값 있으면 마스킹 표시
    $maskedKey = ""
    if ($existingApiKey -and $existingApiKey -ne "" -and $existingApiKey -ne "none") {
        $maskedKey = $existingApiKey.Substring(0, [Math]::Min(8, $existingApiKey.Length)) + "..."
        Write-Host (T "setup.current_apikey" @{key=$maskedKey}) -ForegroundColor DarkCyan
        $apiKeyInput = Safe-ReadHost (T "setup.apikey_prompt_keep")
        if (-not $apiKeyInput) {
            $apiKeyInput = $existingApiKey
            Write-Host (T "setup.keep_existing_key") -ForegroundColor Green
        }
    } else {
        $apiKeyInput = Safe-ReadHost (T "setup.commercial_apikey_prompt")
        if (-not $apiKeyInput) {
            Write-Host (T "setup.commercial_apikey_empty") -ForegroundColor Yellow
        }
    }

    # ── config.json 업데이트 (Commercial) ──
    $configPath = Join-Path $ProjectDir "config.json"
    $config = $null
    if (Test-Path $configPath) {
        try { $config = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json }
        catch { Write-Host (T "setup.config_corrupt") -ForegroundColor Yellow; Remove-Item $configPath -Force }
    }
    if (-not $config) {
        $config = [PSCustomObject]@{
            llm = [PSCustomObject]@{ provider = "local"; providers = [PSCustomObject]@{ local = [PSCustomObject]@{ base_url = "http://10.0.2.2:11434/v1"; api_key = "none"; model_name = "" } }; engine = "ollama" }
            vm = [PSCustomObject]@{ ssh_port = 2222; disk_size = "10G"; memory = "1024"; cpus = 2 }
            server = [PSCustomObject]@{ host = "0.0.0.0"; port = 3000; api_key = "CHANGE_ME_$(Get-Random -Minimum 100000 -Maximum 999999)" }
            assistant = [PSCustomObject]@{ name = "Xoul"; language = "ko" }
            google = [PSCustomObject]@{ enabled = $false }
            clients = [PSCustomObject]@{ telegram = [PSCustomObject]@{ enabled = $false; bot_token = "" } }
            search = [PSCustomObject]@{ tavily_api_key = "" }
            web = [PSCustomObject]@{ backend_url = "https://www.xoulai.net"; frontend_url = "https://www.xoulai.net" }
        }
        Write-Host (T "setup.config_created") -ForegroundColor Gray
    }
    $config.assistant | Add-Member -NotePropertyName "language" -NotePropertyValue $selectedLang -Force
    $config.llm | Add-Member -NotePropertyName "provider" -NotePropertyValue $cmProvider -Force
    $config.llm | Add-Member -NotePropertyName "engine" -NotePropertyValue "commercial" -Force
    # Commercial API는 자체 기본값 사용 → temperature/top_p/max_tokens 제거
    foreach ($removeProp in @("temperature", "top_p", "max_tokens", "presence_penalty", "model_path", "ollama_model")) {
        if ($config.llm.PSObject.Properties[$removeProp]) {
            $config.llm.PSObject.Properties.Remove($removeProp)
        }
    }
    if (-not $config.llm.PSObject.Properties["providers"]) {
        $config.llm | Add-Member -NotePropertyName "providers" -NotePropertyValue ([PSCustomObject]@{}) -Force
    }
    $providerObj = [PSCustomObject]@{
        base_url = $cmBaseUrl
        api_key = if ($apiKeyInput) { $apiKeyInput.Trim() } else { "" }
        model_name = $cmModel
    }
    $config.llm.providers | Add-Member -NotePropertyName $cmProvider -NotePropertyValue $providerObj -Force
    $jsonOut = $config | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($configPath, $jsonOut, (New-Object System.Text.UTF8Encoding $false))
    Write-Host (T "setup.config_updated") -ForegroundColor Green

} elseif ($useExternal) {
    # ── External Model (OpenAI-compatible API) ──
    Write-Host ""
    Write-Host "  ┌──────────────────────────────────────────────┐" -ForegroundColor Magenta
    Write-Host "  │  External Model Setup                        │" -ForegroundColor Magenta
    Write-Host "  └──────────────────────────────────────────────┘" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "  OpenAI-compatible API endpoint information:" -ForegroundColor Gray
    Write-Host "  (e.g. vLLM, LM Studio, text-generation-webui, etc.)" -ForegroundColor DarkGray
    Write-Host ""

    # 기존 외부 모델 설정 복원
    $extUrl = ""; $extModel = ""; $extToken = ""
    if ($existingProvider -eq "external" -and $existingCfg -and $existingCfg.llm.providers.PSObject.Properties["external"]) {
        $extUrl   = $existingCfg.llm.providers.external.base_url
        $extModel = $existingCfg.llm.providers.external.model_name
        $extToken = $existingCfg.llm.providers.external.api_key
    }

    # 1) API URL
    if ($extUrl) { Write-Host (T "setup.ext_current_url" @{url=$extUrl}) -ForegroundColor DarkCyan }
    $inputUrl = Safe-ReadHost "  1) API URL (e.g. http://192.168.0.10:8000/v1)"
    if (-not $inputUrl -and $extUrl) { $inputUrl = $extUrl; Write-Host (T "setup.ext_keep_url") -ForegroundColor Green }
    if (-not $inputUrl) { $inputUrl = "http://localhost:8000/v1"; Write-Host "  → default: $inputUrl" -ForegroundColor Gray }

    # 2) Model Name
    if ($extModel) { Write-Host (T "setup.ext_current_model" @{model=$extModel}) -ForegroundColor DarkCyan }
    $inputModel = Safe-ReadHost "  2) Model Name (e.g. my-model)"
    if (-not $inputModel -and $extModel) { $inputModel = $extModel; Write-Host (T "setup.ext_keep_model") -ForegroundColor Green }
    if (-not $inputModel) { $inputModel = "default"; Write-Host "  → default: $inputModel" -ForegroundColor Gray }

    # 3) API Token
    if ($extToken -and $extToken -ne "none") {
        $masked = $extToken.Substring(0, [Math]::Min(8, $extToken.Length)) + "..."
        Write-Host (T "setup.ext_current_token" @{token=$masked}) -ForegroundColor DarkCyan
    }
    $inputToken = Safe-ReadHost (T "setup.ext_token_prompt")
    if (-not $inputToken -and $extToken) { $inputToken = $extToken; Write-Host (T "setup.ext_keep_token") -ForegroundColor Green }
    if (-not $inputToken) { $inputToken = "none" }

    Write-Host ""
    Write-Host "  ✅ External Model: $inputModel @ $inputUrl" -ForegroundColor Cyan

    # ── config.json 업데이트 (External) ──
    $configPath = Join-Path $ProjectDir "config.json"
    $config = $null
    if (Test-Path $configPath) {
        try { $config = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json }
        catch { Write-Host (T "setup.config_corrupt") -ForegroundColor Yellow; Remove-Item $configPath -Force }
    }
    if (-not $config) {
        $config = [PSCustomObject]@{
            llm = [PSCustomObject]@{ provider = "local"; providers = [PSCustomObject]@{ local = [PSCustomObject]@{ base_url = "http://10.0.2.2:11434/v1"; api_key = "none"; model_name = "" } }; engine = "ollama" }
            vm = [PSCustomObject]@{ ssh_port = 2222; disk_size = "10G"; memory = "1024"; cpus = 2 }
            server = [PSCustomObject]@{ host = "0.0.0.0"; port = 3000; api_key = "CHANGE_ME_$(Get-Random -Minimum 100000 -Maximum 999999)" }
            assistant = [PSCustomObject]@{ name = "Xoul"; language = "ko" }
            google = [PSCustomObject]@{ enabled = $false }
            clients = [PSCustomObject]@{ telegram = [PSCustomObject]@{ enabled = $false; bot_token = "" } }
            search = [PSCustomObject]@{ tavily_api_key = "" }
            web = [PSCustomObject]@{ backend_url = "https://www.xoulai.net"; frontend_url = "https://www.xoulai.net" }
        }
        Write-Host (T "setup.config_created") -ForegroundColor Gray
    }
    $config.assistant | Add-Member -NotePropertyName "language" -NotePropertyValue $selectedLang -Force
    $config.llm | Add-Member -NotePropertyName "provider" -NotePropertyValue "external" -Force
    $config.llm | Add-Member -NotePropertyName "engine" -NotePropertyValue "commercial" -Force
    # temperature/top_p 등 제거 (외부 API 기본값 사용)
    foreach ($removeProp in @("temperature", "top_p", "max_tokens", "presence_penalty", "model_path", "ollama_model")) {
        if ($config.llm.PSObject.Properties[$removeProp]) {
            $config.llm.PSObject.Properties.Remove($removeProp)
        }
    }
    if (-not $config.llm.PSObject.Properties["providers"]) {
        $config.llm | Add-Member -NotePropertyName "providers" -NotePropertyValue ([PSCustomObject]@{}) -Force
    }
    $providerObj = [PSCustomObject]@{
        base_url   = $inputUrl.Trim()
        api_key    = $inputToken.Trim()
        model_name = $inputModel.Trim()
    }
    $config.llm.providers | Add-Member -NotePropertyName "external" -NotePropertyValue $providerObj -Force
    $jsonOut = $config | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($configPath, $jsonOut, (New-Object System.Text.UTF8Encoding $false))
    Write-Host (T "setup.config_updated") -ForegroundColor Green

} else {
    # ── Local Model Selection ──

# ── 모델 선택 ──
Write-Host ""
Write-Host "  ┌──────────────────────────────────────────────┐" -ForegroundColor Cyan
Write-Host "  │  $(T 'setup.model_select_title')" -ForegroundColor Cyan
Write-Host "  └──────────────────────────────────────────────┘" -ForegroundColor Cyan
Write-Host ""
# VRAM별 모델 추천 로직
$vramGB = 0
if ($gpuMode -and $nv) {
    try {
        $vramMB = [regex]::Match($nv, '(\d+)\s*MiB').Groups[1].Value
        $vramGB = [math]::Floor([int]$vramMB / 1024)
    } catch { $vramGB = 0 }
}

# 모델 목록 (models.json — single source of truth)
$modelsJsonPath = Join-Path $ProjectDir "models.json"
$modelsData = [System.IO.File]::ReadAllText($modelsJsonPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$models = @()
foreach ($m in $modelsData.local) {
    $models += @{
        num=$m.num; name=$m.name; vram=$m.vram; scoreNum=$m.score;
        speed=$m.speed; quality=$m.quality; descKey=$m.descKey; tag=$m.tag;
        temp=$m.temp; topP=$m.topP; ctx=$m.ctx; maxTokens=$m.maxTokens
    }
}
$modelCount = $models.Count
$modelNums = 1..$modelCount | ForEach-Object { "$_" }

# VRAM에 맞는 모델 중 가장 높은 점수 → 동점이면 낮은 VRAM 추천
$recommendedNum = "1"  # 기본: 가장 작은 모델
$bestScore = -1
$bestVram = 999
foreach ($m in $models) {
    if ($vramGB -ge $m.vram) {
        if ($m.scoreNum -gt $bestScore -or ($m.scoreNum -eq $bestScore -and $m.vram -lt $bestVram)) {
            $bestScore = $m.scoreNum
            $bestVram = $m.vram
            $recommendedNum = $m.num
        }
    }
}

Write-Host (T "setup.model_vram_low") -ForegroundColor DarkGray
if ($vramGB -gt 0) {
    Write-Host "  VRAM: ${vramGB}GB" -ForegroundColor Magenta
}
Write-Host "  ─────────────────────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  #   Model                          VRAM        Speed        Quality  Description" -ForegroundColor DarkCyan
Write-Host "  ─────────────────────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
foreach ($m in $models) {
    $num = "  $($m.num).".PadRight(5)
    $name = "$($m.name)".PadRight(30)
    $vramLabel = "(~$($m.vram)GB)".PadRight(12)
    $speedLabel = "$($m.speed)".PadRight(13)
    $qualityLabel = "$($m.quality)".PadRight(9)
    $desc = T "setup.$($m.descKey)"
    $line = "${num}${name}${vramLabel}${speedLabel}${qualityLabel}${desc}"
    if ($m.num -eq $recommendedNum) {
        Write-Host "$line  ⭐" -ForegroundColor Yellow
    } else {
        $color = if ($vramGB -ge $m.vram) { "White" } else { "DarkGray" }
        Write-Host $line -ForegroundColor $color
    }
}
Write-Host "  ─────────────────────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host (T "setup.model_vram_high") -ForegroundColor DarkGray
Write-Host ""

do {
    $modelChoice = Safe-ReadHost (T "setup.model_prompt")
    if (-not $modelChoice) { $modelChoice = $recommendedNum }
    if ($modelChoice -notin $modelNums) {
        Write-Host (T "setup.model_invalid") -ForegroundColor Yellow
    }
} while ($modelChoice -notin $modelNums)

# 모델별 최적 설정 (models.json에서 로드)
$selectedModel = $models | Where-Object { $_.num -eq $modelChoice }
if (-not $selectedModel) { $selectedModel = $models[0] }
$modelName = $selectedModel.name
$modelFile = ($selectedModel.name -replace '[:\s/]', '-').ToLower() + ".gguf"
$modelUrl = ""
$modelTemp = $selectedModel.temp
$modelTopP = $selectedModel.topP
$modelCtx = $selectedModel.ctx
$modelMaxTokens = $selectedModel.maxTokens
Write-Host (T "setup.model_selected" @{model=$selectedModel.name; detail="Ollama, VRAM ~$($selectedModel.vram)GB, ctx=$([math]::Floor($selectedModel.ctx/1024))K"}) -ForegroundColor Cyan

$modelPath = Join-Path $modelsDir $modelFile

# 모델 다운로드 / pull
if ($engineType -eq "ollama") {
    # Ollama 모델 태그 (models.json에서 로드)
    $ollamaModel = $selectedModel.tag
    Write-Host (T "setup.model_pulling" @{model=$ollamaModel}) -ForegroundColor Yellow
    $pullSuccess = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        ollama pull $ollamaModel
        if ($LASTEXITCODE -eq 0) {
            $pullSuccess = $true
            break
        }
        Write-Host "  ⚠ Pull failed (attempt $attempt/3), retrying..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
    }
    if ($pullSuccess) {
        Write-Host (T "setup.model_downloaded") -ForegroundColor Green
    } else {
        Write-Host "  ❌ Model download failed after 3 attempts. Run 'ollama pull $ollamaModel' manually." -ForegroundColor Red
    }
} else {
    # GGUF 직접 다운로드
    if (Test-Path $modelPath) {
        Write-Host (T "setup.model_exists" @{file=$modelFile}) -ForegroundColor Green
    } else {
        Write-Host (T "setup.model_downloading" @{file=$modelFile}) -ForegroundColor Yellow
        try {
            & curl.exe -L -o $modelPath --progress-bar $modelUrl
            if (Test-Path $modelPath) {
                $size = (Get-Item $modelPath).Length / 1GB
                Write-Host (T "setup.model_download_done" @{size=[math]::Round($size, 1)}) -ForegroundColor Green
            } else {
                Write-Host (T "setup.model_download_fail") -ForegroundColor Red
            }
        } catch {
            Write-Host (T "setup.model_download_error" @{error=$_}) -ForegroundColor Red
        }
    }
}

# config.json 업데이트
$configPath = Join-Path $ProjectDir "config.json"
$config = $null
if (Test-Path $configPath) {
    try {
        $config = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    } catch {
        Write-Host (T "setup.config_corrupt") -ForegroundColor Yellow
        Remove-Item $configPath -Force
    }
}
if (-not $config) {
    # 기본 config.json 생성
    $config = [PSCustomObject]@{
        llm = [PSCustomObject]@{ provider = "local"; providers = [PSCustomObject]@{ local = [PSCustomObject]@{ base_url = "http://10.0.2.2:11434/v1"; api_key = "none"; model_name = "" } }; engine = "ollama" }
        vm = [PSCustomObject]@{ ssh_port = 2222; disk_size = "10G"; memory = "1024"; cpus = 2 }
        server = [PSCustomObject]@{ host = "0.0.0.0"; port = 3000; api_key = "CHANGE_ME_$(Get-Random -Minimum 100000 -Maximum 999999)" }
        assistant = [PSCustomObject]@{ name = "Xoul"; language = "ko" }
        google = [PSCustomObject]@{ enabled = $false }
        clients = [PSCustomObject]@{ telegram = [PSCustomObject]@{ enabled = $false; bot_token = "" } }
        search = [PSCustomObject]@{ tavily_api_key = "" }
        web = [PSCustomObject]@{ backend_url = "https://www.xoulai.net"; frontend_url = "https://www.xoulai.net" }
    }
    Write-Host (T "setup.config_created") -ForegroundColor Gray
}

# 엔진 타입 저장
# 선택한 언어를 config에도 저장
$config.assistant | Add-Member -NotePropertyName "language" -NotePropertyValue $selectedLang -Force
$config.llm | Add-Member -NotePropertyName "engine" -NotePropertyValue $engineType -Force
$config.llm | Add-Member -NotePropertyName "provider" -NotePropertyValue "local" -Force

# providers.local 업데이트 (Ollama: 11434, llama.cpp: 8080)
$llmPort = if ($engineType -eq "ollama") { "11434" } else { "8080" }
if ($config.llm.PSObject.Properties["providers"]) {
    $config.llm.providers.local | Add-Member -NotePropertyName "base_url" -NotePropertyValue "http://10.0.2.2:$llmPort/v1" -Force
    $config.llm.providers.local | Add-Member -NotePropertyName "api_key" -NotePropertyValue "none" -Force
    # Ollama는 풀 태그가 model_name이어야 함
    if ($engineType -eq "ollama") {
        $config.llm.providers.local | Add-Member -NotePropertyName "model_name" -NotePropertyValue $ollamaModel -Force
    } else {
        $config.llm.providers.local | Add-Member -NotePropertyName "model_name" -NotePropertyValue $modelName -Force
    }
} else {
    $config.llm | Add-Member -NotePropertyName "provider" -NotePropertyValue "local" -Force
    $config.llm | Add-Member -NotePropertyName "providers" -NotePropertyValue @{
        local = @{
            base_url = "http://10.0.2.2:$llmPort/v1"
            api_key = "none"
            model_name = $(if ($engineType -eq "ollama") { $ollamaModel } else { $modelName })
        }
    } -Force
}
# model_path, temperature, etc.
$config.llm | Add-Member -NotePropertyName "model_path" -NotePropertyValue "llm/models/$modelFile" -Force
$config.llm | Add-Member -NotePropertyName "temperature" -NotePropertyValue $modelTemp -Force
$config.llm | Add-Member -NotePropertyName "top_p" -NotePropertyValue $modelTopP -Force
$config.llm | Add-Member -NotePropertyName "max_tokens" -NotePropertyValue $modelMaxTokens -Force
# llm_server 설정
if ($config.PSObject.Properties["ollama"]) {
    $config.PSObject.Properties.Remove("ollama")
}
$config | Add-Member -NotePropertyName "llm_server" -NotePropertyValue @{
    gpu = $gpuMode
    ngl = $ngl
    ctx_size = $modelCtx
    engine = $engineType
} -Force
# Ollama 모델 태그 저장 (launcher에서 사용)
if ($engineType -eq "ollama") {
    $config.llm | Add-Member -NotePropertyName "ollama_model" -NotePropertyValue $ollamaModel -Force
}
# 요약/추출 전용 경량 모델 (CPU에서 실행, 메인 GPU 모델과 충돌 방지)
$config.llm | Add-Member -NotePropertyName "summarize_model" -NotePropertyValue "qwen2.5:3b-cpu" -Force
$jsonOut = $config | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($configPath, $jsonOut, (New-Object System.Text.UTF8Encoding $false))
Write-Host (T "setup.config_updated") -ForegroundColor Green
}  # end Local model selection
# ─────────────────────────────────────────────
# 3. QEMU 설치
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.step3_title") -ForegroundColor Yellow

$qemuOk = $false
foreach ($path in @("qemu-system-x86_64", "$env:ProgramFiles\qemu\qemu-system-x86_64.exe")) {
    try {
        if (Test-Path $path) { $qemuOk = $true; break }
        $found = Get-Command $path -ErrorAction SilentlyContinue
        if ($found) { $qemuOk = $true; break }
    } catch {}
}

if ($qemuOk) {
    Write-Host (T "setup.qemu_found") -ForegroundColor Green
} else {
    Write-Host (T "setup.qemu_installing") -ForegroundColor Yellow
    winget install SoftwareFreedomConservancy.QEMU --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    Write-Host (T "setup.qemu_installed") -ForegroundColor Green
}

# WHPX 하드웨어 가속 테스트 (실제 QEMU 프로브)
$qemuExePath = $null
foreach ($p in @("qemu-system-x86_64", "$env:ProgramFiles\qemu\qemu-system-x86_64.exe")) {
    try {
        if (Test-Path $p) { $qemuExePath = $p; break }
        $cmd = Get-Command $p -ErrorAction SilentlyContinue
        if ($cmd) { $qemuExePath = $cmd.Source; break }
    } catch {}
}
if ($qemuExePath) {
    Write-Host "  🔍 Testing hardware acceleration (WHPX)..." -ForegroundColor Gray
    try {
        # stderr를 임시 파일로 캡처 (Start-Process는 파이프 불가)
        $stderrFile = [System.IO.Path]::GetTempFileName()
        $whpxProc = Start-Process -FilePath $qemuExePath `
            -ArgumentList "-accel whpx -machine q35 -display none -m 64" `
            -WindowStyle Hidden -PassThru -RedirectStandardError $stderrFile
        # 3초 대기: WHPX 실패 시 즉시 종료, 성공 시 계속 실행됨
        $exited = $whpxProc.WaitForExit(3000)
        if ($exited) {
            # 빠르게 종료 = WHPX 실패
            $stderrContent = Get-Content $stderrFile -Raw -ErrorAction SilentlyContinue
            if ($stderrContent -match "(?i)error|not supported|failed|not available") {
                Write-Host "  ℹ️  WHPX not available → VM will use software emulation (TCG)" -ForegroundColor Yellow
                Write-Host "     (Performance: ~10-20% of native. Enable Hyper-V for better performance)" -ForegroundColor DarkGray
            } else {
                # 종료했지만 에러 없음 → 성공으로 간주
                Write-Host "  ✅ WHPX hardware acceleration available! (3~5x faster VM)" -ForegroundColor Green
            }
        } else {
            # 3초 후에도 실행 중 = WHPX 작동 (VM이 정상 부팅됨)
            Write-Host "  ✅ WHPX hardware acceleration available! (3~5x faster VM)" -ForegroundColor Green
            Stop-Process -Id $whpxProc.Id -Force -ErrorAction SilentlyContinue
        }
        Remove-Item $stderrFile -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Host "  ℹ️  Acceleration test skipped" -ForegroundColor Yellow
    }
}

# ─────────────────────────────────────────────
# 4. Python 3.12 venv + 패키지
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.step4_title") -ForegroundColor Yellow

$venvPythonExe = Join-Path $venvPath "Scripts\python.exe"
$venvCfg = Join-Path $venvPath "pyvenv.cfg"
if ((Test-Path $venvPythonExe) -and (Test-Path $venvCfg)) {
    Write-Host (T "setup.venv_exists") -ForegroundColor Green
} else {
    # 깨진 venv 정리 후 재생성
    if (Test-Path $venvPath) {
        Write-Host "  ⚠ Broken venv detected, recreating..." -ForegroundColor Yellow
        Remove-Item $venvPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    & $python312 -m venv $venvPath
    Write-Host (T "setup.venv_created") -ForegroundColor Green
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
$venvVer = & $venvPython --version 2>&1
Write-Host "  📌 $venvVer" -ForegroundColor Cyan

& $venvPython -m pip install --upgrade pip 2>&1 | Out-Null
& $venvPython -m pip install openai
$desktopReqPath = Join-Path $ProjectDir "desktop\requirements.txt"
if (Test-Path $desktopReqPath) {
    Write-Host "  📦 Installing Desktop packages (PyQt6, etc.)..." -ForegroundColor Yellow
    & $venvPython -m pip install -r $desktopReqPath
}
Write-Host (T "setup.packages_installed") -ForegroundColor Green

# ─────────────────────────────────────────────
# 5. VM 설정 (이미지 있으면 복사, 없으면 새로 생성)
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.step5_title") -ForegroundColor Yellow

# 배포 패키지에 포함된 이미지 확인 (여러 위치 탐색)
$prebuiltImage = $null
$searchPaths = @(
    (Join-Path $ProjectDir "vm\xoul.qcow2"),          # 프로젝트 내 vm/
    (Join-Path $ProjectDir "xoul.qcow2"),              # 프로젝트 루트
    (Join-Path (Split-Path $ProjectDir) "xoul.qcow2")  # 상위 폴더 (dist/ 배포 시)
)
foreach ($sp in $searchPaths) {
    if (Test-Path $sp) { $prebuiltImage = $sp; break }
}
$vmTargetDir = "C:\xoul\vm"

# 프로젝트 경로에 비-ASCII가 있으면 안전 경로 사용
$useAsciiPath = $false
try { [System.Text.Encoding]::ASCII.GetBytes($ProjectDir) | Out-Null } catch { $useAsciiPath = $true }
if ($ProjectDir -match '[^\x00-\x7F]' -or $ProjectDir -match ' ') { $useAsciiPath = $true }

if ($useAsciiPath) {
    $vmDir = $vmTargetDir
} else {
    $vmDir = Join-Path $ProjectDir "vm"
}
New-Item -ItemType Directory -Path $vmDir -Force | Out-Null

$targetImage = Join-Path $vmDir "xoul.qcow2"
$installedMarker = Join-Path $vmDir ".installed"

if (Test-Path $installedMarker) {
    Write-Host "  ✅ VM already installed" -ForegroundColor Green
} elseif ($prebuiltImage -and (Test-Path $prebuiltImage) -and ($prebuiltImage -ne $targetImage)) {
    # 배포 이미지 → VM 디렉토리로 복사
    Write-Host "  📦 Pre-built VM image found! Copying... (this may take a few minutes)" -ForegroundColor Cyan
    Copy-Item $prebuiltImage $targetImage -Force
    "installed" | Set-Content $installedMarker
    Write-Host "  ✅ VM image installed (setup skipped!)" -ForegroundColor Green
} elseif (Test-Path $targetImage) {
    "installed" | Set-Content $installedMarker
    Write-Host "  ✅ VM image found" -ForegroundColor Green
} else {
    # 이미지 없음 → cloud-init으로 새로 생성
    Write-Host "  ⚠ No pre-built image. Creating from cloud image (10~15 min)..." -ForegroundColor Yellow
    & $venvPython (Join-Path $ProjectDir "vm_manager.py") setup
}

# ─────────────────────────────────────────────
# 6. 사용자 프로필 + 이메일 설정
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.step6_title") -ForegroundColor Yellow

$configPath = Join-Path $ProjectDir "config.json"
$config = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

# user/email 하위 객체가 없으면 생성
if (-not $config.PSObject.Properties["user"]) {
    $config | Add-Member -NotePropertyName "user" -NotePropertyValue ([PSCustomObject]@{ name=""; location=""; timezone="Asia/Seoul" })
} else {
    # user 객체는 있지만 하위 속성이 빠진 경우 추가
    foreach ($prop in @("name", "location", "timezone")) {
        if (-not $config.user.PSObject.Properties[$prop]) {
            $defaultVal = if ($prop -eq "timezone") { "Asia/Seoul" } else { "" }
            $config.user | Add-Member -NotePropertyName $prop -NotePropertyValue $defaultVal -Force
        }
    }
}
if (-not $config.PSObject.Properties["email"]) {
    $config | Add-Member -NotePropertyName "email" -NotePropertyValue ([PSCustomObject]@{ enabled=$false; address=""; app_password=""; imap_host="imap.gmail.com"; smtp_host="smtp.gmail.com" })
}

# 사용자 이름
$currentName = $config.user.name
if ($currentName) {
    Write-Host (T "setup.current_name" @{name=$currentName}) -ForegroundColor Gray
    $userName = Safe-ReadHost (T "setup.name_prompt_keep")
    if (-not $userName) { $userName = $currentName }
} else {
    $userName = Safe-ReadHost (T "setup.name_prompt_new")
}
if ($userName) {
    $config.user.name = $userName
    Write-Host (T "setup.name_set" @{name=$userName}) -ForegroundColor Green
}

# 에이전트 이름
$currentAgentName = $config.assistant.name
if ($currentAgentName) {
    Write-Host (T "setup.current_agent_name" @{name=$currentAgentName}) -ForegroundColor Gray
    $agentName = Safe-ReadHost (T "setup.agent_name_prompt_keep")
    if (-not $agentName) { $agentName = $currentAgentName }
} else {
    $agentName = Safe-ReadHost (T "setup.agent_name_prompt_new")
    if (-not $agentName) { $agentName = "Xoul" }
}
$config.assistant.name = $agentName
Write-Host (T "setup.agent_name_set" @{name=$agentName}) -ForegroundColor Green

# 위치
$currentLoc = $config.user.location
if ($currentLoc) {
    Write-Host (T "setup.current_location" @{location=$currentLoc}) -ForegroundColor Gray
    $userLoc = Safe-ReadHost (T "setup.location_prompt_keep")
    if (-not $userLoc) { $userLoc = $currentLoc }
} else {
    $userLoc = Safe-ReadHost (T "setup.location_prompt_new")
}
if ($userLoc) {
    $config.user.location = $userLoc
    Write-Host (T "setup.location_set" @{location=$userLoc}) -ForegroundColor Green
}

# 이메일 설정 (선택)
Write-Host ""
Write-Host "  ┌──────────────────────────────────────────────┐" -ForegroundColor Cyan
Write-Host "  │  $(T 'setup.email_title')" -ForegroundColor Cyan
Write-Host "  └──────────────────────────────────────────────┘" -ForegroundColor Cyan
Write-Host ""

$currentEmail = $config.email.address
if ($currentEmail) {
    Write-Host (T "setup.current_email" @{email=$currentEmail}) -ForegroundColor Gray
    do {
        $setupEmail = Safe-ReadHost (T "setup.email_change_prompt")
        if ($setupEmail -notin @("y", "n", "")) {
            Write-Host (T "setup.email_yn_invalid") -ForegroundColor Yellow
        }
    } while ($setupEmail -notin @("y", "n", ""))
} else {
    do {
        $setupEmail = Safe-ReadHost (T "setup.email_setup_prompt")
        if ($setupEmail -notin @("y", "n", "")) {
            Write-Host (T "setup.email_yn_invalid") -ForegroundColor Yellow
        }
    } while ($setupEmail -notin @("y", "n", ""))
}

if ($setupEmail -eq "y") {
    Write-Host ""
    Write-Host "  ┌──────────────────────────────────────────────────────┐" -ForegroundColor Magenta
    Write-Host "  │  $(T 'setup.email_guide_title')" -ForegroundColor Magenta
    Write-Host "  └──────────────────────────────────────────────────────┘" -ForegroundColor Magenta
    Write-Host ""

    Write-Host (T "setup.email_step1") -ForegroundColor Yellow
    Write-Host (T "setup.email_step1_desc") -ForegroundColor White
    Write-Host (T "setup.email_step1_example") -ForegroundColor Gray
    Write-Host ""
    $emailAddr = Safe-ReadHost (T "setup.email_addr_prompt")

    if ($emailAddr) {
        $config.email.address = $emailAddr
        $config.user | Add-Member -NotePropertyName "email" -NotePropertyValue $emailAddr -Force 2>$null
        Write-Host (T "setup.email_addr_set" @{email=$emailAddr}) -ForegroundColor Green
        Write-Host ""

        Write-Host (T "setup.email_step2") -ForegroundColor Yellow
        Write-Host (T "setup.email_step2_desc") -ForegroundColor White
        Write-Host (T "setup.email_step2_1") -ForegroundColor White
        Write-Host (T "setup.email_step2_2" @{email=$emailAddr}) -ForegroundColor White
        Write-Host (T "setup.email_step2_3") -ForegroundColor White
        Write-Host (T "setup.email_step2_skip") -ForegroundColor Gray
        Write-Host ""
        Safe-ReadHost (T "setup.email_open_browser")
        Start-Process "https://myaccount.google.com/security"
        Write-Host (T "setup.email_browser_opened") -ForegroundColor Cyan
        Safe-ReadHost (T "setup.email_step2_done")

        Write-Host ""
        Write-Host (T "setup.email_step3") -ForegroundColor Yellow
        Write-Host (T "setup.email_step3_1") -ForegroundColor White
        Write-Host (T "setup.email_step3_2" @{email=$emailAddr}) -ForegroundColor White
        Write-Host (T "setup.email_step3_3") -ForegroundColor White
        Write-Host (T "setup.email_step3_4_prefix") -NoNewline
        Write-Host (T "setup.email_step3_4_highlight") -ForegroundColor Yellow -NoNewline
        Write-Host (T "setup.email_step3_4_suffix") -ForegroundColor White
        Write-Host (T "setup.email_step3_example") -ForegroundColor Gray
        Write-Host (T "setup.email_step3_warning") -ForegroundColor Red
        Write-Host ""
        Safe-ReadHost (T "setup.email_open_browser")
        Start-Process "https://myaccount.google.com/apppasswords"
        Write-Host (T "setup.email_apppass_opened") -ForegroundColor Cyan
        Write-Host ""

        $appPass = Safe-ReadHost (T "setup.email_apppass_prompt")
        if ($appPass) {
            $config.email.app_password = $appPass.Trim()
            $config.email.enabled = $true
            Write-Host (T "setup.email_done" @{email=$emailAddr}) -ForegroundColor Green
        } else {
            Write-Host (T "setup.email_apppass_empty") -ForegroundColor Yellow
        }
    }
} else {
    if ($currentEmail) {
        Write-Host (T "setup.email_keep_existing") -ForegroundColor Gray
    } else {
        Write-Host (T "setup.email_skipped") -ForegroundColor Gray
    }
}

# ─────────────────────────────────────────────
# 6.4.5. 웹 검색 설정 (Tavily API, 선택)
# ─────────────────────────────────────────────
Write-Host ""
Write-Host "  ┌──────────────────────────────────────────────┐" -ForegroundColor Cyan
Write-Host "  │  $(T 'setup.search_title')                    │" -ForegroundColor Cyan
Write-Host "  └──────────────────────────────────────────────┘" -ForegroundColor Cyan

# 기존 키 확인
$currentTavilyKey = $null
if ($config.PSObject.Properties["search"] -and $config.search.PSObject.Properties["tavily_api_key"]) {
    $currentTavilyKey = $config.search.tavily_api_key
}
if ($currentTavilyKey) {
    $maskedTavily = $currentTavilyKey.Substring(0, [Math]::Min(10, $currentTavilyKey.Length)) + "..."
    Write-Host "  $(T 'setup.search_current_key'): $maskedTavily" -ForegroundColor Gray
}

Write-Host ""
Write-Host "  $(T 'setup.search_desc')" -ForegroundColor White
Write-Host "  $(T 'setup.search_free')" -ForegroundColor Green
Write-Host ""
if ($currentTavilyKey) {
    $searchSetup = Safe-ReadHost "  $(T 'setup.search_prompt') (y/n, default: n)"
    $doSearchSetup = ($searchSetup -eq "y" -or $searchSetup -eq "Y")
} else {
    $searchSetup = Safe-ReadHost "  $(T 'setup.search_prompt') (y/n, default: y)"
    $doSearchSetup = ($searchSetup -ne "n" -and $searchSetup -ne "N")
}
if ($doSearchSetup) {
    Write-Host ""
    Write-Host "  $(T 'setup.search_guide_1')" -ForegroundColor Cyan
    Write-Host "  $(T 'setup.search_guide_2')" -ForegroundColor White
    Write-Host "  $(T 'setup.search_guide_3')" -ForegroundColor White
    Write-Host ""
    Safe-ReadHost "  $(T 'setup.search_open_browser')"
    Start-Process "https://app.tavily.com/home"
    Write-Host "  $(T 'setup.search_browser_opened')" -ForegroundColor Cyan
    Write-Host ""

    $tavilyKey = Safe-ReadHost "  $(T 'setup.search_key_prompt')"
    if ($tavilyKey) {
        if (-not $config.PSObject.Properties["search"]) {
            $config | Add-Member -NotePropertyName "search" -NotePropertyValue ([PSCustomObject]@{ tavily_api_key = "" }) -Force
        }
        $config.search | Add-Member -NotePropertyName "tavily_api_key" -NotePropertyValue $tavilyKey.Trim() -Force
        Write-Host "  ✅ $(T 'setup.search_key_saved')" -ForegroundColor Green
    } else {
        Write-Host "  $(T 'setup.search_key_empty')" -ForegroundColor Yellow
    }
} else {
    if ($currentTavilyKey) {
        Write-Host "  $(T 'setup.search_keep_existing')" -ForegroundColor Gray
    } else {
        Write-Host "  $(T 'setup.search_skipped')" -ForegroundColor Gray
    }
}

# ─────────────────────────────────────────────
# 6.5. 텔레그램 봇 설정
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.tg_title") -ForegroundColor Yellow

$currentTgToken = $null
if ($config.PSObject.Properties["clients"] -and $config.clients.PSObject.Properties["telegram"]) {
    $currentTgToken = $config.clients.telegram.bot_token
}
if ($currentTgToken) {
    Write-Host (T "setup.tg_current_token" @{token=$currentTgToken.Substring(0, [Math]::Min(10, $currentTgToken.Length))}) -ForegroundColor Gray
}

$tgSetup = Safe-ReadHost (T "setup.tg_prompt")
if ($tgSetup -eq "y" -or $tgSetup -eq "Y") {
    Write-Host ""
    Write-Host (T "setup.tg_guide_title") -ForegroundColor Cyan
    Write-Host (T "setup.tg_guide_1") -ForegroundColor White
    Write-Host (T "setup.tg_guide_2") -ForegroundColor White
    Write-Host (T "setup.tg_guide_3") -ForegroundColor White
    Write-Host ""
    
    $botToken = Safe-ReadHost (T "setup.tg_token_prompt")
    if ($botToken) {
        if (-not $config.PSObject.Properties["clients"]) {
            $config | Add-Member -NotePropertyName "clients" -NotePropertyValue ([PSCustomObject]@{}) -Force
        }
        if (-not $config.clients.PSObject.Properties["telegram"]) {
            $config.clients | Add-Member -NotePropertyName "telegram" -NotePropertyValue ([PSCustomObject]@{ enabled = $false; bot_token = ""; chat_id = "" }) -Force
        }
        # chat_id 속성이 없으면 추가
        if (-not $config.clients.telegram.PSObject.Properties["chat_id"]) {
            $config.clients.telegram | Add-Member -NotePropertyName "chat_id" -NotePropertyValue "" -Force
        }
        $config.clients.telegram.bot_token = $botToken.Trim()
        $config.clients.telegram.enabled = $true
        
        Write-Host ""
        Write-Host (T "setup.tg_token_saved") -ForegroundColor Green
        
        # ── 자동 chat_id 감지 ──
        Write-Host ""
        Write-Host (T "setup.tg_chatid_detecting") -ForegroundColor Cyan
        Write-Host (T "setup.tg_chatid_send_msg") -ForegroundColor Yellow
        Write-Host (T "setup.tg_chatid_bot_link") -NoNewline
        # 봇 이름 가져오기
        try {
            $botInfo = Invoke-RestMethod -Uri "https://api.telegram.org/bot$($botToken.Trim())/getMe" -Method Get -ErrorAction Stop
            if ($botInfo.ok) {
                Write-Host "$($botInfo.result.username)" -ForegroundColor Green
            } else {
                Write-Host (T "setup.tg_chatid_bot_fail") -ForegroundColor Red
            }
        } catch {
            Write-Host (T "setup.tg_chatid_bot_fail") -ForegroundColor Red
        }
        Write-Host ""
        Safe-ReadHost (T "setup.tg_chatid_press_enter")
        
        $detectedChatId = $null
        $maxRetries = 3
        for ($retry = 0; $retry -lt $maxRetries; $retry++) {
            try {
                $updates = Invoke-RestMethod -Uri "https://api.telegram.org/bot$($botToken.Trim())/getUpdates" -Method Get -ErrorAction Stop
                if ($updates.ok -and $updates.result.Count -gt 0) {
                    # 가장 최근 메시지의 chat_id
                    $lastUpdate = $updates.result[-1]
                    if ($lastUpdate.message) {
                        $detectedChatId = $lastUpdate.message.chat.id
                        $chatName = $lastUpdate.message.chat.first_name
                        if ($lastUpdate.message.chat.username) {
                            $chatName = "$chatName (@$($lastUpdate.message.chat.username))"
                        }
                    }
                    break
                }
            } catch {}
            if ($retry -lt ($maxRetries - 1)) {
                Write-Host (T "setup.tg_chatid_waiting" @{n="$($retry + 1)"; max="$maxRetries"}) -ForegroundColor Gray
                Start-Sleep -Seconds 3
            }
        }
        
        if ($detectedChatId) {
            Write-Host (T "setup.tg_chatid_detected" @{id="$detectedChatId"; name=$chatName}) -ForegroundColor Green
            $config.clients.telegram.chat_id = "$detectedChatId"
            # 읽은 업데이트를 확인(acknowledge)하여 폴링 서비스 시작 시 중복 처리 방지
            try {
                $ackOffset = $lastUpdate.update_id + 1
                Invoke-RestMethod -Uri "https://api.telegram.org/bot$($botToken.Trim())/getUpdates?offset=$ackOffset" -Method Get -ErrorAction SilentlyContinue | Out-Null
            } catch {}
        } else {
            Write-Host (T "setup.tg_chatid_fail") -ForegroundColor Yellow
            Write-Host (T "setup.tg_chatid_1") -ForegroundColor White
            Write-Host (T "setup.tg_chatid_2") -ForegroundColor White
            Write-Host ""
            $chatId = Safe-ReadHost (T "setup.tg_chatid_prompt")
            if ($chatId) {
                $config.clients.telegram.chat_id = $chatId.Trim()
                Write-Host (T "setup.tg_chatid_set" @{id=$chatId}) -ForegroundColor Green
            }
        }
    } else {
        Write-Host (T "setup.tg_token_empty") -ForegroundColor Yellow
    }
} else {
    if ($currentTgToken) {
        Write-Host (T "setup.tg_keep_existing") -ForegroundColor Gray
    } else {
        Write-Host (T "setup.tg_skipped") -ForegroundColor Gray
    }
}

# config.json 저장
$jsonOut = $config | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($configPath, $jsonOut, (New-Object System.Text.UTF8Encoding $false))
Write-Host (T "setup.config_updated") -ForegroundColor Green

# ─────────────────────────────────────────────
# 6.6. Discord 봇 설정 (선택)
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.dc_title") -ForegroundColor Cyan

$discordChoice = Safe-ReadHost (T "setup.dc_prompt")
if ($discordChoice -eq "y" -or $discordChoice -eq "Y") {
    Write-Host ""
    Safe-ReadHost (T "setup.dc_open_browser")
    Start-Process "https://discord.com/developers/applications"

    Write-Host ""
    Write-Host (T "setup.dc_guide_title") -ForegroundColor Yellow
    Write-Host (T "setup.dc_guide_1") -ForegroundColor Gray
    Write-Host (T "setup.dc_guide_2") -ForegroundColor Gray
    Write-Host (T "setup.dc_guide_3") -ForegroundColor Yellow
    Write-Host (T "setup.dc_guide_4") -ForegroundColor Gray
    Write-Host (T "setup.dc_guide_5") -ForegroundColor Gray
    Write-Host (T "setup.dc_guide_6") -ForegroundColor Gray
    Write-Host (T "setup.dc_guide_7") -ForegroundColor Gray
    Write-Host ""

    $dcToken = Safe-ReadHost (T "setup.dc_token_prompt")
    if ($dcToken) {
        $dcChannel = Safe-ReadHost (T "setup.dc_channel_prompt")

        $config = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $config.PSObject.Properties["clients"]) {
            $config | Add-Member -NotePropertyName "clients" -NotePropertyValue ([PSCustomObject]@{}) -Force
        }
        if (-not $config.clients.PSObject.Properties["discord"]) {
            $config.clients | Add-Member -NotePropertyName "discord" -NotePropertyValue @{} -Force
        }
        $config.clients.discord = @{
            enabled = $true
            bot_token = $dcToken
            channel_id = if ($dcChannel) { $dcChannel } else { "" }
        }
        $config | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
        Write-Host (T "setup.dc_done") -ForegroundColor Green
        Write-Host (T "setup.dc_mention_hint") -ForegroundColor Gray
    }
} else {
    Write-Host (T "setup.dc_skipped") -ForegroundColor Gray
}

# ─────────────────────────────────────────────
# 6.7. Slack 봇 설정 (선택)
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.sl_title") -ForegroundColor Cyan

$slackChoice = Safe-ReadHost (T "setup.sl_prompt")
if ($slackChoice -eq "y" -or $slackChoice -eq "Y") {
    Write-Host ""
    Safe-ReadHost (T "setup.sl_open_browser")
    Start-Process "https://api.slack.com/apps"

    Write-Host ""
    Write-Host (T "setup.sl_guide_title") -ForegroundColor Yellow
    Write-Host (T "setup.sl_guide_1") -ForegroundColor Gray
    Write-Host (T "setup.sl_guide_2") -ForegroundColor Gray
    Write-Host (T "setup.sl_guide_3") -ForegroundColor Gray
    Write-Host (T "setup.sl_guide_3_scopes") -ForegroundColor Yellow
    Write-Host (T "setup.sl_guide_4") -ForegroundColor Gray
    Write-Host (T "setup.sl_guide_5") -ForegroundColor Gray
    Write-Host (T "setup.sl_guide_5_events") -ForegroundColor Yellow
    Write-Host ""

    $slBotToken = Safe-ReadHost (T "setup.sl_bot_token_prompt")
    $slAppToken = Safe-ReadHost (T "setup.sl_app_token_prompt")
    if ($slBotToken -and $slAppToken) {
        $slChannel = Safe-ReadHost (T "setup.sl_channel_prompt")

        $config = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $config.PSObject.Properties["clients"]) {
            $config | Add-Member -NotePropertyName "clients" -NotePropertyValue ([PSCustomObject]@{}) -Force
        }
        if (-not $config.clients.PSObject.Properties["slack"]) {
            $config.clients | Add-Member -NotePropertyName "slack" -NotePropertyValue @{} -Force
        }
        $config.clients.slack = @{
            enabled = $true
            bot_token = $slBotToken
            app_token = $slAppToken
            channel_id = if ($slChannel) { $slChannel } else { "" }
        }
        $config | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
        Write-Host (T "setup.sl_done") -ForegroundColor Green
        Write-Host (T "setup.sl_mention_hint") -ForegroundColor Gray
    }
} else {
    Write-Host (T "setup.sl_skipped") -ForegroundColor Gray
}

# ─────────────────────────────────────────────
# 6.8. GitHub 연동 (선택)
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.gh_title") -ForegroundColor Cyan

$currentGhToken = $null
$config = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.PSObject.Properties["github"] -and $config.github.token) {
    $currentGhToken = $config.github.token
    Write-Host (T "setup.gh_current_token" @{token=$currentGhToken.Substring(0, [Math]::Min(10, $currentGhToken.Length))}) -ForegroundColor Gray
}

$ghSetup = Safe-ReadHost (T "setup.gh_prompt")
if ($ghSetup -eq "y" -or $ghSetup -eq "Y") {
    Write-Host ""
    Write-Host (T "setup.gh_guide_title") -ForegroundColor Yellow
    Write-Host (T "setup.gh_guide_1") -ForegroundColor White
    Write-Host (T "setup.gh_guide_2") -ForegroundColor White
    Write-Host (T "setup.gh_guide_3") -ForegroundColor White
    Write-Host (T "setup.gh_guide_4") -ForegroundColor White
    Write-Host (T "setup.gh_guide_5") -ForegroundColor Yellow
    Write-Host (T "setup.gh_guide_6") -ForegroundColor White
    Write-Host (T "setup.gh_guide_warning") -ForegroundColor Red
    Write-Host ""
    Safe-ReadHost (T "setup.email_open_browser")
    Start-Process "https://github.com/settings/tokens/new"
    Write-Host ""

    $ghUsername = Safe-ReadHost (T "setup.gh_username_prompt")
    $ghToken = Safe-ReadHost (T "setup.gh_token_prompt")
    if ($ghToken) {
        if (-not $config.PSObject.Properties["github"]) {
            $config | Add-Member -NotePropertyName "github" -NotePropertyValue ([PSCustomObject]@{}) -Force
        }
        $config.github = [PSCustomObject]@{
            token = $ghToken.Trim()
            username = if ($ghUsername) { $ghUsername.Trim() } else { "" }
        }
        $config | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
        Write-Host (T "setup.gh_done" @{username=$ghUsername}) -ForegroundColor Green
    } else {
        Write-Host (T "setup.gh_token_empty") -ForegroundColor Yellow
    }
} else {
    if ($currentGhToken) {
        Write-Host (T "setup.gh_keep_existing") -ForegroundColor Gray
    } else {
        Write-Host (T "setup.gh_skipped") -ForegroundColor Gray
    }
}

# ─────────────────────────────────────────────
# 6.9. VM 시스템 패키지 설치 (Chromium 등)
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.vm_packages_title") -ForegroundColor Yellow

Write-Host (T "setup.vm_checking") -ForegroundColor Gray
$vmScript = Join-Path $ProjectDir "vm_manager.py"
$vmStartProc = Start-Process -FilePath $venvPython -ArgumentList "`"$vmScript`" start" -WindowStyle Hidden -PassThru
# VM 시작은 비동기 — 아래 SSH 연결 루프가 대기 처리
Start-Sleep -Seconds 3

$sshKey = Join-Path $ProjectDir "vm\xoul_key"
if (-not (Test-Path $sshKey) -and (Test-Path "C:\xoul\vm\xoul_key")) {
    $sshKey = "C:\xoul\vm\xoul_key"
}
$sshPort = if ($config.PSObject.Properties["vm"] -and $config.vm.ssh_port) { [int]$config.vm.ssh_port } else { 2222 }

$sshOpts = @("-p", $sshPort, "-i", "$sshKey", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", "-o", "UserKnownHostsFile=NUL", "-o", "ConnectTimeout=5")
$connected = $false
$oldErr = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
for ($i = 0; $i -lt 5; $i++) {
    $vmCheck = & ssh @sshOpts root@127.0.0.1 "echo OK" 2>&1
    if ($vmCheck -match "OK") {
        $connected = $true
        break
    }
    Start-Sleep -Seconds 5
}
$ErrorActionPreference = $oldErr

if ($connected) {
    $sshOpts2 = @("-p", $sshPort, "-i", "$sshKey", "-o", "StrictHostKeyChecking=no", "-o", "LogLevel=ERROR", "-o", "UserKnownHostsFile=NUL")
    $chromResult = & ssh @sshOpts2 root@127.0.0.1 "which chromium-browser > /dev/null 2>&1 && dpkg -l fonts-noto-cjk > /dev/null 2>&1 && echo 'ALREADY_INSTALLED' || (apt-get update -qq && apt-get install -y -qq chromium-browser fonts-noto-cjk > /dev/null 2>&1 && echo 'INSTALLED')" 2>&1
    if ($chromResult -match "ALREADY") {
        Write-Host (T "setup.chromium_exists") -ForegroundColor Green
    } else {
        Write-Host (T "setup.chromium_installed") -ForegroundColor Green
    }
} else {
    Write-Host (T "setup.vm_ssh_fail") -ForegroundColor Yellow
}


# ─────────────────────────────────────────────
# 7. VM에 에이전트 코드 배포
# ─────────────────────────────────────────────
Write-Host ""
Write-Host (T "setup.step7_title") -ForegroundColor Yellow
& (Join-Path $ProjectDir "scripts\deploy.ps1")
Write-Host "========================================" -ForegroundColor Green
Write-Host (T "setup.setup_done") -ForegroundColor Green
if ($CPU) {
    Write-Host (T "setup.mode_cpu") -ForegroundColor Gray
} else {
    Write-Host (T "setup.mode_gpu") -ForegroundColor Magenta
}
Write-Host "  Python: $venvVer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# 자동 재시작 (config 변경사항 적용을 위해 필수)
Write-Host ""
Write-Host (T "setup.restart_msg") -ForegroundColor Cyan

# Desktop 앱이 실행 중이면 종료
$desktopProc = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessName -eq "python" -and $_.MainWindowTitle -match "Xoul"
}
if (-not $desktopProc) {
    # MainWindowTitle이 없을 수 있으니 커맨드라인으로도 확인
    try {
        $desktopProc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match "desktop[\\/]main\.py" }
    } catch {}
}
$desktopWasRunning = $false
if ($desktopProc) {
    $desktopWasRunning = $true
    Write-Host (T "setup.desktop_stopping") -ForegroundColor Yellow
    $desktopProc | ForEach-Object {
        try {
            if ($_ -is [System.Diagnostics.Process]) {
                $_ | Stop-Process -Force -ErrorAction SilentlyContinue
            } else {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
    Start-Sleep -Seconds 2
    Write-Host (T "setup.desktop_stopped") -ForegroundColor Green
}

# Launcher 실행 (VM + Ollama + 서버)
Write-Host ""
Write-Host (T "setup.launch_starting") -ForegroundColor Cyan
$launcherScript = Join-Path $ProjectDir "scripts\launcher.ps1"
& $launcherScript

# ─────────────────────────────────────────────
# 디폴트 스킬 Import (첫 설치 시)
# ─────────────────────────────────────────────
$defaultsMarker = Join-Path $ProjectDir ".defaults_imported"
$importScript = Join-Path $ProjectDir "scripts\import_defaults.ps1"
if (-not (Test-Path $defaultsMarker) -and (Test-Path $importScript)) {
    & $importScript
    "imported" | Set-Content $defaultsMarker -Encoding UTF8
}

# Desktop 앱이 실행 중이었으면 자동 재시작
if ($desktopWasRunning) {
    Write-Host ""
    Write-Host (T "setup.desktop_restarting") -ForegroundColor Cyan
    $desktopMain = Join-Path $ProjectDir "desktop\main.py"
    if (Test-Path $desktopMain) {
        Start-Process -FilePath $venvPython -ArgumentList "`"$desktopMain`"" -WindowStyle Minimized
        Write-Host (T "setup.desktop_restarted") -ForegroundColor Green
    }
}

# ─────────────────────────────────────────────
# Windows 시작 시 자동 실행 등록
# ─────────────────────────────────────────────
$startupDir = [System.Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "Xoul.lnk"
$autoStartBat = Join-Path $ProjectDir "scripts\auto_start.bat"
$xoulBat = Join-Path $ProjectDir "desktop\Xoul.bat"

if (Test-Path $autoStartBat) {
    $alreadyRegistered = Test-Path $shortcutPath
    if ($alreadyRegistered) {
        Write-Host (T "setup.autostart_already") -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host (T "setup.autostart_prompt") -ForegroundColor Cyan
        do {
            $autoStartChoice = Safe-ReadHost (T "setup.autostart_yn")
            if (-not $autoStartChoice) { $autoStartChoice = "y" }
            if ($autoStartChoice -notin @("y", "n")) {
                Write-Host (T "setup.autostart_invalid") -ForegroundColor Yellow
            }
        } while ($autoStartChoice -notin @("y", "n"))

        if ($autoStartChoice -eq "y") {
            try {
                $ws = New-Object -ComObject WScript.Shell
                $sc = $ws.CreateShortcut($shortcutPath)
                $sc.TargetPath = $autoStartBat
                $sc.WorkingDirectory = $ProjectDir
                $sc.Description = "Xoul Auto Start (VM + Desktop)"
                $sc.WindowStyle = 7  # Minimized
                $sc.Save()
                [System.Runtime.InteropServices.Marshal]::ReleaseComObject($ws) | Out-Null
                Write-Host (T "setup.autostart_done") -ForegroundColor Green
            } catch {
                Write-Host (T "setup.autostart_fail" @{error=$_.Exception.Message}) -ForegroundColor Yellow
            }
        } else {
            Write-Host (T "setup.autostart_skipped") -ForegroundColor Gray
        }
    }
}

# Desktop 앱이 실행 중이 아니었으면 지금 시작
if (-not $desktopWasRunning -and (Test-Path $xoulBat)) {
    Write-Host (T "setup.desktop_restarting") -ForegroundColor Cyan
    Start-Process -FilePath $xoulBat -WorkingDirectory $ProjectDir -WindowStyle Minimized
    Write-Host (T "setup.desktop_restarted") -ForegroundColor Green
}

Write-Host ""

} catch {
    Write-Host "" 
    Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor Red
    Write-Host "  ║  $(T 'setup.error_title')              ║" -ForegroundColor Red
    Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  Location: $($_.InvocationInfo.ScriptName):$($_.InvocationInfo.ScriptLineNumber)" -ForegroundColor DarkGray
    Write-Host ""
    Read-Host (T "setup.error_press_enter")
    exit 1
}
