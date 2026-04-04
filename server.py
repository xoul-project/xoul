"""
Xoul REST API 서버

에이전트를 HTTP API로 노출합니다.
VM 안에서 실행되며, 다양한 클라이언트(터미널, 텔레그램, 웹)가 접속합니다.

사용법:
    python server.py [--config config.json]
    또는: uvicorn server:app --host 0.0.0.0 --port 3000
"""

import json
import os
import sys
import uuid
import secrets
import asyncio
import threading
from datetime import datetime
from typing import Optional

# ─────────────────────────────────────────────
# 전역 상태
# ─────────────────────────────────────────────
_llm_busy = threading.Lock()   # LLM 동시 접근 방지 (스케줄러 ↔ 채팅)
_scheduler_running = True       # 스케줄러 루프 제어

# ─────────────────────────────────────────────
# 활동 로그 (날짜별 JSONL)
# ─────────────────────────────────────────────

LOG_DIR = os.path.expanduser("~/.xoul/logs")
os.makedirs(LOG_DIR, exist_ok=True)

def log_activity(event_type: str, **data):
    """활동을 날짜별 로그 파일에 기록"""
    try:
        now = datetime.now()
        entry = {
            "time": now.strftime("%H:%M:%S"),
            "type": event_type,
            **data
        }
        log_file = os.path.join(LOG_DIR, now.strftime("%Y-%m-%d") + ".jsonl")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            # 응답 완료 시 빈 줄로 대화 분리
            if event_type in ("response", "max_tools_reached"):
                f.write("\n")
    except Exception:
        pass  # 로그 실패가 서버를 죽이면 안 됨

# FastAPI 임포트 (없으면 설치 안내)
try:
    from fastapi import FastAPI, HTTPException, Depends, Request
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print("❌ FastAPI is not installed.")
    print("   pip install fastapi uvicorn pydantic")
    sys.exit(1)

from llm_client import LLMClient

# ─────────────────────────────────────────────
# 설정 & 초기화
# ─────────────────────────────────────────────

CONFIG_PATH = os.environ.get("XOUL_CONFIG", "config.json")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        return json.load(f)

config = load_config()

# i18n 초기화 (locale 파일 로드)
from i18n import init_from_config as _init_i18n
_init_i18n(config)

# API 키 (config에 없으면 자동 생성)
server_cfg = config.get("server", {})
API_KEY = server_cfg.get("api_key", "")
if not API_KEY:
    API_KEY = secrets.token_urlsafe(32)
    # config에 저장
    config.setdefault("server", {})["api_key"] = API_KEY
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"🔑 API key generated: {API_KEY}")

# LLM 클라이언트
llm_client = LLMClient(config)

# PIM 도구 등록 (일정/연락처/이메일)
try:
    from tools import register_pim_tools
    register_pim_tools()
    # OPENAI_TOOLS에도 PIM 도구 추가 (LLM이 인식하도록)
    from assistant_agent import OPENAI_TOOLS, PIM_OPENAI_TOOLS, EMAIL_OPENAI_TOOLS
    OPENAI_TOOLS.extend(PIM_OPENAI_TOOLS)
    email_enabled = config.get("email", {}).get("enabled", False)
    print(f"🔍 email.enabled = {email_enabled} (type={type(email_enabled).__name__})")
    if email_enabled:
        OPENAI_TOOLS.extend(EMAIL_OPENAI_TOOLS)
        print(f"✅ Email tools registered (send_email, list_emails, read_email)")
    else:
        print(f"⚠ Email not registered: email.enabled={email_enabled}")
    print(f"✅ PIM tools registered (calendar/contacts)")
    print(f"🔍 OPENAI_TOOLS total {len(OPENAI_TOOLS)}: {[t['function']['name'] for t in OPENAI_TOOLS]}")
except Exception as e:
    import traceback
    print(f"⚠ PIM tool load failed: {e}")
    traceback.print_exc()

# Tool Registry 초기화 (Base Tools + Toolkit 계층 구조)
try:
    from tools.tool_registry import init as init_tool_registry
    init_tool_registry(OPENAI_TOOLS)
except Exception as e:
    print(f"⚠ ToolRegistry init failed (fallback to flat mode): {e}")

# ─────────────────────────────────────────────
# 시스템 프로필 (시작 시 1회 수집)
# ─────────────────────────────────────────────

def _collect_system_profile() -> str:
    """Linux 시스템 정보를 수집하여 프롬프트용 문자열 반환"""
    import subprocess
    lines = []
    cmds = {
        "OS": "cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
        "Kernel": "uname -r",
        "Memory": "free -h | awk '/Mem:/{print $2}'",
        "Disk": "df -h / | awk 'NR==2{print $4 \" free / \" $2 \" total\"}'",
        "Python": "python3 --version 2>&1",
        "Main packages": "dpkg -l | grep -E 'curl|wget|ffmpeg|imagemagick|jq|pandoc|git' | awk '{print $2}' | tr '\\n' ', '",
        "pip packages": "pip3 list --format=columns 2>/dev/null | tail -n +3 | awk '{print $1}' | tr '\\n' ', '",
    }
    for label, cmd in cmds.items():
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            val = result.stdout.strip()
            if val:
                lines.append(f"- {label}: {val[:200]}")
        except Exception:
            pass
    if lines:
        profile = "## System Environment\n" + "\n".join(lines)
    else:
        profile = ""
    
    # User profile injection
    user = config.get("user", {})
    email_cfg = config.get("email", {})
    if user.get("name"):
        user_parts = [f"- Name: {user['name']}"]
        if user.get("location"):
            user_parts.append(f"- Location: {user['location']}")
        if user.get("timezone"):
            user_parts.append(f"- Timezone: {user['timezone']}")
        if email_cfg.get("enabled") and email_cfg.get("address"):
            user_parts.append(f"- Email: {email_cfg['address']}")
        profile = "## User Profile\n" + "\n".join(user_parts) + "\n\n" + profile
    
    return profile

# 시작 시 1회 수집
_system_profile = _collect_system_profile()
if _system_profile:
    print(f"📋 System profile collected")

# ─────────────────────────────────────────────
# 세션 관리
# ─────────────────────────────────────────────

class Session:
    """대화 세션"""
    def __init__(self, session_id: str, system_prompt: str):
        self.id = session_id
        self.messages = [{"role": "system", "content": system_prompt}]
        self.created_at = datetime.now()
        self.last_active = datetime.now()
        # Persona mode state
        self.persona_active = False
        self.persona_name = ""
        self.persona_prompt = ""
        self.persona_bg_image = ""
        # Context summarization cache
        self.context_summary = ""       # 이전 대화 요약 캐시
        self.summary_turn_count = 0     # 요약이 커버하는 턴 수
        # Workflow pause/resume state
        self.pending_wf = None          # {"wf_name": str, "remaining": list, "total": int}
        self._needs_input = False       # run_stored_code가 파라미터 입력 필요 신호 보냄

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})
        self.last_active = datetime.now()
        # STM: 사용자/어시스턴트 messages를 Disk에 즉시 저장 (크래시 복구용)
        if role in ("user", "assistant") and content:
            try:
                from tools.memory_tools import save_turn
                save_turn(role, content)
            except Exception:
                pass

    def compact_after_turn(self):
        """
        턴 완료 후 Context cleanup.

        유지: 시스템 프롬프트(index 0) + 유저 messages + 어시스턴트 최종 응답
        제거: 매 턴 주입되는 일회성 시스템 messages, tool messages, 계획/실행 중간 messages

        이렇게 하면:
        - 대화 맥락(유저 질문 + AI 답변)은 100% 보존
        - 도구 호출 중간 과정, 시간 정보, Memory 힌트 등 일회성 내용 제거
        - 컨text 크기가 턴 수에 비례하게 됨 (도구 수에 비례X)
        """
        # 일회성 system messages 패턴 (첫 번째 시스템프롬프트 제외)
        TRANSIENT_PREFIXES = (
            "Current time:",         # Time info (English)
            "현재 시각:",           # Time info (Korean legacy)
            "[시맨틱 Memory",        # Memory injection
            "📌 저장된 기억",        # Memory full inject (auto_retrieve)
            "🧠 관련 기억",          # Semantic match memories (auto_retrieve)
            "📋 이전 대화",          # MTM summary (auto_retrieve)
            "[System Instruction]", # Nudge messages (English)
            "[시스템 지시]",         # Nudge messages (Korean legacy)
            "⚠ '",                 # Duplicate call warning
            "위 검색에서",           # web_search chain guide
            "위 페이지에서",         # fetch_url chain guide
            "[Execution Plan]",    # Plan text (English)
            "[실행 계획]",          # Plan text (Korean legacy)
            "═══",                  # Turn boundary marker
            "████",                 # Turn boundary marker (box)
            "📝 **출력 형식**",     # Markdown guide
            "[Output Format]",     # Markdown format guide (English)
            "[출력 형식]",          # Markdown format guide (Korean legacy)
            "[Installed Apps]",    # Host app list (English)
            "[설치된 앱 목록]",      # Host app list (Korean legacy)
            "--- NEW REQUEST ---",  # Turn separator marker
            "⚠️ REMINDER:",        # Tool-first reminder
            "⚠️ Rules:",           # Boundary rules
            "▶ '",                  # Workflow phase header (e.g. ▶ 'WF Name' Workflow...)
            "Execute the steps below in order",  # Workflow phase body
            "⚠ SCOPE: This is phase",            # Workflow phase scope note
            "⚠ CONTEXT: The conversation above", # Workflow phase context note
        )

        cleaned = []
        executed_tools = []  # 제거되는 tool_calls에서 도구 이름 수집

        for i, msg in enumerate(self.messages):
            role = msg.get("role")

            # 첫 번째 시스템 프롬프트는 항상 유지
            if i == 0:
                cleaned.append(msg)
                continue

            # tool messages 제거 (도구 결과는 최종 응답에 이미 반영됨)
            if role == "tool":
                continue

            # tool_calls가 있는 assistant messages 제거 (중간 과정)
            # 단, 실행된 도구 이름은 수집하여 최종 응답에 주석으로 추가
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {}).get("name", "")
                    if fn and fn not in executed_tools:
                        executed_tools.append(fn)
                continue

            # 빈 assistant 메시지 제거 (content 없고 tool_calls도 없음 — 이전 턴 실패 잔재)
            if role == "assistant" and not (msg.get("content") or "").strip() and not msg.get("tool_calls"):
                continue

            # 일회성 system messages 제거
            if role == "system":
                content = msg.get("content", "")
                if any(content.startswith(p) for p in TRANSIENT_PREFIXES):
                    continue

            # 실행 지시 user messages 제거 (계획 후 주입되는 긴 프롬프트)
            if role == "user":
                content = msg.get("content", "")
                if content.startswith("Based on the above plan") or content.startswith("위 계획과 도구 결과를 바탕으로"):
                    continue
                # Tool result wrapper messages
                if content.startswith("<tool_response>") or content.startswith("[도구 결과]") or content.startswith("[Tool Result]"):
                    continue
                # boundary separator (user role로 주입된 것) 제거
                if "════" in content and "██ Above is" in content:
                    continue
                # exec instruction 제거 ([USE TOOL], Planned Tools 등 planning→exec 주입 메시지)
                if content.startswith("[USE TOOL]:") or content.startswith("Planned Tools:"):
                    continue
                # workflow preamble placeholder 제거 (빈 preamble 방지)
                # ▶ 'WF Name' Workflow — Phase N 형태의 헤더도 제거
                if "Execute the steps below in order" in content and "⚠ Do NOT call run_workflow" in content:
                    continue
                if content.startswith("▶ '") and "Workflow" in content:
                    continue

            cleaned.append(msg)

        # [Previous turn completed: tools] 주석은 제거 (다음 턴에서 같은 툴 재호출을 막는 부작용)
        # 대신 boundary separator가 이전 대화를 context로만 취급하도록 안내

        # 숨겨진 데이터 태그 제거 (<!--WFDATA:...-->, <!--CODE_DATA:...-->, <!--PERSONA_DATA:...-->)
        # 데스크톱 앱에서 이미 파싱 완료됐으므로 messages에 남을 필요 없음
        import re
        _hidden_tag_re = re.compile(r'\s*<!--(?:WFDATA|CODE_DATA|PERSONA_DATA):.*?-->', re.DOTALL)
        for msg in cleaned:
            content = msg.get("content", "")
            if content and "<!--" in content:
                msg["content"] = _hidden_tag_re.sub("", content).rstrip()

        before = len(self.messages)
        self.messages = cleaned
        after = len(self.messages)
        if before != after:
            print(f"\033[96m[Context cleanup] {before} → {after} messages ({before - after} removed)\\033[0m", flush=True)

# 활성 세션 저장
sessions: dict[str, Session] = {}

def get_or_create_session(session_id: str = None) -> Session:
    """세션 가져오기 또는 새로 생성"""
    if session_id and session_id in sessions:
        return sessions[session_id]

    sid = session_id or str(uuid.uuid4())

    # ★ 새 세션 생성 시 config.json을 다시 읽어 최신 설정 반영
    #   (서버 재시작 없이 언어 변경 등이 즉시 적용되도록)
    # 시스템 프롬프트 구성
    from tools import TOOL_DESCRIPTIONS
    from assistant_agent import SYSTEM_IDENTITY, USER_INSTRUCTION_TEMPLATE, OPENAI_TOOLS, get_system_identity

    try:
        fresh_config = load_config()
    except Exception:
        fresh_config = config
    # i18n도 최신 config으로 재초기화
    try:
        _init_i18n(fresh_config)
    except Exception:
        pass

    # Qwen model 감지 → Qwen3 공식 tool calling 형식 사용
    model_name = fresh_config.get("llm", {}).get("model_name", "").lower()
    if not model_name:
        model_name = fresh_config.get("llm", {}).get("ollama_model", "").lower()
    is_qwen = "qwen" in model_name
    is_gemma = "gemma" in model_name
    is_nanbeige = "nanbeige" in model_name
    # native tool calling 미지원 모델: 프롬프트에 도구 목록 직접 삽입 필요
    _needs_prompt_tools = is_qwen or is_gemma or is_nanbeige

    if _needs_prompt_tools:
        # 도구 목록을 프롬프트에 직접 삽입 (native tool calling 미지원 모델)
        import json
        tools_json_list = []
        for t in OPENAI_TOOLS:
            if "function" in t:
                tools_json_list.append(json.dumps(t["function"], ensure_ascii=False))
        tools_xml = "<tools>\n" + "\n".join(tools_json_list) + "\n</tools>"
        tools_block = (
            "You may call one or more functions to assist with the user query.\n\n"
            "You are provided with function signatures within <tools></tools> XML tags:\n"
            f"{tools_xml}\n\n"
            "For each function call, return a json object with function name and arguments "
            "within <tool_call></tool_call> XML tags:\n"
            "<tool_call>\n"
            '{"name": <function-name>, "arguments": <args-json-object>}\n'
            "</tool_call>"
        )
    else:
        # Non-Qwen models: basic tool call format
        tools_block = (
            "When you need a tool, use this format:\n\n"
            "<tool_call>\n"
            '{"name": "tool_name", "arguments": {"param": "value"}}\n'
            "</tool_call>"
        )

    # Response language instruction based on config
    lang = fresh_config.get("assistant", {}).get("language", "ko")
    if lang == "en":
        response_language_instruction = "## Response Language\nAlways respond in English."
    else:
        response_language_instruction = "## 응답 언어\n한국어로 답하세요. 파일로 저장하는 내용, 대본, 보고서, 이메일 본문 등 모든 생성 텍스트도 반드시 한국어로 작성하세요."

    # User instruction prompt (모든 규칙/도구 설명)
    user_instruction = USER_INSTRUCTION_TEMPLATE.format(
        tools_block=tools_block,
        response_language_instruction=response_language_instruction
    )
    if _system_profile:
        user_instruction = _system_profile + "\n\n" + user_instruction

    # Email not configured notice
    if not fresh_config.get("email", {}).get("enabled", False):
        user_instruction += (
            "\n\n## ⚠ Email Not Configured\n"
            "Email tools (send_email, list_emails, read_email) are not configured. "
            "If the user requests email features, inform them to run setup_env.ps1 to set up email."
        )

    # 마크다운 포맷 가이드를 초기 세션 프롬프트에 포함 (format="markdown"일 때 매 턴 주입 대신)
    user_instruction += "\n\n" + MARKDOWN_FORMAT_GUIDE

    # Session 생성: system=config 기반 identity, user=전체 instruction
    system_identity = get_system_identity(fresh_config)
    session = Session(sid, system_identity)
    session.messages.append({"role": "system", "content": user_instruction})
    sessions[sid] = session
    return session


