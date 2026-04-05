#Requires -Version 5.1
# deploy.ps1 — Xoul 에이전트 코드를 VM에 배포
# setup_env.ps1 실행 후 한 번만 실행하면 됩니다.
#
# 사용법: .\deploy.ps1              # 전체 배포
#         .\deploy.ps1 -ConfigOnly  # config.json만 빠르게 배포

param(
    [switch]$ConfigOnly
)

# --config-only / --quick (Unix 스타일) 지원
if ($args -contains "--config-only") { $ConfigOnly = $true }

$ErrorActionPreference = "Continue"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# 이전 VM 호스트 키 충돌 방지
try { ssh-keygen -R "[127.0.0.1]:2222" 2>&1 | Out-Null } catch {}

Write-Host ""
Write-Host "  📦 Xoul Agent Deployment" -ForegroundColor Cyan
Write-Host "  ─────────────────────────" -ForegroundColor DarkGray

# config 로드
$configPath = Join-Path $ProjectDir "config.json"
$config = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$sshPort = 2222
if ($config.vm -and $config.vm.ssh_port) {
    $sshPort = [int]$config.vm.ssh_port
}
# SSH 키 경로 (한글 경로 회피)
$sshKey = Join-Path $ProjectDir "vm\xoul_key"
if (-not (Test-Path $sshKey) -and (Test-Path "C:\xoul\vm\xoul_key")) {
    $sshKey = "C:\xoul\vm\xoul_key"
}
Write-Host "  SSH Port: $sshPort, Key: $sshKey" -ForegroundColor Gray

# ── ConfigOnly 빠른 경로 ──
if ($ConfigOnly) {
    Write-Host "  ⚡ Config-only mode (fast deploy)" -ForegroundColor Yellow
    scp -P $sshPort -i "$sshKey" -o StrictHostKeyChecking=no "$configPath" root@127.0.0.1:/root/xoul/config.json 2>$null
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "sed -i 's/\r$//' /root/xoul/config.json && sed -i '1s/^\xEF\xBB\xBF//' /root/xoul/config.json"
    # LLM URL 업데이트 (localhost → 10.0.2.2) — stdin 파이프 방식으로 따옴표 문제 회피
    $pyFix = @'
import json
f = '/root/xoul/config.json'
c = json.load(open(f))
p = c.get('llm', {}).get('providers', {}).get('local', {})
old = p.get('base_url', '')
if 'localhost' in old or '127.0.0.1' in old:
    p['base_url'] = old.replace('localhost', '10.0.2.2').replace('127.0.0.1', '10.0.2.2')
    json.dump(c, open(f, 'w'), ensure_ascii=False, indent=2)
'@ -replace "`r", ""
    $pyFix | ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "python3 -"
    Write-Host "  ✅ config.json deployed" -ForegroundColor Green
    # 서비스 재시작 (enable 보장 + disabled 상태 자동 수정)
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "systemctl enable xoul 2>/dev/null; systemctl stop xoul; sleep 1; fuser -k 3000/tcp 2>/dev/null; sleep 1; systemctl start xoul"
    Start-Sleep -Seconds 2
    $status = ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "systemctl is-active xoul"
    Write-Host "  ✅ Service restarted ($status)" -ForegroundColor Green
    Write-Host ""
    exit 0
}

# ── 1. VM 실행 확인 (재시도 포함) ──
Write-Host "  🔧 Checking VM connection..." -ForegroundColor Yellow
$connected = $false
for ($i = 0; $i -lt 5; $i++) {
    $vmCheck = ssh -p $sshPort -i "$sshKey" -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o BatchMode=yes root@127.0.0.1 "echo OK" 2>$null
    if ($vmCheck -match "OK") {
        Write-Host "  ✅ VM connection confirmed" -ForegroundColor Green
        $connected = $true
        break
    }
    if ($i -lt 4) {
        Write-Host "  ⏳ Waiting for VM SSH... ($($i+1)/5)" -ForegroundColor Gray
        Start-Sleep -Seconds 10
    }
}
if (-not $connected) {
    Write-Host "  ❌ VM SSH connection failed" -ForegroundColor Red
    Write-Host "     Check if VM is running: python vm_manager.py start" -ForegroundColor Yellow
    exit 1
}

