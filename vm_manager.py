#!/usr/bin/env python3
"""
Xoul - QEMU Ubuntu Linux VM 관리자

Ubuntu VM을 생성, 시작, 중지하고 SSH로 명령을 실행합니다.

사용법:
    python vm_manager.py setup   # VM 최초 생성
    python vm_manager.py start   # VM 시작
    python vm_manager.py stop    # VM 중지
    python vm_manager.py status  # VM 상태 확인
    python vm_manager.py ssh     # SSH 접속
    python vm_manager.py exec "command"  # VM에서 명령 실행
"""

import json
import os
import sys

# Windows 콘솔 cp949 → UTF-8 강제 (이모지 출력 오류 방지)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import subprocess
import time
import urllib.request
import shutil
import socket
import argparse
from i18n import t as _t

# ─────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# VM Disk는 ASCII-only 경로에 저장 (QEMU는 한글/공백 경로 불가)
def _safe_vm_dir():
    """QEMU가 문제없이 접근 가능한 VM 디렉토리 결정"""
    default = os.path.join(SCRIPT_DIR, "vm")
    # 경로에 비-ASCII(한글 등)나 공백이 있으면 안전한 경로 사용
    try:
        SCRIPT_DIR.encode("ascii")
        has_non_ascii = False
    except UnicodeEncodeError:
        has_non_ascii = True
    
    if has_non_ascii or " " in SCRIPT_DIR:
        safe = r"C:\xoul\vm"
        os.makedirs(safe, exist_ok=True)
        # SSH 키 등은 원래 vm/ 폴더에서 복사
        if os.path.isdir(default):
            for f in ["xoul_key", "xoul_key.pub", "qemu.pid", ".installed"]:
                src = os.path.join(default, f)
                dst = os.path.join(safe, f)
                if os.path.isfile(src) and not os.path.isfile(dst):
                    shutil.copy2(src, dst)
        print(f"  📂 VM path: {safe} (QEMU compatible)")
        return safe
    return default

VM_DIR = _safe_vm_dir()
DISK_IMAGE = os.path.join(VM_DIR, "xoul.qcow2")
# 이전 이름 호환 (ubuntu.qcow2 → xoul.qcow2)
_old_image = os.path.join(VM_DIR, "ubuntu.qcow2")
if not os.path.isfile(DISK_IMAGE) and os.path.isfile(_old_image):
    try:
        os.rename(_old_image, DISK_IMAGE)
    except OSError:
        # VM 실행 중이면 파일 잠금 → 이전 이름 그대로 사용
        DISK_IMAGE = _old_image
PID_FILE = os.path.join(VM_DIR, "qemu.pid")

UBUNTU_CLOUD_IMAGE = "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"


def load_config():
    config_path = os.path.join(SCRIPT_DIR, "config.json")
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def get_vm_config():
    config = load_config()
    vm = config.get("vm", {})
    return {
        "ssh_port": str(vm.get("ssh_port", 2222)),
        "disk_size": vm.get("disk_size", "10G"),
        "memory": str(vm.get("memory", 1024)),
        "cpus": vm.get("cpus", 2),
    }


# ─────────────────────────────────────────────
# QEMU 실행 파일 찾기
# ─────────────────────────────────────────────

def find_qemu():
    """QEMU 실행 파일 위치 반환"""
    # PATH에서 찾기
    qemu = shutil.which("qemu-system-x86_64") or shutil.which("qemu-system-x86_64.exe")
    if qemu:
        return qemu

    # Windows 기본 설치 경로
    for prog_dir in [os.environ.get("ProgramFiles", r"C:\Program Files")]:
        p = os.path.join(prog_dir, "qemu", "qemu-system-x86_64.exe")
        if os.path.isfile(p):
            return p

    return None


# ─────────────────────────────────────────────
# 하드웨어 가속 감지 (WHPX / TCG fallback)
# ─────────────────────────────────────────────

_cached_accel = None