# ─────────────────────────────────────────────
# FastAPI 앱
# ─────────────────────────────────────────────

app = FastAPI(
    title="Xoul API",
    description="Self-evolving AI Personal Assistant API",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 인증
security = HTTPBearer(auto_error=False)

async def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """API 키 검증"""
    if not credentials or credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials


# ─────────────────────────────────────────────
# 요청/응답 model
# ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    format: Optional[str] = None  # "markdown" | "plain" | None
    host_context: Optional[dict] = None  # 데스크톱 앱에서 전달하는 호스트 정보

class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list = []
    persona_active: bool = False
    persona_name: str = ""
    persona_bg_image: str = ""

class StatusResponse(BaseModel):
    name: str
    version: str
    llm_provider: str
    llm_connected: bool
    active_sessions: int
    uptime_seconds: float

class MemoryResponse(BaseModel):
    context: str


# ─────────────────────────────────────────────
# 에이전트 실행 (도구 루프 포함)
# ─────────────────────────────────────────────

MAX_TOOL_CALLS = 30

def _truncate_tool_result(result: str, tool_name: str = "") -> str:
    """도구 결과를 적절한 길이로 자르기. fetch_url/web_search는 더 긴 결과 허용."""
    limits = {"fetch_url": 6000, "web_search": 6000, "browse_url": 6000, "list_workflows": 8000, "list_personas": 8000, "list_codes": 8000, "run_stored_code": 4000, "run_python_code": 4000}
    limit = limits.get(tool_name, 1500)
    if len(result) > limit:
        # <!--WFDATA:...--> 같은 숨겨진 데이터 태그 보존
        import re
        hidden_tag = ""
        m = re.search(r'(<!--(?:WFDATA|CODE_DATA|PERSONA_DATA):.*?-->)', result, re.DOTALL)
        if m:
            hidden_tag = "\n" + m.group(1)
            result = result[:m.start()] + result[m.end():]
        return result[:limit] + "..." + hidden_tag
    return result


def _parse_tool_call_text(raw: str) -> dict:
    """
    <tool_call> 태그 안의 JSON을 강력하게 파싱.
    LLM이 생성한 JSON은 종종 문자열 안에 이스케이프되지 않은 \n이 있음.
    """
    import re
    text = raw.strip()
    
    # 1차: 그대로 파싱
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 2차: 문자열 값 안의 이스케이프되지 않은 줄바꾼을 \n으로 변환
    try:
        fixed = re.sub(r'(?<=")([^"]*?)(?=")', 
                       lambda m: m.group(1).replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'),
                       text)
        return json.loads(fixed)
    except (json.JSONDecodeError, re.error):
        pass
    
    # 3차: name과 arguments를 개별 추출
    try:
        name_m = re.search(r'"name"\s*:\s*"([^"]+)"', text)
        args_m = re.search(r'"arguments"\s*:\s*(\{.*)', text, re.DOTALL)
        if name_m:
            name = name_m.group(1)
            args = {}
            if args_m:
                args_raw = args_m.group(1).strip()
                # 마지막 } 찾기 (중첩 괄호 고려)
                depth = 0
                end = 0
                for i, c in enumerate(args_raw):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end > 0:
                    args_str = args_raw[:end]
                    # 줄바꾼 이스케이프
                    args_str = args_str.replace('\n', '\\n').replace('\r', '\\r')
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError:
                        args = {"_raw": args_str[:500]}
            return {"name": name, "arguments": args}
    except Exception:
        pass
    
    # 4차: GLM 스타일 — 도구 이름만 있는 경우 (e.g., "get_datetime")
    # 또는 name(args) 함수 호출 스타일 (e.g., 'get_datetime()')
    from tools import TOOLS
    stripped = text.strip().rstrip(')')
    
    # name() 또는 name({"key": "val"}) 형식
    func_m = re.match(r'^(\w+)\s*\((.*)\)?\s*$', text.strip(), re.DOTALL)
    if func_m:
        fname = func_m.group(1)
        fargs_raw = func_m.group(2).strip() if func_m.group(2) else ""
        if fname in TOOLS:
            args = {}
            if fargs_raw:
                try:
                    args = json.loads(fargs_raw)
                except json.JSONDecodeError:
                    pass
            return {"name": fname, "arguments": args}
    
    # 순수 도구 이름만 (e.g., "get_datetime")
    bare_name = text.strip()
    if bare_name in TOOLS:
        return {"name": bare_name, "arguments": {}}
    
    return None

# 마크다운 출력 포맷 가이드 (format="markdown" 요청 시 주입)
MARKDOWN_FORMAT_GUIDE = """[Output Format] Respond in markdown:
- Headings: use ## (never #), emphasis: **bold**, *italic*
- Lists: - or 1. 2. 3.
- Code: ```language code block (specify language)
- Tables: | Header | format (use actively for comparisons/listings)
- Dividers: --- (for major section breaks)
- Emoji: use appropriately for headings and key points
- Keep it short and scannable. No unnecessary lengthy text."""


def _check_persona_activation(session: Session, tool_result: str) -> dict:
    """
    도구 결과가 persona 활성화 마커인지 확인.
    맞으면 세션의 시스템 프롬프트를 페르소나 프롬프트로 교체.
    아니면 None 반환.
    """
    if not tool_result or '"__persona_activate__"' not in tool_result:
        return None
    try:
        data = json.loads(tool_result)
        if not data.get("__persona_activate__"):
            return None
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None

    name = data.get("name", "Persona")
    prompt = data.get("prompt", "")
    description = data.get("description", "")
    bg_image = data.get("bg_image", "")
    icon = data.get("icon", "🎭")

    # 원래 시스템 프롬프트 백업 (복원용)
    if not hasattr(session, '_original_system_prompt'):
        session._original_system_prompt = session.messages[0]["content"]

    # Replace system prompt with persona prompt
    persona_system = f"""You are now in persona mode as "{name}".

{prompt}

## Important Rules
- Stay in character as "{name}" at all times
- You have access to tools relevant to your role (web_search, browse_url, run_command, save_file, etc.)
- Do NOT use management tools: list_workflows, create_workflow, update_workflow, delete_workflow, run_workflow, list_personas, create_persona, update_persona, delete_persona
- If the user asks for these, reply: "페르소나 모드에서는 지원하지 않는 기능입니다. '페르소나 종료' 또는 /done 으로 일반 모드로 돌아간 후 시도해주세요."
- When the user says /done, /종료, or natural language exit phrases like "페르소나 종료", "종료해줘", "끝", deactivate persona mode
- Respond in the same language as the user
- **When you first activate, greet the user warmly in character.** Introduce yourself, summarize your available tools/capabilities from the description, and what you can help with. Use the persona's description to present your full scope of abilities.
"""

    session.messages[0]["content"] = persona_system

    # 세션 상태 업데이트
    session.persona_active = True
    session.persona_name = name
    session.persona_prompt = prompt
    session.persona_bg_image = bg_image

    # Activation message — LLM generates greeting afterwards
    greeting_lines = [f"{icon} **{name}** persona mode activated!"]
    if description:
        greeting_lines.append(f"\n{description}")
    greeting_lines.append(f"\n---\nType `/done` or say '페르소나 종료' to return to normal mode.")
    greeting = "\n".join(greeting_lines)
    session.add_message("assistant", greeting)

    # Greeting trigger: system message to LLM requesting a greeting
    _greeting_desc = f"\n\nPersona description:\n{description}" if description else ""
    session.add_message("system",
        f"[System Instruction] The '{name}' persona has just been activated. "
        f"Please greet the user warmly and naturally in character. "
        f"Then summarize your available tools and capabilities from the persona description below, "
        f"so the user knows exactly what you can help with.{_greeting_desc}"
    )

    return {"name": name, "greeting": greeting, "bg_image": bg_image, "needs_greeting": True}



# ── Code tool streaming helper ──
from tools import _STREAMING_TOOLS, execute_tool as _execute_tool_fn

# 스크린캐스트 대상 도구 (브라우저 사용하는 도구들)
_SCREENCAST_TOOLS = {"web_search", "browse_url", "fetch_url"}

def _execute_tool_streaming(t_name, t_args):
    """Execute a tool, yielding code_output and browser_frame events.
    
    Yields:
        {"type": "code_output", "line": str}  — for each stdout line during code execution
        {"type": "browser_frame", "data": str} — base64 JPEG screenshot frame
    
    Returns (via final yield):
        {"type": "__done__", "result": str}    — the final tool result
    """
    # ── 워크플로우에서 정의한 파라미터 자동 주입 ──
    if t_name == "run_stored_code":
        try:
            import tools.workflow_tools as _wf_mod
            _wf_data = _wf_mod._pending_wf_chunks
            if _wf_data and _wf_data.get("remaining"):
                _code_name = t_args.get("name", "")
                for _ch in _wf_data["remaining"]:
                    for _cs in _ch.get("steps", []):
                        if _cs.get("code_name") == _code_name and _cs.get("args"):
                            t_args["params"] = json.dumps(_cs["args"], ensure_ascii=False)
                            print(f"\033[96m[WF-PARAMS] {_code_name} → {t_args['params']}\033[0m", flush=True)
                            break
        except Exception:
            pass

    # ── 스크린캐스트 대상 도구: 백그라운드 스크린캐스트 + 도구 실행 동시 진행 ──
    if t_name in _SCREENCAST_TOOLS:
        import queue
        import threading
        import urllib.request
        import time

        output_queue = queue.Queue()
        _sc_stop = threading.Event()
        _sc_resp = [None]  # SSE response 객체 저장 (강제 종료용)

        def _screencast_consumer():
            """browser_daemon의 /screencast SSE를 소비하여 프레임을 queue에 넣기"""
            try:
                # 도구에 따라 적절한 URL/쿼리 전달
                import urllib.parse
                # web_search: Tavily 사용 시 DDG 검색 페이지 불필요 → 버퍼 모드 (URL browse 프레임만 표시)
                #             Tavily 없으면 DuckDuckGo 검색 페이지 캡처
                # browse_url/fetch_url: 전용 탭 모드 (해당 URL 직접 캡처)
                if t_name == "web_search":
                    q = t_args.get("query", "")
                    # Tavily API 키가 있으면 DDG 페이지 열지 않음 (버퍼 폴링 모드)
                    _tavily_key = config.get("search", {}).get("tavily_api_key", "")
                    if _tavily_key and q:
                        param = ""  # 버퍼 폴링 모드 — URL browse 프레임만 표시
                    elif q:
                        param = f"?q={urllib.parse.quote(q)}"
                    else:
                        param = ""
                elif t_name in ("browse_url", "fetch_url"):
                    u = t_args.get("url", "")
                    param = f"?url={urllib.parse.quote(u, safe='')}" if u else ""
                else:
                    param = ""

                daemon_url = f"http://127.0.0.1:9223/screencast{param}"
                print(f"[screencast] 📹 connecting to {daemon_url[:100]}...", flush=True)
                req = urllib.request.Request(daemon_url)
                resp = urllib.request.urlopen(req, timeout=35)
                _sc_resp[0] = resp  # 강제 종료를 위해 저장
                try:
                    print(f"[screencast] 📹 connected, reading frames...", flush=True)
                    frame_count = 0
                    for raw_line in resp:
                        if _sc_stop.is_set():
                            break
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data: "):
                            continue
                        try:
                            data = json.loads(line[6:])
                            if data.get("frame"):
                                frame_count += 1
                                source = data.get("source", "ddg")
                                url_idx = data.get("url_idx", 0)
                                output_queue.put(("__FRAME__", data["frame"], source, url_idx))
                                if frame_count % 5 == 1:
                                    print(f"[screencast] 📹 frame #{frame_count} ({len(data['frame'])}B) source={source} url_idx={url_idx}", flush=True)
                            elif data.get("done"):
                                print(f"[screencast] 📹 done ({frame_count} frames total)", flush=True)
                                break
                        except (json.JSONDecodeError, KeyError):
                            continue
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
                    _sc_resp[0] = None
            except Exception as e:
                if not _sc_stop.is_set():
                    print(f"[screencast] ❌ consumer error: {e}", flush=True)

        def _run_tool():
            try:
                result = _execute_tool_fn(t_name, t_args)
            except Exception as e:
                result = f"Error: {e}"
            # dict 결과에서 text만 추출 (screenshots는 SSE로 이미 전달)
            if isinstance(result, dict) and "__screenshots__" in result:
                result = result.get("text", str(result))
            output_queue.put(("__DONE__", result))

        # SSE consumer 스레드 시작 (도구보다 먼저)
        print(f"[screencast] 🚀 starting for tool={t_name}", flush=True)
        sc_thread = threading.Thread(target=_screencast_consumer, daemon=True)
        sc_thread.start()

        # 도구 실행 스레드
        tool_thread = threading.Thread(target=_run_tool, daemon=True)
        tool_thread.start()

        yielded_frames = 0
        while True:
            try:
                item = output_queue.get(timeout=0.5)
                if isinstance(item, tuple):
                    if item[0] == "__DONE__":
                        tool_result = item[1]
                        _sc_stop.set()
                        # SSE response 강제 종료 (SLiRP 즉시 해제)
                        if _sc_resp[0]:
                            try:
                                _sc_resp[0].close()
                            except Exception:
                                pass
                        try:
                            urllib.request.urlopen("http://127.0.0.1:9223/screencast/stop", timeout=2)
                        except Exception:
                            pass
                        print(f"[screencast] ✅ tool done, yielded {yielded_frames} frames", flush=True)
                        yield {"type": "__done__", "result": tool_result}
                        break
                    elif item[0] == "__FRAME__":
                        yielded_frames += 1
                        yield {"type": "browser_frame", "data": item[1], "source": item[2] if len(item) > 2 else "url", "url_idx": item[3] if len(item) > 3 else 0}
            except queue.Empty:
                if not tool_thread.is_alive() and output_queue.empty():
                    _sc_stop.set()
                    if _sc_resp[0]:
                        try:
                            _sc_resp[0].close()
                        except Exception:
                            pass
                    print(f"[screencast] ⚠ tool thread ended, yielded {yielded_frames} frames", flush=True)
                    yield {"type": "__done__", "result": "(completed)"}
                    break
        return

    if t_name not in _STREAMING_TOOLS:
        # Non-streaming: execute synchronously, yield result
        result = _execute_tool_fn(t_name, t_args)
        yield {"type": "__done__", "result": result}
        return

    import queue
    import threading

    output_queue = queue.Queue()

    def on_output(line):
        output_queue.put(line)

    def run_in_thread():
        try:
            result = _execute_tool_fn(t_name, t_args, on_output=on_output)
        except Exception as e:
            result = f"Error: {e}"
        output_queue.put(("__DONE__", result))

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()

    while True:
        try:
            item = output_queue.get(timeout=1.0)
            if isinstance(item, tuple) and item[0] == "__DONE__":
                yield {"type": "__done__", "result": item[1]}
                break
            yield {"type": "code_output", "line": str(item)}
        except queue.Empty:
            if not thread.is_alive():
                yield {"type": "__done__", "result": "(completed)"}
                break


# ── 스마트 중복 호출 감지 ──
# 특정 도구는 primary key만 비교 (인자 전체가 아닌 핵심 필드만)
_DEDUP_PRIMARY_KEY_TOOLS = {
    "web_search": "query",       # 같은 쿼리 중복 검색 방지
    "run_stored_code": "name",
    "run_workflow": "name",      # 같은 턴 내 동일 워크플로우 중복 실행 방지
    "view_workflow": "name",
    "send_notification": "message",  # 동일 메시지 중복 전송 방지
}


# send_notification 세션별 중복 차단 (60초 이내 동일 메시지)
import time as _time_mod
_notif_sent_log: dict = {}  # {session_id: {msg_hash: timestamp}}
_NOTIF_DEDUP_WINDOW = 60  # seconds

def _check_notif_dedup(session_id: str, message: str) -> bool:
    """True = 중복으로 차단해야 함, False = 전송 허용."""
    import hashlib
    now = _time_mod.time()
    key = hashlib.md5(message.encode("utf-8", errors="ignore")).hexdigest()[:16]
    bucket = _notif_sent_log.setdefault(session_id, {})
    # 만료 항목 정리
    _notif_sent_log[session_id] = {k: v for k, v in bucket.items() if now - v < _NOTIF_DEDUP_WINDOW}
    bucket = _notif_sent_log[session_id]
    if key in bucket:
        return True  # 중복
    bucket[key] = now
    return False

# 이전 결과가 에러면 재시도 허용
_ERROR_INDICATORS = ("Traceback", "Error:", "TypeError", "ValueError", "KeyError",
                     "ModuleNotFoundError", "ImportError", "NameError", "⚠")



def _find_dup_result(tool_name: str, tool_args: dict, tool_calls_log: list):
    """
    tool_calls_log에서 중복 호출을 찾아 이전 결과를 반환.
    - run_stored_code 등: name 파라미터만 비교 (api_key 등 무시)
    - 그 외: 전체 args JSON 비교
    - 이전 결과가 에러면 재시도 허용 (None 반환)
    중복이면 이전 result 반환, 아니면 None.
    """
    primary_key = _DEDUP_PRIMARY_KEY_TOOLS.get(tool_name)

    for prev in tool_calls_log:
        if prev["tool"] != tool_name:
            continue

        matched = False
        if primary_key:
            if tool_args.get(primary_key) == prev["args"].get(primary_key):
                matched = True
        else:
            if json.dumps(tool_args, sort_keys=True, ensure_ascii=False) == \
               json.dumps(prev["args"], sort_keys=True, ensure_ascii=False):
                matched = True

        if matched:
            # 이전 결과가 에러였으면 재시도 허용
            prev_result = prev.get("result", "")
            if any(indicator in prev_result for indicator in _ERROR_INDICATORS):
                return None  # 에러 → 재시도 허용
            return prev_result

    return None



# ── Context Summarization ──

_SUMMARY_TURN_THRESHOLD = 4   # 이전 대화가 이 턴 수 이상일 때 요약 적용
_SUMMARY_RECENT_KEEP = 2      # 최근 N턴은 원문 유지

def _summarize_previous_context(session, conv_turns: list) -> str:
    """
    이전 대화 턴들을 요약합니다.
    
    Args:
        session: Session 객체 (캐시 활용)
        conv_turns: [(user_msg, assistant_msg), ...] 형태의 이전 대화 턴 리스트
    
    Returns:
        요약 문자열. 실패 시 빈 문자열.
    """
    if not conv_turns:
        return ""
    
    # 캐시 확인: 턴 수가 같으면 이전 요약 재사용
    if session.context_summary and session.summary_turn_count == len(conv_turns):
        return session.context_summary
    
    # 요약 대상 텍스트 구성
    conv_text_parts = []
    for i, (user_msg, asst_msg) in enumerate(conv_turns, 1):
        conv_text_parts.append(f"Turn {i}:")
        conv_text_parts.append(f"  User: {user_msg[:500]}")
        conv_text_parts.append(f"  Assistant: {asst_msg[:500]}")
    conv_text = "\n".join(conv_text_parts)
    
    summary_prompt = [
        {"role": "system", "content": "You are a conversation summarizer. Output ONLY the summary, nothing else."},
        {"role": "user", "content": (
            "Summarize the conversation below in 2-5 concise bullet points.\n"
            "Rules:\n"
            "- Use PAST TENSE and COMPLETED form only (e.g., 'User asked about X. Assistant answered Y.')\n"
            "- NEVER use imperative/request form (NEVER 'tell me', 'search for', 'show me')\n"
            "- Focus on: what was asked, what was answered, key facts exchanged\n"
            "- Omit: greetings, tool names, formatting details, thinking process\n"
            "- Keep total under 300 characters\n"
            "- Use the same language as the conversation\n\n"
            f"Conversation:\n{conv_text}"
        )}
    ]
    
    try:
        result = llm_client.chat(summary_prompt, tools=None)
        
        summary = (result.get("content", "") or "").strip()
        
        # 요약이 비어있거나 에러이면 폴백
        if not summary or summary.startswith("[LLM") or summary.startswith("[Claude"):
            print(f"\033[93m[Context Summary] Failed — fallback to raw context\033[0m", flush=True)
            return ""
        
        # 캐시 저장
        session.context_summary = summary
        session.summary_turn_count = len(conv_turns)
        
        print(f"\033[96m[Context Summary] {len(conv_turns)} turns → {len(summary)} chars\033[0m", flush=True)
        return summary
        
    except Exception as e:
        print(f"\033[93m[Context Summary] Error: {e} — fallback to raw context\033[0m", flush=True)
        return ""


def _build_2msg_prompt(messages: list, session=None) -> list:
    """
    messages 배열을 LLM 호출 직전에 2개로 압축합니다.
    [0] system: 모든 system 메시지 병합
    [1] user:   대화이력(요약 or 태그) + 현재 요청(태그 없음)

    이전 대화가 4턴 이상이면:
    - 오래된 턴들은 요약문으로 압축
    - 최근 2턴은 원문 유지
    """
    # Hidden data tag 제거용 패턴
    import re
    _hidden_tag_re = re.compile(r'\s*<!--(?:WFDATA|CODE_DATA|PERSONA_DATA):.*?-->', re.DOTALL)

    system_parts, conv_parts = [], []
    _tc_id_to_name = {}  # tool_call_id → 도구 이름 매핑
    _tc_name_queue = []  # tool_call_id 없는 경우 순서 기반 매핑용
    for msg in messages:
        role = msg.get("role", "")
        content = (msg.get("content", "") or "").strip()
        # assistant의 tool_calls에서 도구 이름 매핑 수집
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {}).get("name", "")
                tc_id = tc.get("id", "")
                if fn:
                    if tc_id:
                        _tc_id_to_name[tc_id] = fn
                    _tc_name_queue.append(fn)
        if not content:
            continue
        # Hidden data tags 제거 (데스크톱 앱에서 이미 파싱 완료)
        if "<!--" in content:
            content = _hidden_tag_re.sub("", content).strip()
            if not content:
                continue
        # tool 메시지에 도구 이름 연결
        if role == "tool":
            tc_id = msg.get("tool_call_id", "")
            tool_name = _tc_id_to_name.get(tc_id, "")
            if not tool_name and _tc_name_queue:
                tool_name = _tc_name_queue.pop(0)
            conv_parts.append((role, content, tool_name))
        elif role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant"):
            
            # if role == "user":
            #     boundry = "════════════════════════════════════════\n"
            #         +"██ Above is previous conversation (CONTEXT ONLY) ██\n"
            #         +"██ Below is the user's NEW request — respond ONLY to this ██\n"
            #         +"════════════════════════════════════════\n\n"
            #     content = boundry+content
            conv_parts.append((role, content, ""))

    last_user_idx = next(
        (i for i in range(len(conv_parts) - 1, -1, -1) if conv_parts[i][0] == "user"),
        None
    )

    # ── 이전 대화 턴 수집 (요약 대상 판별) ──
    # "턴" = user + assistant 쌍
    previous_turns = []  # [(user_content, assistant_content), ...]
    if last_user_idx is not None and last_user_idx > 0:
        current_user = None
        current_asst = None
        for i, (role, content, _) in enumerate(conv_parts):
            if i >= last_user_idx:
                break
            if role == "user":
                # 이전 턴이 있으면 저장
                if current_user is not None:
                    previous_turns.append((current_user, current_asst or ""))
                current_user = content
                current_asst = None
            elif role == "assistant" and current_user is not None:
                current_asst = content
        # 마지막 쌍 저장
        if current_user is not None:
            previous_turns.append((current_user, current_asst or ""))

    # ── 요약 적용 여부 결정 ──
    use_summary = False
    summary_text = ""
    turns_to_summarize = 0
    
    print(f"\033[96m[2msg] conv_parts={len(conv_parts)} last_user_idx={last_user_idx} previous_turns={len(previous_turns)} threshold={_SUMMARY_TURN_THRESHOLD} session={'yes' if session else 'no'}\033[0m", flush=True)
    
    if session and len(previous_turns) >= _SUMMARY_TURN_THRESHOLD:
        turns_to_summarize = max(0, len(previous_turns) - _SUMMARY_RECENT_KEEP)
        if turns_to_summarize > 0:
            summary_text = _summarize_previous_context(session, previous_turns[:turns_to_summarize])
            if summary_text:
                use_summary = True
    
    print(f"\033[96m[2msg] use_summary={use_summary} turns_to_summarize={turns_to_summarize}\033[0m", flush=True)

    user_lines = []
    
    if use_summary:
        # ── 요약 모드: 오래된 턴은 요약, 최근 턴은 원문 ──
        user_lines.append(f"[Previous Context Summary]\n{summary_text}")
        
        # 최근 _SUMMARY_RECENT_KEEP 턴은 원문 유지
        # conv_parts에서 최근 턴들의 시작 인덱스 찾기
        recent_turn_start = 0
        turn_count = 0
        current_in_user = False
        for i, (role, content, _) in enumerate(conv_parts):
            if i >= last_user_idx:
                break
            if role == "user":
                turn_count += 1
                if turn_count > turns_to_summarize:
                    recent_turn_start = i
                    break
        
        # 최근 턴 원문 추가
        for i in range(recent_turn_start, last_user_idx):
            role, content, tool_name = conv_parts[i]
            if role == "user":
                user_lines.append(f"[USER(Previous)]\n{content}")
            elif role == "assistant":
                user_lines.append(f"[ASSISTANT(Previous)]\n{content}")
            elif role == "tool":
                name_tag = f" : {tool_name} executed" if tool_name else ""
                user_lines.append(f"[TOOL(Previous){name_tag}]\n{content}")
        
        # Boundary separator
        user_lines.append(
            "════════════════════════════════════════════════════════════════════════════════\n"
            "██ Above is previous conversation (CONTEXT ONLY — already completed) ██\n"
            "██ Below is the user's NEW request — respond ONLY to this ██\n"
            "██ Do NOT repeat any previous actions. Focus ONLY on current prompt. ██\n"
            "════════════════════════════════════════════════════════════════════════════════"
        )
        
        # 현재 요청 + 현재 턴 중간 결과
        for i in range(last_user_idx, len(conv_parts)):
            role, content, tool_name = conv_parts[i]
            is_current_turn = (i > last_user_idx)
            if role == "user":
                user_lines.append(content)
            elif role == "assistant":
                if is_current_turn and content.strip():
                    user_lines.append(f"[ASSISTANT Response]\n{content}")
            elif role == "tool":
                name_tag = f" : {tool_name} executed" if tool_name else ""
                if is_current_turn:
                    user_lines.append(f"[Tool Result{name_tag}]\n{content}")
    else:
        # ── 기존 모드: 원문 그대로 ──
        for i, (role, content, tool_name) in enumerate(conv_parts):
            # 현재 요청(마지막 user) 직전에 boundary separator 삽입
            if i == last_user_idx and last_user_idx > 0:
                user_lines.append(
                    "════════════════════════════════════════════════════════════════════════════════\n"
                    "██ Above is previous conversation (CONTEXT ONLY) ██\n"
                    "██ Below is the user's NEW request — respond ONLY to this ██\n"
                    "██ Do not do something in advance. Do it for just current prompt. ██\n"
                    "════════════════════════════════════════════════════════════════════════════════"
                )
            # 마지막 user 이후의 메시지는 현재 턴의 중간 결과
            is_current_turn = (last_user_idx is not None and i > last_user_idx)
            if role == "user":
                user_lines.append(content if i == last_user_idx else f"[USER(Previous)]\n{content}")
            elif role == "assistant":
                if is_current_turn:
                    if content.strip():
                        user_lines.append(f"[ASSISTANT Response]\n{content}")
                else:
                    user_lines.append(f"[ASSISTANT(Previous)]\n{content}")
            elif role == "tool":
                name_tag = f" : {tool_name} executed" if tool_name else ""
                if is_current_turn:
                    user_lines.append(f"[Tool Result{name_tag}]\n{content}")
                else:
                    user_lines.append(f"[TOOL(Previous){name_tag}]\n{content}")

    result = []
    if system_parts:
        result.append({"role": "system", "content": "\n\n".join(system_parts)})
    
    if user_lines:
        MAX_CHARS = 40_000
        total_len = sum(len(u) for u in user_lines)
        
        # 최신 지시문(가장 마지막 user_line)은 무조건 보존해야 하므로 len(user_lines) > 1 일 때만 자름
        while len(user_lines) > 1 and total_len > MAX_CHARS:
            removed = user_lines.pop(0)
            total_len -= len(removed)
            
        result.append({"role": "user", "content": "\n\n".join(user_lines)})
    
    return result



