"""
Git 도구 — GitHub PAT 기반 git 작업 (VM SSH 실행)
"""

import os
import json

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_github_config() -> dict:
    """config.json에서 github 설정 읽기"""
    config_path = os.path.join(_SCRIPT_DIR, "config.json")
    try:
        with open(config_path, "r", encoding="utf-8-sig") as f:
            config = json.load(f)
        return config.get("github", {})
    except Exception:
        return {}


def _ensure_token() -> str:
    """토큰 확인, 없으면 에러 메시지 반환"""
    gh = _get_github_config()
    token = gh.get("token", "")
    if not token:
        return "⚠ GitHub 토큰이 설정되지 않았습니다. setup_env.ps1을 다시 실행하거나 config.json의 github.token을 설정해주세요."
    return ""


def _ssh(cmd: str, timeout: int = 60) -> str:
    """VM에서 SSH 명령 실행"""
    try:
        from vm_manager import ssh_exec
        return ssh_exec(cmd, timeout=timeout, quiet=True)
    except ImportError:
        return "[오류] vm_manager를 불러올 수 없습니다."


def _setup_git_credential():
    """VM에 git credential 설정 (매 호출 시 최신 토큰 반영)"""
    gh = _get_github_config()
    token = gh.get("token", "")
    username = gh.get("username", "")
    if not token:
        return
    _ssh("git config --global credential.helper store", timeout=10)
    _ssh(f"echo 'https://{username}:{token}@github.com' > ~/.git-credentials", timeout=10)
    if username:
        _ssh(f"git config --global user.name '{username}'", timeout=10)
    _ssh("git config --global user.email 'agent@xoul.ai'", timeout=10)


def git_clone(repo_url: str, directory: str = "") -> str:
    """git clone 실행"""
    err = _ensure_token()
    if err:
        return err
    _setup_git_credential()

    if not directory:
        # repo URL에서 이름 추출
        name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        directory = f"/root/workspace/{name}"

    result = _ssh(f"git clone '{repo_url}' '{directory}' 2>&1", timeout=120)
    # 존재 확인
    check = _ssh(f"test -d '{directory}/.git' && echo EXISTS", timeout=10)
    if "EXISTS" in check:
        return f"✅ Clone 완료: {repo_url} → {directory}\n{result}"
    elif "already exists" in result:
        return f"📂 이미 존재합니다: {directory}\n{result}"
    else:
        return f"❌ Clone 실패: {result}"


def git_status(directory: str = "") -> str:
    """git status + 최근 로그"""
    if not directory:
        directory = "/root/workspace"

    status = _ssh(f"cd '{directory}' && git status --short 2>&1", timeout=15)
    branch = _ssh(f"cd '{directory}' && git branch --show-current 2>&1", timeout=10)
    log = _ssh(f"cd '{directory}' && git log --oneline -5 2>&1", timeout=10)

    return f"📂 {directory}\n🌿 Branch: {branch}\n\n📋 Status:\n{status or '(clean)'}\n\n📜 Recent commits:\n{log}"


def git_commit(message: str, directory: str = "", files: str = ".") -> str:
    """git add + commit"""
    if not directory:
        directory = "/root/workspace"

    _ssh(f"cd '{directory}' && git add {files} 2>&1", timeout=30)
    result = _ssh(f"cd '{directory}' && git commit -m '{message}' 2>&1", timeout=30)

    if "nothing to commit" in result:
        return f"📋 커밋할 변경사항이 없습니다.\n{result}"
    elif "create mode" in result or "file changed" in result or "insertions" in result:
        return f"✅ 커밋 완료: {message}\n{result}"
    else:
        return f"📋 결과:\n{result}"


def git_push(directory: str = "", branch: str = "") -> str:
    """git push"""
    err = _ensure_token()
    if err:
        return err
    _setup_git_credential()

    if not directory:
        directory = "/root/workspace"
    if not branch:
        branch = _ssh(f"cd '{directory}' && git branch --show-current 2>&1", timeout=10).strip()
        if not branch:
            branch = "main"

    result = _ssh(f"cd '{directory}' && git push origin {branch} 2>&1", timeout=60)

    if "Everything up-to-date" in result:
        return f"✅ 이미 최신 상태입니다 ({branch})"
    elif "error" in result.lower() or "fatal" in result.lower():
        return f"❌ Push 실패:\n{result}"
    else:
        return f"✅ Push 완료 ({branch}):\n{result}"