def detect_accel(qemu_path=None):
    """WHPX 하드웨어 가속 가능 여부 테스트.
    
    QEMU를 아주 짧게 실행해서 -accel whpx가 작동하는지 확인.
    성공하면 'whpx', 실패하면 'tcg' 반환.
    결과는 캐시하여 세션당 1회만 테스트.
    """
    global _cached_accel
    if _cached_accel is not None:
        return _cached_accel

    if sys.platform != "win32":
        _cached_accel = "tcg"
        return _cached_accel

    qemu = qemu_path or find_qemu()
    if not qemu:
        _cached_accel = "tcg"
        return _cached_accel

    print("  🔍 Testing hardware acceleration (WHPX)...")
    try:
        proc = subprocess.run(
            [qemu, "-accel", "whpx", "-machine", "q35", "-display", "none",
             "-device", "isa-debug-exit,iobase=0xf4,iosize=0x04",
             "-no-reboot", "-m", "64"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stderr = proc.stderr.lower()
        # WHPX 실패 시 stderr에 에러 메시지 출력
        if "whpx" in stderr and ("error" in stderr or "not supported" in stderr or "failed" in stderr):
            print("  ℹ️  WHPX not available → using software emulation (TCG)")
            _cached_accel = "tcg"
        else:
            print("  ✅ WHPX hardware acceleration available!")
            _cached_accel = "whpx"
    except subprocess.TimeoutExpired:
        # 타임아웃 = QEMU가 정상 실행됨 (WHPX 작동)
        print("  ✅ WHPX hardware acceleration available!")
        _cached_accel = "whpx"
    except Exception:
        print("  ℹ️  Acceleration test failed → using software emulation (TCG)")
        _cached_accel = "tcg"

    # 테스트용 QEMU 프로세스 정리
    try:
        import signal
        os.kill(proc.pid, signal.SIGTERM)
    except Exception:
        pass

    return _cached_accel


def _force_tcg_fallback():
    """WHPX 캐시를 무효화하고 TCG로 강제 전환"""
    global _cached_accel
    _cached_accel = "tcg"
    print("  ⚠ WHPX crashed during boot → falling back to TCG")


def _is_whpx_crash(stderr_text: str) -> bool:
    """stderr 내용이 WHPX 관련 크래시인지 확인"""
    lower = stderr_text.lower()
    return "whpx" in lower and any(kw in lower for kw in ["injection failed", "unexpected vp exit", "error", "failed"])


def _build_accel_args(qemu_path=None):
    """가속 모드에 따른 QEMU 인자 반환"""
    accel = detect_accel(qemu_path)
    if accel == "whpx":
        # WHPX + kernel-irqchip=off (segfault 방지)
        return ["-accel", "whpx"]
    else:
        return ["-cpu", "max,-x2apic"]


def _short_path(long_path: str) -> str:
    """Windows 8.3 짧은 경로 반환 (공백/한글/괄호 회피)"""
    if sys.platform != "win32" or not os.path.exists(long_path):
        return long_path
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(500)
        ctypes.windll.kernel32.GetShortPathNameW(str(long_path), buf, 500)
        return buf.value or long_path
    except Exception:
        return long_path


# ─────────────────────────────────────────────
# Cloud 이미지 다운로드
# ─────────────────────────────────────────────

def download_cloud_image():
    """Ubuntu cloud qcow2 이미지 다운로드 + 리사이즈"""
    if os.path.isfile(DISK_IMAGE):
        print(f"  ✅ Disk image exists: {DISK_IMAGE}")
        return True

    os.makedirs(VM_DIR, exist_ok=True)
    print(f"  📥 Downloading Ubuntu Cloud image... (~600MB)") 
    print(f"     {UBUNTU_CLOUD_IMAGE}")

    try:
        req = urllib.request.Request(UBUNTU_CLOUD_IMAGE, headers={"User-Agent": "Xoul/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(DISK_IMAGE, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        print(f"\r     {downloaded // (1024*1024)}MB / {total // (1024*1024)}MB ({pct}%)", end="", flush=True)
            print()
        print(f"  ✅ Image download complete")

        # Disk resize
        vm_cfg = get_vm_config()
        qemu_img = shutil.which("qemu-img") or shutil.which("qemu-img.exe")
        if not qemu_img:
            for prog_dir in [os.environ.get("ProgramFiles", r"C:\Program Files")]:
                p = os.path.join(prog_dir, "qemu", "qemu-img.exe")
                if os.path.isfile(p):
                    qemu_img = p
                    break

        if qemu_img:
            print(f"  💾 Disk resize ({vm_cfg['disk_size']})...")
            subprocess.run(
                [qemu_img, "resize", DISK_IMAGE, vm_cfg["disk_size"]],
                capture_output=True, text=True
            )
            print(f"  ✅ Disk resize complete")

        return True
    except Exception as e:
        print(f"  ❌ Image download failed: {e}")
        if os.path.isfile(DISK_IMAGE):
            os.remove(DISK_IMAGE)
        return False


# ─────────────────────────────────────────────
# 설치 후 초기 설정 (SSH, 패키지)
# ─────────────────────────────────────────────

def post_install_setup():
    """Cloud 이미지 부팅 → cloud-init seed로 패스워드 주입 → SSH로 패키지 설치"""
    vm_cfg = get_vm_config()
    qemu = find_qemu()

    print("  🔧 Initial setup in progress...")

    # SSH 키 미리 준비
    ssh_key_path = os.path.join(VM_DIR, "xoul_key")
    if not os.path.isfile(ssh_key_path):
        print("  🔑 Generating SSH key...")
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", ssh_key_path, "-N", "", "-q"],
            check=True, timeout=10
        )

    # ── cloud-init seed ISO 생성 (항상 재생성) ──
    seed_iso = os.path.join(VM_DIR, "seed.iso")
    if True:  # 항상 최신 설정으로 재생성
        print("  ☁ Creating cloud-init seed ISO...")

        # pycdlib 설치/임포트
        try:
            import pycdlib
        except ImportError:
            print("  📦 Installing pycdlib...")
            subprocess.run([sys.executable, "-m", "pip", "install", "pycdlib", "-q"], timeout=60)
            import pycdlib

        # SSH 공개키
        pub_key = ""
        if os.path.isfile(ssh_key_path + ".pub"):
            with open(ssh_key_path + ".pub", "r", encoding="utf-8") as f:
                pub_key = f.read().strip()

        meta_data = "instance-id: xoul-vm\nlocal-hostname: xoul\n"
        user_data = (
            "#cloud-config\n"
            "users:\n"
            "  - name: root\n"
            "    lock_passwd: false\n"
            "    plain_text_passwd: androi123\n"
            f"    ssh_authorized_keys:\n"
            f"      - {pub_key}\n"
            "ssh_pwauth: true\n"
            "disable_root: false\n"
            "runcmd:\n"
            "  - echo 'root:androi123' | chpasswd\n"
            "  - sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config\n"
            "  - sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config\n"
            "  - systemctl restart sshd\n"
        )

        import io
        iso = pycdlib.PyCdlib()
        iso.new(vol_ident="cidata", rock_ridge="1.09")

        meta_bytes = meta_data.encode("utf-8")
        user_bytes = user_data.encode("utf-8")

        iso.add_fp(
            fp=io.BytesIO(meta_bytes),
            length=len(meta_bytes),
            iso_path="/META_DAT.;1",
            rr_name="meta-data",
        )
        iso.add_fp(
            fp=io.BytesIO(user_bytes),
            length=len(user_bytes),
            iso_path="/USER_DAT.;1",
            rr_name="user-data",
        )

        iso.write(seed_iso)
        iso.close()
        print(f"  ✅ seed ISO created")

    # ── VM 부팅 (cloud-init seed 포함) ──
    accel_args = _build_accel_args(qemu)
    cmd = [
        qemu,
    ] + accel_args + [
        "-m", vm_cfg["memory"],
    ] + ["-smp", str(vm_cfg["cpus"])] + [
        "-drive", f"file={_short_path(DISK_IMAGE)},format=qcow2,if=virtio",
        "-cdrom", _short_path(seed_iso),
        "-netdev", f"user,id=net0,dns=8.8.8.8,hostfwd=tcp::{vm_cfg['ssh_port']}-:22,hostfwd=tcp::3000-:3000",
        "-device", "virtio-net-pci,netdev=net0",
        "-display", "none",
        "-serial", f"file:{_short_path(os.path.join(VM_DIR, 'serial.log'))}",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        time.sleep(2)
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace")
            # WHPX 크래시 → TCG로 자동 재시도
            if _is_whpx_crash(stderr) and detect_accel() == "whpx":
                _force_tcg_fallback()
                new_args = _build_accel_args(qemu)
                cmd = [qemu] + new_args + cmd[len(accel_args) + 1:]  # accel 인자만 교체
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                time.sleep(2)
                if proc.poll() is not None:
                    stderr2 = proc.stderr.read().decode("utf-8", errors="replace")
                    print(f"  ❌ QEMU exited immediately: {stderr2[:300]}")
                    return False
            else:
                print(f"  ❌ QEMU exited immediately: {stderr[:300]}")
                return False

        # SSH 대기
        print("  ⏳ Waiting for VM boot + cloud-init...", end="", flush=True)
        ssh_ready = False
        for i in range(90):  # 최대 3분
            time.sleep(2)
            if proc.poll() is not None:
                print(f"\n  ❌ VM terminated abnormally")
                return False
            if is_port_open(vm_cfg["ssh_port"]):
                print(f"\n  ✅ SSH port open!")
                print("  ⏳ Waiting for cloud-init to finish... (30s)")
                time.sleep(30)  # cloud-init이 패스워드 설정 + sshd 재시작
                ssh_ready = True
                break
            print(".", end="", flush=True)

        if not ssh_ready:
            print(f"\n  ❌ SSH wait timeout")
            proc.kill()
            return False

        # PID 저장
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))

        # paramiko로 SSH 접속
        try:
            import paramiko
        except ImportError:
            print("  📦 Installing paramiko...")
            subprocess.run([sys.executable, "-m", "pip", "install", "paramiko", "-q"],
                          timeout=60, capture_output=True)
            import paramiko
        import logging
        logging.getLogger("paramiko").setLevel(logging.CRITICAL)

        def _ssh_run(command, quiet=False, stream=False):
            """SSH로 명령 실행. stream=True이면 실시간 출력."""
            for auth_kwargs in [
                {"key_filename": ssh_key_path},
                {"password": "androi123"},
            ]:
                try:
                    client = paramiko.SSHClient()
                    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    client.connect("127.0.0.1", port=int(vm_cfg["ssh_port"]),
                                 username="root", timeout=10,
                                 allow_agent=False, look_for_keys=False,
                                 **auth_kwargs)
                    _, stdout_ch, stderr_ch = client.exec_command(command, timeout=600)

                    if stream:
                        # 실시간 출력 (줄 단위)
                        lines = []
                        for line in iter(stdout_ch.readline, ""):
                            line = line.rstrip()
                            if line:
                                print(f"     {line[:200]}")
                                lines.append(line)
                        output = "\n".join(lines)
                    else:
                        output = stdout_ch.read().decode("utf-8", errors="replace").strip()
                    client.close()
                    if not stream and not quiet and output:
                        print(f"     {output[:200]}")
                    return output
                except Exception:
                    try: client.close()
                    except: pass
            if not quiet:
                print(f"  ⚠ SSH connection failed")
            return ""

        # SSH 접속 테스트 (waiting for cloud-init 포함 재시도)
        print("  🔗 Testing SSH connection...")
        test_result = ""
        for attempt in range(15):
            test_result = _ssh_run("echo SSH_OK", quiet=True)
            if "SSH_OK" in test_result:
                break
            if attempt < 14:
                print(f"  ⏳ SSH retry ({attempt+2}/15)... waiting for cloud-init")
                time.sleep(15)
        if "SSH_OK" not in test_result:
            print("  ❌ SSH connection failed")
            print(f"     ssh -p {vm_cfg['ssh_port']} root@127.0.0.1  (password: androi123)")
            return False

        print("  ✅ SSH connection successful!")

        # 패키지 설치
        print("  📦 Updating package lists...")
        _ssh_run("apt-get update -qq", quiet=True)
        print("  ✅ Package lists updated")

        # apt 패키지를 그룹별로 설치 (진행 상황 표시)
        apt_groups = [
            ("Python (python3, pip, venv)", "python3 python3-pip python3-venv"),
            ("Core tools (bash, curl, git, jq, file)", "bash curl git jq file"),
            ("Document tools (poppler-utils)", "poppler-utils"),
            ("Chromium browser", "chromium-browser"),
            ("Browser dependencies (libnss3, libdrm2, etc.)", "libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2"),
            ("Fonts (Noto CJK, DejaVu)", "fonts-noto-cjk fonts-dejavu-core"),
        ]
        total_groups = len(apt_groups)
        for idx, (label, packages) in enumerate(apt_groups, 1):
            print(f"  📦 [{idx}/{total_groups}] Installing {label}...")
            _ssh_run(f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {packages}", quiet=True)
            print(f"  ✅ [{idx}/{total_groups}] {label} done")

        _ssh_run("fc-cache -f", quiet=True)
        # snap Chromium 폰트 캐시 갱신 (disable → enable)
        print("  🔄 Refreshing Chromium font cache...")
        _ssh_run("snap disable chromium 2>/dev/null; snap enable chromium 2>/dev/null", quiet=True)
        _ssh_run("ln -sf /usr/bin/python3 /usr/local/bin/python 2>/dev/null", quiet=True)

        # pip 시스템 설치 허용 (Ubuntu 24.04 EXTERNALLY-MANAGED 제한 해제)
        _ssh_run("rm -f /usr/lib/python*/EXTERNALLY-MANAGED", quiet=True)
        _ssh_run("mkdir -p /root/.config/pip && echo '[global]\nbreak-system-packages = true' > /root/.config/pip/pip.conf", quiet=True)

        # Python 패키지 사전 설치 (Google API, 기타 자주 쓰는 것들)
        print("  📦 Installing Python packages (google-api, requests, etc.)...")
        _ssh_run("pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib requests 2>&1", stream=True)
        print("  ✅ Python packages installed")

        # workspace
        _ssh_run("mkdir -p /root/workspace /root/share", quiet=True)

        # swap 설정 (1GB — OOM 방지)
        print("  💾 Setting up 1GB swap...")
        _ssh_run("fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile", quiet=True)
        _ssh_run("grep -q swapfile /etc/fstab || echo '/swapfile swap swap defaults 0 0' >> /etc/fstab", quiet=True)
        print("  ✅ Swap configured")

        print(f"  ✅ VM setup complete! (SSH port {vm_cfg['ssh_port']})")
        return True

    except Exception as e:
        print(f"  ❌ Setup failed: {e}")
        try: proc.kill()
        except: pass
        return False




# ─────────────────────────────────────────────
# 통합 설정 (setup)
# ─────────────────────────────────────────────


def _clean_known_hosts():
    """이전 VM의 SSH 호스트 키를 known_hosts에서 제거"""
    vm_cfg = get_vm_config()
    known_hosts = os.path.expanduser("~/.ssh/known_hosts")
    if os.path.isfile(known_hosts):
        try:
            subprocess.run(
                ["ssh-keygen", "-R", f"[127.0.0.1]:{vm_cfg['ssh_port']}"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass


def setup():
    """VM 원클릭 설정 (Cloud 이미지 다운 → 부팅 → SSH 설정)"""
    # SSH 호스트 키 충돌 방지 (VM 재생성 시)
    _clean_known_hosts()

    print()
    print("========================================")
    print("  Xoul VM Setup (QEMU + Ubuntu Linux)")
    print("========================================")
    print()

    # 1. QEMU 확인
    qemu = find_qemu()
    if not qemu:
        print("  ❌ QEMU is not installed.")
        print("     Install: winget install SoftwareFreedomConservancy.QEMU")
        return False
    print(f"  ✅ QEMU: {qemu}")

    # 이미 설치된 VM 확인
    installed_marker = os.path.join(VM_DIR, ".installed")
    if os.path.isfile(installed_marker):
        print("  ✅ Ubuntu VM already installed")
        return True

    # 2. Cloud 이미지 다운로드 + 리사이즈
    if not download_cloud_image():
        return False

    # 3. 초기 설정 (부팅 → SSH → 패키지)
    if not post_install_setup():
        return False

    # 설치 완료 마커
    with open(installed_marker, "w") as f:
        f.write("installed")

    print()
    print("  🎉 Ubuntu VM setup complete!")
    print("  You can now run assistant_agent.py.")
    return True


def is_port_open(port, host="localhost"):
    """포트가 열려있는지 확인"""
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def is_vm_running():
    """VM이 실행 중인지 확인"""
    vm_cfg = get_vm_config()
    return is_port_open(vm_cfg["ssh_port"])


def get_qemu_pid():
    """저장된 QEMU PID 읽기"""
    if os.path.isfile(PID_FILE):
        with open(PID_FILE, "r") as f:
            try:
                return int(f.read().strip())
            except ValueError:
                return None
    return None


def _mount_9p_share():
    """VM에서 9P share 마운트"""
    try:
        # 마운트 포인트 생성
        ssh_exec("mkdir -p /root/share", quiet=True)
        
        # 9P 커널 모듈 로드
        ssh_exec("modprobe 9pnet_virtio 2>/dev/null", quiet=True)
        ssh_exec("modprobe 9p 2>/dev/null", quiet=True)
        
        # 이미 마운트되어 있는지 확인
        check = ssh_exec("mountpoint -q /root/share && echo 'mounted'", quiet=True)
        if "mounted" in check:
            print("  📂 share/ already mounted")
            return
        
        # 마운트
        result = ssh_exec(
            "mount -t 9p -o trans=virtio,version=9p2000.L hostshare /root/share 2>&1",
            quiet=True
        )
        if result and "error" in result.lower():
            print(f"  ⚠ 9P mount failed: {result}")
            print("  📂 Falling back to SCP method")
            return
        
        # fstab에 추가 (재부팅 시 자동 마운트)
        fstab_check = ssh_exec("grep hostshare /etc/fstab", quiet=True)
        if "hostshare" not in fstab_check:
            ssh_exec(
                "echo 'hostshare /root/share 9p trans=virtio,version=9p2000.L 0 0' >> /etc/fstab",
                quiet=True
            )
        
        print("  📂 share/ live sync enabled (/root/share)")
    except Exception as e:
        print(f"  ⚠ 9P mount error: {e} (Falling back to SCP method)")


def start_vm(install_mode=False):
    """QEMU VM 시작"""
    qemu = find_qemu()
    if not qemu:
        print("  ❌ Cannot find QEMU.")
        print("     Install: winget install SoftwareFreedomConservancy.QEMU")
        return False

    vm_cfg = get_vm_config()

    if is_vm_running():
        print(f"  ✅ VM already running (SSH port {vm_cfg['ssh_port']})")
        return True

    if not os.path.isfile(DISK_IMAGE):
        print("  ❌ Disk image not found. Run setup first.")
        return False

    seed_iso = os.path.join(VM_DIR, "seed.iso")
    accel_args = _build_accel_args(qemu)
    cmd = [
        qemu,
    ] + accel_args + [
        "-m", vm_cfg["memory"],
    ] + ["-smp", str(vm_cfg["cpus"])] + [
        "-drive", f"file={_short_path(DISK_IMAGE)},format=qcow2,if=virtio",
        "-netdev", f"user,id=net0,dns=8.8.8.8,hostfwd=tcp::{vm_cfg['ssh_port']}-:22,hostfwd=tcp::3000-:3000",
        "-device", "virtio-net-pci,netdev=net0",
        "-display", "none",
        "-serial", f"file:{_short_path(os.path.join(VM_DIR, 'serial.log'))}",
    ]
    if os.path.isfile(seed_iso):
        cmd.extend(["-cdrom", _short_path(seed_iso)])

    # share/ 디렉토리 생성 (SCP 기반 동기화 사용)
    os.makedirs(os.path.join(SCRIPT_DIR, "share"), exist_ok=True)

    print(f"  🚀 Starting VM...")
    print(f"     Memory: {vm_cfg['memory']}MB, CPU: {vm_cfg['cpus']}cores")
    print(f"     SSH: localhost:{vm_cfg['ssh_port']}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        # 시작 직후 크래시 체크
        time.sleep(2)
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace")
            # WHPX 크래시 → TCG로 자동 재시도
            if _is_whpx_crash(stderr) and detect_accel() == "whpx":
                _force_tcg_fallback()
                new_args = _build_accel_args(qemu)
                cmd = [qemu] + new_args + cmd[len(accel_args) + 1:]  # accel 인자만 교체
                print(f"  🔄 Retrying with TCG...")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                time.sleep(2)
                if proc.poll() is not None:
                    stderr2 = proc.stderr.read().decode("utf-8", errors="replace")
                    print(f"  ❌ QEMU exited immediately (code: {proc.returncode})")
                    if stderr2:
                        print(f"     Error: {stderr2[:500]}")
                    return False
            else:
                print(f"  ❌ QEMU exited immediately (code: {proc.returncode})")
                if stderr:
                    print(f"     Error: {stderr[:500]}")
                return False

        # PID 저장
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))

        # SSH 대기
        print("  ⏳ Waiting for SSH...", end="", flush=True)
        for i in range(60):
            time.sleep(2)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", errors="replace")
                print(f"\n  ❌ VM terminated abnormally (code: {proc.returncode})")
                if stderr:
                    print(f"     Error: {stderr[:500]}")
                return False
            if is_port_open(vm_cfg["ssh_port"]):
                print(f"\n  ✅ VM started! (SSH port {vm_cfg['ssh_port']})")
                # sshd 완전 준비 대기 (kex_exchange_identification 방지)
                time.sleep(15)
                # SSH 키 인증 설정
                setup_ssh_key()
                # VM에 share 디렉토리 생성
                ssh_exec("mkdir -p /root/share", quiet=True)
                # Git credential 자동 설정 (config에 github.token이 있으면)
                try:
                    config = load_config()
                    gh = config.get("github", {})
                    gh_token = gh.get("token", "")
                    gh_user = gh.get("username", "")
                    if gh_token:
                        ssh_exec("git config --global credential.helper store", quiet=True)
                        ssh_exec(f"echo 'https://{gh_user}:{gh_token}@github.com' > ~/.git-credentials", quiet=True)
                        if gh_user:
                            ssh_exec(f"git config --global user.name '{gh_user}'", quiet=True)
                        ssh_exec("git config --global user.email 'agent@xoul.ai'", quiet=True)
                        print("  🐙 Git credential configured")
                except Exception:
                    pass
                # 브라우저 데몬 확인 및 시작 (필수 인프라)
                try:
                    browser_check = ssh_exec("systemctl is-active xoul-browser 2>/dev/null", quiet=True).strip()
                    if browser_check != "active":
                        ssh_exec("systemctl enable xoul-browser 2>/dev/null; systemctl start xoul-browser", quiet=True)
                        print("  🌐 Browser daemon started")
                    else:
                        print("  🌐 Browser daemon running")
                except Exception:
                    pass
                return True
            print(".", end="", flush=True)

        print(f"\n  ⚠ SSH timeout. VM may still be booting.")
        return False

    except Exception as e:
        print(f"  ❌ VM start failed: {e}")
        return False


def stop_vm():
    """VM 중지"""
    vm_cfg = get_vm_config()

    # SSH로 정상 종료 시도
    if is_vm_running():
        print("  🛑 Stopping VM...")
        ssh_exec("poweroff", timeout=5, quiet=True)
        time.sleep(3)

    # PID로 강제 종료
    pid = get_qemu_pid()
    if pid:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                             capture_output=True)
            else:
                os.kill(pid, 9)
        except Exception:
            pass
        if os.path.isfile(PID_FILE):
            os.remove(PID_FILE)

    print("  ✅ VM stopped")


# ─────────────────────────────────────────────
# SSH 인증 및 실행
# ─────────────────────────────────────────────

SSH_KEY = os.path.join(VM_DIR, "xoul_key")
_ssh_key_ready = False


def _ssh_common_opts():
    """리턴: SSH/SCP 공통 옵션 리스트"""
    nullfile = "NUL" if sys.platform == "win32" else "/dev/null"
    opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", f"UserKnownHostsFile={nullfile}",
        "-o", "LogLevel=ERROR",
    ]
    # 키 인증이 확인된 경우에만 BatchMode 사용
    if _ssh_key_ready and os.path.isfile(SSH_KEY):
        opts.extend(["-i", SSH_KEY, "-o", "BatchMode=yes"])
    elif os.path.isfile(SSH_KEY):
        opts.extend(["-i", SSH_KEY])
    return opts


def setup_ssh_key():
    """SSH 키 인증 설정 — 키 생성, 복사, 테스트"""
    global _ssh_key_ready
    if _ssh_key_ready:
        return True
    
    # 1. 키 파일 없으면 생성
    if not os.path.isfile(SSH_KEY):
        print("  🔑 Generating SSH key...")
        try:
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", SSH_KEY, "-N", "", "-q"],
                check=True, timeout=10
            )
        except Exception as e:
            print(f"  ⚠ SSH key generation failed: {e}")
            return False
    
    # 2. 키 인증 테스트
    if _test_ssh_key():
        _ssh_key_ready = True
        print("  ✅ SSH key authentication enabled")
        return True
    
    # 3. 테스트 실패 → paramiko로 공개키 복사 (비대화형)
    print("  🔑 Copying SSH key...")
    try:
        import paramiko
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "paramiko", "-q"],
                      timeout=60, capture_output=True)
        import paramiko
    import logging
    logging.getLogger("paramiko").setLevel(logging.CRITICAL)

    try:
        with open(SSH_KEY + ".pub", "r", encoding="utf-8") as f:
            pub_key = f.read().strip()
        
        vm_cfg = get_vm_config()
        port = vm_cfg["ssh_port"]
        
        remote_cmd = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f"echo '{pub_key}' > ~/.ssh/authorized_keys && "
            "chmod 600 ~/.ssh/authorized_keys && "
            "sed -i 's/#PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config && "
            "sed -i 's/PubkeyAuthentication no/PubkeyAuthentication yes/' /etc/ssh/sshd_config && "
            "systemctl restart sshd 2>/dev/null; echo KEY_DONE"
        )
        
        # paramiko로 비대화형 SSH 접속 — 재시도 포함
        success = False
        for attempt in range(5):
            if attempt > 0:
                print(f"  ⏳ Waiting for sshd... ({attempt+1}/5)")
                time.sleep(10)
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                # 패스워드 xoul123 → 빈 패스워드 순서로 시도
                connected = False
                for pw in ["androi123", ""]:
                    try:
                        client.connect("127.0.0.1", port=port, username="root",
                                     password=pw, timeout=15,
                                     allow_agent=False, look_for_keys=False)
                        connected = True
                        break
                    except Exception:
                        try: client.close()
                        except: pass
                        client = paramiko.SSHClient()
                        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                
                if not connected:
                    continue
                
                _, stdout_ch, _ = client.exec_command(remote_cmd, timeout=30)
                output = stdout_ch.read().decode("utf-8", errors="replace").strip()
                client.close()
                if "KEY_DONE" in output:
                    success = True
                    break
            except Exception as e:
                try: client.close()
                except: pass
                if attempt == 4:
                    print(f"  ⚠ SSH connection error: {e}")
        
        if not success:
            print("  ⚠ SSH key copy failed")
            return False
    except Exception as e:
        print(f"  ⚠ SSH key copy failed: {e}")
        return False
    
    # 4. 복사 후 재테스트
    if _test_ssh_key():
        _ssh_key_ready = True
        print("  ✅ SSH key authentication enabled")
        return True
    
    print("  ⚠ SSH key auth failed — using password authentication")
    return False