# ─────────────────────────────────────────────
# _run_agent_loop 헬퍼 함수들
# ─────────────────────────────────────────────

def _try_direct_command(session: Session, user_message: str):
    """
    사용자 입력이 도구 이름과 유사하면 LLM 없이 직접 실행.
    소형 LLM이 짧은 명령어(recall, remember 등)를 오해하는 문제 방지.
    매칭 성공 시 final 이벤트 dict 반환, 실패 시 None.
    """
    msg = user_message.strip()
    # 너무 긴 입력은 스킵 (문장은 LLM이 처리해야 함)
    if len(msg) > 30:
        return None

    # 입력에서 핵심 단어 추출 (소문자, 공백 분리)
    words = msg.lower().split()
    if not words:
        return None
    cmd = words[0]
    rest = " ".join(words[1:]) if len(words) > 1 else ""

    # 직접 실행할 명령어 매핑 {후보: (도구이름, 인자생성함수)}
    from tools.memory_tools import recall, forget

    def _lev_dist(s1, s2):
        """간단한 Levenshtein 거리"""
        if len(s1) < len(s2):
            return _lev_dist(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(prev[j + 1] + 1, curr[j] + 1,
                               prev[j] + (0 if c1 == c2 else 1)))
            prev = curr
        return prev[-1]

    # recall 매칭 (오타 허용: edit distance <= 2)
    if _lev_dist(cmd, "recall") <= 2:
        session.add_message("user", user_message)
        result = recall(rest)
        session.add_message("assistant", result)
        log_activity("direct_command", session=session.id, cmd="recall", query=rest)
        return {
            "type": "final", "content": result,
            "session_id": session.id, "tool_calls": [{"tool": "recall", "args": {"query": rest}}],
            "persona_active": session.persona_active,
            "persona_name": session.persona_name,
            "persona_bg_image": session.persona_bg_image,
        }

    # forget 매칭
    if _lev_dist(cmd, "forget") <= 2 and rest:
        session.add_message("user", user_message)
        result = forget(rest)
        session.add_message("assistant", result)
        log_activity("direct_command", session=session.id, cmd="forget", key=rest)
        return {
            "type": "final", "content": result,
            "session_id": session.id, "tool_calls": [{"tool": "forget", "args": {"key": rest}}],
            "persona_active": session.persona_active,
            "persona_name": session.persona_name,
            "persona_bg_image": session.persona_bg_image,
        }

    return None

