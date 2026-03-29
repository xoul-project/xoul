#Requires -Version 5.1
<#
.SYNOPSIS
    Xoul 배포용 ZIP 파일 생성
.DESCRIPTION
    다른 사용자가 setup_env.ps1만 실행하면 바로 쓸 수 있도록
    필요한 파일만 패키징합니다.
#>

$ErrorActionPreference = "Stop"

# ─── 설정 ───
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Version     = Get-Date -Format "yyyyMMdd"
$OutName     = "xoul.zip"
$DistDir     = Join-Path $ProjectRoot "dist"
if (-not (Test-Path $DistDir)) { New-Item -ItemType Directory -Path $DistDir -Force | Out-Null }
$OutPath     = Join-Path $DistDir $OutName
$TempDir     = Join-Path $env:TEMP "xoul_release_$Version"

Write-Host ""
Write-Host "  📦 Xoul 배포 패키지 생성" -ForegroundColor Cyan
Write-Host "  ─────────────────────────"
Write-Host "  출력: $OutName"
Write-Host ""

# ─── VM 이미지 확인 (별도 배포) ───
$vmImage = Join-Path $DistDir "xoul.qcow2"
if (Test-Path $vmImage) {
    $imgSize = [math]::Round((Get-Item $vmImage).Length / 1GB, 1)
    Write-Host "  ✅ dist/xoul.qcow2 (${imgSize}GB) — 별도 배포" -ForegroundColor Green
} else {
    Write-Host "  ⚠ dist/xoul.qcow2 없음 — 필요 시 build_image.ps1 실행" -ForegroundColor Yellow
}

# ─── 기존 파일 정리 ───
if (Test-Path $TempDir) { Remove-Item $TempDir -Recurse -Force }
if (Test-Path $OutPath) { Remove-Item $OutPath -Force }
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

# ─── 포함할 파일 목록 ───
$IncludeFiles = @(
    # 핵심 코드
    "server.py",
    "assistant_agent.py",
    "llm_client.py",
    "vm_manager.py",
    "terminal_client.py",
    "tool_call_parser.py",
    "browser_daemon.py",
    "google_auth.py",
    "telegram_client.py",
    "discord_client.py",
    "slack_client.py",
    "requirements.txt",
    "README.md",

    # i18n (다국어 지원)
    "i18n.ps1",
    "i18n.py",

    # 스크립트
    "scripts\setup_env.ps1",
    "scripts\deploy.ps1",
    "scripts\launcher.ps1",
    "scripts\reset.ps1",
    "scripts\uninstall.ps1",
    "scripts\install.bat",
    "scripts\import_defaults.ps1"
)

$IncludeDirs = @(
    "tools"
    "desktop"
    "services"
    "locales"
)

# ─── 파일 복사 ───
Write-Host "  🔧 파일 복사 중..." -ForegroundColor Yellow

# 개별 파일 복사
foreach ($f in $IncludeFiles) {
    $src = Join-Path $ProjectRoot $f
    if (Test-Path $src) {
        $dest = Join-Path $TempDir $f
        $destDir = Split-Path -Parent $dest
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
        Copy-Item $src $dest -Force
        Write-Host "    ✅ $f" -ForegroundColor DarkGray
    } else {
        Write-Host "    ⚠ $f (없음, 건너뜀)" -ForegroundColor DarkYellow
    }
}

# 디렉토리 복사 (__pycache__ 제외)
foreach ($d in $IncludeDirs) {
    $src = Join-Path $ProjectRoot $d
    if (Test-Path $src) {
        $dest = Join-Path $TempDir $d
        Copy-Item $src $dest -Recurse -Force
        # __pycache__ 제거
        Get-ChildItem $dest -Directory -Filter "__pycache__" -Recurse | Remove-Item -Recurse -Force
        $count = (Get-ChildItem $dest -File -Recurse).Count
        Write-Host "    ✅ $d/ ($count개 파일)" -ForegroundColor DarkGray
    }
}

# share/ 빈 디렉토리 생성
New-Item -ItemType Directory -Path (Join-Path $TempDir "share") -Force | Out-Null
Write-Host "    ✅ share/ (빈 디렉토리)" -ForegroundColor DarkGray