# ── 2. VM에 디렉토리 생성 ──
Write-Host "  🔧 Creating VM directory structure..." -ForegroundColor Yellow
ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "mkdir -p /root/xoul/tools /root/xoul/services /root/xoul/locales /root/xoul/clients /root/.xoul/skills /root/.xoul/sessions /root/workspace /root/share"

# ── 3. API 키 동기화 ──
# HOST config.json에 api_key 없으면 생성 (VM과 동일 키 사용)
if (-not ($config.PSObject.Properties["server"] -and $config.server.api_key)) {
    Write-Host "  🔑 Generating API key..." -ForegroundColor Yellow
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    $apiKey = [Convert]::ToBase64String($bytes) -replace '[+/=]',''
    $apiKey = $apiKey.Substring(0, [math]::Min(32, $apiKey.Length))
    if (-not $config.server) {
        $config | Add-Member -NotePropertyName "server" -NotePropertyValue ([PSCustomObject]@{ api_key = $apiKey })
    } else {
        $config.server | Add-Member -NotePropertyName "api_key" -NotePropertyValue $apiKey -Force
    }
    $jsonText = $config | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($configPath, $jsonText, [System.Text.UTF8Encoding]::new($false))
    Write-Host "  ✅ API key generated: $($apiKey.Substring(0,8))..." -ForegroundColor Green
}

# ── 4. 에이전트 코드 배포 (압축 전송) ──
Write-Host "  🔧 Deploying agent code..." -ForegroundColor Yellow

$tempTar = Join-Path $env:TEMP "xoul_deploy.tar.gz"

# 배포할 파일 수집
$filesToDeploy = @()

# 핵심 파일
$agentFiles = @(
    "server.py", "llm_client.py", "assistant_agent.py",
    "clients/terminal_client.py", "config.json", "google_auth.py", "i18n.py",
    "requirements.txt",
    "services/xoul.service", "services/xoul-telegram.service", "services/xoul-browser.service",
    "services/xoul-discord.service", "services/xoul-slack.service",
    "clients/telegram_client.py", "clients/discord_client.py", "clients/slack_client.py",
    "tool_call_parser.py", "browser_daemon.py"
)
foreach ($file in $agentFiles) {
    $src = Join-Path $ProjectDir $file
    if (Test-Path $src) { $filesToDeploy += $file }
}

# credentials
foreach ($file in @("credentials.json", "token.json")) {
    $src = Join-Path $ProjectDir $file
    if (Test-Path $src) { $filesToDeploy += $file }
}

# tools/*.py
$toolsDir = Join-Path $ProjectDir "tools"
$toolFiles = Get-ChildItem -Path $toolsDir -Filter "*.py" -ErrorAction SilentlyContinue
foreach ($tf in $toolFiles) {
    $filesToDeploy += "tools/$($tf.Name)"
}

# tools/toolkits/*.json (Toolkit 정의)
$toolkitsDir = Join-Path $toolsDir "toolkits"
if (Test-Path $toolkitsDir) {
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "mkdir -p /root/xoul/tools/toolkits" 2>$null
    $tkFiles = Get-ChildItem -Path $toolkitsDir -Filter "*.json" -ErrorAction SilentlyContinue
    foreach ($tkf in $tkFiles) {
        $filesToDeploy += "tools/toolkits/$($tkf.Name)"
    }
}

# locales/*.json
$localesDir = Join-Path $ProjectDir "locales"
$localeFiles = Get-ChildItem -Path $localesDir -Filter "*.json" -ErrorAction SilentlyContinue
foreach ($lf in $localeFiles) {
    $filesToDeploy += "locales/$($lf.Name)"
}

Write-Host "    📦 $($filesToDeploy.Count)files compressing..." -ForegroundColor Gray

# tar.gz 생성 (Windows tar 사용)
$fileArgs = ($filesToDeploy | ForEach-Object { """$_""" }) -join " "
Push-Location $ProjectDir
$tarCmd = "tar czf ""$tempTar"" $fileArgs 2>&1"
Invoke-Expression $tarCmd | Out-Null
Pop-Location

