#Requires -Version 5.1
<#
.SYNOPSIS
    Xoul 배포용 ZIP 파일 생성
.DESCRIPTION
    다른 사용자가 setup_env.ps1만 실행하면 바로 쓸 수 있도록
    필요한 파일만 패키징합니다.
#>

$ErrorActionPreference = "Stop"
Add-Type -Assembly System.IO.Compression.FileSystem

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

# ─── 제외 목록 (Blacklist) ───
# 여기에 없는 파일/디렉토리는 모두 배포에 포함됩니다.
# 새 파일 추가 시 자동으로 빌드에 포함!
$ExcludeDirs = @(
    ".git"
    ".gitignore"
    ".vscode"
    ".idea"
    ".gemini"
    ".agents"
    "__pycache__"
    ".venv"
    "venv"
    "vm"
    "dist"
    "logs"
    "share"
    "workspace"
    "tests"
    "res"
    "llm"
    # 별도 레포
    "arena_server"
    "web_service"
    "xoul-store"
)

$ExcludeFiles = @(
    # 비밀/사용자 설정
    "config.json"
    "credentials.json"
    "token.json"
    ".env"
    ".defaults_imported"
    "server_checkpoint.txt"
    # 임시/로그
    "gitlog.txt"
    "korean_strings.csv"
    # 빌드 스크립트 자체
    "scripts\build_release.ps1"
    "scripts\build_image.ps1"
)

$ExcludePatterns = @(
    "*.pyc"
    "*.pyo"
    "*.zip"
    "*.log"
    "*.bak"
    "*.db"
    "tmp_*.*"
    "temp_*.*"
)

# ─── 프로젝트 전체 복사 후 제외 항목 삭제 ───
Write-Host "  🔧 파일 복사 중..." -ForegroundColor Yellow

# 1) 전체 복사 (robocopy: 빠르고 안정적)
$robocopyExclDirs = $ExcludeDirs
$robocopyExclFiles = $ExcludePatterns + $ExcludeFiles
& robocopy $ProjectRoot $TempDir /E /NFL /NDL /NJH /NJS /NC /NS /NP `
    /XD $robocopyExclDirs `
    /XF $robocopyExclFiles | Out-Null

# 2) 남아 있을 수 있는 __pycache__ 정리
Get-ChildItem $TempDir -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 3) 복사된 파일 목록 출력
$copiedDirs = Get-ChildItem $TempDir -Directory | ForEach-Object {
    $count = (Get-ChildItem $_.FullName -File -Recurse -ErrorAction SilentlyContinue).Count
    Write-Host "    ✅ $($_.Name)/ ($count개 파일)" -ForegroundColor DarkGray
}
$copiedRootFiles = Get-ChildItem $TempDir -File | ForEach-Object {
    Write-Host "    ✅ $($_.Name)" -ForegroundColor DarkGray
}

# 4) share/ 빈 디렉토리 생성
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
        "backend_url": "https://www.xoulai.net",
        "frontend_url": "https://www.xoulai.net"
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
$zip = [IO.Compression.ZipFile]::OpenRead($OutPath)
$fileCount = ($zip.Entries | Where-Object { $_.Name -ne "" }).Count
$zip.Dispose()

Write-Host ""
Write-Host "  ═══════════════════════════════" -ForegroundColor Green
Write-Host "  ✅ 배포 패키지 생성 완료!" -ForegroundColor Green
Write-Host "  ─────────────────────────────"
Write-Host "  폴더: dist/" -ForegroundColor White
Write-Host "  파일: $OutName + install.bat" -ForegroundColor White
Write-Host "  파일 수: $fileCount" -ForegroundColor White
Write-Host "  크기: ${size}KB" -ForegroundColor White
Write-Host "  ═══════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  📖 사용법: dist/ 폴더를 배포 → install.bat 실행" -ForegroundColor Cyan
Write-Host ""
