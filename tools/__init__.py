"""
Xoul 도구 패키지

카테고리:
- web_tools: URL 가져오기, 웹 검색 (Windows 직접)
- file_tools: 파일 읽기/쓰기/목록 (VM SSH)
- system_tools: 셸 명령, 날짜/시간, 계산 (VM SSH)
- code_tools: Python 코드 실행 (VM SSH)
- meta_tools: 도구 생성/관리 (동적 도구 시스템)
- memory_tools: 장기 기억/진화 (시맨틱 메모리)
- email_tools: 이메일 (IMAP/SMTP + App Password)
- calendar_tools: 일정 관리 (로컬 JSON)
- contact_tools: 연락처 관리 (로컬 JSON)
"""

import os
from i18n import t, get_language

from .web_tools import tool_fetch_url, tool_web_search, tool_summarize_url, tool_browse_url
from .notification_tools import tool_send_notification
from .file_tools import tool_read_file, tool_write_file, tool_list_files
from .system_tools import tool_run_command, tool_get_datetime, tool_calculate
from .code_tools import run_python_code, run_stored_code, list_codes, delete_code, create_code, share_to_store, stop_running_code, list_running_codes
from .meta_tools import create_tool, list_custom_tools, remove_tool, restore_custom_tools, evolve_skill, find_skill, install_service
from .memory_tools import remember, recall, forget, auto_retrieve, save_turn, check_and_summarize
from .workflow_tools import create_workflow, run_workflow, list_workflows, view_workflow, update_workflow, delete_workflow, get_notifications, ask_user
from .persona_tools import create_persona, list_personas, activate_persona, update_persona, delete_persona
from .weather_tools import tool_weather
from .git_tools import git_clone, git_status, git_commit, git_push
try:
    from vm_manager import scp_to_vm, scp_from_vm, SHARE_DIR
except ImportError:
    # VM 내부 실행 시: vm_manager 불필요, 로컬 경로 사용
    SHARE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "share")
    scp_to_vm = None
    scp_from_vm = None


# ─────────────────────────────────────────────
# 호스트(Windows) 파일 도구
# ─────────────────────────────────────────────

def _host_list_files(path: str = "") -> str:
    """호스트 share/ 폴더 파일 목록"""
    target = os.path.join(SHARE_DIR, path) if path else SHARE_DIR
    os.makedirs(SHARE_DIR, exist_ok=True)
    
    if not os.path.exists(target):
        return t("tools.path_not_found", path=target)
    
    if os.path.isfile(target):
        size = os.path.getsize(target)
        return t("tools.file_info", name=os.path.basename(target), size=_format_size(size))
    
    items = []
    try:
        for entry in os.scandir(target):
            if entry.is_file():
                size = entry.stat().st_size
                items.append(f"  📄 {entry.name} ({_format_size(size)})")
            elif entry.is_dir():
                count = len(os.listdir(entry.path))
                items.append(f"  📁 {entry.name}/ ({count} items)")
    except Exception as e:
        return t("tools.error", error=str(e))
    
    if not items:
        return t("tools.share_empty", path=SHARE_DIR)
    
    header = f"📂 share/{path} ({len(items)} items):"
    return header + "\n" + "\n".join(items)




def _host_to_vm(filename: str, vm_path: str = "") -> str:
    """호스트 share/ → VM /root/share/ 파일 복사 (스마트 검색 + 중복 방지)"""
    import hashlib

    # 1) 정확한 파일 먼저 찾기
    local_path = os.path.join(SHARE_DIR, filename)
    if not os.path.isfile(local_path):
        # 2) share/ 하위 재귀 검색 (부분 매칭)
        found = _find_file_in_share(filename)
        if len(found) == 1:
            local_path = found[0]
        elif len(found) > 1:
            names = [os.path.relpath(f, SHARE_DIR) for f in found]
            return t("tools.multiple_files_found") + "\n" + "\n".join(f"  📄 {n}" for n in names)
        else:
            return t("tools.file_not_found_in_share", filename=filename, path=SHARE_DIR)

    # 3) VM 대상 경로 결정 (항상 /root/share/ 기준)
    if not vm_path:
        vm_path = f"/root/share/{os.path.basename(local_path)}"

    # 4) 중복 체크 (MD5 해시 비교)
    file_hash = _file_md5(local_path)
    log = _load_transfer_log()
    log_key = f"htv:{os.path.basename(local_path)}"
    if log.get(log_key, {}).get("hash") == file_hash:
        return t("tools.file_already_up_to_date", name=os.path.basename(local_path), vm_path=vm_path)

    # 5) SCP 전송
    result = scp_to_vm(local_path, vm_path)

    # 6) 성공 시 로그 기록
    if "✅" in result:
        _save_transfer_log(log_key, file_hash, "htv", local_path, vm_path)

    return result