def _handle_persona_exit(session: Session, user_message: str):
    """
    페르소나 종료 커맨드(/done, /종료 등) 감지.
    종료 시 상태 초기화 후 final 이벤트 dict 반환. 계속 진행이면 None 반환.
    """
    _exit_cmds = {"/done", "/abort", "/종료"}
    _exit_phrases = [
        "페르소나 종료", "페르소나 끝", "페르소나 끄기",
        "종료해줘", "종료해", "종료할게", "끝내줘", "끝낼게",
        "그만해", "그만할게", "나가기", "나갈게",
        "일반 모드", "일반모드", "원래대로",
    ]
    msg_low = user_message.strip().lower()
    is_exit = msg_low in _exit_cmds or any(p in msg_low for p in _exit_phrases)

    if not (session.persona_active and is_exit):
        return None

    if hasattr(session, "_original_system_prompt"):
        session.messages[0]["content"] = session._original_system_prompt
    session.persona_active = False
    persona_name = session.persona_name
    session.persona_name = ""
    session.persona_prompt = ""
    session.persona_bg_image = ""
    exit_msg = f"🎭 '{persona_name}' persona mode deactivated."
    session.add_message("assistant", exit_msg)
    return {
        "type": "final", "content": exit_msg,
        "session_id": session.id, "tool_calls": [],
        "persona_active": False, "persona_name": "", "persona_bg_image": "",
    }


def _select_tools_for_request(session: Session, user_message: str,
                               exclude_tools: set = None, only_tools: set = None) -> list:
    """
    Toolkit Router로 도구 목록을 선택하고 exclude/only 필터를 적용해 반환한다.
    """
    from assistant_agent import OPENAI_TOOLS
    from tools.tool_registry import select_tools as _select_tools, _initialized as _registry_ready

    # Toolkit Router: 실제 step 내용만 추출 (워크플로우 chunk boilerplate 제거)
    import re as _re
    _input = user_message
    if "Workflow — Phase" in user_message:
        _steps = _re.findall(r'\d+\.\s*\[.*?\]\s*(.*)', user_message)
        if _steps:
            _input = " ".join(_steps)
            print(f"[ToolRouter] Extracted step content: {_input[:100]}", flush=True)

    selected = _select_tools(_input) if _registry_ready else OPENAI_TOOLS
    _dbg_wf = [t.get("function", {}).get("name") for t in selected if "workflow" in t.get("function", {}).get("name", "")]
    print(f"[WF-DBG] select_tools returned {len(selected)} tools, workflow={_dbg_wf}, registry_ready={_registry_ready}", flush=True)

    # Failsafe: workflow 도구가 selected에 없으면 OPENAI_TOOLS에서 주입
    # (_all_openai_tools 상태 이슈로 누락될 수 있음)
    _WORKFLOW_TOOLS = {"run_workflow", "list_workflows", "create_workflow", "view_workflow", "update_workflow", "delete_workflow", "ask_user"}
    _sel_names = {t.get("function", {}).get("name") for t in selected}
    _missing_wf = _WORKFLOW_TOOLS - _sel_names
    if _missing_wf:
        for _t in OPENAI_TOOLS:
            if _t.get("function", {}).get("name") in _missing_wf:
                selected = list(selected)  # 복사 (원본 보존)
                selected.append(_t)
                _missing_wf.discard(_t.get("function", {}).get("name"))
        print(f"[WF-INJECT] Workflow tools injected from OPENAI_TOOLS: {_WORKFLOW_TOOLS - _missing_wf}", flush=True)

    print('selected tools ', selected)

    # 워크플로우 실행 중이면 run_workflow 제거, 아니면 ask_user 제거
    import tools.workflow_tools as _wf
    _excl = set(exclude_tools or set())
    if _wf._pending_wf_chunks is not None:
        _excl |= {"run_workflow", "list_workflows"}
    else:
        # ask_user는 워크플로우 실행 중에만 사용 (일반 채팅에서는 LLM이 직접 질문)
        _excl.add("ask_user")
    if _excl:
        _before = len(selected)
        selected = [t for t in selected if t.get("function", {}).get("name") not in _excl]
        print(f"[WF-GUARD] Excluded {_excl} ({_before}→{len(selected)})", flush=True)

    # only_tools: 화이트리스트 모드
    if only_tools:
        _before = len(selected)
        selected = [t for t in selected if t.get("function", {}).get("name") in only_tools]
        print(f"[WF-ONLY] Restricted to {only_tools} ({_before}→{len(selected)})", flush=True)

    _names = [t.get("function", {}).get("name", "?") for t in selected]
    print(f"[TOOLS] {len(_names)} tools: {', '.join(_names[:15])}", flush=True)
    return selected


def _prepare_context(session: Session, user_message: str) -> list:
    """
    LLM 호출 전 컨텍스트를 준비한다:
    - 이전 Planning 잔여물 제거
    - 시간 정보 주입
    - 호스트 앱 목록 주입
    - 시맨틱 메모리 주입
    - 기억/교정 넛지 주입
    - 턴 경계 마커 주입
    - 유저 메시지 추가 및 로깅

    반환값: 빈 tool_calls_log 리스트 (호출자가 축적용으로 사용)
    """
    from tools.memory_tools import auto_retrieve, check_and_summarize

    # ── 0. Planning 잔여물 정리 ──
    _PLAN_MARKERS = (
        "[Tool Calls Planned",
        "[No tools called in planning phase]",
        "사용자의 요청에 답하세요. 반드시 도구를 먼저",
    )
    session.messages[:] = [
        m for m in session.messages
        if not any((m.get("content") or "").startswith(mk) for mk in _PLAN_MARKERS)
    ]

    # ── 1. 시간 정보 주입 ──
    session.messages[:] = [
        m for m in session.messages
        if not (m.get("role") == "system" and (m.get("content", "") or "").startswith("Current time:"))
    ]
    now = datetime.now()
    weekdays_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    time_info = f"Current time: {now.strftime('%Y-%m-%d')} {weekdays_en[now.weekday()]} {now.strftime('%H:%M')} (KST)"
    try:
        import holidays
        kr_holidays = holidays.Korea(years=now.year)
        today = now.date()
        if today in kr_holidays:
            time_info += f" — Today is {kr_holidays[today]} (public holiday)."
        elif now.weekday() >= 5:
            time_info += f" — Today is {'Saturday' if now.weekday() == 5 else 'Sunday'} (weekend)."
        else:
            time_info += " — Weekday."
    except ImportError:
        pass
    session.add_message("system", time_info)

    # ── 2. 호스트 앱 목록 주입 ──
    host_apps = getattr(session, "host_app_list", "")
    if host_apps:
        session.messages[:] = [
            m for m in session.messages
            if not (m.get("role") == "system" and (m.get("content", "") or "").startswith("[Installed Apps]"))
        ]
        session.add_message("system",
            f"[Installed Apps] When calling host_open_app, use exactly these names "
            f"(no translation/conversion): {host_apps}"
        )

    # ── 3. 시맨틱 메모리 주입 ──
    check_and_summarize()
    try:
        _mem_prefixes = ("📌 저장된 기억", "🧠 관련 기억", "📋 이전 대화")
        session.messages[:] = [
            m for m in session.messages
            if not (m.get("role") == "system" and any(m.get("content", "").startswith(p) for p in _mem_prefixes))
        ]
        mem_ctx = auto_retrieve(user_message)
        if mem_ctx:
            session.add_message("system", mem_ctx)
            log_activity("auto_retrieve", session=session.id, context=mem_ctx[:300])
    except Exception as e:
        log_activity("auto_retrieve_error", session=session.id, error=str(e)[:200])

    # ── 4. 기억/교정 넛지 주입 ──
    msg_lower = user_message.strip()
    correction_patterns = ["아니", "틀렸", "아닌데", "그게 아니라", "잘못", "아냐", "아뇨", "아니요", "아니야"]
    personal_patterns = ["내 이름", "나는 ", "내가 좋아하는", "내 생일", "내 번호", "내 주소", "우리 집"]
    is_correction = any(msg_lower.startswith(p) or f" {p}" in msg_lower for p in correction_patterns)
    is_personal = any(p in msg_lower for p in personal_patterns)
    is_remember = any(w in msg_lower for w in ["기억해", "기억해줘", "기억 해줘", "remember", "외워", "외워줘"])

    session.messages[:] = [
        m for m in session.messages
        if not (m.get("role") == "system" and (m.get("content", "") or "").startswith("[System Instruction]"))
    ]
    if is_remember:
        session.add_message("system",
            "[System Instruction] The user explicitly requested to remember something. "
            "You MUST call the remember() tool to save it. Do not respond without using the tool."
        )
    elif is_correction:
        session.add_message("system",
            "[System Instruction] The user is correcting a previous response. "
            "You MUST save the corrected fact using remember() before responding."
        )
    elif is_personal:
        session.add_message("system",
            "[System Instruction] The user is sharing personal information. "
            "You MUST save it using remember() before responding."
        )

    # ── 5. 턴 경계 마커 — _build_2msg_prompt()에서 자동 삽입하므로 여기서는 생략 ──

    # ── 6. 유저 메시지 추가 ──
    session.add_message("user", user_message)
    log_activity("user_message", session=session.id, message=user_message[:500])
    log_activity("llm_messages_debug", session=session.id,
                 message_count=len(session.messages),
                 messages=[{"role": m["role"], "content": (m.get("content") or "")[:200]}
                           for m in session.messages[-10:]])
    return []  # tool_calls_log 초기