if (Test-Path $tempTar) {
    $tarSize = [math]::Round((Get-Item $tempTar).Length / 1KB, 1)
    Write-Host "    📤 $($tarSize)KB transferring..." -ForegroundColor Gray

    # 단일 scp + 원격 해제
    scp -P $sshPort -i "$sshKey" -o StrictHostKeyChecking=no $tempTar root@127.0.0.1:/tmp/xoul_deploy.tar.gz 2>$null
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "cd /root/xoul && tar xzf /tmp/xoul_deploy.tar.gz && rm /tmp/xoul_deploy.tar.gz && find /root/xoul -name '*.py' -o -name '*.json' -o -name '*.service' -o -name '*.txt' | xargs sed -i 's/\r$//' 2>/dev/null; find /root/xoul -name '*.json' | xargs sed -i '1s/^\xEF\xBB\xBF//' 2>/dev/null; echo done"

    Remove-Item $tempTar -Force -ErrorAction SilentlyContinue
    Write-Host "  ✅ $($filesToDeploy.Count)files deployed (compressed transfer)" -ForegroundColor Green
} else {
    Write-Host "  ❌ Compression failed, falling back to individual transfer..." -ForegroundColor Red
    # 폴백: 개별 scp
    foreach ($file in $filesToDeploy) {
        $src = Join-Path $ProjectDir $file
        if ($file -like "tools/*") {
            scp -P $sshPort -i "$sshKey" -o StrictHostKeyChecking=no $src root@127.0.0.1:/root/xoul/tools/ 2>$null
        } else {
            scp -P $sshPort -i "$sshKey" -o StrictHostKeyChecking=no $src root@127.0.0.1:/root/xoul/ 2>$null
        }
        Write-Host "    ✅ $file" -ForegroundColor Green
    }
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "find /root/xoul -name '*.py' -o -name '*.json' -o -name '*.service' -o -name '*.txt' | xargs sed -i 's/\r$//' 2>/dev/null; echo done"
    Write-Host "  ✅ Line ending conversion complete" -ForegroundColor Green
}



# ── 4. VM 내부 패키지 설치 ──
Write-Host "  🔧 Python Installing packages..." -ForegroundColor Yellow
# Ubuntu 22.04 pip에는 --break-system-packages 없음 → 동적 감지
ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "BSP=''; pip3 install --break-system-packages --help >/dev/null 2>&1 && BSP='--break-system-packages'; pip3 install `$BSP --ignore-installed typing_extensions 2>&1 | tail -1 && pip3 install `$BSP fastapi uvicorn pydantic holidays beautifulsoup4 2>&1 | tail -3"

# Discord/Slack 패키지 (활성화된 경우만)
if ($config.PSObject.Properties["clients"] -and $config.clients.PSObject.Properties["discord"] -and $config.clients.discord.enabled) {
    Write-Host "  📦 Discord Installing packages..." -ForegroundColor Yellow
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "BSP=''; pip3 install --break-system-packages --help >/dev/null 2>&1 && BSP='--break-system-packages'; pip3 install `$BSP discord.py 2>&1 | tail -1"
}
if ($config.PSObject.Properties["clients"] -and $config.clients.PSObject.Properties["slack"] -and $config.clients.slack.enabled) {
    Write-Host "  📦 Slack Installing packages..." -ForegroundColor Yellow
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "BSP=''; pip3 install --break-system-packages --help >/dev/null 2>&1 && BSP='--break-system-packages'; pip3 install `$BSP slack-bolt slack-sdk 2>&1 | tail -1"
}
Write-Host "  ✅ Packages installed" -ForegroundColor Green
# CJK 폰트 설치 + snap Chromium 폰트 캐시 갱신
ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "dpkg -l fonts-noto-cjk >/dev/null 2>&1 || apt-get install -y -qq fonts-noto-cjk fonts-dejavu-core; fc-cache -f; snap disable chromium 2>/dev/null; snap enable chromium 2>/dev/null" 2>$null

# ── 5. config.json에서 LLM URL 업데이트 (호스트 IP) ──
Write-Host "  🔧 Updating config (LLM host IP)..." -ForegroundColor Yellow
$pyScript = @'
import json
with open("/root/xoul/config.json", "r", encoding="utf-8-sig") as f:
    cfg = json.load(f)