def _vm_to_host(vm_path: str, filename: str = "") -> str:
    """VM → 호스트 share/ 파일 복사 (스마트 검색 + 중복 방지)"""
    import hashlib
    import shutil

    # 1) vm_path가 전체 경로가 아닌 파일명만이면 VM에서 검색
    if "/" not in vm_path:
        # VM 내부 실행: 직접 find
        if scp_from_vm is None:
            import subprocess
            try:
                result = subprocess.run(
                    ["find", "/root", "/tmp", "-name", f"*{vm_path}*", "-type", "f"],
                    capture_output=True, text=True, timeout=10
                )
                lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
                if lines:
                    vm_path = lines[0]
                else:
                    return t("tools.file_not_found_in_vm", path=vm_path)
            except Exception:
                return t("tools.file_not_found_in_vm", path=vm_path)
        else:
            found = _find_file_in_vm(vm_path)
            if found:
                vm_path = found
            else:
                return t("tools.file_not_found_in_vm", path=vm_path)

    # 2) 로컬 저장 경로
    if not filename:
        filename = os.path.basename(vm_path)
    local_path = os.path.join(SHARE_DIR, filename)
    os.makedirs(SHARE_DIR, exist_ok=True)

    # 3) 파일 복사 (VM 내부: shutil.copy / 호스트: SCP)
    try:
        if scp_from_vm is None:
            # VM 내부 실행 → 로컬 파일 복사
            if not os.path.isfile(vm_path):
                return t("tools.file_not_found", path=vm_path)
            shutil.copy2(vm_path, local_path)
            result = f"✅ {vm_path} → share/{filename} copied"
        else:
            # 호스트에서 실행 → SCP
            result = scp_from_vm(vm_path, local_path)
    except Exception as e:
        return t("tools.file_copy_failed", error=str(e))

    # 4) 성공 시 로그 기록
    if "✅" in result and os.path.isfile(local_path):
        file_hash = _file_md5(local_path)
        log_key = f"vth:{filename}"
        _save_transfer_log(log_key, file_hash, "vth", vm_path, local_path)

    # 5) VM 내부 실행 시: 호스트 sync 서버에 SCP pull 요청
    if scp_from_vm is None and "✅" in result:
        try:
            import urllib.request, json as _json
            sync_data = _json.dumps({
                "filename": filename,
                "vm_path": local_path  # VM share/ 경로
            }).encode()
            sync_req = urllib.request.Request(
                "http://10.0.2.2:3100/sync",
                data=sync_data,
                headers={"Content-Type": "application/json"},
            )
            sync_resp = urllib.request.urlopen(sync_req, timeout=15)
            sync_result = _json.loads(sync_resp.read())
            if sync_result.get("status") == "ok":
                result += f" → transferred to host PC"
        except Exception as e:
            # sync 서버가 없어도 VM share/ 복사는 성공으로 유지
            print(f"[vm_to_host] Host sync failed (sync server not running?): {e}")

    return result


def _find_file_in_share(query: str) -> list:
    """share/ 디렉토리에서 파일명 부분 매칭 검색"""
    os.makedirs(SHARE_DIR, exist_ok=True)
    results = []
    query_lower = query.lower()
    for root, dirs, files in os.walk(SHARE_DIR):
        for f in files:
            if f.startswith("."):
                continue
            if query_lower in f.lower():
                results.append(os.path.join(root, f))
    return results