def _run_planning_phase(session: Session, selected_tools: list, phase_num: int, attempt: int) -> tuple:
    """
    Planning 단계 LLM 호출.
    Returns: (updated_selected_tools, plan_tool_results, user_message_was_updated)

    사이드 이펙트:
    - session.messages[-1] 의 content에 [Required tools: ...] 프리픽스 삽입 가능
    - selected_tools에 누락된 계획 도구 추가
    """
    from tools import TOOL_DESCRIPTIONS as _TD, TOOLS
    from assistant_agent import OPENAI_TOOLS

    yield {"type": "planning", "status": "analyzing"}

    # compact tool description 생성
    _compact = []
    for _line in _TD.split('\n'):
        _s = _line.strip()
        if _s.startswith('- ') and ': ' in _s:
            _compact.append(_s[2:].split('. Params:')[0].split(' Params:')[0])
    _tool_desc_text = "\n".join(_compact)

    plan_messages = _build_2msg_prompt(session.messages, session=session)
    plan_messages.append({
        "role": "user",
        "content": (
            "[Planning] Which tools do you need to answer the user's request?\n"
            "Available tools (name: description):\n"
            f"{_tool_desc_text}\n\n"
            "Rules:\n"
            "- Reply with ONLY tool names, comma-separated. NO other text.\n"
            "- Do NOT use <tool_call> or any XML/JSON format. Plain text only.\n"
            "- If no tool is needed (greeting, opinion), reply: none\n"
            "- Examples:\n"
            "  User: \"주식 시황 알려줘\" → web_search\n"
            "  User: \"워크플로우 실행해줘\" → run_workflow\n"
            "  User: \"안녕\" → none\n"
            "  User: \"검색 후 메신저로 보내줘\" → web_search, send_notification\n"
        )
    })

    # Planning LLM 호출 디버그 저장
    try:
        _total = sum(len(m.get("content", "") or "") for m in plan_messages)
        _dbg = {
            "ts": datetime.now().isoformat(), "session": session.id,
            "attempt": attempt, "phase": "planning",
            "msg_count": len(plan_messages), "total_chars": _total,
            "est_tokens": _total // 4,
            "messages": [{"role": m.get("role"), "chars": len(m.get("content", "") or ""),
                          "preview": (m.get("content", "") or "")[:150]} for m in plan_messages]
        }
        with open("/tmp/llm_prompt_debug.json", "w", encoding="utf-8") as _f:
            json.dump(plan_messages, _f, ensure_ascii=False, indent=2, default=str)
        with open("/tmp/llm_prompt_debug.jsonl", "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_dbg, ensure_ascii=False) + "\n")
    except Exception:
        pass

    plan_msg = llm_client.chat(plan_messages, tools=None)
    plan_text = (plan_msg.get("content", "") or "").strip()
    plan_thinking = plan_msg.get("_thinking", "")
    if plan_thinking:
        log_activity("plan_thinking", session=session.id, thinking=plan_thinking[:500])
    log_activity("plan_result", session=session.id, plan=plan_text[:200])

    # 텍스트에서 도구 이름 파싱
    import re as _re_plan
    plan_tool_results = []
    if plan_text.strip().lower() not in ("none", "없음", ""):
        for _tok in _re_plan.split(r"[,\n]+", plan_text):
            _name = _re_plan.sub(r"[^a-z_]", "", _tok.strip().lower())
            if _name in TOOLS:
                entry = f"[plan] {_name}"
                if entry not in plan_tool_results:
                    plan_tool_results.append(entry)

    print(f"\033[96m[PLAN-TEXT] '{plan_text[:80]}' → {plan_tool_results}\033[0m", flush=True)

    # UI에 계획된 도구 표시
    for _pt in plan_tool_results:
        _pname = _pt.replace("[plan] ", "")
        _phase_str = f"P{phase_num}:Plan" if phase_num > 0 else "Plan"
        yield {"type": "tool_start", "tool": _pname, "args": {}, "phase": _phase_str}

    yield {"type": "planning", "status": "executing"}

    # 계획된 도구가 selected_tools에 없으면 추가
    if plan_tool_results:
        _planned_names = {pt.replace("[plan] ", "") for pt in plan_tool_results}
        _selected_names = {t.get("function", {}).get("name") for t in selected_tools}
        _missing = _planned_names - _selected_names
        if _missing:
            for t in OPENAI_TOOLS:
                if t.get("function", {}).get("name") in _missing:
                    selected_tools.append(t)
        print(f"[PLAN→EXEC] planned={_planned_names}, embed={len(selected_tools)} tools", flush=True)

        # 마지막 user 메시지에 Required tools 힌트 삽입
        _tool_names_str = ", ".join(pt.replace("[plan] ", "") for pt in plan_tool_results)
        if (session.messages and session.messages[-1].get("role") == "user"):
            _last_content = session.messages[-1].get("content", "")
            if not _last_content.startswith("[Required tools:"):
                session.messages[-1] = dict(session.messages[-1])
                session.messages[-1]["content"] = (
                    f"[Required tools: {_tool_names_str}]\n"
                    "Use the required tools listed above to handle the following user request:\n\n"
                    + _last_content
                )

    # Execution 단계 디버그 저장
    try:
        _total = sum(len(m.get("content", "") or "") for m in session.messages)
        _dbg = {
            "ts": datetime.now().isoformat(), "session": session.id,
            "attempt": attempt, "phase": "execution",
            "msg_count": len(session.messages), "total_chars": _total,
            "est_tokens": _total // 4,
            "messages": [{"role": m.get("role"), "chars": len(m.get("content", "") or ""),
                          "preview": (m.get("content", "") or "")[:150]} for m in session.messages]
        }
        with open("/tmp/llm_prompt_debug.jsonl", "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_dbg, ensure_ascii=False) + "\n")
    except Exception:
        pass

    yield {"type": "__planning_done__", "selected_tools": selected_tools, "plan_tool_results": plan_tool_results}


def _execute_single_tool(session: Session, tool_name: str, tool_args: dict,
                          call_id: str, tool_calls_log: list, phase_num: int, attempt: int):
    """
    도구 1개를 실행하고 이벤트를 yield한다.

    Yields:
        tool_start, code_output, tool_result 이벤트
        persona_activated 이벤트 (페르소나 활성화 시)

    Returns (via final yield):
        {"type": "__result__", "short_result": str, "persona_data": dict|None}
    """
    _phase = f"P{phase_num}:E{attempt}" if phase_num > 0 else f"E{attempt}"
    yield {"type": "tool_start", "tool": tool_name, "args": tool_args, "phase": _phase}

    # host_ 도구: 데스크톱에서 실행 (서버 스텁)
    if tool_name.startswith("host_"):
        short_result = f"✅ 데스크톱에서 실행됨: {tool_name}"
        yield {"type": "tool_result", "tool": tool_name, "result": short_result}
        yield {"type": "__result__", "short_result": short_result, "persona_data": None}
        return

    # Streaming 실행
    tool_result = None
    for evt in _execute_tool_streaming(tool_name, tool_args):
        if evt["type"] == "__done__":
            tool_result = evt["result"]
        else:
            yield evt  # code_output, browser_frame 등 모두 전달
    if tool_result is None:
        tool_result = "(no result)"

    # __NEEDS_INPUT__ 신호 감지 (run_stored_code에서 빈 파라미터)
    if tool_result and "__NEEDS_INPUT__" in tool_result:
        import re as _ni_re
        _ni_match = _ni_re.search(r'__NEEDS_INPUT__(.+?)__END_NEEDS_INPUT__', tool_result)
        if _ni_match:
            try:
                _ni_meta = json.loads(_ni_match.group(1))
                session._needs_input = True

                # ── 워크플로우 상태 즉시 저장 (클라이언트 끊김 대비) ──
                import tools.workflow_tools as _wf_mod_ni
                _wf_chunks = _wf_mod_ni._pending_wf_chunks
                if _wf_chunks and _wf_chunks.get("remaining"):
                    _remaining = list(_wf_chunks["remaining"])
                    # 현재 phase 이후의 chunks만 남기기 (현재는 이미 실행됨)
                    _rest = [ch for ch in _remaining if ch.get("chunk_num", 0) > phase_num]
                    session.pending_wf = {
                        "wf_name": _wf_chunks["wf_name"],
                        "remaining": _rest,
                        "total": _wf_chunks.get("total_chunks", len(_remaining)),
                    }
                    _wf_mod_ni._pending_wf_chunks = None
                    print(f"\033[93m[WF-PAUSE-EARLY] Saved {len(_rest)} remaining chunks (phase={phase_num})\033[0m", flush=True)

                # SSE 이벤트: 데스크톱이 팝업 다이얼로그 표시 가능
                yield {
                    "type": "needs_input",
                    "code_name": _ni_meta.get("code_name", ""),
                    "params": _ni_meta.get("params", []),
                    "session_id": session.id,
                }
                print(f"\033[93m[NEEDS_INPUT] {_ni_meta.get('code_name')}: {[p['name'] for p in _ni_meta.get('params', [])]}\033[0m", flush=True)
            except Exception:
                pass
            # 마커 제거 — 사용자에게는 깔끔한 메시지만
            tool_result = _ni_re.sub(r'__NEEDS_INPUT__.*?__END_NEEDS_INPUT__\n?', '', tool_result)

    # 페르소나 활성화 감지
    persona_data = _check_persona_activation(session, tool_result)
    if persona_data:
        yield {
            "type": "persona_activated", "content": persona_data["greeting"],
            "persona_active": True, "persona_name": persona_data["name"],
            "persona_bg_image": persona_data.get("bg_image", ""),
        }
        # 페르소나 설명/프롬프트를 요약해 LLM에 전달 → 풍부한 인사말 생성
        _p_name = persona_data.get("name", "")
        _p_desc = persona_data.get("description", "") or ""
        _p_prompt = persona_data.get("prompt", "") or ""
        _greeting_hint_parts = [f"'{_p_name}' 페르소나가 활성화되었습니다."]
        if _p_desc:
            _greeting_hint_parts.append(f"\n\n📝 페르소나 설명: {_p_desc}")
        if _p_prompt:
            # 프롬프트가 길면 앞부분만 전달
            _prompt_preview = _p_prompt[:500] + ("..." if len(_p_prompt) > 500 else "")
            _greeting_hint_parts.append(f"\n\n🎯 페르소나 역할:\n{_prompt_preview}")
        _greeting_hint_parts.append(
            "\n\n위 페르소나 정보를 바탕으로 캐릭터에 맞는 자연스러운 인사말을 해주세요. "
            "페르소나의 전문 분야와 특징을 반영하여 사용자에게 어떤 도움을 줄 수 있는지 간략히 소개해 주세요."
        )
        _greeting_hint = "".join(_greeting_hint_parts)
        yield {"type": "__result__", "short_result": _greeting_hint, "persona_data": persona_data}
        return

    short_result = _truncate_tool_result(tool_result, tool_name)
    yield {"type": "tool_result", "tool": tool_name, "result": short_result}
    yield {"type": "__result__", "short_result": short_result, "persona_data": None}


def _unwrap_tool_args(tool_name: str, tool_args: dict) -> dict:
    """중첩된 args 구조를 평탄화한다 (LLM이 function call schema를 args로 넘기는 패턴 방어)."""
    from tools import TOOLS
    if tool_args.get("name") in TOOLS and tool_args["name"] == tool_name:
        inner = {k: v for k, v in tool_args.items() if k != "name"}
        if "arguments" in inner and isinstance(inner["arguments"], dict):
            return inner["arguments"]
        if "params" in inner:
            p = inner["params"]
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except Exception:
                    pass
            return p if isinstance(p, dict) else inner
        return inner
    if "arguments" in tool_args and isinstance(tool_args["arguments"], dict):
        return tool_args["arguments"]
    return tool_args


def _handle_native_tool_calls(session: Session, native_tool_calls: list,
                               tool_calls_log: list, selected_tools: list,
                               phase_num: int, attempt: int):
    """
    PATH-1: LLM이 반환한 native tool_calls 배치를 처리한다.

    Yields: tool 관련 이벤트들
    Returns (via final yield): {"type": "__native_done__", "any_executed": bool, "selected_tools": list}
    """
    from tools import TOOLS
    _seen_calls = set()
    any_executed = False

    for tc in native_tool_calls:
        tool_name = tc["function"]["name"]
        try:
            tool_args = json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
        except (json.JSONDecodeError, TypeError):
            tool_args = {}
        tool_args = _unwrap_tool_args(tool_name, tool_args)
        call_id = tc.get("id", f"call_{attempt}")

        # 배치 내 중복 방지
        _tc_key = tool_name + "|" + json.dumps(tool_args, sort_keys=True, ensure_ascii=False)
        if _tc_key in _seen_calls:
            session.messages.append({"role": "tool", "tool_call_id": call_id, "content": "(duplicate, skipped)"})
            continue
        _seen_calls.add(_tc_key)

        # 루프 간 중복 방지
        prev_result = _find_dup_result(tool_name, tool_args, tool_calls_log)
        if prev_result is not None:
            log_activity("tool_call_dedup", session=session.id, tool=tool_name, args=tool_args)
            print(f"\033[93m[PATH-1 DEDUP] skipping {tool_name}\033[0m", flush=True)
            session.messages.append({"role": "tool", "tool_call_id": call_id, "content": prev_result})
            continue

        # send_notification 중복 억제
        if tool_name == "send_notification":
            _notif_msg = tool_args.get("message", "")
            if _check_notif_dedup(session.id, _notif_msg):
                _dup = "⚠ Duplicate notification suppressed (same message sent within 60s)"
                log_activity("notif_dedup", session=session.id, message=_notif_msg[:100])
                print(f"\033[93m[NOTIF-DEDUP] suppressed: {_notif_msg[:80]}\033[0m", flush=True)
                yield {"type": "tool_result", "tool": tool_name, "result": _dup}
                session.messages.append({"role": "tool", "tool_call_id": call_id, "content": _dup})
                tool_calls_log.append({"tool": tool_name, "args": tool_args, "result": _dup})
                continue

        log_activity("tool_call", session=session.id, tool=tool_name, args=tool_args)

        # 도구 실행
        short_result = None
        persona_data = None
        for evt in _execute_single_tool(session, tool_name, tool_args, call_id, tool_calls_log, phase_num, attempt):
            if evt["type"] == "__result__":
                short_result = evt["short_result"]
                persona_data = evt["persona_data"]
            else:
                yield evt

        if persona_data:
            session.messages.append({"role": "tool", "tool_call_id": call_id, "content": short_result})
            tool_calls_log.append({"tool": tool_name, "args": tool_args, "result": short_result})
            any_executed = True
            continue

        log_activity("tool_result", session=session.id, tool=tool_name, result=(short_result or "")[:200])
        if tool_name in ("remember", "recall", "forget", "evolve_skill", "find_skill"):
            print(f"\033[91m[{tool_name}] {(short_result or '')[:300]}\033[0m", flush=True)

        tool_calls_log.append({"tool": tool_name, "args": tool_args, "result": short_result or ""})
        print(f"\033[92m[PATH-1 result→LLM] {tool_name}: {(short_result or '')[:200]}\033[0m", flush=True)

        # run_workflow 후 워크플로우 도구 동적 제거
        if tool_name in ("run_workflow", "list_workflows"):
            _wf_remove = {"run_workflow", "list_workflows", "view_workflow",
                          "create_workflow", "update_workflow", "delete_workflow"}
            _before = len(selected_tools)
            selected_tools[:] = [t for t in selected_tools if t.get("function", {}).get("name") not in _wf_remove]
            print(f"[WF-DYN] Removed workflow tools after {tool_name} ({_before}→{len(selected_tools)})", flush=True)

        session.messages.append({"role": "tool", "tool_call_id": call_id, "content": short_result or ""})
        any_executed = True

    yield {"type": "__native_done__", "any_executed": any_executed, "selected_tools": selected_tools}


def _handle_text_tool_calls(session: Session, all_tool_calls_parsed: list, content: str,
                              tool_calls_log: list, phase_num: int, attempt: int,
                              is_qwen: bool, model_type: str):
    """
    PATH-2: 텍스트 파싱으로 추출한 tool_calls 목록을 처리한다.

    Yields: tool 관련 이벤트들
    Returns (via final yield): {"type": "__text_done__", "attempt": int}
    """
    import re as _re_tag

    for td in all_tool_calls_parsed:
        if attempt >= MAX_TOOL_CALLS:
            break
        tool_name = td.get("name", "")
        tool_args = _unwrap_tool_args(tool_name, td.get("arguments", {}))

        # 중복 방지
        if _find_dup_result(tool_name, tool_args, tool_calls_log) is not None:
            log_activity("tool_call_dedup_text", session=session.id, tool=tool_name, args=tool_args)
            print(f"\033[93m[PATH-2 DEDUP] skipping {tool_name}\033[0m", flush=True)
            continue

        log_activity("tool_call_text", session=session.id, tool=tool_name, args=tool_args)

        short_result = None
        persona_data = None
        for evt in _execute_single_tool(session, tool_name, tool_args, f"call_{attempt}", tool_calls_log, phase_num, attempt):
            if evt["type"] == "__result__":
                short_result = evt["short_result"]
                persona_data = evt["persona_data"]
            else:
                yield evt

        if persona_data:
            yield {
                "type": "final", "content": persona_data["greeting"],
                "session_id": session.id, "tool_calls": tool_calls_log,
                "persona_active": True, "persona_name": persona_data["name"],
                "persona_bg_image": persona_data.get("bg_image", ""),
            }
            return

        if tool_name in ("remember", "recall", "forget", "evolve_skill", "find_skill"):
            print(f"\033[91m[{tool_name}] {(short_result or '')[:300]}\033[0m", flush=True)

        tool_calls_log.append({"tool": tool_name, "args": tool_args, "result": short_result or ""})
        attempt += 1

    # 도구 결과를 세션에 추가
    session.add_message("assistant", content)
    all_results = "\n".join(
        f"[{log['tool']}] {log['result']}"
        for log in tool_calls_log[-len(all_tool_calls_parsed):]
    )
    tag_hint = "<TOOLCALL>" if model_type == "nemotron" else "<tool_call>"
    pending_reminder = (
        f"\n\n⚠ 사용자의 원래 요청에서 아직 실행하지 않은 작업이 있다면 "
        f"(write_file, send_email, create_event 등) 반드시 {tag_hint}로 실행하세요. "
        "text로 대체하지 마세요."
    )
    if is_qwen:
        session.add_message("user", f"<tool_response>\n{all_results}\n</tool_response>{pending_reminder}")
    else:
        session.add_message("user",
            f"[도구 결과]\n{all_results}\n\n"
            f"위 결과를 바탕으로 사용자에게 친절하게 답변하세요.{pending_reminder}"
        )
    yield {"type": "__text_done__", "attempt": attempt}


def _finalize_response(session: Session, content: str, tool_calls_log: list):
    """
    최종 응답 처리:
    - 워크플로우 pending 억제 감지
    - Planning 잔여물 정리
    - context compact
    - 로그 기록
    - final 이벤트 yield
    """
    # (WF-SUPPRESS 중간 통신 강제 숨김 로직을 완전히 제거하여 정상적으로 텍스트가 표시되게 함)

    session.add_message("assistant", content)
    log_activity("response", session=session.id, response=content[:500], tool_count=len(tool_calls_log))

    # Planning 잔여물 정리
    _PLAN_CLEANUP = (
        "[Tool Calls Planned",
        "[No tools called in planning phase]",
        "사용자의 요청에 답하세요. 반드시 도구를 먼저",
    )
    session.messages[:] = [
        m for m in session.messages
        if not any((m.get("content") or "").startswith(mk) for mk in _PLAN_CLEANUP)
    ]
    session.compact_after_turn()

    yield {
        "type": "final", "content": content, "session_id": session.id,
        "tool_calls": tool_calls_log, "memory_extract": None,
        "persona_active": session.persona_active,
        "persona_name": session.persona_name,
        "persona_bg_image": session.persona_bg_image,
    }


# ─────────────────────────────────────────────
# 통합 에이전트 루프 (본체)
# ─────────────────────────────────────────────

def _run_agent_loop(session: Session, user_message: str, output_format: str = None,
                    exclude_tools: set = None, only_tools: set = None,
                    phase_num: int = 0, max_tool_calls: int = None):
    """
    통합 에이전트 루프 (제너레이터).
    이벤트 dict를 yield합니다. run_agent_turn과 chat_stream 모두 이 함수를 사용합니다.

    Yields:
        {"type": "thinking", "attempt": int}
        {"type": "reasoning", "content": str}
        {"type": "planning", "status": str}
        {"type": "tool_start", "tool": str, "args": dict}
        {"type": "tool_result", "tool": str, "result": str}
        {"type": "code_output", "line": str}
        {"type": "persona_activated", ...}
        {"type": "wf_suppressed", ...}
        {"type": "error", "content": str}
        {"type": "final", "content": str, "session_id": str, "tool_calls": list}
    """
    from tools import TOOLS
    from tool_call_parser import detect_model_type, extract_tool_calls

    # ── 0. 페르소나 종료 확인 ──
    exit_event = _handle_persona_exit(session, user_message)
    if exit_event:
        yield exit_event
        return

    # ── 0.5. 직접 명령어 인터셉트 (소형 LLM 오작동 방지) ──
    _direct = _try_direct_command(session, user_message)
    if _direct is not None:
        yield _direct
        return

    # ── 1. 도구 선택 ──
    selected_tools = _select_tools_for_request(session, user_message, exclude_tools, only_tools)
    print(f"[MSG] {user_message[:150]}...", flush=True)

    # ── 2. 컨텍스트 준비 ──
    tool_calls_log = _prepare_context(session, user_message)

    # ── 3. 모델 정보 ──
    _llm = config.get("llm", {})
    _provider = _llm.get("provider", "local")
    model_name = _llm.get("providers", {}).get(_provider, {}).get("model_name", "")
    if not model_name:
        model_name = _llm.get("ollama_model", _llm.get("model_name", ""))
    model_name = model_name.lower()
    is_qwen = "qwen" in model_name
    model_type = detect_model_type(model_name)
    is_nanbeige = "nanbeige" in model_name

    print("==START===================")
    _effective_max = max_tool_calls if max_tool_calls is not None else MAX_TOOL_CALLS

    # ── 4. LLM + 도구 루프 ──
    for attempt in range(_effective_max + 1):
        print("loop start :", attempt)
        yield {"type": "thinking", "attempt": attempt}

        # Planning 단계 스킵 (ToolRouter가 도구 필터링 담당)
        if attempt == 0:
            yield {"type": "planning", "status": "analyzing"}
            yield {"type": "planning", "status": "executing"}

        # LLM 호출
        try:
            _total = sum(len(m.get("content", "") or "") for m in session.messages)
            _dbg = {
                "ts": datetime.now().isoformat(), "session": session.id,
                "attempt": attempt, "phase": f"attempt_{attempt}",
                "msg_count": len(session.messages), "total_chars": _total,
                "est_tokens": _total // 4,
            }
            with open("/tmp/llm_prompt_debug.jsonl", "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_dbg, ensure_ascii=False) + "\n")
        except Exception:
            pass
        msg = llm_client.chat(_build_2msg_prompt(session.messages, session=session), tools=selected_tools)

        content = msg.get("content", "") or ""
        thinking = msg.get("_thinking", "")
        print('msg', msg)
        native_tool_calls = msg.get("tool_calls")

        # 사고 과정 로그 (debug 모드에서만 클라이언트 전송)
        if thinking:
            if config.get("server", {}).get("debug", False):
                yield {"type": "reasoning", "content": thinking}
            log_activity("llm_thinking", session=session.id, attempt=attempt, thinking=thinking)

        # LLM 오류
        if content.startswith("[LLM") or content.startswith("[Claude"):
            log_activity("llm_error", session=session.id, error=content[:300])
            yield {"type": "error", "content": content}
            return

        # ── PATH-1: native tool_calls ──
        if native_tool_calls and attempt < MAX_TOOL_CALLS:
            print(f"\033[92m[PATH-1 native] attempt={attempt} tools={[tc['function']['name'] for tc in native_tool_calls]}\033[0m", flush=True)
            session.messages.append({"role": "assistant", "content": content, "tool_calls": native_tool_calls})

            any_executed = False
            _wf_direct = None
            for evt in _handle_native_tool_calls(session, native_tool_calls, tool_calls_log,
                                                  selected_tools, phase_num, attempt):
                if evt["type"] == "__native_done__":
                    any_executed = evt["any_executed"]
                    selected_tools = evt["selected_tools"]
                else:
                    yield evt

            # run_workflow / send_notification 실행 후: LLM에 돌리지 않고 직접 종료
            _executed_names = [tc["function"]["name"] for tc in native_tool_calls]

            if "run_workflow" in _executed_names and any_executed:
                _wf_direct = None
                for m in reversed(session.messages):
                    _mc = m.get("content") or ""
                    # guard 메시지("⚠ STOP: A workflow is already in progress") 제외
                    # 정상 시작 메시지(⚡ 또는 "Workflow") 만 직접 출력
                    if m.get("role") == "tool" and _mc.strip() and "already in progress" not in _mc:
                        _wf_direct = _mc
                        break
                if _wf_direct:
                    session.add_message("assistant", _wf_direct)
                    yield {"type": "final", "content": _wf_direct, "tool_calls": tool_calls_log}
                    return

            if "send_notification" in _executed_names and any_executed:
                # 전송 완료 — LLM에 다시 넣으면 중복 호출 발생, 직접 종료
                from i18n import t as _t
                yield {"type": "final", "content": content or _t("notification.send_done"), "tool_calls": tool_calls_log}
                return

            # 자체 완결형 도구: 결과가 곧 응답이므로 2차 LLM 호출 불필요 (5초 절약)
            _DIRECT_RETURN_TOOLS = {
                "recall", "remember", "forget",
                "host_open_app", "host_open_url", "delete_workflow", "delete_persona",
                "delete_code",
                "run_stored_code", "run_workflow",
                "ask_user",
            }
            if any_executed and _executed_names and all(n in _DIRECT_RETURN_TOOLS for n in _executed_names):
                _direct = None
                for m in reversed(session.messages):
                    if m.get("role") == "tool" and (m.get("content") or "").strip():
                        _direct = m["content"]
                        break
                if _direct:
                    session.add_message("assistant", _direct)
                    yield {"type": "final", "content": _direct, "tool_calls": tool_calls_log}
                    return

            if not any_executed:
                session.messages.append({"role": "system", "content": "⚠ 이미 실행된 도구들입니다. 기존 결과를 사용하여 답변하세요."})
            continue

        # ── PATH-2: 텍스트 파싱 tool_calls ──
        print('content test', content)
        print('thinking ', thinking)
        search_text = content if content.strip() else (thinking or "")
        all_tool_calls_parsed = extract_tool_calls(search_text, model_type)

        if all_tool_calls_parsed and attempt < MAX_TOOL_CALLS:
            print(f"\033[93m[PATH-2 text] attempt={attempt} tools={[td.get('name') for td in all_tool_calls_parsed]}\033[0m", flush=True)
            done = False
            for evt in _handle_text_tool_calls(session, all_tool_calls_parsed, content,
                                                tool_calls_log, phase_num, attempt,
                                                is_qwen, model_type):
                if evt["type"] == "__text_done__":
                    attempt = evt["attempt"]
                elif evt["type"] == "final":
                    yield evt
                    done = True
                    break
                else:
                    yield evt
            if done:
                return
            continue

        # ── 최종 응답 ──
        print(f"\033[96m[DEBUG] NO-TOOL PATH reached. user_msg={user_message[:60]}\033[0m", flush=True)
        done = False
        for evt in _finalize_response(session, content, tool_calls_log):
            yield evt
            if evt["type"] in ("final", "wf_suppressed"):
                done = True
        if done:
            return

    # 도구 호출 한도 도달
    print(f"\033[96m[DEBUG] MAX-TOOLS PATH reached. user_msg={user_message[:60]}\033[0m", flush=True)
    log_activity("max_tools_reached", session=session.id)
    session.compact_after_turn()
    yield {
        "type": "final", "content": "⚠ 도구 호출 한도에 도달했습니다.",
        "session_id": session.id, "tool_calls": tool_calls_log, "memory_extract": None,
        "persona_active": session.persona_active, "persona_name": session.persona_name,
        "persona_bg_image": session.persona_bg_image,
    }


def run_agent_turn(session: Session, user_message: str, output_format: str = None) -> dict:
    """에이전트 1턴 실행 (비스트리밍). 워크플로우 chunk 순차 실행 포함."""
    import tools.workflow_tools as _wf_mod
    _wf_mod._pending_wf_chunks = None
    _wf_mod._last_wf_run_time = 0  # reset cooldown for new user turn

    final_result = None
    all_tool_calls = []

    # ── 일시정지된 워크플로우가 있으면 메인 루프 건너뛰고 바로 재개 ──
    if session.pending_wf:
        _resumed = session.pending_wf
        session.pending_wf = None
        _wf_mod._pending_wf_chunks = _resumed
        print(f"[WF-RESUME] Skipping main loop, resuming '{_resumed['wf_name']}' with {len(_resumed['remaining'])} remaining chunks", flush=True)
        session.messages.append({"role": "user", "content": user_message})
        final_result = {
            "response": "", "tool_calls": [],
            "persona_active": session.persona_active,
            "persona_name": session.persona_name,
            "persona_bg_image": session.persona_bg_image,
        }
    else:
        for event in _run_agent_loop(session, user_message, output_format=output_format):
            if event["type"] == "final":
                final_result = {
                    "response": event["content"],
                    "tool_calls": event.get("tool_calls", []),
                    "persona_active": event.get("persona_active", session.persona_active),
                    "persona_name": event.get("persona_name", session.persona_name),
                    "persona_bg_image": event.get("persona_bg_image", session.persona_bg_image),
                }
                all_tool_calls.extend(event.get("tool_calls", []))
            elif event["type"] == "error":
                return {"response": event["content"], "tool_calls": [],
                        "persona_active": session.persona_active,
                        "persona_name": session.persona_name,
                        "persona_bg_image": session.persona_bg_image}

        # 메인 루프 후 pending_wf 감지
        if session.pending_wf and not _wf_mod._pending_wf_chunks:
            _resumed = session.pending_wf
            session.pending_wf = None
            _wf_mod._pending_wf_chunks = _resumed
            print(f"[WF-RESUME] Resuming '{_resumed['wf_name']}' with {len(_resumed['remaining'])} remaining chunks", flush=True)

    if not final_result:
        return {"response": "⚠ 예상치 못한 오류", "tool_calls": [],
                "persona_active": False, "persona_name": "", "persona_bg_image": ""}

    # 남은 워크플로우 chunks 순차 실행
    from tools.workflow_tools import _format_chunk
    wf_data = _wf_mod._pending_wf_chunks

    if wf_data and wf_data.get("remaining"):
        remaining = list(wf_data["remaining"])
        wf_name = wf_data["wf_name"]
        total = wf_data.get("total_chunks", wf_data.get("total", 0))
        _wf_paused = False

        for i, chunk_info in enumerate(remaining):
            chunk_num = chunk_info["chunk_num"]
            _step_types = set(chunk_info.get("step_types", ["prompt"]))

            _before_cleanup = len(session.messages)
            session.messages[:] = [
                m for m in session.messages
                if m.get("role") not in ("tool",)
                and not (m.get("role") == "assistant" and m.get("tool_calls"))
            ]

            instruction = _format_chunk(
                chunk_info["steps"], chunk_num, total,
                wf_name, chunk_info["step_offset"]
            )
            _wf_exclude = {"run_workflow", "list_workflows", "view_workflow",
                           "create_workflow", "update_workflow", "delete_workflow"}
            _max_calls = None
            _wf_only = {"run_stored_code", "stop_running_code", "list_running_codes"} if _step_types == {"code"} else None

            # 마지막 chunk에는 STOP 지시 추가
            if chunk_info is remaining[-1]:
                instruction += (
                    "\n\n⚠ FINAL PHASE: After completing the steps above, provide a summary of all results. "
                    "Do NOT call any more tools. Do NOT re-execute any previous steps."
                )

            session._needs_input = False  # reset before chunk
            for event in _run_agent_loop(session, instruction, output_format=output_format,
                                         exclude_tools=_wf_exclude, only_tools=_wf_only,
                                         phase_num=chunk_num, max_tool_calls=_max_calls):
                if event["type"] == "final":
                    final_result = {
                        "response": event["content"],
                        "tool_calls": all_tool_calls + event.get("tool_calls", []),
                        "persona_active": event.get("persona_active", session.persona_active),
                        "persona_name": event.get("persona_name", session.persona_name),
                        "persona_bg_image": event.get("persona_bg_image", session.persona_bg_image),
                    }

            # ── PAUSE 감지 ──
            if session._needs_input:
                session._needs_input = False
                session.pending_wf = {
                    "wf_name": wf_name,
                    "remaining": remaining[i+1:],  # 현재 chunk는 이미 실행됨, 다음부터
                    "total": total,
                }
                print(f"[WF-PAUSE] Paused at chunk {chunk_num}/{total}, {len(remaining)-i-1} remaining", flush=True)
                _wf_paused = True
                break

        if not _wf_paused:
            # 워크플로우 끝난 뒤 context 정리
            session.messages[:] = [
                m for m in session.messages
                if m.get("role") not in ("tool",)
                and not (m.get("role") == "assistant" and m.get("tool_calls"))
            ]
        _wf_mod._pending_wf_chunks = None

    return final_result


# ─────────────────────────────────────────────
# API 엔드포인트
# ─────────────────────────────────────────────

_start_time = datetime.now()

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _=Depends(verify_api_key)):
    """대화 엔드포인트"""
    session = get_or_create_session(req.session_id)

    # 동기 에이전트 루프를 별도 스레드에서 실행 (스케줄러와 충돌 방지)
    def _run_with_lock():
        with _llm_busy:
            return run_agent_turn(session, req.message, output_format=req.format)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, _run_with_lock
    )

    return ChatResponse(
        response=result["response"],
        session_id=session.id,
        tool_calls=result["tool_calls"],
        persona_active=result.get("persona_active", False),
        persona_name=result.get("persona_name", ""),
        persona_bg_image=result.get("persona_bg_image", ""),
    )


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, _=Depends(verify_api_key)):
    """실시간 스트리밍 대화 엔드포인트 (SSE) — _run_agent_loop 소비"""
    session = get_or_create_session(req.session_id)
    print(f"\033[96m[CHAT_STREAM] req.session_id={req.session_id}, session.id={session.id}, pending_wf={bool(session.pending_wf)}\033[0m", flush=True)

    # 호스트 컨context (설치된 앱 목록 등) 주입
    if req.host_context:
        apps = req.host_context.get("installed_apps", [])
        if apps:
            session.host_app_list = ", ".join(apps[:50])

    # 기억 추출용 원본 메시지 저장
    _user_msg_for_extract = req.message

    def event_generator():
        try:
            import tools.workflow_tools as _wf_mod

            # ── 일시정지된 워크플로우가 있으면 메인 루프 건너뛰고 바로 재개 ──
            if session.pending_wf:
                _resumed = session.pending_wf
                session.pending_wf = None
                _wf_mod._pending_wf_chunks = _resumed
                # resume 경로에서는 _pending_wf_chunks를 리셋하지 않음 (Fix 4)
                print(f"[WF-RESUME] Skipping main loop, resuming '{_resumed['wf_name']}' with {len(_resumed['remaining'])} remaining chunks", flush=True)
                # 사용자 답변을 컨텍스트에 추가 — instruction에도 포함하여 LLM이 맥락을 이해
                session.messages.append({"role": "user", "content": f"[사용자 답변] {req.message}", "_wf_resume": True})
            else:
                _wf_mod._pending_wf_chunks = None
                _wf_mod._last_wf_run_time = 0  # reset cooldown for new user turn
                # 메인 에이전트 루프 실행
                for event in _run_agent_loop(session, req.message, output_format=req.format):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                # 메인 루프 후 pending_wf 감지 (새로 pause된 경우)
                if session.pending_wf and not _wf_mod._pending_wf_chunks:
                    _resumed = session.pending_wf
                    session.pending_wf = None
                    _wf_mod._pending_wf_chunks = _resumed
                    print(f"[WF-RESUME] Resuming '{_resumed['wf_name']}' with {len(_resumed['remaining'])} remaining chunks", flush=True)

            # 남은 워크플로우 chunks 순차 실행
            from tools.workflow_tools import _format_chunk
            wf_data = _wf_mod._pending_wf_chunks
            if wf_data and wf_data.get("remaining"):
                remaining = list(wf_data["remaining"])
                wf_name = wf_data["wf_name"]
                total = wf_data.get("total_chunks", wf_data.get("total", 0))
                _wf_paused = False

                # ── WF progress: 시작 ──
                yield f"data: {json.dumps({'type': 'wf_progress', 'wf_name': wf_name, 'step': 0, 'total': total, 'status': 'running'}, ensure_ascii=False)}\n\n"

                for i, chunk_info in enumerate(remaining):
                    chunk_num = chunk_info["chunk_num"]
                    _step_types = set(chunk_info.get("step_types", ["prompt"]))

                    # 이전 chunk의 tool 메시지 정리
                    _before_cleanup = len(session.messages)
                    session.messages[:] = [
                        m for m in session.messages
                        if m.get("role") not in ("tool",)
                        and not (m.get("role") == "assistant" and m.get("tool_calls"))
                    ]
                    _after_cleanup = len(session.messages)
                    if _before_cleanup != _after_cleanup:
                        print(f"[WF-CLEANUP] Removed {_before_cleanup - _after_cleanup} tool messages", flush=True)

                    instruction = _format_chunk(
                        chunk_info["steps"], chunk_num, total,
                        wf_name, chunk_info["step_offset"]
                    )
                    _wf_exclude = {"run_workflow", "list_workflows", "view_workflow",
                                   "create_workflow", "update_workflow", "delete_workflow"}

                    # 제한 없음 — chunk 내에서 필요한 만큼 tool 호출 허용
                    _max_calls = None
                    _wf_only = {"run_stored_code", "stop_running_code", "list_running_codes"} if _step_types == {"code"} else None
                    print(f"[WF-PHASE] Phase {chunk_num}/{total} types={_step_types} max_calls={_max_calls}", flush=True)

                    # ── WF progress: 단계 시작 ──
                    yield f"data: {json.dumps({'type': 'wf_progress', 'wf_name': wf_name, 'step': chunk_num, 'total': total, 'status': 'running'}, ensure_ascii=False)}\n\n"

                    session._needs_input = False  # reset before chunk
                    for event in _run_agent_loop(session, instruction, output_format=req.format,
                                                 exclude_tools=_wf_exclude,
                                                 only_tools=_wf_only,
                                                 phase_num=chunk_num,
                                                 max_tool_calls=_max_calls):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                    # ── PAUSE 감지 ──
                    if session._needs_input:
                        session._needs_input = False
                        session.pending_wf = {
                            "wf_name": wf_name,
                            "remaining": remaining[i+1:],  # 현재 chunk는 이미 실행됨, 다음부터
                            "total": total,
                        }
                        print(f"[WF-PAUSE] Paused at chunk {chunk_num}/{total}, {len(remaining)-i-1} remaining", flush=True)
                        # ── WF progress: 일시정지 ──
                        yield f"data: {json.dumps({'type': 'wf_progress', 'wf_name': wf_name, 'step': chunk_num, 'total': total, 'status': 'paused'}, ensure_ascii=False)}\n\n"
                        _wf_paused = True
                        break

                # pause 여부 관계없이 _pending_wf_chunks 초기화 (re-entry guard 해제)
                _wf_mod._pending_wf_chunks = None

                # ── WF progress: 완료 ──
                if not _wf_paused:
                    yield f"data: {json.dumps({'type': 'wf_progress', 'wf_name': wf_name, 'step': total, 'total': total, 'status': 'done'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            print(f"[event_generator] ERROR: {e}", flush=True)
            import traceback; traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': f'서버 내부 오류: {str(e)[:200]}'}, ensure_ascii=False)}\n\n"

        # 기억 추출 — SSE 완료 직후 별도 스레드로 실행 (HTTP 응답 차단 없음)
        import threading
        def _bg_extract():
            try:
                from tools.memory_tools import extract_and_remember
                extract_and_remember(_user_msg_for_extract)
            except Exception as e:
                print(f"[extract_and_remember] ERROR: {e}", flush=True)
        threading.Thread(target=_bg_extract, daemon=True).start()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@app.get("/status", response_model=StatusResponse)
