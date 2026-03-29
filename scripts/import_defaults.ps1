# import_defaults.ps1 — Setup 후 디폴트 워크플로우/페르소나/코드 Import
# Usage: .\import_defaults.ps1 [-Lang ko|en]

param(
    [string]$Lang = ""
)

$ErrorActionPreference = "SilentlyContinue"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $ProjectDir "config.json"))) {
    $ProjectDir = Split-Path -Parent $PSScriptRoot
}

# ─── config.json 읽기 ───
$configPath = Join-Path $ProjectDir "config.json"
if (-not (Test-Path $configPath)) {
    Write-Host "  ⚠ config.json not found, skipping defaults import" -ForegroundColor Yellow
    exit 0
}

$config = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$port = 3000
$apiKey = ""
if ($config.server) {
    $port = $config.server.port
    $apiKey = $config.server.api_key
}

# 언어 결정
if (-not $Lang) {
    $Lang = "ko"
    if ($config.assistant -and $config.assistant.language) {
        $Lang = $config.assistant.language
    }
}

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor Cyan
if ($Lang -eq "ko") {
    Write-Host "  ║  📦 디폴트 스킬 설치 중...                    ║" -ForegroundColor Cyan
} else {
    Write-Host "  ║  📦 Installing default skills...              ║" -ForegroundColor Cyan
}
Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ─── 서버 대기 ───
$baseUrl = "http://127.0.0.1:$port"
$headers = @{ "Authorization" = "Bearer $apiKey"; "Content-Type" = "application/json" }

$maxWait = 60
$waited = 0
while ($waited -lt $maxWait) {
    try {
        $resp = Invoke-RestMethod -Uri "$baseUrl/status" -Method Get -TimeoutSec 3 -ErrorAction Stop
        if ($resp) { break }
    } catch {}
    Start-Sleep -Seconds 2
    $waited += 2
}
if ($waited -ge $maxWait) {
    Write-Host "  ⚠ Server not ready, skipping defaults import" -ForegroundColor Yellow
    exit 0
}

# ─── Helper: 다국어 필드에서 값 추출 ───
function Get-Localized {
    param($Value, [string]$FallbackLang = "en")
    if ($null -eq $Value) { return "" }
    if ($Value -is [string]) {
        # JSON 문자열로 인코딩된 딕셔너리인 경우 파싱
        if ($Value.StartsWith("{")) {
            try {
                $parsed = $Value | ConvertFrom-Json
                if ($parsed.$Lang) { return $parsed.$Lang }
                if ($parsed.$FallbackLang) { return $parsed.$FallbackLang }
                return $Value
            } catch { return $Value }
        }
        return $Value
    }
    # PSObject (JSON object)
    if ($Value.PSObject.Properties[$Lang]) { return $Value.$Lang }
    if ($Value.PSObject.Properties[$FallbackLang]) { return $Value.$FallbackLang }
    return $Value.ToString()
}

# ─── Helper: REST API 호출 ───
function Invoke-Import {
    param([string]$Endpoint, [hashtable]$Body)
    try {
        $json = $Body | ConvertTo-Json -Depth 10 -Compress
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        $resp = Invoke-RestMethod -Uri "$baseUrl$Endpoint" -Method Post -Headers $headers -Body $bytes -TimeoutSec 10 -ErrorAction Stop
        return $resp
    } catch {
        $statusCode = 0
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        # 400 = 이미 존재 → 무시
        if ($statusCode -eq 400) { return @{ skipped = $true } }
        return @{ error = $_.Exception.Message }
    }
}