providers = cfg.get("llm", {}).get("providers", {})
if "local" in providers:
    old_url = providers["local"].get("base_url", "")
    if "localhost" in old_url or "127.0.0.1" in old_url:
        providers["local"]["base_url"] = old_url.replace("localhost", "10.0.2.2").replace("127.0.0.1", "10.0.2.2")
        with open("/root/xoul/config.json", "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("LLM URL updated -> 10.0.2.2")
    else:
        print("LLM URL already set")
'@ -replace "`r", ""
$pyScript | ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "python3 -"

# ── 6. JWT_SECRET .env 파일 생성 ──
$jwtSecret = $config.server.api_key
if ($jwtSecret) {
    Write-Host "  🔑 Setting JWT_SECRET in .env..." -ForegroundColor Yellow
    ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "echo 'JWT_SECRET=$jwtSecret' > /root/xoul/.env && chmod 600 /root/xoul/.env"
    Write-Host "  ✅ JWT_SECRET configured" -ForegroundColor Green
}

# ── 7. systemd 서비스 재시작 (변경 시에만 재등록) ──
Write-Host "  🔧 Restarting services..." -ForegroundColor Yellow
# restart 스크립트를 파일로 생성 (BOM 없는 UTF8) → VM에 전송 → 실행
$restartScriptPath = Join-Path $env:TEMP "xoul_restart.sh"
$scriptContent = @"
cp /root/xoul/services/*.service /etc/systemd/system/ 2>/dev/null
systemctl daemon-reload

systemctl enable xoul 2>/dev/null
systemctl stop xoul 2>/dev/null
pkill -f browser_daemon 2>/dev/null
pkill -f chromium 2>/dev/null
sleep 1
systemctl reset-failed xoul 2>/dev/null
systemctl start xoul 2>/dev/null

systemctl enable xoul-browser 2>/dev/null
systemctl stop xoul-browser 2>/dev/null
systemctl reset-failed xoul-browser 2>/dev/null
sleep 2
systemctl start xoul-browser 2>/dev/null

python3 -c "
import json, subprocess
c = json.load(open('/root/xoul/config.json'))
for k, v in c.get('clients', {}).items():
    svc = f'xoul-{k}'
    if v.get('enabled'):
        subprocess.run(['systemctl', 'enable', '--now', svc], capture_output=True)
    else:
        subprocess.run(['systemctl', 'stop', svc], capture_output=True)
        subprocess.run(['systemctl', 'disable', svc], capture_output=True)
" 2>/dev/null

python3 -c "
import json
try:
    c = json.load(open('/root/xoul/config.json'))
    gh = c.get('github', {})
    t, u = gh.get('token',''), gh.get('username','')
    if t:
        import subprocess
        subprocess.run(['git','config','--global','credential.helper','store'], capture_output=True)
        open('/root/.git-credentials','w').write(f'https://{u}:{t}@github.com\n')
        subprocess.run(['git','config','--global','user.name',u], capture_output=True)
        subprocess.run(['git','config','--global','user.email','agent@xoul.ai'], capture_output=True)
except: pass
" 2>/dev/null

sleep 1
systemctl is-active xoul
systemctl is-active xoul-browser
"@
$scriptContent = $scriptContent -replace "`r`n", "`n"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($restartScriptPath, $scriptContent, $utf8NoBom)
scp -P $sshPort -i "$sshKey" -o StrictHostKeyChecking=no $restartScriptPath root@127.0.0.1:/tmp/xoul_restart.sh 2>$null
ssh -p $sshPort -i "$sshKey" -o StrictHostKeyChecking=no root@127.0.0.1 "bash /tmp/xoul_restart.sh; rm -f /tmp/xoul_restart.sh"
Remove-Item $restartScriptPath -Force -ErrorAction SilentlyContinue
Write-Host "  ✅ Services restarted" -ForegroundColor Green

# ── 7. 완료 ──
$apiPort = if ($config.server.port) { $config.server.port } else { 3000 }
Write-Host ""
Write-Host "  ═══════════════════════════════" -ForegroundColor DarkGray
Write-Host "  ✅ Deployment complete!" -ForegroundColor Green
Write-Host "  ─────────────────────────────" -ForegroundColor DarkGray
Write-Host "  API URL: http://localhost:$apiPort" -ForegroundColor White
Write-Host "  Docs:    http://localhost:$apiPort/docs" -ForegroundColor White
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "    .\launcher.ps1  — Start VM+LLM daily" -ForegroundColor Gray
Write-Host "    .\deploy.ps1    — Redeploy after code update" -ForegroundColor Gray
Write-Host "  ═══════════════════════════════" -ForegroundColor DarkGray