async def status():
    """에이전트 상태 (인증 불필요)"""
    return StatusResponse(
        name=config.get("assistant", {}).get("name", "Xoul"),
        version="2.0.0",
        llm_provider=llm_client.get_provider_info(),
        llm_connected=llm_client.check_connection(),
        active_sessions=len(sessions),
        uptime_seconds=(datetime.now() - _start_time).total_seconds()
    )


@app.get("/memories", response_model=MemoryResponse)
async def memories(_=Depends(verify_api_key)):
    """저장된 기억 조회"""
    from tools.memory_tools import recall
    try:
        ctx = recall()
    except Exception:
        ctx = "기억을 로드할 수 없습니다."
    return MemoryResponse(context=ctx)


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, _=Depends(verify_api_key)):
    """세션 삭제"""
    if session_id in sessions:
        del sessions[session_id]
        return {"message": f"세션 {session_id} 삭제됨"}
    raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")


class WorkflowImportRequest(BaseModel):
    name: str
    description: str = ""
    prompts: str = ""          # 줄바꿈으로 구분된 프롬프트
    hint_tools: str = ""       # 쉼표로 구분된 도구 목록
    schedule: str = ""

@app.post("/workflow/import")
async def import_workflow(req: WorkflowImportRequest, _=Depends(verify_api_key)):
    """Workflow Store에서 직접 Import — LLM 거치지 않고 create_workflow 호출"""
    from tools.workflow_tools import create_workflow, list_workflows
    result = create_workflow(
        name=req.name,
        prompts=req.prompts,
        description=req.description,
        hint_tools=req.hint_tools,
        schedule=req.schedule,
    )
    if result.startswith("⚠"):
        raise HTTPException(status_code=400, detail=result)
    import re
    wf_list = list_workflows()
    wfdata = ""
    m = re.search(r'(<!--(?:WFDATA|CODE_DATA|PERSONA_DATA):.*?-->)', wf_list, re.DOTALL)
    if m:
        wfdata = m.group(1)
    return {"message": result, "wfdata": wfdata}


