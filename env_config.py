"""
Xoul 환경 설정 자동 감지 — Git 브랜치 기반

- main 브랜치 → 프로덕션 (xoulai.net)
- 그 외 모든 브랜치 (dev, feature/*, bugfix/* 등) → 개발 (localhost)

나중에 staging 추가 시 STG_BRANCHES / STG_WEB 만 추가하면 됨.
"""

import json
import subprocess
from functools import lru_cache
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent

# ── 환경별 Web URL ──
PROD_WEB = {
    "backend_url": "https://www.xoulai.net",
    "frontend_url": "https://www.xoulai.net",
}
DEV_WEB = {
    "backend_url": "http://localhost:8080",
    "frontend_url": "http://localhost:8081",
}
# STG_WEB = {"backend_url": "https://stg.xoulai.net", ...}  # 나중에 추가

# ── 브랜치 분류 ──
PROD_BRANCHES = {"main", "master"}
# STG_BRANCHES = {"staging", "stg"}  # 나중에 추가


@lru_cache(maxsize=1)
def get_git_branch() -> str:
    """현재 Git 브랜치 이름 반환 (캐싱됨, 프로세스당 1회 실행)"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
            cwd=str(_PROJECT_ROOT), timeout=3,
        )
        return result.stdout.strip() if result.returncode == 0 else "main"
    except Exception:
        return "main"  # git 없으면 안전하게 prod


def get_env() -> str:
    """현재 환경 반환: 'prod', 'dev' (나중에 'stg' 추가 가능)"""
    branch = get_git_branch()
    if branch in PROD_BRANCHES:
        return "prod"
    # if branch in STG_BRANCHES:
    #     return "stg"
    return "dev"


def is_dev() -> bool:
    return get_env() == "dev"


def is_prod() -> bool:
    return get_env() == "prod"


def get_web_config() -> dict:
    """
    Web URL 설정 반환.

    - prod: config.json의 web 섹션 사용 (기본값 xoulai.net)
    - dev: config.json 무시, 항상 localhost 사용
    """
    env = get_env()

    if env == "dev":
        return DEV_WEB.copy()
    # if env == "stg":
    #     return STG_WEB.copy()

    # prod: config.json에서 읽기 (fallback: PROD_WEB)
    cfg_path = _PROJECT_ROOT / "config.json"
    if cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
            web = cfg.get("web", {})
            return {
                "backend_url": web.get("backend_url", PROD_WEB["backend_url"]).rstrip("/"),
                "frontend_url": web.get("frontend_url", PROD_WEB["frontend_url"]).rstrip("/"),
            }
        except Exception:
            pass
    return PROD_WEB.copy()


# 모듈 로드 시 한 번 출력 (디버깅용)
_branch = get_git_branch()
_env = get_env()
_web = get_web_config()
print(f"🌿 [{_env.upper()}] branch={_branch} → {_web['backend_url']}")