def _test_ssh_key() -> bool:
    """SSH 키 인증 테스트"""
    vm_cfg = get_vm_config()
    try:
        test = subprocess.run(
            ["ssh", "-i", SSH_KEY,
             "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null",
             "-o", "LogLevel=ERROR",
             "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=5",
             "-p", str(vm_cfg["ssh_port"]),
             "root@127.0.0.1", "echo ok"],
            capture_output=True, text=True, timeout=10
        )
        return "ok" in test.stdout
    except Exception:
        return False

def ssh_exec(command: str, timeout: int = 30, quiet: bool = False) -> str:
    """VM에서 SSH 명령 실행"""
    vm_cfg = get_vm_config()

    ssh_cmd = [
        "ssh",
    ] + _ssh_common_opts() + [
        "-o", f"ConnectTimeout={min(timeout, 10)}",
        "-p", str(vm_cfg["ssh_port"]),
        "root@127.0.0.1",
        command,
    ]

    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=False,
            timeout=timeout,
        )
        output = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        if stderr and not quiet:
            output += "\n" + stderr
        return output.strip()
    except subprocess.TimeoutExpired:
        return _t("vm.timeout", timeout=timeout)
    except Exception as e:
        return f"[SSH Error: {e}]"


def ssh_write_file(path: str, content: str) -> str:
    """VM에 파일 쓰기"""
    import base64
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    cmd = f"echo '{encoded}' | base64 -d > '{path}'"
    return ssh_exec(cmd)