class WorkflowSaveRequest(BaseModel):
    name: str
    prompts: str  # JSON 배열 문자열
    description: str = ""
    schedule: str = ""

@app.post("/workflow/save")
async def save_workflow(req: WorkflowSaveRequest, _=Depends(verify_api_key)):
    """데스크톱에서 직접 Workflow 생성/수정 — schedule은 _normalize_schedule 통과"""
    from tools.workflow_tools import list_workflows, _normalize_schedule, _fill_missing_embeddings_async
    import re, os, sqlite3, json

    db_path = os.path.expanduser("~/.xoul/workflows.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    exists = conn.execute("SELECT 1 FROM workflows WHERE name = ?", (req.name,)).fetchone()

    # schedule 정규화
    normalized_schedule = _normalize_schedule(req.schedule) if req.schedule else ""

    if exists:
        # UPDATE — prompts를 그대로 저장 (re-parse 없이)
        updates = []
        params = []
        if req.prompts:
            updates.append("prompts = ?")
            params.append(req.prompts)
        if req.description:
            updates.append("description = ?")
            params.append(req.description)
        if req.schedule is not None:
            updates.append("schedule = ?")
            params.append(normalized_schedule)
        if updates:
            params.append(req.name)
            conn.execute(f"UPDATE workflows SET {', '.join(updates)} WHERE name = ?", params)
            conn.commit()
        msg = f"✅ '{req.name}' 워크플로우 수정 완료"
    else:
        # CREATE
        conn.execute(
            "INSERT INTO workflows (name, prompts, description, schedule, run_count) VALUES (?, ?, ?, ?, 0)",
            (req.name, req.prompts, req.description or "", normalized_schedule),
        )
        conn.commit()
        msg = f"✅ '{req.name}' 워크플로우 생성 완료"

    conn.close()

    # 워크플로우 목록 갱신
    wf_list = list_workflows()
    wfdata = ""
    m = re.search(r'(<!--(?:WFDATA|CODE_DATA|PERSONA_DATA):.*?-->)', wf_list, re.DOTALL)
    if m:
        wfdata = m.group(1)
    return {"message": msg, "wfdata": wfdata}

# ─── Persona Endpoints ───

class PersonaImportRequest(BaseModel):
    name: str
    description: str = ""
    prompt: str = ""
    bg_image: str = ""

@app.post("/persona/import")
async def import_persona(req: PersonaImportRequest, _=Depends(verify_api_key)):
    """Persona Store에서 직접 Import"""
    from tools.persona_tools import create_persona
    result = create_persona(
        name=req.name,
        prompt=req.prompt,
        description=req.description,
        bg_image=req.bg_image,
    )
    if result.startswith("⚠"):
        raise HTTPException(status_code=400, detail=result)
    return {"message": result}

@app.get("/personas")
async def list_personas_api(_=Depends(verify_api_key)):
    """로컬 페르소나 목록"""
    try:
        import sqlite3, os
        db_path = os.path.expanduser("~/.xoul/workflows.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT name FROM personas ORDER BY created DESC").fetchall()
        conn.close()
        names = [r["name"] for r in rows]
        return {"personas": names}
    except Exception as e:
        return {"personas": [], "error": str(e)}

@app.get("/workflows/list")
async def list_workflows_paginated(page: int = 1, per_page: int = 10, _=Depends(verify_api_key)):
    """워크플로우 목록 + WFDATA 반환 (Desktop 페이지네이션 직접 호출용)"""
    import re
    from tools.workflow_tools import list_workflows
    result = list_workflows(str(page), str(per_page))
    wfdata = ""
    m = re.search(r'(<!--(?:WFDATA):.*?-->)', result, re.DOTALL)
    if m:
        wfdata = m.group(1)
    return {"wfdata": wfdata}

@app.get("/workflows")
async def list_workflows_api(_=Depends(verify_api_key)):
    """보유 워크플로우 이름 목록 반환 (Desktop → Web 동기화용)"""
    import sqlite3
    try:
        db_path = os.path.expanduser("~/.xoul/workflows.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT name FROM workflows ORDER BY created DESC").fetchall()
        names = [r["name"] for r in rows]
        conn.close()
        return {"workflows": names}
    except Exception as e:
        return {"workflows": [], "error": str(e)}


# ─── Code Endpoints ───

class CodeImportRequest(BaseModel):
    name: str
    description: str = ""
    code: str = ""
    params: str = "[]"

@app.post("/code/import")
async def import_code(req: CodeImportRequest, _=Depends(verify_api_key)):
    """Code Store에서 직접 Import — codes 테이블에 저장"""
    import sqlite3
    try:
        db_path = os.path.expanduser("~/.xoul/workflows.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT DEFAULT '',
                code TEXT DEFAULT '',
                params TEXT DEFAULT '[]',
                category TEXT DEFAULT 'Other',
                created TEXT DEFAULT ''
            )
        """)
        # 이미 존재하면 업데이트 (최신 코드로 덮어쓰기)
        existing = conn.execute("SELECT name FROM codes WHERE name = ?", (req.name,)).fetchone()
        if existing:
            conn.execute("""
                UPDATE codes SET code = ?, description = ?, params = ? WHERE name = ?
            """, (req.code, req.description, req.params, req.name))
            conn.commit()
            conn.close()
            return {"message": f"✅ '{req.name}' 코드가 업데이트 되었습니다."}

        from datetime import datetime
        conn.execute("""
            INSERT INTO codes (name, description, code, params, created)
            VALUES (?, ?, ?, ?, ?)
        """, (req.name, req.description, req.code, req.params, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        conn.close()
        return {"message": f"✅ '{req.name}' 코드가 import 되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/codes")
async def list_codes_api(_=Depends(verify_api_key)):
    """로컬 코드 목록"""
    try:
        import sqlite3
        db_path = os.path.expanduser("~/.xoul/workflows.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT name FROM codes ORDER BY created DESC").fetchall()
            names = [r["name"] for r in rows]
        except Exception:
            names = []
        conn.close()
        return {"codes": names}
    except Exception as e:
        return {"codes": [], "error": str(e)}

@app.get("/code/{name}")
async def get_code_detail(name: str, _=Depends(verify_api_key)):
    """코드 상세 정보 반환 (Desktop 수정 다이얼로그용)"""
    import sqlite3
    try:
        db_path = os.path.expanduser("~/.xoul/workflows.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT name, description, code, params, category FROM codes WHERE name = ?",
            (name,)
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"Code '{name}' not found")
        return {
            "name": row["name"],
            "description": row["description"] or "",
            "code": row["code"] or "",
            "params": row["params"] or "[]",
            "category": row["category"] or "Other",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CodeUpdateRequest(BaseModel):
    name: str = ""
    description: str = ""
    code: str = ""

@app.put("/code/{name}")
async def update_code(name: str, req: CodeUpdateRequest, _=Depends(verify_api_key)):
    """코드 수정 (Desktop 수정 다이얼로그용)"""
    import sqlite3
    try:
        db_path = os.path.expanduser("~/.xoul/workflows.db")
        conn = sqlite3.connect(db_path)
        new_name = req.name or name
        conn.execute(
            "UPDATE codes SET name=?, description=?, code=? WHERE name=?",
            (new_name, req.description, req.code, name)
        )
        conn.commit()
        conn.close()
        return {"message": f"✅ Code '{new_name}' updated."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/code/stop")
async def stop_code_api(req: dict = {}, _=Depends(verify_api_key)):
    """실행 중인 코드 중지 (Desktop Stop 버튼용)"""
    from tools.code_tools import stop_running_code
    name = req.get("name", "") if req else ""
    result = stop_running_code(name)
    return {"message": result}

@app.post("/code/run")
async def run_code_api(req: dict, _=Depends(verify_api_key)):
    """실행 로그 노출 없이 백그라운드 스레드에서 코드 실행 (Desktop Arena Join 용도)"""
    code_name = req.get("name")
    params = req.get("params", {})
    timeout = str(req.get("timeout", "600"))
    
    if not code_name:
        return {"error": "Missing code name"}

    def _run_bg():
        from tools.code_tools import run_stored_code
        import json as _json
        p_str = _json.dumps(params, ensure_ascii=False) if isinstance(params, dict) else str(params)
        try:
            run_stored_code(code_name, params=p_str, timeout=timeout)
        except Exception as e:
            print(f"[BG Exec Error] {code_name}: {e}")

    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_bg)
    return {"message": f"'{code_name}' 백그라운드 실행 시작"}

@app.post("/code/run-sync")
async def run_code_sync_api(req: dict, _=Depends(verify_api_key)):
    """코드를 동기 실행하고 결과를 반환 (Desktop 팝업 파라미터 입력용, LLM 우회)"""
    code_name = req.get("name")
    params = req.get("params", {})
    timeout = str(req.get("timeout", "30"))
    session_id = req.get("session_id")

    if not code_name:
        return {"error": "Missing code name"}

    import asyncio
    loop = asyncio.get_event_loop()

    def _run():
        from tools.code_tools import run_stored_code
        import json as _json
        p_str = _json.dumps(params, ensure_ascii=False) if isinstance(params, dict) else str(params)
        return run_stored_code(code_name, params=p_str, timeout=timeout)

    result = await loop.run_in_executor(None, _run)
    return {"result": result, "code_name": code_name}


@app.post("/workflow/cancel")
async def workflow_cancel(req: dict, _=Depends(verify_api_key)):
    """일시정지된 워크플로우를 취소 (pending_wf 클리어)"""
    session_id = req.get("session_id")
    if not session_id or session_id not in sessions:
        return {"status": "no_session"}
    session = sessions[session_id]
    wf_name = ""
    if session.pending_wf:
        wf_name = session.pending_wf.get("wf_name", "")
        session.pending_wf = None
    import tools.workflow_tools as _wf_mod
    _wf_mod._pending_wf_chunks = None
    session._needs_input = False
    print(f"\033[93m[WF-CANCEL] session={session_id}, wf={wf_name}\033[0m", flush=True)
    return {"status": "cancelled", "wf_name": wf_name}


@app.post("/workflow/resume")
async def workflow_resume(req: dict, _=Depends(verify_api_key)):
    """일시정지된 워크플로우를 직접 재개 (LLM 턴 없이 남은 chunks 즉시 실행)"""
    session_id = req.get("session_id")
    if not session_id or session_id not in sessions:
        return JSONResponse({"error": "Invalid session"}, status_code=400)

    session = sessions[session_id]
    if not session.pending_wf:
        return JSONResponse({"error": "No paused workflow"}, status_code=400)

    wf_data = session.pending_wf
    session.pending_wf = None

    from tools.workflow_tools import _format_chunk

    def event_generator():
        remaining = list(wf_data["remaining"])
        wf_name = wf_data["wf_name"]
        total = wf_data.get("total_chunks", wf_data.get("total", 0))

        yield f"data: {json.dumps({'type': 'thinking', 'attempt': 0}, ensure_ascii=False)}\n\n"
        # ── WF progress: resume 시작 ──
        yield f"data: {json.dumps({'type': 'wf_progress', 'wf_name': wf_name, 'step': 0, 'total': total, 'status': 'running'}, ensure_ascii=False)}\n\n"

        for i, chunk_info in enumerate(remaining):
            chunk_num = chunk_info["chunk_num"]
            _step_types = set(chunk_info.get("step_types", ["prompt"]))

            # 이전 chunk tool 메시지 + resume 입력 메시지 정리
            session.messages[:] = [
                m for m in session.messages
                if m.get("role") not in ("tool",)
                and not (m.get("role") == "assistant" and m.get("tool_calls"))
                and not (i > 0 and m.get("_wf_resume"))  # 첫 chunk 이후엔 resume 파라미터 제거
            ]

            instruction = _format_chunk(
                chunk_info["steps"], chunk_num, total,
                wf_name, chunk_info["step_offset"]
            )
            _wf_exclude = {"run_workflow", "list_workflows", "view_workflow",
                           "create_workflow", "update_workflow", "delete_workflow"}
            _max_calls = None
            _wf_only = {"run_stored_code", "stop_running_code", "list_running_codes"} if _step_types == {"code"} else None

            session._needs_input = False
            # ── WF progress: 단계 시작 ──
            yield f"data: {json.dumps({'type': 'wf_progress', 'wf_name': wf_name, 'step': chunk_num, 'total': total, 'status': 'running'}, ensure_ascii=False)}\n\n"
            for event in _run_agent_loop(session, instruction, output_format="markdown",
                                         exclude_tools=_wf_exclude,
                                         only_tools=_wf_only,
                                         phase_num=chunk_num,
                                         max_tool_calls=_max_calls):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # PAUSE 감지
            if session._needs_input:
                session._needs_input = False
                session.pending_wf = {
                    "wf_name": wf_name,
                    "remaining": remaining[i+1:],  # 현재 chunk는 이미 실행됨, 다음부터
                    "total": total,
                }
                print(f"[WF-RESUME-PAUSE] Paused at chunk {chunk_num}/{total}, {len(remaining)-i-1} remaining", flush=True)
                # ── WF progress: 일시정지 ──
                yield f"data: {json.dumps({'type': 'wf_progress', 'wf_name': wf_name, 'step': chunk_num, 'total': total, 'status': 'paused'}, ensure_ascii=False)}\n\n"
                break

        # ── WF progress: 완료 (pause 안 된 경우) ──
        if not session.pending_wf:
            yield f"data: {json.dumps({'type': 'wf_progress', 'wf_name': wf_name, 'step': total, 'total': total, 'status': 'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class MemorySaveRequest(BaseModel):
    key: str
    value: str
    category: str = "general"
    api_key: str = ""

@app.post("/memory/save")
async def memory_save(req: MemorySaveRequest, _=Depends(verify_api_key)):
    """파라미터를 LTM에 직접 저장 (LLM 미경유)"""
    from tools.memory_tools import remember
    result = remember(req.key, req.value, req.category)
    return {"ok": True, "result": result}


@app.get("/code/running")
async def running_codes_api(_=Depends(verify_api_key)):
    """실행 중인 코드 목록"""
    from tools.code_tools import list_running_codes, _load_running_codes, _cleanup_dead_pids, _save_running_codes
    entries = _load_running_codes()
    entries = _cleanup_dead_pids(entries)
    _save_running_codes(entries)
    return {"running": entries}

@app.get("/workflow/{name}")
async def get_workflow_detail(name: str, _=Depends(verify_api_key)):
    """워크플로우 상세 정보 반환 (Desktop 수정 다이얼로그용)"""
    import sqlite3
    try:
        db_path = os.path.expanduser("~/.xoul/workflows.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT name, description, prompts, hint_tools, schedule FROM workflows WHERE name = ?",
            (name,)
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"Workflow '{name}' not found")
        prompts = json.loads(row["prompts"]) if row["prompts"] else []
        return {
            "name": row["name"],
            "description": row["description"] or "",
            "prompts": prompts,
            "hint_tools": row["hint_tools"] or "[]",
            "schedule": row["schedule"] or "",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/persona/{name}")
async def get_persona_detail(name: str, _=Depends(verify_api_key)):
    """페르소나 상세 정보 반환 (Desktop 수정 다이얼로그용)"""
    import sqlite3
    try:
        db_path = os.path.expanduser("~/.xoul/workflows.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT name, description, prompt FROM personas WHERE name = ?",
            (name,)
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"Persona '{name}' not found")
        return {
            "name": row["name"],
            "description": row["description"] or "",
            "prompt": row["prompt"] or "",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/notifications")
async def get_notifications_api(_=Depends(verify_api_key)):
    """읽지 않은 스케줄 알림 조회"""
    try:
        from tools.workflow_tools import get_notifications
        result = get_notifications(unread_only=True)
        return {"notifications": result}
    except Exception as e:
        return {"notifications": [], "error": str(e)}


@app.post("/notifications/read")
async def mark_notifications_read_api(_=Depends(verify_api_key)):
    """모든 알림을 읽음 처리"""
    try:
        from tools.workflow_tools import mark_notifications_read
        mark_notifications_read()
        return {"message": "알림을 읽음 처리했습니다."}
    except Exception as e:
        return {"error": str(e)}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """텔레그램 봇 웹훅"""
    tg_cfg = config.get("clients", {}).get("telegram", {})
    if not tg_cfg.get("enabled"):
        return {"ok": False, "error": "Telegram not enabled"}

    bot_token = tg_cfg.get("bot_token", "")
    if not bot_token:
        return {"ok": False, "error": "No bot token"}

    body = await request.json()

    # ── callback_query 처리 (인라인 버튼 클릭) ──
    callback = body.get("callback_query")
    if callback:
        cb_data = callback.get("data", "")
        cb_chat_id = callback.get("message", {}).get("chat", {}).get("id")

        if cb_data.startswith("wf_result:") and cb_chat_id:
            try:
                notif_id = int(cb_data.split(":")[1])
                from tools.workflow_tools import _get_db
                db = _get_db()
                row = db.execute(
                    "SELECT wf_name, result, created FROM wf_notifications WHERE id = ?",
                    (notif_id,)
                ).fetchone()

                from telegram_client import TelegramBot
                bot = TelegramBot(bot_token)

                if row:
                    result_text = row["result"] or "(결과 없음)"
                    header = f"📋 *{row['wf_name']}*\n🕐 {row['created']}\n\n"
                    full_msg = header + result_text
                    bot.send_message(cb_chat_id, full_msg)
                    # 읽음 처리
                    db.execute("UPDATE wf_notifications SET read = 1 WHERE id = ?", (notif_id,))
                    db.commit()
                else:
                    bot.send_message(cb_chat_id, "⚠ 결과를 찾을 수 없습니다.")

                # callback_query 응답 (버튼 로딩 해제)
                try:
                    import urllib.request as _urllib
                    answer_url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
                    _urllib.urlopen(_urllib.Request(
                        answer_url,
                        data=json.dumps({"callback_query_id": callback["id"]}).encode(),
                        headers={"Content-Type": "application/json"}
                    ), timeout=5)
                except Exception:
                    pass

            except Exception as e:
                print(f"Telegram callback error: {e}")

        return {"ok": True}

    # ── 일반 메시지 처리 ──
    message = body.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return {"ok": True}

    # 텔레그램 chat_id를 세션 ID로 사용
    session_id = f"tg_{chat_id}"
    session = get_or_create_session(session_id)

    # 에이전트 실행 (별도 스레드)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, run_agent_turn, session, text
    )

    # 텔레그램으로 응답 전송
    import urllib.request
    reply = result["response"]
    if len(reply) > 4000:
        reply = reply[:4000] + "\n... (잘림)"

    send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": reply}).encode("utf-8")
    try:
        req = urllib.request.Request(
            send_url, data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}")

    return {"ok": True}


@app.post("/webhook/kakao")
async def kakao_webhook(request: Request):
    """카카오톡 챗봇 웹훅"""
    kakao_cfg = config.get("clients", {}).get("kakao", {})
    if not kakao_cfg.get("enabled"):
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "카카오 연동이 비활성화되어 있습니다."}}]}}

    body = await request.json()
    utterance = body.get("userRequest", {}).get("utterance", "")
    user_id = body.get("userRequest", {}).get("user", {}).get("id", "unknown")

    if not utterance:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "messages를 입력해주세요."}}]}}

    session_id = f"kakao_{user_id}"
    session = get_or_create_session(session_id)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, run_agent_turn, session, utterance
    )

    reply = result["response"]
    if len(reply) > 1000:
        reply = reply[:1000] + "\n... (잘림)"

    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": reply}}]
        }
    }


# ─────────────────────────────────────────────
# 내장 스케줄러 (백그라운드 스레드)
# ─────────────────────────────────────────────

import threading


_scheduler_running = True

def _run_scheduled_task(task: dict):
    """예약된 워크플로우를 격리 세션으로 실행"""
    from tools.workflow_tools import mark_workflow_executed, save_workflow_result

    wf_name = task["name"]
    action = task["action"]

    try:
        session_id = f"scheduler_{wf_name}"
        session = get_or_create_session(session_id)

        log_activity("scheduler_run", workflow=wf_name, action=action[:200])
        print(f"⏰ Workflow exec: [{wf_name}] {action[:50]}")

        result = run_agent_turn(session, action)
        response = result.get("response", "특별한 결과 없음")

        # DB에 전체 결과 저장 → notification ID 반환
        notif_id = save_workflow_result(wf_name, action, response)

        print(f"✅ Workflow done: [{wf_name}] -> {response[:80]}")

        # 활성화된 모든 메신저로 알림 전송
        try:
            preview = response.strip().split("\n")[0][:100]
            notify_title = f"⏰ {wf_name} 완료!"
            notify_msg = f"📝 {preview}"

            # 텔레그램: 인라인 "상세 보기" 버튼 포함
            tg_cfg = config.get("clients", {}).get("telegram", {})
            if tg_cfg.get("enabled") and tg_cfg.get("bot_token") and tg_cfg.get("chat_id"):
                from telegram_client import TelegramBot
                bot = TelegramBot(tg_cfg["bot_token"])
                msg_text = f"⏰ *{wf_name}* 완료!\n\n📝 {preview}"
                bot.send_message_with_button(
                    int(tg_cfg["chat_id"]),
                    msg_text,
                    button_text="📄 상세 보기",
                    callback_data=f"wf_result:{notif_id}"
                )

            # Discord / Slack: 버튼 미지원 → 전체 결과 전송
            full_notify = f"{notify_msg}\n\n{response[:1500]}"
            try:
                from discord_client import send_notification as dc_send
                dc_send(full_notify, notify_title)
            except Exception:
                pass
            try:
                from slack_client import send_notification as sl_send
                sl_send(full_notify, notify_title)
            except Exception:
                pass

        except Exception as ne:
            print(f"⚠ Notification failed: [{wf_name}] {ne}")

        if session_id in sessions:
            del sessions[session_id]

    except Exception as e:
        print(f"❌ Workflow failed: [{wf_name}] {e}")

    mark_workflow_executed(wf_name)


def _scheduler_loop():
    """60초마다 실행되는 스케줄러 루프"""
    import time
    from tools.workflow_tools import get_due_workflows, init_workflow_schedules

    # 시작 시 next_run 초기화
    init_workflow_schedules()
    print("📅 Workflow scheduler started (60s interval)")

    while _scheduler_running:
        try:
            due = get_due_workflows()[:3]  # 한 주기 최대 3개
            for task in due:
                if _llm_busy.locked():
                    print(f"⏳ Workflow waiting: [{task['name']}] LLM in use")
                    continue

                with _llm_busy:
                    _run_scheduled_task(task)

        except Exception as e:
            print(f"⚠ Scheduler error: {e}")

        time.sleep(60)


# ─────────────────────────────────────────────
# 텔레그램 callback_query 폴링 (상세보기 버튼)
# ─────────────────────────────────────────────

def _telegram_callback_poller():
    """텔레그램 인라인 버튼 클릭(callback_query)을 폴링으로 처리"""
    import time
    import urllib.request

    tg_cfg = config.get("clients", {}).get("telegram", {})
    if not tg_cfg.get("enabled") or not tg_cfg.get("bot_token"):
        print("📱 Telegram callback poller: disabled (not configured)")
        return

    bot_token = tg_cfg["bot_token"]
    offset = 0
    print("📱 Telegram callback poller started (10s interval)")

    while _scheduler_running:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getUpdates?offset={offset}&timeout=5&allowed_updates=%5B%22callback_query%22%5D"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                callback = update.get("callback_query")
                if not callback:
                    continue

                cb_data = callback.get("data", "")
                cb_chat_id = callback.get("message", {}).get("chat", {}).get("id")

                # answerCallbackQuery (버튼 로딩 해제)
                try:
                    answer_url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
                    payload = json.dumps({"callback_query_id": callback["id"]}).encode()
                    urllib.request.urlopen(urllib.request.Request(
                        answer_url, data=payload,
                        headers={"Content-Type": "application/json"}
                    ), timeout=5)
                except Exception:
                    pass

                if cb_data.startswith("wf_result:") and cb_chat_id:
                    try:
                        notif_id = int(cb_data.split(":")[1])
                        from tools.workflow_tools import _get_db
                        db = _get_db()
                        row = db.execute(
                            "SELECT wf_name, result, created FROM wf_notifications WHERE id = ?",
                            (notif_id,)
                        ).fetchone()

                        from telegram_client import TelegramBot
                        bot = TelegramBot(bot_token)

                        if row:
                            result_text = row["result"] or "(결과 없음)"
                            # 텔레그램 메시지 4096자 제한
                            if len(result_text) > 3500:
                                result_text = result_text[:3500] + "\n... (잘림)"
                            header = f"📋 *{row['wf_name']}*\n🕐 {row['created']}\n\n"
                            bot.send_message(cb_chat_id, header + result_text)
                            db.execute("UPDATE wf_notifications SET read = 1 WHERE id = ?", (notif_id,))
                            db.commit()
                        else:
                            bot.send_message(cb_chat_id, "⚠ 결과를 찾을 수 없습니다.")
                        db.close()
                    except Exception as e:
                        print(f"⚠ Telegram callback error: {e}")

        except Exception as e:
            if "timed out" not in str(e).lower():
                print(f"⚠ Telegram poller error: {e}")

        time.sleep(5)


# ─────────────────────────────────────────────
# 서버 시작
# ─────────────────────────────────────────────

if __name__ == "__main__":
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 3000)

    print(f"🚀 Xoul API server started")
    print(f"   URL: http://{host}:{port}")
    print(f"   LLM: {llm_client.get_provider_info()}")
    print(f"   API Key: {API_KEY[:8]}...")
    print(f"   Docs: http://{host}:{port}/docs")
    print()

    # .xoul 디렉토리 구조 초기화
    for d in ["/root/.xoul/workspace", "/root/.xoul/tmp"]:
        os.makedirs(d, exist_ok=True)

    # Orphan 코드 프로세스 정리 (이전 세션 잔존 프로세스 kill)
    def _cleanup_orphan_codes():
        try:
            rc_path = "/root/.xoul/running_codes.json"
            if os.path.isfile(rc_path):
                with open(rc_path, "r") as f:
                    entries = json.load(f)
                for e in entries:
                    pid = e.get("pid")
                    if pid:
                        try:
                            os.kill(pid, 9)
                            print(f"  🧹 Killed orphan code PID {pid} ({e.get('name', '?')})")
                        except (ProcessLookupError, OSError):
                            pass
                # 파일 초기화
                with open(rc_path, "w") as f:
                    f.write("[]")
                print("  🧹 Orphan code cleanup done")
        except Exception as e:
            print(f"  ⚠️ Orphan cleanup error: {e}")
    _cleanup_orphan_codes()

    # 모델 Warm-up (백그라운드 스레드로 LLM + Embed 모델 로딩)
    def _warmup_models():
        import urllib.request
        llm_cfg = config.get("llm", {})
        provider = llm_cfg.get("provider", "local")
        p = llm_cfg.get("providers", {}).get(provider, {})
        base_url = p.get("base_url", "http://10.0.2.2:11434/v1")
        api_key = p.get("api_key", "none")
        model = p.get("model_name", "") or llm_cfg.get("ollama_model", "")

        # 1) LLM warm-up
        try:
            data = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }).encode()
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=data,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            )
            urllib.request.urlopen(req, timeout=60)
            print(f"  🔥 LLM warm-up done ({model})")
        except Exception as e:
            print(f"  ⚠️ LLM warm-up failed: {e}")

        # 2) Embed model warm-up
        try:
            data = json.dumps({"model": "bge-m3", "prompt": "warmup", "options": {"num_gpu": 0}}).encode()
            req = urllib.request.Request(
                "http://10.0.2.2:11434/api/embeddings",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=60)
            print(f"  🔥 Embed warm-up done (bge-m3)")
        except Exception as e:
            print(f"  ⚠️ Embed warm-up failed: {e}")

    warmup_thread = threading.Thread(target=_warmup_models, daemon=True)
    warmup_thread.start()

    # 스케줄러 백그라운드 스레드 시작
    scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    scheduler_thread.start()

    # 텔레그램 콜백 폴러 — telegram_client.py의 run_polling에서 통합 처리
    # (server.py와 telegram_client.py가 동시에 getUpdates 호출하면 409 Conflict 발생)
    # tg_poller_thread = threading.Thread(target=_telegram_callback_poller, daemon=True)
    # tg_poller_thread.start()

    uvicorn.run(app, host=host, port=port, log_level="info")
