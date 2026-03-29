"""
Xoul Toolkit Client — Premium Toolkit Store API 클라이언트

Store 서버에서 컴파일된 .so toolkit을 다운로드하고 동적 import.
라이선스 heartbeat: 시작 시 검증, 실패 시 캐시 삭제.
"""

import os
import json
import time
import importlib
import importlib.util
import urllib.request
import urllib.error

PREMIUM_DIR = os.path.join(os.path.expanduser("~"), ".xoul", "premium_toolkits")
VERIFY_CACHE_PATH = os.path.join(PREMIUM_DIR, ".license_verified.json")
GRACE_PERIOD_HOURS = 24  # 서버 접속 불가 시 허용 시간
os.makedirs(PREMIUM_DIR, exist_ok=True)

# Store 서버 URL (향후 https://store.xoul.ai로 교체)
_store_url = "http://localhost:5100"
_license_key = ""
_license_valid = False


def init(config: dict):
    """config.json에서 store URL과 라이선스 키 로드"""
    global _store_url, _license_key
    store_cfg = config.get("toolkit_store", {})
    _store_url = store_cfg.get("url", "http://localhost:5100")
    _license_key = store_cfg.get("license_key", "")
    if _license_key:
        print(f"  🔑 Toolkit Store: {_store_url} (license: {_license_key[:8]}...)")