def _find_file_in_vm(query: str) -> str:
    """VM에서 파일명 검색 (/root/share/ 와 /root/workspace/ 탐색)"""
    if scp_from_vm is None:
        return ""
    try:
        from vm_manager import ssh_exec
        result = ssh_exec(
            f"find /root/share /root/workspace /root/.xoul/workspace -name '*{query}*' -type f 2>/dev/null | head -5",
            timeout=10, quiet=True
        )
        if result and not result.startswith("["):
            lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
            return lines[0] if lines else ""
    except Exception:
        pass
    return ""


def _file_md5(filepath: str) -> str:
    """파일 MD5 해시 계산"""
    import hashlib
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


_TRANSFER_LOG_PATH = os.path.join(SHARE_DIR, ".transfer_log.json")


def _load_transfer_log() -> dict:
    """전송 로그 로드"""
    import json as _json
    os.makedirs(SHARE_DIR, exist_ok=True)
    if os.path.isfile(_TRANSFER_LOG_PATH):
        try:
            with open(_TRANSFER_LOG_PATH, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    return {}


def _save_transfer_log(key: str, file_hash: str, direction: str, src: str, dst: str):
    """전송 로그 저장"""
    import json as _json
    from datetime import datetime
    log = _load_transfer_log()
    log[key] = {
        "hash": file_hash,
        "direction": direction,
        "src": src,
        "dst": dst,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        with open(_TRANSFER_LOG_PATH, "w", encoding="utf-8") as f:
            _json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _format_size(size: int) -> str:
    """파일 크기 포맷"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size/1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size/1024/1024:.1f} MB"
    else:
        return f"{size/1024/1024/1024:.1f} GB"


def _write_file_and_transfer(path: str, content: str) -> str:
    """write_file 후 자동으로 vm_to_host 실행하여 호스트 PC로 전송"""
    result = tool_write_file(path, content)
    if "Error" in result or "오류" in result:
        return result

    # 자동 전송
    filename = os.path.basename(path)
    try:
        transfer_result = _vm_to_host(path, filename)
        host_path = os.path.join(SHARE_DIR, filename)
        if "✅" in transfer_result:
            result += t("tools.auto_transfer_ok", host_path=host_path)
        else:
            result += t("tools.auto_transfer_fail", result=transfer_result)
    except Exception as e:
        result += t("tools.auto_transfer_error", error=str(e))

    return result


# ─────────────────────────────────────────────
# 도구 설명 (시스템 프롬프트에 포함)
# ─────────────────────────────────────────────

TOOL_DESCRIPTIONS = """\
# Available Tools

⚠ Important Rules:
- When including URLs in responses, always output them as-is: https://example.com. NEVER use HTML tags (<a href=...>)!
- For "open" requests, use host_open_url. When recommending multiple URLs, list bare URLs in text.

## Web/Internet Tools
- web_search: Web search (Tavily→DuckDuckGo→Google) + auto-fetch top result bodies. Params: {"query": "search query"}
  Returns search result list plus auto-fetched body of the first result.
- summarize_url: Extract key content from a URL page (for summarization). Params: {"url": "URL"}
- browse_url: Read pages requiring JS rendering (headless browser). Params: {"url": "URL"}

## Notification Tools
- send_notification: Send notification via Telegram. Params: {"message": "content", "title": "title(optional)"}

## File Management Tools
- read_file: Read/view/output file contents. Use for read requests. Params: {"path": "file_path"}
- write_file: Create new file or modify existing. Auto-transfers to host PC share/ folder after saving. Params: {"path": "file_path", "content": "content"}
  ⚠ Do NOT use write_file for read requests! Use read_file only for reading.
- list_files: List files. Params: {"path": "directory_path"}

## System Tools
- run_command: Execute Linux shell command. Params: {"command": "command"}
- get_datetime: Get current date/time
- calculate: Math calculation. Params: {"expression": "expression"}
- run_python_code: Run Python code. Params: {"code": "code", "description": "description"}
- run_stored_code: Execute imported code with parameters. Params: {"name": "code_name", "params": "{\"game_id\": \"abc\", \"agent_name\": \"Bot\"}", "timeout": "600"}
  ⚠ Use this tool when asked to "run code"! Code name is looked up in the codes DB.
  ⚠ If a code has required params (marked with * in list_codes), ASK THE USER for values before executing! Use default values for optional params if not provided.
- list_codes: List imported codes. No params. ⚠ Always call this tool for "show list" requests. Do NOT summarize from memory! (Required for Desktop UI rendering)
- delete_code: Delete an imported code. Params: {"name": "code_name"}
- stop_running_code: Stop a running code (e.g. arena agent). Params: {"name": "code name (partial match)"} — If only 1 code running, name is optional.
- list_running_codes: List currently running codes. No params.
- share_to_store: Share local code/workflow/persona to Xoul Store (creates GitHub PR). Params: {"share_type": "code|workflow|persona", "name": "item_name", "category": "Other"}
- create_code: Create a new code snippet. Params: {"name": "code_name", "code": "python code", "description": "what it does", "category": "Other", "params": "[{\"name\":\"x\",\"type\":\"str\",\"desc\":\"input\"}]"}
- pip_install: Install Python package. Params: {"package": "package_name"} (Use when ModuleNotFoundError occurs)

## Host File Tools (share/ folder)
- host_list_files: List share/ files. Params: {"path": "subpath"}
- host_to_vm: Copy share/ → VM /root/share/. Supports partial filename matching. Prevents duplicate transfers. Params: {"filename": "filename"}
- vm_to_host: Copy VM → share/. Auto-searches VM if only filename given. Params: {"vm_path": "path or filename", "filename": "save filename"}

## Weather Tools
- weather: Get weather/temperature/forecast. Current weather + 3-day forecast (max/min temps, precipitation probability, wind, sunrise/sunset). Params: {"location": "city name (Korean/English)", "days": 3}

## Host PC Tools (runs on user's Windows)
- host_open_url: Open URL in user's browser. Params: {"url": "https://..."}
  ⚠ Use for "open Google", "play YouTube" requests. file://, javascript:// blocked.
- host_find_file: Search files on user's PC (Desktop/Documents/Downloads only). Params: {"query": "search term", "directory": "path(optional)"}
  ⚠ Search by filename only, does not read content.
- host_show_notification: Show notification on user's PC. Params: {"title": "title", "message": "content"}
- host_open_app: Launch app on user's Windows. Params: {"app_name": "English program name"}
  ⚠ Must use English program names! E.g., "open CapCut" → {"app_name": "CapCut"}
  ⚠ Do NOT use run_command to launch Windows programs!
- host_organize_files: Organize/move files. Params: {"source": "source_folder", "destination": "dest_folder", "pattern": "*.pdf"}
- host_run_command: Execute PowerShell command on user's Windows. Params: {"command": "command"}
  ⚠ run_command is for Linux VM only. For Windows commands, use host_run_command!

## Meta Tools
- create_tool: Create custom tool
- list_custom_tools: List custom tools
- remove_tool: Delete custom tool. Params: {"name": "tool_name"}

## Memory Tools
- remember: Save to memory. Params: {"key": "key", "value": "value"}
- recall: Search memories. Params: {"query": "search query"}
- forget: Delete memory. Params: {"key": "key"}

## Skill Tools
- evolve_skill: Save skill. Params: {"name": "skill_name", "description": "desc", "trigger_keywords": "keywords", "method": "method"}
- find_skill: Search skills. Params: {"query": "search query"}

## Workflow Tools (multi-step task automation + scheduler)
- create_workflow: Create workflow. Params: {"name": "name", "prompts": "prompt1\nprompt2\nprompt3", "description": "desc", "hint_tools": "tool1,tool2", "schedule": "daily 08:00"}
- run_workflow: Execute workflow. Params: {"name": "name"}
- list_workflows: List workflows. ⚠ Always call this tool for "show list" requests. Do NOT summarize from memory! (Required for Desktop UI rendering)
- view_workflow: View workflow steps in detail. Params: {"name": "name"} — Use to show current steps before editing
- update_workflow: Modify workflow. Params: {"name": "name", "prompts": "new prompts", "description": "new desc", "schedule": "new schedule"}
- delete_workflow: Delete workflow. Params: {"name": "name"} — ⚠ Always call this tool. Pass exact name user said, the tool handles fuzzy matching.
- ask_user: Ask user a question and wait for answer during workflow. Params: {"question": "question text", "fields": "field1,field2(optional)"}

## Service Tools
- install_service: Register systemd service. Params: {"name": "name", "description": "desc", "command": "command"}

## Git Tools (GitHub PAT auth)
- git_clone: Clone GitHub repo. Params: {"repo_url": "https://github.com/user/repo.git", "directory": "/path(optional)"}
- git_status: Check repo status. Params: {"directory": "/path"}
- git_commit: Commit changes. Params: {"message": "commit message", "directory": "/path", "files": "."}
- git_push: Push to remote. Params: {"directory": "/path", "branch": "branch_name"}

## Persona Tools (AI role/personality management)
- list_personas: List all personas. ⚠ Always call this tool for "show list" requests. Do NOT summarize from memory! (Required for Desktop UI rendering)
- activate_persona: Activate persona (switch conversation mode). Params: {"name": "name"}
- create_persona: Create new persona. Params: {"name": "name", "prompt": "system prompt", "description": "desc"}
- delete_persona: Delete persona. Params: {"name": "name"}

## Greeting Tool
- greeting: Generate a greeting message with current time/date context. Called automatically when user first connects. Params: {"user_name": "optional user name"}
"""

EMAIL_TOOL_DESCRIPTIONS = """\
## Email Tools (Gmail IMAP/SMTP)
- send_email: Send email. Params: {"to": "recipient", "subject": "subject", "body": "body", "attachments": ["/path/file.pdf"]} (attachments optional)
- list_emails: List emails. Params: {"query": "UNSEEN", "max_results": 5}
- read_email: Read email. Params: {"email_id": "email_ID"}
"""

CALENDAR_TOOL_DESCRIPTIONS = """\
## Calendar Tools (local calendar)
- create_event: Create event. Params: {"title": "title", "date": "2026-02-20", "time": "14:00", "duration": "60", "description": "desc"}
- list_events: List events. Params: {"date": "2026-02-18", "days": 1} (default: today)
- delete_event: Delete event. Params: {"event_id": "ID"}
- update_event: Update event. Params: {"event_id": "ID", "title": "new_title", "date": "new_date", "time": "new_time"}
"""

CONTACT_TOOL_DESCRIPTIONS = """\
## Contact Tools
- add_contact: Add contact. Params: {"name": "name", "phone": "number", "email": "email", "memo": "memo"}
- find_contact: Search contacts. Params: {"query": "search query"}
- list_contacts: List all contacts
- delete_contact: Delete contact. Params: {"name": "name"}
"""


# ─────────────────────────────────────────────
# 도구 매핑 (이름 → 실행 함수)
# ─────────────────────────────────────────────

TOOLS = {
    # 웹/인터넷 도구 (Windows 직접)
    "web_search": lambda args: tool_web_search(args["query"]),
    "summarize_url": lambda args: tool_summarize_url(args["url"]),
    "browse_url": lambda args: tool_browse_url(args["url"]),
    # 날씨 도구
    "weather": lambda args: tool_weather(args["location"], int(args.get("days", 3))),
    # 알림 도구
    "send_notification": lambda args: tool_send_notification(args["message"], args.get("title", "")),
    # 파일 관리 도구 (VM SSH)
    "read_file": lambda args: tool_read_file(args["path"]),
    "write_file": lambda args: _write_file_and_transfer(args["path"], args["content"]),
    "list_files": lambda args: tool_list_files(args.get("path", ".")),
    # 시스템 도구 (VM SSH)
    "run_command": lambda args: tool_run_command(args["command"]),
    "get_datetime": lambda args: tool_get_datetime(),
    "calculate": lambda args: tool_calculate(args["expression"]),
    # 코드 실행 (VM SSH) — on_output은 execute_tool에서 주입
    "run_python_code": lambda args, **kw: run_python_code(args["code"], args.get("description", ""), args.get("timeout", "30"), on_output=kw.get("on_output")),
    "run_stored_code": lambda args, **kw: run_stored_code(args["name"], args.get("params", "{}"), args.get("timeout", "30"), on_output=kw.get("on_output")),
    "list_codes": lambda args, **kw: list_codes(),
    "delete_code": lambda args, **kw: delete_code(args["name"]),
    "share_to_store": lambda args, **kw: share_to_store(args["share_type"], args["name"], args.get("category", "Other")),
    "create_code": lambda args, **kw: create_code(args["name"], args["code"], args.get("description", ""), args.get("category", "Other"), args.get("params", "[]")),
    "stop_running_code": lambda args, **kw: stop_running_code(args.get("name", "")),
    "list_running_codes": lambda args, **kw: list_running_codes(),
    "pip_install": lambda args: _pip_install(args["package"]),
    # 호스트 파일 도구 (Windows share/)
    "host_list_files": lambda args: _host_list_files(args.get("path", "")),
    "host_to_vm": lambda args: _host_to_vm(args["filename"], args.get("vm_path", "")),
    "vm_to_host": lambda args: _vm_to_host(args["vm_path"], args.get("filename", "")),
    # 호스트 PC 도구 (데스크톱 앱에서 실행, 서버는 스텁)
    "host_open_url": lambda args: t("tools.host_url_opened", url=args.get('url', '')),
    "host_find_file": lambda args: t("tools.host_file_search_done"),
    "host_show_notification": lambda args: t("tools.host_notification_shown"),
    "host_open_app": lambda args: t("tools.host_app_launched", app=args.get('app_name', args.get('name', ''))),
    "host_organize_files": lambda args: t("tools.host_files_organized"),
    "host_run_command": lambda args: t("tools.host_command_executed"),
    # 메타 도구
    "create_tool": lambda args: create_tool(
        args["name"], args["description"], args["command_template"],
        args.get("packages", ""), args.get("parameters", "")
    ),
    "list_custom_tools": lambda args: list_custom_tools(),
    "remove_tool": lambda args: remove_tool(args["name"]),
    # 기억 도구 (시맨틱 메모리)
    "remember": lambda args: remember(args["key"], args["value"]),
    "recall": lambda args: recall(args.get("query", "")),
    "forget": lambda args: forget(args["key"]),
    # 스킬 도구
    "evolve_skill": lambda args: evolve_skill(
        args["name"], args["description"], args["trigger_keywords"],
        args["method"], args.get("packages", ""), args.get("script", "")
    ),
    "find_skill": lambda args: find_skill(args["query"]),
    "get_notifications": lambda args: get_notifications(args.get("unread_only", True)),
    # Workflow 도구
    "create_workflow": lambda args: create_workflow(
        args["name"], args["prompts"],
        args.get("description", ""), args.get("hint_tools", ""), args.get("schedule", "")
    ),
    "run_workflow": lambda args: run_workflow(args["name"]),
    "list_workflows": lambda args: list_workflows(args.get("page", "1"), args.get("per_page", "10")),
    "view_workflow": lambda args: view_workflow(args["name"]),
    "update_workflow": lambda args: update_workflow(
        args["name"], args.get("prompts", ""),
        args.get("description", ""), args.get("schedule", "__KEEP__")
    ),
    "delete_workflow": lambda args: delete_workflow(args["name"]),
    "ask_user": lambda args: ask_user(args["question"], args.get("fields", "")),
    # 서비스 도구
    "install_service": lambda args: install_service(
        args["name"], args["description"], args["command"],
        args.get("schedule", ""), args.get("oneshot", False)
    ),
    # Git 도구
    "git_clone": lambda args: git_clone(args["repo_url"], args.get("directory", "")),
    "git_status": lambda args: git_status(args.get("directory", "")),
    "git_commit": lambda args: git_commit(args["message"], args.get("directory", ""), args.get("files", ".")),
    "git_push": lambda args: git_push(args.get("directory", ""), args.get("branch", "")),
    # 페르소나 도구
    "list_personas": lambda args: list_personas(args.get("page", "1"), args.get("per_page", "10")),
    "activate_persona": lambda args: activate_persona(args["name"]),
    "create_persona": lambda args: create_persona(
        args["name"], args["prompt"], args.get("description", ""),
        args.get("bg_image", "")
    ),
    "update_persona": lambda args: update_persona(
        args["name"], args.get("prompt", ""), args.get("description", ""),
        args.get("new_name", "")
    ),
    "delete_persona": lambda args: delete_persona(args["name"]),
    # Greeting tool
    "greeting": lambda args: _greeting(args.get("user_name", "")),
}


def register_pim_tools():
    """PIM 도구 등록 (이메일/일정/연락처)"""
    global TOOL_DESCRIPTIONS

    # 1) 일정 도구 (항상 등록)
    try:
        from .calendar_tools import create_event, list_events, delete_event, update_event
        TOOLS.update({
            "create_event": lambda args: create_event(
                args["title"], args["date"],
                args.get("time", ""), args.get("duration", "60"), args.get("description", "")
            ),
            "list_events": lambda args: list_events(args.get("date", ""), args.get("days", 1)),
            "delete_event": lambda args: delete_event(args["event_id"]),
            "update_event": lambda args: update_event(
                args["event_id"],
                args.get("title", ""), args.get("date", ""),
                args.get("time", ""), args.get("description", "")
            ),
        })
        TOOL_DESCRIPTIONS += "\n" + CALENDAR_TOOL_DESCRIPTIONS
        print(f"  ✅ Calendar tools registered")
    except Exception as e:
        print(f"  ⚠ Calendar tools load failed: {e}")

    # 2) 연락처 도구 (항상 등록)
    try:
        from .contact_tools import add_contact, find_contact, list_contacts, delete_contact
        TOOLS.update({
            "add_contact": lambda args: add_contact(
                args["name"], args.get("phone", ""),
                args.get("email", ""), args.get("memo", "")
            ),
            "find_contact": lambda args: find_contact(args["query"]),
            "list_contacts": lambda args: list_contacts(),
            "delete_contact": lambda args: delete_contact(args["name"]),
        })
        TOOL_DESCRIPTIONS += "\n" + CONTACT_TOOL_DESCRIPTIONS
        print(f"  ✅ Contact tools registered")
    except Exception as e:
        print(f"  ⚠ Contact tools load failed: {e}")

    # 3) 이메일 도구 (config에서 enabled일 때)
    try:
        import json
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
        with open(config_path, encoding="utf-8-sig") as f:
            config = json.load(f)
        if config.get("email", {}).get("enabled"):
            from .email_tools import send_email, list_emails, read_email
            TOOLS.update({
                "send_email": lambda args: send_email(args["to"], args["subject"], args["body"], args.get("attachments")),
                "list_emails": lambda args: list_emails(args.get("query", "UNSEEN"), args.get("max_results", 5)),
                "read_email": lambda args: read_email(args["email_id"]),
            })
            TOOL_DESCRIPTIONS += "\n" + EMAIL_TOOL_DESCRIPTIONS
            print(f"  ✅ Email tools registered")
    except Exception as e:
        print(f"  ⚠ Email tools load failed: {e}")


# 스트리밍 지원 도구 목록 (on_output 콜백 전달)
_STREAMING_TOOLS = {"run_python_code", "run_stored_code"}

def execute_tool(name: str, arguments: dict, on_output=None) -> str:
    """도구 실행. on_output이 제공되면 코드 실행 도구에 실시간 출력 콜백으로 전달."""
    fn = TOOLS.get(name)
    if not fn:
        return t("tools.unknown_tool", name=name)
    # LLM이 중첩 형태로 보낼 때 보정: {"name":"tool","arguments":{"real":"args"}} → {"real":"args"}
    if "arguments" in arguments and isinstance(arguments["arguments"], dict):
        nested = arguments.pop("arguments")
        arguments.update(nested)
    try:
        if on_output and name in _STREAMING_TOOLS:
            return fn(arguments, on_output=on_output)
        return fn(arguments)
    except KeyError as e:
        # 어떤 파라미터가 필요한지 힌트 제공
        tool_hints = {
            "write_file": '{"path": "/path/filename", "content": "content"}',
            "read_file": '{"path": "/path/filename"}',
            "run_command": '{"command": "command"}',
            "send_email": '{"to": "email", "subject": "subject", "body": "body", "attachments": ["/path/file"]}',
            "run_python_code": '{"code": "python_code", "description": "description"}',
        }
        hint = tool_hints.get(name, "")
        hint_str = f" Correct format: {hint}" if hint else ""
        return t("tools.missing_param", name=name, param=str(e), hint=hint_str)
    except Exception as e:
        return t("tools.exec_error", error=str(e))

def _greeting(user_name: str = "") -> str:
    """현재 시각/날짜 컨텍스트를 수집하여 LLM이 상황에 맞는 인사말을 생성하도록 반환"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    hour = now.hour
    lang = get_language()

    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday_ko = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

    if lang == "ko":
        if hour < 6:
            time_period = "새벽"
        elif hour < 12:
            time_period = "아침"
        elif hour < 18:
            time_period = "오후"
        else:
            time_period = "저녁"
    else:
        if hour < 6:
            time_period = "dawn"
        elif hour < 12:
            time_period = "morning"
        elif hour < 18:
            time_period = "afternoon"
        else:
            time_period = "evening"

    is_weekend = now.weekday() >= 5

    context_parts = [
        f"Current time: {now.strftime('%Y-%m-%d %H:%M')}",
        f"Day: {weekday_ko[now.weekday()]} ({weekday_names[now.weekday()]})",
        f"Time period: {time_period}",
        f"Weekend: {'Yes' if is_weekend else 'No'}",
    ]
    if user_name:
        context_parts.append(f"User name: {user_name}")

    # Holiday check
    try:
        import holidays
        kr_holidays = holidays.Korea(years=now.year)
        today = now.date()
        if today in kr_holidays:
            context_parts.append(f"Holiday: {kr_holidays[today]}")
    except ImportError:
        pass

    if lang == "ko":
        instruction = (
            "위 정보를 바탕으로 따뜻한 한국어 인사말을 생성하세요. "
            "자연스럽고 간결하게 (2~3문장). 시간대나 요일 등 상황에 맞는 멘트를 포함하세요. "
            "주말이나 공휴일이면 언급해주세요."
        )
    else:
        instruction = (
            "Please greet the user warmly based on the current time of day. "
            "Be natural and concise (2-3 sentences). Mention relevant context like time of day or day of week. "
            "If it's a holiday or weekend, acknowledge it."
        )

    return (
        "Greeting context (use this to generate a warm, natural greeting):\n"
        + "\n".join(context_parts)
        + f"\n\n{instruction}"
    )


def _pip_install(package: str) -> str:
    """Python 패키지 설치 (VM에서 pip install 실행)"""
    import subprocess
    # 보안: 패키지명에 위험한 문자 방지
    safe = "".join(c for c in package if c.isalnum() or c in "-_.=<>!~,[]")
    if not safe:
        return t("tools.invalid_package")
    if os.path.exists("/root/xoul") or os.name != "nt":
        # VM 내부
        try:
            result = subprocess.run(
                f"pip install {safe} 2>&1",
                shell=True, capture_output=True, text=True, timeout=120
            )
            output = result.stdout + result.stderr
            if result.returncode == 0:
                return t("tools.install_ok", package=safe)
            return t("tools.install_fail", output=output[:500])
        except Exception as e:
            return t("tools.error", error=str(e))
    else:
        # Windows → SSH
        try:
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            from vm_manager import ssh_exec
            result = ssh_exec(f"pip install {safe} 2>&1", timeout=120)
            if "Successfully installed" in result or "already satisfied" in result:
                return t("tools.install_ok", package=safe)
            return t("tools.install_result", result=result[:500])
        except Exception as e:
            return t("tools.error", error=str(e))


# 시작 시 저장된 커스텀 도구 복원
try:
    restore_custom_tools()
except Exception:
    pass