def ssh_read_file(path: str) -> str:
    """VM에서 파일 읽기"""
    return ssh_exec(f"cat '{path}'")


# ─────────────────────────────────────────────
# share 디렉토리 (Windows ↔ VM 파일 공유)
# ─────────────────────────────────────────────

SHARE_DIR = os.path.join(SCRIPT_DIR, "share")
VM_SHARE_DIR = "/root/share"


def _scp_common_args():
    """SCP 공통 인자"""
    vm_cfg = get_vm_config()
    return _ssh_common_opts() + [
        "-P", str(vm_cfg["ssh_port"]),
    ]


def scp_to_vm(local_path: str, vm_path: str = "") -> str:
    """Windows → VM 파일 복사"""
    if not os.path.exists(local_path):
        return _t("vm.file_not_found", path=local_path)
    
    if not vm_path:
        vm_path = f"{VM_SHARE_DIR}/{os.path.basename(local_path)}"
    
    # VM에 대상 디렉토리 생성
    vm_dir = os.path.dirname(vm_path)
    if vm_dir:
        ssh_exec(f"mkdir -p '{vm_dir}'", quiet=True)
    
    cmd = ["scp"] + _scp_common_args()
    if os.path.isdir(local_path):
        cmd.append("-r")
    cmd.extend([local_path, f"root@127.0.0.1:{vm_path}"])
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=False, timeout=300,
        )
        if result.returncode == 0:
            return _t("vm.upload_done", name=os.path.basename(local_path), path=vm_path)
        stderr = result.stderr.decode("utf-8", errors="replace")
        return f"Error: {stderr}"
    except Exception as e:
        return f"SCP Error: {e}"