def fetch_catalog() -> list:
    """Store 서버에서 사용 가능한 premium toolkit 목록 조회"""
    try:
        req = urllib.request.Request(
            f"{_store_url}/api/catalog",
            headers={"X-License-Key": _license_key}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        return data.get("toolkits", [])
    except Exception as e:
        print(f"  ⚠ Toolkit Store unreachable: {e}")
        return []


def verify_license() -> dict:
    """라이선스 키 유효성 검증"""
    if not _license_key:
        return {"valid": False, "reason": "no license key configured"}
    try:
        data = json.dumps({"license_key": _license_key}).encode()
        req = urllib.request.Request(
            f"{_store_url}/api/verify",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        return {"valid": False, "reason": str(e)}


def download_toolkit(name: str) -> str | None:
    """
    Store 서버에서 compiled .so toolkit 다운로드.
    반환: 로컬 .so 파일 경로 (실패 시 None)
    """
    if not _license_key:
        print(f"  ⚠ No license key. Cannot download premium toolkit: {name}")
        return None

    try:
        req = urllib.request.Request(
            f"{_store_url}/api/toolkit/{name}",
            headers={"X-License-Key": _license_key}
        )
        resp = urllib.request.urlopen(req, timeout=30)

        # 파일명 결정
        content_disp = resp.headers.get("Content-Disposition", "")
        if "filename=" in content_disp:
            fname = content_disp.split("filename=")[-1].strip('"')
        else:
            fname = f"{name}.so"

        local_path = os.path.join(PREMIUM_DIR, fname)
        with open(local_path, "wb") as f:
            f.write(resp.read())

        print(f"  📥 Downloaded: {name} → {local_path}")
        return local_path

    except urllib.error.HTTPError as e:
        if e.code == 403:
            print(f"  🔒 License denied for toolkit: {name}")
        elif e.code == 404:
            print(f"  ❌ Toolkit not found: {name}")
        else:
            print(f"  ⚠ Download error ({e.code}): {e}")
        return None
    except Exception as e:
        print(f"  ⚠ Download failed: {e}")
        return None


def import_toolkit(so_path: str):
    """
    다운받은 .so 파일을 동적 import.
    반환: imported module (실패 시 None)
    """
    try:
        name = os.path.splitext(os.path.basename(so_path))[0]
        # cpython ABI tag 제거 (e.g., demo_premium.cpython-312-x86_64-linux-gnu.so)
        base_name = name.split(".")[0] if "." in name else name

        spec = importlib.util.spec_from_file_location(base_name, so_path)
        if spec is None:
            print(f"  ⚠ Cannot create spec for: {so_path}")
            return None

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        print(f"  ✅ Imported premium toolkit: {base_name}")
        return mod

    except Exception as e:
        print(f"  ⚠ Import failed ({so_path}): {e}")
        return None


def load_premium_toolkit(name: str) -> bool:
    """
    Premium toolkit 로드 (라이선스 검증 → 다운로드/캐시 → import → 등록).
    """
    global _license_valid
    from tools.tool_registry import register_premium_toolkit

    # 1) 라이선스 검증 (시작 시 1회, 이후 캐시)
    if not _license_valid:
        _license_valid = _check_license_with_grace()
    if not _license_valid:
        _purge_cache()
        return False

    # 2) 캐시 체크
    cached = _find_cached(name)
    if cached:
        mod = import_toolkit(cached)
        if mod:
            register_premium_toolkit(name, mod)
            return True

    # 3) 서버에서 다운로드
    so_path = download_toolkit(name)
    if not so_path:
        return False

    # 4) import + 등록
    mod = import_toolkit(so_path)
    if mod:
        register_premium_toolkit(name, mod)
        return True

    return False


def _check_license_with_grace() -> bool:
    """
    라이선스 검증 + grace period.
    서버 연결 가능  → 실시간 검증
    서버 연결 불가  → 마지막 성공으로부터 GRACE_PERIOD_HOURS 이내면 허용
    """
    result = verify_license()

    if result.get("valid"):
        # 성공: 검증 시간 기록
        _save_verify_time()
        print(f"  ✅ License valid (user: {result.get('user', '?')})")
        return True

    reason = result.get("reason", "")

    # 서버 접속 불가 → grace period 확인
    if "urlopen" in reason.lower() or "connection" in reason.lower() or "timeout" in reason.lower():
        last_verified = _load_verify_time()
        if last_verified:
            elapsed_hours = (time.time() - last_verified) / 3600
            if elapsed_hours < GRACE_PERIOD_HOURS:
                print(f"  ⚠ Store unreachable, grace period active ({elapsed_hours:.1f}h / {GRACE_PERIOD_HOURS}h)")
                return True
            else:
                print(f"  🔒 Store unreachable, grace period expired ({elapsed_hours:.1f}h > {GRACE_PERIOD_HOURS}h)")
                return False
        print(f"  🔒 Store unreachable, no previous verification")
        return False

    # 라이선스 자체 무효/만료
    print(f"  🔒 License invalid: {reason}")
    return False


def _save_verify_time():
    """마지막 검증 성공 시간 저장"""
    try:
        with open(VERIFY_CACHE_PATH, "w") as f:
            json.dump({"verified_at": time.time()}, f)
    except Exception:
        pass


def _load_verify_time() -> float | None:
    """마지막 검증 성공 시간 로드"""
    try:
        with open(VERIFY_CACHE_PATH, "r") as f:
            data = json.load(f)
            return data.get("verified_at")
    except Exception:
        return None


def _purge_cache():
    """라이선스 무효 시 캐시된 .so 파일 삭제"""
    count = 0
    if os.path.isdir(PREMIUM_DIR):
        for f in os.listdir(PREMIUM_DIR):
            if f.endswith(".so"):
                os.remove(os.path.join(PREMIUM_DIR, f))
                count += 1
    if count:
        print(f"  🗑️ Purged {count} cached premium toolkit(s)")
    # 검증 기록도 삭제
    if os.path.isfile(VERIFY_CACHE_PATH):
        os.remove(VERIFY_CACHE_PATH)


def _find_cached(name: str) -> str | None:
    """Premium 캐시 디렉토리에서 기존 .so 찾기"""
    if not os.path.isdir(PREMIUM_DIR):
        return None
    for f in os.listdir(PREMIUM_DIR):
        if f.startswith(name) and f.endswith(".so"):
            return os.path.join(PREMIUM_DIR, f)
    return None


def load_all_premium(config: dict):
    """서버 시작 시: 라이선스 검증 → premium toolkit 로드"""
    init(config)
    if not _license_key:
        return

    # catalog에서 사용 가능한 toolkit 확인
    catalog = fetch_catalog()
    if not catalog:
        return

    for tk in catalog:
        name = tk.get("name", "")
        if name:
            load_premium_toolkit(name)