$imported = 0
$skipped = 0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Workflows Import
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$wfDistPath = Join-Path $ProjectDir "xoul-store\dist\workflows.json"
if (Test-Path $wfDistPath) {
    $wfAll = [System.IO.File]::ReadAllText($wfDistPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

    # 대상 워크플로우 IDs
    $targetWfIds = @("wf-001", "wf-003", "wf-006")

    foreach ($wf in $wfAll) {
        if ($wf.id -notin $targetWfIds) { continue }

        $wfName = Get-Localized $wf.name
        $wfDesc = Get-Localized $wf.description

        # steps → prompts 배열 (문자열 or dict)
        $prompts = @()
        foreach ($step in $wf.steps) {
            if ($step.type -eq "code") {
                # 코드 스텝은 dict로 전달
                $prompts += @{ type = "code"; code_name = $step.content; content = $step.content }
            } else {
                # 프롬프트 스텝은 로컬라이즈된 텍스트
                $stepContent = Get-Localized $step.content
                $prompts += $stepContent
            }
        }

        $promptsJson = $prompts | ConvertTo-Json -Depth 5 -Compress
        if ($prompts.Count -eq 1) {
            # 단일 항목이면 배열로 감싸기
            $promptsJson = "[$promptsJson]"
        }

        $body = @{
            name        = $wfName
            description = $wfDesc
            prompts     = $promptsJson
            hint_tools  = if ($wf.hint_tools) { $wf.hint_tools } else { "" }
            schedule    = if ($wf.schedule) { $wf.schedule } else { "" }
        }

        $result = Invoke-Import "/workflow/import" $body
        if ($result.skipped) {
            $skipped++
            Write-Host "  ⏭ $wfName" -ForegroundColor DarkGray
        } elseif ($result.error) {
            Write-Host "  ❌ $wfName : $($result.error)" -ForegroundColor Red
        } else {
            $imported++
            Write-Host "  ✅ Workflow: $wfName" -ForegroundColor Green
        }
    }
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Personas Import
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$pDistPath = Join-Path $ProjectDir "xoul-store\dist\personas.json"
if (Test-Path $pDistPath) {
    $pAll = [System.IO.File]::ReadAllText($pDistPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

    # 대상 페르소나 base IDs (언어별 suffix: -ko, -en)
    $targetPIds = @("p-029", "p-023")

    foreach ($baseId in $targetPIds) {
        $targetId = "$baseId-$Lang"
        $persona = $pAll | Where-Object { $_.id -eq $targetId } | Select-Object -First 1
        if (-not $persona) {
            # 폴백: 다른 언어 시도
            $fallbackId = if ($Lang -eq "ko") { "$baseId-en" } else { "$baseId-ko" }
            $persona = $pAll | Where-Object { $_.id -eq $fallbackId } | Select-Object -First 1
        }
        if (-not $persona) { continue }

        $body = @{
            name        = $persona.name
            description = if ($persona.description) { $persona.description } else { "" }
            prompt      = if ($persona.prompt) { $persona.prompt } else { "" }
            bg_image    = ""
        }

        $result = Invoke-Import "/persona/import" $body
        if ($result.skipped) {
            $skipped++
            Write-Host "  ⏭ $($persona.name)" -ForegroundColor DarkGray
        } elseif ($result.error) {
            Write-Host "  ❌ $($persona.name) : $($result.error)" -ForegroundColor Red
        } else {
            $imported++
            Write-Host "  ✅ Persona: $($persona.name)" -ForegroundColor Green
        }
    }
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Codes Import
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$cDistPath = Join-Path $ProjectDir "xoul-store\dist\codes.json"
if (Test-Path $cDistPath) {
    $cAll = [System.IO.File]::ReadAllText($cDistPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

    # 대상 코드 IDs
    $targetCodeIds = @("crypto-prices", "stock-price")

    foreach ($code in $cAll) {
        if ($code.id -notin $targetCodeIds) { continue }

        $codeName = Get-Localized $code.name
        $codeDesc = Get-Localized $code.description
        $codeContent = if ($code.code) { $code.code } else { "" }
        $codeParams = "[]"
        if ($code.params) {
            $codeParams = $code.params | ConvertTo-Json -Depth 5 -Compress
            if ($code.params.Count -eq 1) {
                $codeParams = "[$codeParams]"
            }
        }

        $body = @{
            name        = $codeName
            description = $codeDesc
            code        = $codeContent
            params      = $codeParams
        }

        $result = Invoke-Import "/code/import" $body
        if ($result.skipped) {
            $skipped++
            Write-Host "  ⏭ $codeName" -ForegroundColor DarkGray
        } elseif ($result.error) {
            Write-Host "  ❌ $codeName : $($result.error)" -ForegroundColor Red
        } else {
            $imported++
            Write-Host "  ✅ Code: $codeName" -ForegroundColor Green
        }
    }
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Write-Host ""
if ($Lang -eq "ko") {
    Write-Host "  📦 완료: ${imported}개 설치, ${skipped}개 이미 존재" -ForegroundColor Cyan
} else {
    Write-Host "  📦 Done: ${imported} installed, ${skipped} already exist" -ForegroundColor Cyan
}