def scp_from_vm(vm_path: str, local_path: str = "") -> str:
    """VM → Windows 파일 복사"""
    if not local_path:
        os.makedirs(SHARE_DIR, exist_ok=True)
        local_path = os.path.join(SHARE_DIR, os.path.basename(vm_path))
    
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    
    cmd = ["scp"] + _scp_common_args()
    cmd.extend([f"root@127.0.0.1:{vm_path}", local_path])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=60)
        if result.returncode == 0:
            return _t("vm.download_done", src=vm_path, dst=local_path)
        stderr = result.stderr.decode("utf-8", errors="replace")
        return f"Error: {stderr}"
    except Exception as e:
        return f"SCP Error: {e}"


def sync_share_to_vm() -> str:
    """share/ 폴더 total를 VM으로 동기화"""
    os.makedirs(SHARE_DIR, exist_ok=True)
    ssh_exec(f"mkdir -p {VM_SHARE_DIR}", quiet=True)
    
    files = os.listdir(SHARE_DIR)
    if not files:
        return _t("vm.share_empty")
    
    results = []
    for f in files:
        local = os.path.join(SHARE_DIR, f)
        result = scp_to_vm(local, f"{VM_SHARE_DIR}/{f}")
        results.append(result)
    
    return "\n".join(results)