# vm/xoul.qcow2 — ZIP에 넣지 않음 (PowerShell ZIP 2GB 제한)
# dist/xoul.qcow2로 별도 배포, setup_env.ps1이 자동 감지
$vmImage = Join-Path $DistDir "xoul.qcow2"
if (Test-Path $vmImage) {
    $imgSize = [math]::Round((Get-Item $vmImage).Length / 1GB, 1)
    Write-Host "    ✅ xoul.qcow2 (${imgSize}GB — dist/ 별도 배포)" -ForegroundColor Green
} else {
    Write-Host "    ⚠ xoul.qcow2 없음 — setup 시 cloud image에서 생성 (느림)" -ForegroundColor Yellow
}

# ─── config.json 템플릿 생성 (개인정보 제거) ───
Write-Host "  🔧 config.json 템플릿 생성..." -ForegroundColor Yellow

$configTemplate = @'
{
    "assistant": {
        "name": "Xoul"
    },
    "user": {
        "name": ""
    },
    "llm": {
        "provider": "local",
        "providers": {
            "local": {
                "type": "ollama",
                "model_name": "hf.co/Qwen/Qwen3-8B-GGUF:Q8_0",
                "base_url": "http://localhost:11434/v1"
            }
        }
    },
    "email": {
        "enabled": false,
        "address": "",
        "app_password": "",
        "imap_host": "imap.gmail.com",
        "smtp_host": "smtp.gmail.com"
    },
    "google": {
        "enabled": false
    },
    "web": {
        "backend_url": "http://ec2-15-165-31-212.ap-northeast-2.compute.amazonaws.com",
        "frontend_url": "http://ec2-15-165-31-212.ap-northeast-2.compute.amazonaws.com"
    }
}
'@

$configTemplate | Set-Content (Join-Path $TempDir "config.json") -Encoding UTF8

# ─── UTF-8 BOM 인코딩 보장 (.ps1 파일) ───
Write-Host "  🔧 인코딩 변환 (UTF-8 BOM)..." -ForegroundColor Yellow
$utf8Bom = New-Object System.Text.UTF8Encoding $true
Get-ChildItem $TempDir -Recurse -Include "*.ps1","*.py","*.service","*.md","*.txt" | ForEach-Object {
    $content = [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($_.FullName, $content, $utf8Bom)
}
Write-Host "    ✅ 모든 텍스트 파일 UTF-8 BOM 적용" -ForegroundColor DarkGray

# ─── ZIP 생성 ───
Write-Host "  🔧 ZIP 압축 중..." -ForegroundColor Yellow

Compress-Archive -Path "$TempDir\*" -DestinationPath $OutPath -Force

# install.bat을 dist/에 복사
$installBat = Join-Path $ProjectRoot "scripts\install.bat"
if (Test-Path $installBat) {
    Copy-Item $installBat (Join-Path $DistDir "install.bat") -Force
    Write-Host "    ✅ install.bat → dist/" -ForegroundColor DarkGray
}

# ─── 정리 ───
Remove-Item $TempDir -Recurse -Force

# ─── 결과 ───
$size = [math]::Round((Get-Item $OutPath).Length / 1KB, 1)
$fileCount = $IncludeFiles.Count + ($IncludeDirs | ForEach-Object {
    (Get-ChildItem (Join-Path $ProjectRoot $_) -File -Recurse -Exclude "__pycache__").Count
} | Measure-Object -Sum).Sum + 2  # +2 for config.json template + share/

Write-Host ""
Write-Host "  ═══════════════════════════════" -ForegroundColor Green
Write-Host "  ✅ 배포 패키지 생성 완료!" -ForegroundColor Green
Write-Host "  ─────────────────────────────"
Write-Host "  폴더: dist/" -ForegroundColor White
Write-Host "  파일: $OutName + install.bat" -ForegroundColor White
Write-Host "  크기: ${size}KB" -ForegroundColor White
Write-Host "  ═══════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  📖 사용법: dist/ 폴더를 배포 → install.bat 실행" -ForegroundColor Cyan
Write-Host ""
