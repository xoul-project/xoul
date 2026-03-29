#Requires -Version 5.1
<#
.SYNOPSIS
    배포용 클린 VM 이미지 생성
.DESCRIPTION
    현재 VM 이미지를 복사하고, 민감 데이터를 제거한 후
    압축된 배포용 이미지(xoul.qcow2)를 생성합니다.
    
    현재 실행 중인 VM은 잠시 정지 후 복사하고 다시 시작합니다.
.NOTES
    사용법: .\build_image.ps1
    출력: dist\xoul.qcow2
#>

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# ── 설정 ──
$VmDir = "C:\xoul\vm"
if (-not (Test-Path $VmDir)) { $VmDir = Join-Path $ProjectDir "vm" }

$SourceImage = Join-Path $VmDir "xoul.qcow2"
# 이전 이름(ubuntu.qcow2) 호환
if (-not (Test-Path $SourceImage)) {
    $SourceImage = Join-Path $VmDir "ubuntu.qcow2"
}
if (-not (Test-Path $SourceImage)) {
    Write-Host "  ❌ VM 이미지를 찾을 수 없습니다: $VmDir" -ForegroundColor Red
    exit 1
}

$SshKey       = Join-Path $VmDir "xoul_key"
$TempImage    = Join-Path $VmDir "xoul_build_temp.qcow2"
$DistDir      = Join-Path $ProjectDir "dist"
$OutImage     = Join-Path $DistDir "xoul.qcow2"
$TempSshPort  = 2223
$VmPid        = $null

# QEMU 찾기
$qemuExe = Get-Command "qemu-system-x86_64" -ErrorAction SilentlyContinue
if (-not $qemuExe) {
    $qemuExe = Get-Command "$env:ProgramFiles\qemu\qemu-system-x86_64.exe" -ErrorAction SilentlyContinue
}
if (-not $qemuExe) {
    Write-Host "  ❌ QEMU가 설치되어 있지 않습니다" -ForegroundColor Red
    exit 1
}
$qemu = $qemuExe.Source

$qemuImg = Get-Command "qemu-img" -ErrorAction SilentlyContinue
if (-not $qemuImg) {
    $qemuImg = Get-Command "$env:ProgramFiles\qemu\qemu-img.exe" -ErrorAction SilentlyContinue
}
if (-not $qemuImg) {
    Write-Host "  ❌ qemu-img가 설치되어 있지 않습니다" -ForegroundColor Red
    exit 1
}
$qemuImgExe = $qemuImg.Source

# dist 디렉토리
if (-not (Test-Path $DistDir)) { New-Item -ItemType Directory -Path $DistDir -Force | Out-Null }

Write-Host ""
Write-Host "  ═══════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  📦 Xoul 배포용 VM 이미지 생성" -ForegroundColor Cyan
Write-Host "  ═══════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  소스: $SourceImage" -ForegroundColor Gray
Write-Host "  출력: $OutImage" -ForegroundColor Gray
Write-Host ""

# ── 1. 현재 VM 정지 ──
Write-Host "  [1/6] VM 정지 중..." -ForegroundColor Yellow
$venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { $venvPython = "python" }
& $venvPython (Join-Path $ProjectDir "vm_manager.py") stop 2>&1 | Out-Null
Start-Sleep -Seconds 3
Write-Host "  ✅ VM 정지" -ForegroundColor Green

# ── 2. 이미지 복사 ──
Write-Host "  [2/6] 이미지 복사 중... (수 분 소요)" -ForegroundColor Yellow
if (Test-Path $TempImage) { Remove-Item $TempImage -Force }
Copy-Item $SourceImage $TempImage -Force
$copySize = [math]::Round((Get-Item $TempImage).Length / 1GB, 1)
Write-Host "  ✅ 복사 완료 (${copySize}GB)" -ForegroundColor Green

# ── 3. 원본 VM 다시 시작 ──
Write-Host "  [3/6] 원본 VM 재시작..." -ForegroundColor Yellow
& $venvPython (Join-Path $ProjectDir "vm_manager.py") start 2>&1 | Out-Null
Write-Host "  ✅ 원본 VM 재시작 완료" -ForegroundColor Green

# ── 4. 임시 이미지 부팅 + 데이터 정리 ──
Write-Host "  [4/6] 클린 이미지 부팅 (포트 $TempSshPort)..." -ForegroundColor Yellow

$seedIso = Join-Path $VmDir "seed.iso"
$tempArgs = @(
    "-cpu", "max,-x2apic", "-m", "4096", "-smp", "4",
    "-drive", "file=$TempImage,format=qcow2,if=virtio",
    "-netdev", "user,id=n,hostfwd=tcp::${TempSshPort}-:22",
    "-device", "virtio-net-pci,netdev=n",
    "-display", "none"
)
if (Test-Path $seedIso) {
    $tempArgs += @("-cdrom", $seedIso)
}