def sync_share_from_vm() -> str:
    """VM의 /root/share/ total를 Windows로 동기화"""
    os.makedirs(SHARE_DIR, exist_ok=True)
    
    # VM에서 파일 목록
    file_list = ssh_exec(f"ls {VM_SHARE_DIR} 2>/dev/null", quiet=True)
    if not file_list or "No such file" in file_list:
        return _t("vm.vm_share_empty")
    
    results = []
    for f in file_list.strip().split("\n"):
        f = f.strip()
        if f:
            result = scp_from_vm(f"{VM_SHARE_DIR}/{f}", os.path.join(SHARE_DIR, f))
            results.append(result)
    
    return "\n".join(results)


# ─────────────────────────────────────────────
# 초기 설정 (setup)
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=_t("vm.cli_desc"))
    parser.add_argument("action", nargs="?", default="status",
                       choices=["setup", "start", "stop", "restart", "status", "ssh", "exec"],
                       help=_t("vm.cli_action"))
    parser.add_argument("command", nargs="?", default="",
                       help=_t("vm.cli_command"))
    args = parser.parse_args()

    if args.action == "setup":
        setup()
    elif args.action == "start":
        start_vm()
    elif args.action == "restart":
        print("  🔄 Restarting VM...")
        stop_vm()
        time.sleep(2)
        start_vm()
    elif args.action == "stop":
        stop_vm()
    elif args.action == "status":
        if is_vm_running():
            vm_cfg = get_vm_config()
            print(f"  ✅ VM running (SSH port {vm_cfg['ssh_port']})")
            hostname = ssh_exec("hostname", timeout=5, quiet=True)
            uname = ssh_exec("uname -a", timeout=5, quiet=True)
            print(f"     Host: {hostname}")
            print(f"     System: {uname}")
        else:
            print("  ⛔ VM is offline")
            print("     Start: python vm_manager.py start")
    elif args.action == "ssh":
        vm_cfg = get_vm_config()
        os.system(f"ssh -o StrictHostKeyChecking=no -p {vm_cfg['ssh_port']} root@127.0.0.1")
    elif args.action == "exec":
        if args.command:
            print(ssh_exec(args.command))
        else:
            print("  Usage: python vm_manager.py exec \"command\"")


if __name__ == "__main__":
    main()