$tempProc = Start-Process -FilePath $qemu -ArgumentList $tempArgs `
    -WindowStyle Hidden -PassThru

# SSH 대기
Write-Host "  ⏳ SSH 대기..." -NoNewline -ForegroundColor Gray
$sshReady = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try {
        $conn = New-Object System.Net.Sockets.TcpClient
        $conn.Connect("127.0.0.1", $TempSshPort)
        $conn.Close()
        $sshReady = $true
        break
    } catch {}
    Write-Host "." -NoNewline -ForegroundColor Gray
}
Write-Host ""

if (-not $sshReady) {
    Write-Host "  ❌ 임시 VM SSH 연결 실패" -ForegroundColor Red
    Stop-Process -Id $tempProc.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $TempImage -Force -ErrorAction SilentlyContinue
    exit 1
}

# SSH 연결 안정화 대기 (sshd 완전 시작)
Write-Host "  ⏳ sshd 안정화 대기 (30초)..." -ForegroundColor Gray
Start-Sleep -Seconds 30

# 민감 데이터 삭제
Write-Host "  🧹 민감 데이터 삭제 중..." -ForegroundColor Yellow

$cleanCommands = @(
    # 인증/토큰
    "rm -f /root/.git-credentials",
    "rm -f /root/xoul/credentials.json /root/androi/credentials.json",
    # 개인 데이터
    "rm -rf /root/.xoul/memory.db",
    "rm -rf /root/.xoul/logs/",
    "rm -rf /root/.xoul/tmp/",
    "rm -rf /root/.xoul/scheduler/",
    # SSH (새 사용자가 재생성)
    "> /root/.ssh/authorized_keys",
    # 히스토리/캐시
    "rm -f /root/.bash_history",
    "rm -f /root/.python_history",
    "rm -rf /tmp/*.py /tmp/*.sh",
    # 이전 프로젝트 잔여
    "rm -rf /root/androi",
    # 서버 로그
    "rm -f /root/.xoul/server.log",
    # systemd 저널 정리
    "journalctl --vacuum-time=1s 2>/dev/null",
    # 패키지 캐시 정리 (이미지 크기 축소)
    "apt-get clean 2>/dev/null",
    "rm -rf /var/cache/apt/archives/*.deb",
    "rm -rf /var/log/*.log /var/log/*.gz",
    # 완료 확인
    "echo CLEAN_DONE"
)

$cleanScript = $cleanCommands -join " && "

# SSH 재시도 (최대 5회, 15초 간격)
$cleaned = $false
for ($attempt = 1; $attempt -le 5; $attempt++) {
    try {
        if (Test-Path $SshKey) {
            $result = ssh -p $TempSshPort -i "$SshKey" -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=30 -o BatchMode=yes root@127.0.0.1 $cleanScript 2>&1
        } else {
            Write-Host "  ❌ SSH 키를 찾을 수 없습니다: $SshKey" -ForegroundColor Red
            break
        }
        if ($result -match "CLEAN_DONE") {
            $cleaned = $true
            Write-Host "  ✅ 민감 데이터 삭제 완료" -ForegroundColor Green
            break
        } else {
            Write-Host "  ⚠ SSH 시도 $attempt/5 — 응답 없음, 재시도..." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  ⚠ SSH 시도 $attempt/5 — $_" -ForegroundColor Yellow
    }
    if ($attempt -lt 5) {
        Write-Host "  ⏳ 15초 후 재시도..." -ForegroundColor Gray
        Start-Sleep -Seconds 15
    }
}

if (-not $cleaned) {
    Write-Host "  ❌ 데이터 삭제 실패 — SSH 연결 확인 필요" -ForegroundColor Red
    Stop-Process -Id $tempProc.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $TempImage -Force -ErrorAction SilentlyContinue
    exit 1
}

# ── 5. 임시 VM 종료 ──
Write-Host "  [5/6] 임시 VM 종료..." -ForegroundColor Yellow
try {
    ssh -p $TempSshPort -i "$SshKey" -o StrictHostKeyChecking=no -o LogLevel=ERROR root@127.0.0.1 "poweroff" 2>&1 | Out-Null
} catch {}
Start-Sleep -Seconds 5
Stop-Process -Id $tempProc.Id -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Host "  ✅ 임시 VM 종료" -ForegroundColor Green

# ── 6. 이미지 압축 ──
Write-Host "  [6/6] 이미지 압축 중... (수 분 소요)" -ForegroundColor Yellow
if (Test-Path $OutImage) { Remove-Item $OutImage -Force }

& $qemuImgExe convert -c -O qcow2 $TempImage $OutImage

if (Test-Path $OutImage) {
    $outSize = [math]::Round((Get-Item $OutImage).Length / 1GB, 2)
    Write-Host "  ✅ 압축 완료: ${outSize}GB" -ForegroundColor Green
} else {
    Write-Host "  ❌ 압축 실패" -ForegroundColor Red
}

# 임시 파일 삭제
Remove-Item $TempImage -Force -ErrorAction SilentlyContinue

# ── 완료 ──
Write-Host ""
Write-Host "  ═══════════════════════════════════════" -ForegroundColor Green
Write-Host "  ✅ 배포용 VM 이미지 생성 완료!" -ForegroundColor Green
Write-Host "  ─────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  파일: dist\xoul.qcow2" -ForegroundColor White
Write-Host "  크기: ${outSize}GB" -ForegroundColor White
Write-Host "  ═══════════════════════════════════════" -ForegroundColor Green
Write-Host ""
