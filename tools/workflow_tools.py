"""
Workflow 시스템 — 복합 작업 흐름 관리

사용자가 대화 중 수행한 작업들을 Workflow로 저장하고,
나중에 이름으로 실행하거나 수정/삭제할 수 있습니다.

저장소: /root/.xoul/workflows.db (독립 DB, 임베딩 포함)

Workflow = 저장된 프롬프트/코드 스텝 + 메타데이터 + 임베딩
실행 시 프롬프트는 LLM에게, 코드는 python_run_code로 실행.

스텝 포맷:
  - prompt: {"type": "prompt", "content": "..."}
  - code:   {"type": "code",   "content": "def run(arg1, arg2): ..."}
  - 하위호환: 문자열 "text" → {"type": "prompt", "content": "text"}
"""

import json
import ast
import sqlite3
from datetime import datetime
from .system_tools import tool_run_command
from i18n import t as _t, get_language as _get_lang


# ─────────────────────────────────────────────
# DB 헬퍼 (독립 workflows.db)
# ─────────────────────────────────────────────

WF_DB_PATH = "/root/.xoul/workflows.db"
_wf_db_conn = None


def _resolve_i18n(val):
    """i18n dict {"ko": "...", "en": "..."} → 현재 언어 문자열로 resolve"""
    if isinstance(val, dict) and ("ko" in val or "en" in val):
        lang = _get_lang()
        return val.get(lang, val.get("en", val.get("ko", str(val))))
    return val if isinstance(val, str) else str(val) if val else ""


def _get_db():
    """workflows.db 연결 + 테이블 보장 + memory.db에서 자동 마이그레이션"""
    global _wf_db_conn
    # 닫힌 연결 감지
    if _wf_db_conn is not None:
        try:
            _wf_db_conn.execute("SELECT 1")
        except Exception as e:
            print(f"[WF_DB] Connection dead: {e}", flush=True)
            _wf_db_conn = None
    if _wf_db_conn is None:
        import os
        print(f"[WF_DB] Creating NEW connection to {WF_DB_PATH} (exists={os.path.exists(WF_DB_PATH)})", flush=True)
        tool_run_command("mkdir -p /root/.xoul")
        _wf_db_conn = sqlite3.connect(WF_DB_PATH, check_same_thread=False, timeout=10)
        _wf_db_conn.row_factory = sqlite3.Row
        _wf_db_conn.execute("PRAGMA journal_mode=WAL")

        _wf_db_conn.execute("""
            CREATE TABLE IF NOT EXISTS workflows (
                name        TEXT PRIMARY KEY,
                description TEXT DEFAULT '',
                prompts     TEXT DEFAULT '[]',
                hint_tools  TEXT DEFAULT '[]',
                schedule    TEXT DEFAULT '',
                embedding   BLOB,
                run_count   INTEGER DEFAULT 0,
                last_run    TEXT,
                next_run    TEXT,
                created     TEXT
            )
        """)

        # notifications 테이블
        _wf_db_conn.execute("""
            CREATE TABLE IF NOT EXISTS wf_notifications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                wf_name     TEXT,
                action      TEXT,
                result      TEXT,
                created     TEXT,
                read        INTEGER DEFAULT 0
            )
        """)
        _wf_db_conn.commit()
        cnt = _wf_db_conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
        print(f"[WF_DB] New connection ready, {cnt} workflows in DB", flush=True)

        # ── memory.db → workflows.db 자동 마이그레이션 ──
        _migrate_from_memory_db()

    return _wf_db_conn


def _migrate_from_memory_db():
    """memory.db에 있던 workflows/wf_notifications를 workflows.db로 이전"""
    import os
    mem_db_path = "/root/.xoul/memory.db"
    if not os.path.exists(mem_db_path):
        return
    try:
        mem_conn = sqlite3.connect(mem_db_path, timeout=5)
        mem_conn.row_factory = sqlite3.Row
        # workflows 테이블이 있는지 확인
        tables = [r[0] for r in mem_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('workflows', 'wf_notifications')"
        ).fetchall()]
        if "workflows" not in tables:
            mem_conn.close()
            return

        # 데이터 복사 (이미 workflows.db에 있으면 스킵)
        rows = mem_conn.execute("SELECT * FROM workflows").fetchall()
        migrated = 0
        for row in rows:
            try:
                _wf_db_conn.execute("""
                    INSERT OR IGNORE INTO workflows
                    (name, description, prompts, hint_tools, schedule, embedding, run_count, last_run, next_run, created)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (row["name"], row["description"], row["prompts"],
                      row["hint_tools"], row["schedule"], row["embedding"],
                      row["run_count"], row["last_run"],
                      row["next_run"] if "next_run" in row.keys() else None,
                      row["created"]))
                migrated += 1
            except Exception:
                pass

        # wf_notifications도 이전
        if "wf_notifications" in tables:
            notifs = mem_conn.execute("SELECT * FROM wf_notifications").fetchall()
            for n in notifs:
                try:
                    _wf_db_conn.execute("""
                        INSERT OR IGNORE INTO wf_notifications (wf_name, action, result, created, read)
                        VALUES (?, ?, ?, ?, ?)
                    """, (n["wf_name"], n["action"], n["result"], n["created"], n["read"]))
                except Exception:
                    pass

        _wf_db_conn.commit()

        # memory.db에서 원본 테이블 삭제
        if migrated > 0:
            mem_conn.execute("DROP TABLE IF EXISTS workflows")
            mem_conn.execute("DROP TABLE IF EXISTS wf_notifications")
            mem_conn.commit()
            print(f"[workflow] {_t('workflow.migration_done', migrated=migrated)}", flush=True)

        mem_conn.close()
    except Exception as e:
        print(f"[workflow] migration error: {e}", flush=True)


def _get_embed(text: str):
    """임베딩 생성 (memory_tools 재사용)"""
    from .memory_tools import _get_embedding
    return _get_embedding(text)


def _cosine_sim(a, b) -> float:
    """코사인 유사도 (memory_tools 재사용)"""
    from .memory_tools import _cosine_similarity
    return _cosine_similarity(a, b)


def _fill_missing_embeddings_async():
    """NULL embedding이 있는 workflows/codes/personas를 비동기로 채움"""
    import threading
    def _fill():
        try:
            db = _get_db()
            # workflows
            rows = db.execute("SELECT name, description FROM workflows WHERE embedding IS NULL").fetchall()
            for r in rows:
                text = f"{r['name']} {r['description']}" if r['description'] else r['name']
                emb = _get_embed(text)
                if emb:
                    db.execute("UPDATE workflows SET embedding = ? WHERE name = ?", (emb, r['name']))
            # codes (embedding 컬럼 없으면 추가)
            try:
                db.execute("SELECT embedding FROM codes LIMIT 1")
            except Exception:
                try:
                    db.execute("ALTER TABLE codes ADD COLUMN embedding BLOB")
                    db.commit()
                except Exception:
                    pass
            try:
                rows = db.execute("SELECT name, description FROM codes WHERE embedding IS NULL").fetchall()
                for r in rows:
                    text = f"{r['name']} {r['description']}" if r['description'] else r['name']
                    emb = _get_embed(text)
                    if emb:
                        db.execute("UPDATE codes SET embedding = ? WHERE name = ?", (emb, r['name']))
            except Exception:
                pass
            # personas
            try:
                rows = db.execute("SELECT name FROM personas WHERE embedding IS NULL").fetchall()
                for r in rows:
                    emb = _get_embed(r['name'])
                    if emb:
                        db.execute("UPDATE personas SET embedding = ? WHERE name = ?", (emb, r['name']))
            except Exception:
                pass
            db.commit()
            print(f"[EMBED-FILL] Done", flush=True)
        except Exception as e:
            print(f"[EMBED-FILL] Error: {e}", flush=True)
    threading.Thread(target=_fill, daemon=True).start()


def _lenient_extract_steps(raw: str):
    """
    JSON 파싱 실패 시 regex로 스텝 추출.
    코드 스텝의 code_name을 찾아 run_stored_code 참조로 변환.
    prompt 스텝의 content를 찾아 추출.
    성공하면 list, 아무것도 못 찾으면 None 반환.
    """
    import re
    steps = []

    # code_name이 있는 코드 스텝 추출
    for m in re.finditer(r'"code_name"\s*:\s*"([^"]+)"', raw):
        code_name = m.group(1)
        steps.append({
            "_pos": m.start(),
            "type": "prompt",
            "content": f'run_stored_code(name="{code_name}")',
        })

    # prompt 스텝의 content 추출 (type이 prompt인 것)
    # "type": "prompt", "content": "..." 패턴 찾기
    for m in re.finditer(r'"type"\s*:\s*"prompt"\s*,\s*"content"\s*:\s*"((?:[^"\\]|\\.)*)"', raw):
        content = m.group(1).replace('\\"', '"').replace('\\n', '\n')
        steps.append({
            "_pos": m.start(),
            "type": "prompt",
            "content": content,
        })

    if not steps:
        return None

    # 원본 텍스트에서의 위치 순서로 정렬
    steps.sort(key=lambda s: s["_pos"])
    for s in steps:
        del s["_pos"]
    return steps


def _normalize_steps(raw) -> list:
    """
    prompts 필드를 정규화된 스텝 리스트로 변환.
    하위호환: ["text1", "text2"] → [{"type":"prompt","content":"text1"}, ...]
    새 포맷:  [{"type":"prompt","content":...}, {"type":"code","content":...}]
    이중 래핑 방지: content가 JSON 배열이면 풀어서 flat으로 병합
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return [{"type": "prompt", "content": raw}]
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if isinstance(item, str):
            # 문자열이 JSON 배열인지 확인 (이중 래핑 방지)
            try:
                inner = json.loads(item)
                if isinstance(inner, list) and len(inner) > 0 and isinstance(inner[0], dict):
                    result.extend(_normalize_steps(inner))
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
            result.append({"type": "prompt", "content": item})
        elif isinstance(item, dict) and "type" in item and "content" in item:
            # content가 JSON 배열 문자열인지 확인 (이중 래핑 방지)
            content = item.get("content", "")
            if isinstance(content, str) and content.strip().startswith("["):
                try:
                    inner = json.loads(content)
                    if isinstance(inner, list) and len(inner) > 0 and isinstance(inner[0], dict):
                        result.extend(_normalize_steps(inner))
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(item)
        else:
            result.append({"type": "prompt", "content": str(item)})
    return result


def _auto_install_imports(code_str: str) -> str:
    """
    코드에서 import 문을 파싱하여 없는 패키지를 자동 설치하는
    래퍼 코드를 생성. 실행 전 호출.
    반환값: 자동 설치 코드 + 원본 코드를 합친 전체 실행 코드.
    """
    # 표준 라이브러리 (설치 불필요)
    stdlib = {
        'os', 'sys', 'json', 'math', 're', 'datetime', 'time', 'random',
        'collections', 'itertools', 'functools', 'pathlib', 'io', 'csv',
        'hashlib', 'base64', 'urllib', 'http', 'email', 'html', 'xml',
        'sqlite3', 'subprocess', 'threading', 'multiprocessing', 'socket',
        'ssl', 'typing', 'abc', 'copy', 'string', 'struct', 'textwrap',
        'shutil', 'tempfile', 'glob', 'fnmatch', 'stat', 'gzip', 'zipfile',
        'tarfile', 'logging', 'unittest', 'pprint', 'argparse', 'configparser',
        'uuid', 'decimal', 'fractions', 'statistics', 'enum', 'dataclasses',
        'contextlib', 'weakref', 'operator', 'heapq', 'bisect', 'array',
        'queue', 'sched', 'calendar', 'locale', 'gettext', 'platform',
        'ctypes', 'importlib', 'pkgutil', 'inspect', 'dis', 'traceback',
        'warnings', 'atexit', 'signal', 'mmap', 'ast', 'token', 'tokenize',
        'codecs', 'unicodedata', 'difflib', 'pickle', 'shelve', 'dbm',
    }
    try:
        tree = ast.parse(code_str)
    except SyntaxError:
        return code_str  # 파싱 불가 → 그대로 실행

    packages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                packages.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                packages.add(node.module.split('.')[0])

    packages -= stdlib  # 표준 라이브러리 제외
    if not packages:
        return code_str

    install_code = "import importlib, subprocess\n"
    for pkg in sorted(packages):
        install_code += (
            f"try:\n"
            f"    importlib.import_module('{pkg}')\n"
            f"except ImportError:\n"
            f"    subprocess.check_call(['pip', 'install', '-q', '{pkg}'])\n"
        )
    return install_code + "\n" + code_str


# ─────────────────────────────────────────────
# 스케줄 정규화 (한국어 → 영어)
# ─────────────────────────────────────────────

def _normalize_schedule(raw: str) -> str:
    """스케줄 표현을 파서가 이해하는 영어 형식으로 변환 (LLM 활용)"""
    import re
    if not raw or not raw.strip():
        return ""
    s = raw.strip()

    # 이미 올바른 영어 형식이면 그대로 반환
    if re.match(r'^(daily|weekday)\s+\d{1,2}:\d{2}$', s, re.I) or \
       re.match(r'^every\s+\d+min$', s, re.I) or \
       re.match(r'^(weekly|monthly)$', s, re.I) or \
       re.match(r'^\d{1,2}:\d{2}$', s):
        return s

    # LLM으로 변환
    try:
        import os
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        if not os.path.isfile(cfg_path):
            cfg_path = os.path.expanduser("~/.xoul/config.json")
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.loads(f.read())
        from llm_client import LLMClient
        llm = LLMClient(cfg)
        resp = llm.chat(messages=[
            {"role": "user", "content": f"Convert to schedule format. Reply ONLY with the result, no explanation.\nAllowed formats: 'daily HH:MM', 'weekday HH:MM', 'every Nmin', 'weekly', 'monthly'\nInput: {s}"}
        ])
        converted = (resp.get("content") or "").strip().strip('"').strip("'")
        if re.match(r'^(daily|weekday)\s+\d{1,2}:\d{2}$', converted, re.I) or \
           re.match(r'^every\s+\d+min$', converted, re.I) or \
           re.match(r'^(weekly|monthly)$', converted, re.I):
            print(f"[SCHEDULE-LLM] '{s}' → '{converted}'", flush=True)
            return converted
    except Exception:
        pass

    return s


# ─────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────

def _brief_workflow_list() -> str:
    """생성/수정 후 LLM에게 list_workflows 호출을 유도하는 힌트"""
    return "\n\n[중요: 사용자에게 최신 워크플로우 목록을 보여주기 위해 반드시 list_workflows를 호출하세요.]"


def _step_summary(step) -> str:
    """스텝을 간결한 한 줄 요약으로 변환 (코드 전체 포함 안 함)"""
    if isinstance(step, dict):
        stype = step.get('type', 'prompt')
        content = _resolve_i18n(step.get('content', ''))
        if stype == 'code':
            code_name = step.get('code_name', '')
            if code_name:
                return f'🐍 {code_name}'
            first_line = content.split('\n')[0][:60] if content else _t('workflow.empty_code')
            return f'🐍 {first_line}'
        else:
            # run_stored_code 패턴 감지
            code_ref = _extract_code_ref(content)
            if code_ref:
                return f'🐍 {code_ref}'
            return f'💬 {content[:80]}' if len(content) > 80 else f'💬 {content}'
    return str(step)[:80]


def _extract_code_ref(text: str):
    """코드 참조 패턴에서 코드 이름 추출. 없으면 None."""
    import re
    # run_stored_code(name="...")
    m = re.search(r'run_stored_code\s*\(\s*name\s*=\s*["\']([^"\']+)["\']', text)
    if m:
        return m.group(1)
    # Run code "...", Execute code "...", 코드 실행 "..."
    m = re.search(r'(?:Run|Execute|실행)\s+(?:code|코드)\s*[:\s]*["\u201c]([^"\u201d]+)["\u201d]', text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def create_workflow(name: str, prompts: str, description: str = "",
                    hint_tools: str = "", schedule: str = "") -> str:
    """
    Workflow를 생성합니다.

    Args:
        name: Workflow 이름 (예: "아침 브리핑")
        prompts: 실행할 프롬프트들. 줄바꿈(\\n)으로 구분.
        description: Workflow 설명
        hint_tools: 힌트 도구 목록 (쉼표 구분)
        schedule: 예약 실행 스케줄. 아래 정확한 형식만 허용됩니다. 이 외의 형식은 무시됩니다.
            형식 (대소문자 구분 없음):
              - "daily HH:MM"   → 매일 지정 시각. 예: "daily 08:00", "daily 14:30"
              - "weekday HH:MM" → 평일(월~금)만. 예: "weekday 09:00"
              - "every Nmin"    → N분 간격 반복 (최소 5분). 예: "every 10min", "every 30min"
              - "weekly"        → 매주 월요일 09:00
              - "monthly"       → 매월 1일 09:00
              - "HH:MM"         → daily HH:MM와 동일. 예: "08:00"
            변환 규칙 (사용자가 자연어로 말한 경우 반드시 변환):
              - "매일 아침 8시" → "daily 08:00"
              - "평일 오전 9시" → "weekday 09:00"
              - "10분마다" / "10분 간격" → "every 10min"
              - "매주" / "주마다" → "weekly"
              - "매월" / "월마다" → "monthly"
              - "오후 3시 30분" → "daily 15:30"
              - 시간에 "오후"가 포함되면 12를 더하세요 (오후 1시=13:00)
            ⚠ 절대 한국어 그대로 넣지 마세요. 반드시 위 영어 형식으로 변환 후 전달.
    """
    if not name or not name.strip():
        return _t("workflow.name_required")
    if not prompts or not prompts.strip():
        return _t("workflow.prompts_required")

    name = name.strip()
    schedule = _normalize_schedule(schedule)
    db = _get_db()

    # 중복 확인
    existing = db.execute("SELECT name FROM workflows WHERE name = ?", (name,)).fetchone()
    if existing:
        return _t("workflow.already_exists", name=name)

    # 파싱 — 새 포맷(JSON 배열)과 기존 포맷(\n 구분 텍스트) 모두 지원
    try:
        parsed = json.loads(prompts)
        if isinstance(parsed, list):
            prompt_list = _normalize_steps(parsed)
        else:
            prompt_list = [p.strip() for p in prompts.split("\n") if p.strip()]
            prompt_list = _normalize_steps(prompt_list)
    except (json.JSONDecodeError, TypeError):
        prompt_list = [p.strip() for p in prompts.split("\n") if p.strip()]
        prompt_list = _normalize_steps(prompt_list)
    if not prompt_list:
        return _t("workflow.no_valid_prompts")

    tools_list = [t.strip() for t in hint_tools.split(",") if t.strip()] if hint_tools else []

    # 임베딩 생성 (이름 + 설명) — 실패해도 워크플로우는 생성
    embed_text = f"{name} {description}" if description else name
    try:
        embedding = _get_embed(embed_text)
    except Exception:
        embedding = None

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    db.execute("""
        INSERT INTO workflows (name, description, prompts, hint_tools, schedule, embedding, run_count, created)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
    """, (
        name,
        description or f"{name} workflow",
        json.dumps(prompt_list, ensure_ascii=False),
        json.dumps(tools_list, ensure_ascii=False),
        schedule,
        embedding,
        now
    ))
    db.commit()
    _fill_missing_embeddings_async()

    steps = "\n".join(f"  {i+1}. {_step_summary(p)}" for i, p in enumerate(prompt_list))
    result = _t("workflow.created", name=name, steps=steps)
    if schedule:
        result += _t("workflow.scheduled", schedule=schedule)
    result += _brief_workflow_list()
    result += "\n[이 결과를 사용자에게 간결하게 전달하세요. 각 스텝을 다시 설명하지 마세요.]"
    return result


def run_workflow(name: str, **kwargs) -> str:
    """
    저장된 Workflow를 실행합니다.
    1) Space 제거 정확 매칭
    2) 부분 매칭 (LIKE)
    3) Embedding 유사도 → 후보 최대 3개 표시 → 사용자 선택

    Args:
        name: 실행할 Workflow 이름

    Returns:
        실행할 프롬프트 (LLM이 이어서 처리) 또는 후보 목록
    """
    global _pending_wf_chunks, _last_wf_run_time

    # Re-entry guard: 이미 워크플로우 실행 중이면 재호출 방지
    if _pending_wf_chunks is not None:
        return (
            "⚠ STOP: A workflow is already in progress. "
            "Do NOT call run_workflow again. "
            "Execute the steps from the previous instructions directly."
        )

    db = _get_db()

    # 1) Space 제거 정확 매칭
    all_rows = db.execute("SELECT name FROM workflows").fetchall()
    stripped_input = name.replace(" ", "").replace("워크플로우", "").strip()
    for r in all_rows:
        if r["name"].replace(" ", "") == stripped_input:
            row = db.execute("SELECT * FROM workflows WHERE name = ?", (r["name"],)).fetchone()
            return _execute_workflow(db, row)

    # 정확 매칭 (원본 이름)
    row = db.execute("SELECT * FROM workflows WHERE name = ?", (name,)).fetchone()
    if row:
        return _execute_workflow(db, row)

    # "워크플로우" 접미사 제거 후 재시도
    stripped = name.replace(" 워크플로우", "").replace("워크플로우", "").strip()
    if stripped and stripped != name:
        row = db.execute("SELECT * FROM workflows WHERE name = ?", (stripped,)).fetchone()
        if row:
            return _execute_workflow(db, row)

    # 2) 부분 매칭 (LIKE)
    search_term = stripped or name
    row = db.execute(
        "SELECT * FROM workflows WHERE name LIKE ? OR ? LIKE '%' || name || '%'",
        (f"%{search_term}%", search_term)
    ).fetchone()
    if row:
        return _execute_workflow(db, row)

    # 3) Embedding 유사도 → 후보 최대 3개 표시
    candidates = _fuzzy_find_workflow(name)
    if candidates:
        top_name, top_score = candidates[0]
        if top_score >= 0.75:
            # 유사도가 충분히 높으면 자동 실행
            row = db.execute("SELECT * FROM workflows WHERE name = ?", (top_name,)).fetchone()
            if row:
                return _execute_workflow(db, row)

        # 후보 목록 표시 → 사용자가 번호로 선택
        lines = [_t("workflow.similar_found", name=name)]
        for i, (cname, score) in enumerate(candidates[:3], 1):
            lines.append(_t("workflow.similarity", i=i, name=cname, score=f"{score:.0%}"))
        lines.append(_t("workflow.select_number"))
        return "\n".join(lines)

    return _t("workflow.not_found_hint", name=name)


# ── ask_user: 사용자에게 질문/입력을 받는 툴 ──

def ask_user(question: str, fields: str = "") -> str:
    """사용자에게 질문을 표시하고 답변을 기다립니다.
    워크플로우 실행 중 사용자 입력이 필요할 때 사용합니다.

    Args:
        question: 사용자에게 보여줄 질문 (팝업 타이틀로 표시)
        fields: 쉼표 구분 입력 필드명 (예: 'bedtime,waketime,quality'). 비어있으면 단일 답변 필드.

    Returns:
        __NEEDS_INPUT__ 신호 → 데스크톱 팝업/채팅 입력 대기
    """
    params = []
    if fields:
        for f in fields.split(","):
            f = f.strip()
            if f:
                params.append({"name": f, "desc": f})
    else:
        params = [{"name": "answer", "desc": question}]

    _meta = json.dumps({"code_name": f"💬 {question}", "params": params}, ensure_ascii=False)
    return (
        f"__NEEDS_INPUT__{_meta}__END_NEEDS_INPUT__\n"
        f"⏸ 사용자 입력 대기 중..."
    )


# 모듈 레벨: 코드 기준 분할 후 남은 chunks (server.py에서 읽어감)
_pending_wf_chunks = None
_last_wf_run_time = 0  # re-entry guard: 마지막 실행 시각


def _chunk_by_code(steps: list) -> list:
    """각 스텝을 개별 chunk로 분할 (1 step = 1 phase).

    4B 소형 모델에서 정확도를 높이기 위해 한 번에 1개 스텝만 실행.

    예: [P, P, C, P] → [[P], [P], [C], [P]]
    """
    return [[step] for step in steps]


def _format_chunk(steps: list, chunk_num: int, total_chunks: int,
                  wf_name: str, step_offset: int) -> str:
    """chunk 내 스텝을 지시문 텍스트로 포맷 (1 step per chunk)"""
    step_lines = []
    for i, step in enumerate(steps):
        step_lines.append(_format_step(step, step_offset + i))

    return (
        "Generate answer for below Users's Instruction. Use previous information if it is retrieved previously(in above context) and do not call tools to get same information which is already stated in past. Focus on only current step.\n"
        "\n⚠ WORKFLOW TOOL RULES:\n"
        "- To send messages/notifications, use send_notification (API-based). Do NOT use host_open_app to open messenger apps (KakaoTalk, Telegram, Discord, Slack, etc.).\n"
        "- host_open_app is ONLY for launching desktop productivity apps (e.g., Chrome, Excel, VSCode) when explicitly requested by a workflow step.\n"
        "\n" + "\n".join(step_lines)
    )


def _execute_workflow(db, row) -> str:
    """Workflow 실행 — 코드 기준으로 chunk 분할 후 첫 chunk 반환.

    코드가 없으면 전체를 한 번에 실행.
    코드가 있으면 코드까지 한 chunk, 나머지는 chat_stream에서 순차 실행.
    """
    global _pending_wf_chunks, _last_wf_run_time
    _pending_wf_chunks = None
    _last_wf_run_time = __import__('time').time()  # re-entry cooldown

    raw_prompts = json.loads(row["prompts"])
    steps = _normalize_steps(raw_prompts)
    if not steps:
        return _t("workflow.no_prompts", name=row['name'])

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.execute(
        "UPDATE workflows SET run_count = run_count + 1, last_run = ? WHERE name = ?",
        (now, row["name"])
    )
    db.commit()

    chunks = _chunk_by_code(steps)
    total_chunks = len(chunks)

    # 모든 chunks를 서버에서 순차 실행 (LLM이 해석할 필요 없음)
    step_offset = 1
    all_remaining = []
    for i, chunk_steps in enumerate(chunks, 1):
        step_types = set()
        for s in chunk_steps:
            st = s.get("type", "prompt")
            if st == "code" or s.get("code_name"):
                step_types.add("code")
            else:
                step_types.add("prompt")
        all_remaining.append({
            "steps": chunk_steps,
            "chunk_num": i,
            "step_offset": step_offset,
            "step_types": list(step_types),
        })
        step_offset += len(chunk_steps)

    _pending_wf_chunks = {
        "wf_name": row["name"],
        "total_chunks": total_chunks,
        "remaining": all_remaining,
    }

    step_list = "\n".join(
        f"  {i+1}. {_step_summary(s)}" for i, s in enumerate(steps)
    )
    return (
        f"## ⚡ '{row['name']}' Workflow execution is in progress.\nEach step will be executed step by step.\n"
        # + step_list
        # + f"\n\n총 **{total_chunks}단계** 순서대로 실행합니다."
    )


def _format_step(step: dict, step_num: int) -> str:
    """단일 워크플로우 스텝을 지시문 텍스트로 변환"""
    stype = step.get("type", "prompt")
    content = _resolve_i18n(step.get("content", ""))

    # code_name 참조: run_stored_code로 실행 지시
    if stype == "code" and step.get("code_name"):
        code_name = step["code_name"]
        step_args = step.get("args", {})

        # DB에서 코드의 실제 파라미터 목록 조회
        _param_info = ""
        try:
            import sqlite3 as _sq3, os as _os
            _db = _os.path.expanduser("~/.xoul/workflows.db")
            if _os.path.isfile(_db):
                _conn = _sq3.connect(_db)
                _conn.row_factory = _sq3.Row
                _row = _conn.execute("SELECT params FROM codes WHERE name = ?", (code_name,)).fetchone()
                if _row and _row["params"]:
                    _params = json.loads(_row["params"])
                    if isinstance(_params, list) and _params:
                        _pdesc = []
                        for p in _params:
                            if not isinstance(p, dict): continue
                            pn = p.get("name", "")
                            if "default" in p:
                                _pdesc.append(f"{pn} (optional, default={p['default']!r})")
                            else:
                                _pdesc.append(f"{pn} (REQUIRED)")
                        _param_info = f"\n     📋 This code ONLY accepts: {_pdesc}. Do NOT pass any other parameters. Use defaults when user hasn't specified."
                _conn.close()
        except Exception:
            pass

        if step_args:
            args_json = json.dumps(step_args, ensure_ascii=False)
            return (
                f"  {step_num}. [🐍 CODE] Call run_stored_code with EXACTLY these parameters:\n"
                f"     - name: \"{code_name}\"\n"
                f"     - params: '{args_json}'\n"
                f"     - timeout: \"30\"\n"
                f"     ⚠ CRITICAL: You MUST pass params EXACTLY as shown above. Do NOT use empty params {{}}.{_param_info}\n"
                f"     ⚠ Do NOT write code manually. Use run_stored_code tool only."
            )
        return (
            f"  {step_num}. [🐍 CODE] Call run_stored_code(name=\"{code_name}\").{_param_info}\n"
            f"     ⚠ You MUST use run_stored_code tool. Do NOT write code manually.\n"
            f"     ⚠ ALWAYS call the tool even if you don't have required parameters. The tool will handle missing params automatically."
        )

    if stype == "code":
        wrapped_code = _auto_install_imports(content)
        return (
            f"  {step_num}. [🐍 CODE] Execute the Python code below using run_python_code.\n"
            f"     ```python\n{wrapped_code}\n"
            f"     # Call the function (determine args from context above)\n"
            f"     result = run(...)\n"
            f"     print(result)\n"
            f"     ```"
        )
    else:
        _ask_hint = ""
        _lower = content.lower()
        if any(kw in _lower for kw in ["물어", "입력", "ask", "질문", "확인해", "알려줘", "선택"]):
            _ask_hint = "\n     💡 If this step requires user input/answer, use ask_user tool to get their response."
        return f"  {step_num}. [💬 PROMPT] {content}{_ask_hint}"




def _fuzzy_find_workflow(query: str) -> list:
    """
    Embedding 유사도로 Workflow 검색.
    저장된 임베딩 사용 (빠름).
    threshold 이상인 후보를 (이름, 점수) 리스트로 반환.
    """
    THRESHOLD = 0.6

    query_embed = _get_embed(query)
    if not query_embed:
        return []

    db = _get_db()
    rows = db.execute("SELECT name, embedding FROM workflows WHERE embedding IS NOT NULL").fetchall()

    candidates = []
    for row in rows:
        if row["embedding"]:
            try:
                sim = _cosine_sim(query_embed, row["embedding"])
                if sim >= THRESHOLD:
                    candidates.append((row["name"], sim))
            except Exception:
                continue

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:3]


def list_workflows(page: str = "1", per_page: str = "10") -> str:
    """
    저장된 Workflow 목록을 조회합니다.

    Args:
        page: 페이지 번호 (기본 1)
        per_page: 페이지당 항목 수 (기본 10)

    Returns:
        Workflow 목록 (마크다운 표)
    """
    db = _get_db()

    total = db.execute("SELECT COUNT(*) as cnt FROM workflows").fetchone()["cnt"]
    print(f"[WF_DB] list_workflows: total={total}, db={WF_DB_PATH}, id={id(db)}", flush=True)
    if total == 0:
        return "📭 저장된 Workflow가 없습니다."

    pg = max(1, int(page))
    pp = max(1, min(50, int(per_page)))
    offset = (pg - 1) * pp
    total_pages = (total + pp - 1) // pp

    rows = db.execute(
        "SELECT name, description, prompts, schedule, run_count FROM workflows ORDER BY created DESC LIMIT ? OFFSET ?",
        (pp, offset)
    ).fetchall()

    if not rows:
        return _t("workflow.empty_list")

    # 마크다운 표
    lines = [_t("workflow.list_header", total=total, pg=pg, total_pages=total_pages)]
    lines.append("")
    lines.append(f"| # | {_t('workflow.th_name')} | {_t('workflow.th_steps')} | {_t('workflow.th_runs')} | {_t('workflow.th_schedule')} | {_t('workflow.th_desc')} |")
    lines.append("|---|------|-------|------|--------|------|")

    page_items = []
    for i, row in enumerate(rows, offset + 1):
        raw_prompts = json.loads(row["prompts"])
        steps = _normalize_steps(raw_prompts)
        sch = row["schedule"] if row["schedule"] else "-"
        desc = (row["description"] or "")[:30]
        code_count = sum(1 for s in steps if s.get("type") == "code")
        step_info = f"{len(steps)}" + (f" (🐍{code_count})" if code_count else "")
        lines.append(
            f"| {i} | {row['name']} | {step_info} | {_t('workflow.run_count', count=row['run_count'])} | {sch} | {desc} |"
        )
        page_items.append({
            "name": row["name"],
            "steps": len(steps),
            "code_steps": code_count,
            "runs": row["run_count"],
            "schedule": row["schedule"] or "",
            "description": desc,
        })

    # 페이지 정보 (텍스트 네비게이션은 LLM이 자동 호출하므로 제거, UI 버튼만 사용)
    if total_pages > 1:
        lines.append(_t("workflow.page_info", pg=pg, total_pages=total_pages, total=total))

    # Desktop UI용 base64 데이터 (페이지 메타 포함)
    import base64
    wf_payload = {
        "_meta": {"page": pg, "total_pages": total_pages, "total": total},
        "items": page_items
    }
    data_json = json.dumps(wf_payload, ensure_ascii=False)
    data_b64 = base64.b64encode(data_json.encode()).decode()
    lines.append(f"\n<!--WFDATA:{data_b64}-->")
    lines.append("\n[이 결과를 사용자에게 그대로 전달하세요. 추가 도구 호출 불필요.]")

    return "\n".join(lines)


def view_workflow(name: str) -> str:
    """
    Workflow의 상세 내용(각 단계 프롬프트 포함)을 조회합니다.

    Args:
        name: 조회할 Workflow 이름

    Returns:
        Workflow 상세 정보
    """
    db = _get_db()
    row = db.execute("SELECT * FROM workflows WHERE name = ?", (name,)).fetchone()

    if not row:
        candidates = _fuzzy_find_workflow(name)
        if len(candidates) == 1:
            row = db.execute("SELECT * FROM workflows WHERE name = ?", (candidates[0][0],)).fetchone()
        elif len(candidates) > 1:
            lines = [_t("workflow.similar_list", name=name)]
            for i, (wf_name, score) in enumerate(candidates[:3], 1):
                lines.append(_t("workflow.similarity", i=i, name=wf_name, score=f"{score:.0%}"))
            return "\n".join(lines)
        else:
            return _t("workflow.not_found", name=name)

    raw_prompts = json.loads(row["prompts"])
    steps = _normalize_steps(raw_prompts)

    lines = ["[아래 내용을 그대로 사용자에게 보여주세요.]"]
    lines.append("")
    lines.append(f"## ⚡ {row['name']}")
    lines.append(_t("workflow.detail_desc", desc=row['description'] or '-'))
    lines.append(_t("workflow.detail_schedule", schedule=row['schedule'] or _t('workflow.manual_run')))
    lines.append(_t("workflow.detail_runs", count=row['run_count']))
    lines.append("")
    lines.append("| # | 타입 | 내용 |")
    lines.append("|---|------|------|")
    for i, step in enumerate(steps, 1):
        stype = step.get("type", "prompt")
        content = _resolve_i18n(step.get("content", ""))
        if stype == "code":
            code_name = step.get("code_name", "")
            if code_name:
                lines.append(f"| {i} | 🐍 Code | {code_name} |")
            else:
                first_line = content.split("\n")[0] if content else _t("workflow.empty_code")
                lines.append(f"| {i} | 🐍 Code | `{first_line}` |")
        else:
            # run_stored_code 패턴 감지
            code_ref = _extract_code_ref(content)
            if code_ref:
                lines.append(f"| {i} | 🐍 Code | {code_ref} |")
            else:
                lines.append(f"| {i} | 💬 Prompt | {content} |")
    lines.append("")
    lines.append("수정하려면 변경할 내용을 알려주세요.")
    lines.append("\n[이 결과를 사용자에게 그대로 전달하세요. 추가 도구 호출 불필요.]")

    return "\n".join(lines)


def update_workflow(name: str, prompts: str = "", description: str = "",
                    schedule: str = "__KEEP__") -> str:
    """
    Workflow를 수정합니다.

    Args:
        name: 수정할 Workflow 이름
        prompts: 새 프롬프트 JSON 배열. 빈 문자열이면 기존 유지.
            ⚠ 사용자가 제공한 JSON을 반드시 그대로(raw) 전달하세요.
            ⚠ 절대 번역, 요약, 재구성하지 마세요!
            ⚠ JSON이 아닌 경우 줄바꿈으로 구분된 프롬프트 사용 가능.
        description: 새 설명. 빈 문자열이면 기존 유지.
        schedule: 새 스케줄. "__KEEP__"이면 기존 유지, 빈 문자열이면 예약 제거.
            허용 형식: "daily HH:MM", "weekday HH:MM", "every Nmin", "weekly", "monthly", "HH:MM"
            한국어 입력 변환 예시:
              "매일 아침 8시" → "daily 08:00" / "10분마다" → "every 10min" / "오후 3시" → "daily 15:00"
            ⚠ 절대 한국어 그대로 넣지 마세요.

    Returns:
        수정 결과
    """
    db = _get_db()
    row = db.execute("SELECT * FROM workflows WHERE name = ?", (name,)).fetchone()
    if not row:
        return _t("workflow.not_found", name=name)

    changes = []
    new_prompts_json = row["prompts"]
    new_desc = row["description"]
    new_schedule = row["schedule"]
    need_re_embed = False

    # 프롬프트 업데이트 — 새 JSON 배열 포맷과 기존 \n 구분 모두 지원
    if prompts and prompts.strip():
        prompt_list = None
        raw = prompts.strip()

        # 1차: 그대로 JSON 파싱
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                prompt_list = _normalize_steps(parsed)
            else:
                prompt_list = _normalize_steps([raw])
        except (json.JSONDecodeError, TypeError):
            pass

        # 2차: 따옴표로 감싸진 JSON 문자열이면 벗기고 재시도
        if prompt_list is None and raw.startswith(("'[", "\"[")):
            try:
                inner = raw[1:-1] if raw[-1] in ("'", '"') else raw[1:]
                parsed = json.loads(inner)
                if isinstance(parsed, list):
                    prompt_list = _normalize_steps(parsed)
            except (json.JSONDecodeError, TypeError, IndexError):
                pass

        # 3차: JSON 파싱 실패 시 regex로 스텝 추출 (코드에 깨진 따옴표 대응)
        if prompt_list is None and raw.startswith("["):
            prompt_list = _lenient_extract_steps(raw)

        # 4차: JSON이 아닌 일반 텍스트 → 줄바꿈 구분
        if prompt_list is None:
            import re as _re
            # literal \n도 실제 줄바꿈으로 변환
            text = raw.replace("\\n", "\n")
            lines = [p.strip() for p in text.split("\n") if p.strip()]
            # 번호 접두사 제거: "1. Set api_key" → "Set api_key"
            cleaned = []
            for line in lines:
                line = _re.sub(r'^\d+\.\s*', '', line)
                if line:
                    cleaned.append(line)
            prompt_list = _normalize_steps(cleaned)
        if prompt_list:
            # 기존 코드 스텝의 used_by 참조 제거
            try:
                from tools.code_tools import update_code_used_by
                old_steps = json.loads(row["prompts"]) if row["prompts"] else []
                for step in old_steps:
                    if isinstance(step, dict) and step.get("code_name"):
                        update_code_used_by(step["code_name"], name, action="remove")
                # 새 코드 스텝의 used_by 참조 추가
                for step in prompt_list:
                    if isinstance(step, dict) and step.get("code_name"):
                        update_code_used_by(step["code_name"], name, action="add")
            except Exception:
                pass  # 참조 업데이트 실패해도 워크플로우는 수정
            new_prompts_json = json.dumps(prompt_list, ensure_ascii=False)
            changes.append(_t("workflow.change_prompts", count=len(prompt_list)))

    # 설명 업데이트
    if description and description.strip():
        new_desc = description.strip()
        changes.append("설명 업데이트")
        need_re_embed = True

    # 스케줄 업데이트
    if schedule != "__KEEP__":
        new_schedule = _normalize_schedule(schedule)
        changes.append(_t("workflow.change_schedule", schedule=schedule or _t('workflow.manual_run')))

    if not changes:
        return "⚠ 변경사항이 없습니다."

    # 임베딩 재계산 (이름/설명 변경 시 또는 기존 embedding이 없으면)
    embedding = row["embedding"]
    if need_re_embed or embedding is None:
        new_embed = _get_embed(f"{name} {new_desc}")
        if new_embed:
            embedding = new_embed

    db.execute("""
        UPDATE workflows SET prompts = ?, description = ?, schedule = ?, embedding = ?
        WHERE name = ?
    """, (new_prompts_json, new_desc, new_schedule, embedding, name))
    db.commit()
    _fill_missing_embeddings_async()

    prompt_list = json.loads(new_prompts_json)
    steps = "\n".join(f"  {i+1}. {_step_summary(p)}" for i, p in enumerate(prompt_list))
    return (f"✅ '{name}' Workflow 수정!\n변경: {', '.join(changes)}\n{steps}"
            + _brief_workflow_list()
            + "\n[이 결과를 사용자에게 간결하게 전달하세요. 각 스텝을 다시 설명하지 마세요.]")


def delete_workflow(name: str) -> str:
    """
    Workflow를 삭제합니다.

    Args:
        name: 삭제할 Workflow 이름

    Returns:
        삭제 결과
    """
    db = _get_db()

    # 1) 정확 매칭
    row = db.execute("SELECT name, prompts FROM workflows WHERE name = ?", (name,)).fetchone()

    # 2) 임베딩 유사도 매칭 (언어 무관, 유사도 높을 때만)
    if not row:
        candidates = _fuzzy_find_workflow(name)
        if candidates and candidates[0][1] >= 0.7:
            row = db.execute("SELECT name, prompts FROM workflows WHERE name = ?", (candidates[0][0],)).fetchone()

    if not row:
        return _t("workflow.not_found", name=name)

    actual_name = row["name"]

    # 코드 스텝의 used_by 참조 제거
    try:
        from tools.code_tools import update_code_used_by
        steps = json.loads(row["prompts"]) if row["prompts"] else []
        for step in steps:
            if isinstance(step, dict):
                code_name = step.get("code_name", "")
                if code_name:
                    update_code_used_by(code_name, actual_name, action="remove")
    except Exception:
        pass  # 참조 정리 실패해도 워크플로우는 삭제

    db.execute("DELETE FROM workflows WHERE name = ?", (actual_name,))
    db.commit()
    return _t("workflow.deleted", name=actual_name)


# ─────────────────────────────────────────────
# 스케줄러 통합 (구 scheduler_tools 기능)
# ─────────────────────────────────────────────

def _parse_next_run(schedule: str, now=None):
    """
    schedule 문자열을 파싱하여 다음 실행 시간(datetime)을 반환.
    
    지원 형식:
      - "daily HH:MM"      매일 HH:MM
      - "weekday HH:MM"    평일(월~금) HH:MM
      - "weekly"           매주 월요일 09:00
      - "monthly"          매월 1일 09:00
      - "every Nmin"       N분마다 (최소 5)
      - "HH:MM"            daily HH:MM과 동일
    """
    from datetime import timedelta
    if now is None:
        now = datetime.now()
    s = schedule.strip().lower()
    if not s:
        return None

    # every Nmin
    if s.startswith("every "):
        parts = s.replace("every ", "").strip()
        minutes = int(''.join(c for c in parts if c.isdigit()) or '5')
        minutes = max(minutes, 5)  # 최소 5분
        return now + timedelta(minutes=minutes)

    # HH:MM 파싱 헬퍼
    def _next_hhmm(hhmm_str, now):
        try:
            h, m = map(int, hhmm_str.split(":"))
        except ValueError:
            return None
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    # daily HH:MM 또는 단순 HH:MM
    if s.startswith("daily "):
        return _next_hhmm(s.replace("daily ", "").strip(), now)
    if ":" in s and len(s) <= 5:
        return _next_hhmm(s, now)

    # weekday HH:MM
    if s.startswith("weekday "):
        hhmm = s.replace("weekday ", "").strip()
        nxt = _next_hhmm(hhmm, now)
        if nxt:
            while nxt.weekday() >= 5:  # 토,일 건너뛰기
                nxt += timedelta(days=1)
        return nxt

    # weekly
    if s.startswith("weekly"):
        hhmm = s.replace("weekly", "").strip() or "09:00"
        nxt = _next_hhmm(hhmm, now)
        if nxt:
            while (nxt - now).days < 7:
                nxt += timedelta(days=1)
        return nxt

    # monthly
    if s.startswith("monthly"):
        nxt = now.replace(day=1, hour=9, minute=0, second=0)
        if nxt <= now:
            if nxt.month == 12:
                nxt = nxt.replace(year=nxt.year + 1, month=1)
            else:
                nxt = nxt.replace(month=nxt.month + 1)
        return nxt

    return None


def init_workflow_schedules():
    """서버 시작 시 schedule이 있고 next_run이 없는 워크플로우에 next_run 계산"""
    db = _get_db()
    rows = db.execute(
        "SELECT name, schedule FROM workflows WHERE schedule != '' AND (next_run IS NULL OR next_run = '')"
    ).fetchall()
    for row in rows:
        nxt = _parse_next_run(row[1])
        if nxt:
            db.execute("UPDATE workflows SET next_run = ? WHERE name = ?",
                       (nxt.strftime("%Y-%m-%d %H:%M:%S"), row[0]))
    db.commit()


def get_due_workflows() -> list:
    """실행 시간이 된 워크플로우 반환 (서버 스케줄러 루프에서 호출)"""
    db = _get_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = db.execute(
        "SELECT name, prompts, schedule, description FROM workflows "
        "WHERE schedule != '' AND next_run IS NOT NULL AND next_run <= ?",
        (now_str,)
    ).fetchall()
    result = []
    for r in rows:
        prompts_raw = json.loads(r[1]) if r[1] else []
        # 프롬프트가 dict({'type':'prompt','content':'...'}) 또는 str일 수 있음
        prompts = []
        for p in prompts_raw:
            if isinstance(p, dict):
                prompts.append(p.get("content", str(p)))
            else:
                prompts.append(str(p))
        # 모든 프롬프트를 하나의 action으로 결합
        action = "\n".join(prompts) if prompts else r[3] or r[0]
        result.append({
            "name": r[0],
            "action": action,
            "schedule": r[2],
            "description": r[3] or r[0],
        })
    return result


def mark_workflow_executed(name: str):
    """실행 완료 — run_count 증가, next_run 갱신"""
    db = _get_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = db.execute("SELECT schedule FROM workflows WHERE name = ?", (name,)).fetchone()
    if not row:
        db.close()
        return
    schedule = row[0]
    nxt = _parse_next_run(schedule)
    nxt_str = nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else None
    db.execute(
        "UPDATE workflows SET run_count = run_count + 1, last_run = ?, next_run = ? WHERE name = ?",
        (now_str, nxt_str, name)
    )
    db.commit()
    db.close()


def save_workflow_result(name: str, action: str, result: str) -> int:
    """실행 결과를 알림으로 저장. 알림 ID를 반환합니다."""
    db = _get_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = db.execute(
        "INSERT INTO wf_notifications (wf_name, action, result, created) VALUES (?, ?, ?, ?)",
        (name, action[:200], result, now_str)
    )
    db.commit()
    notif_id = cursor.lastrowid
    db.close()
    return notif_id


def get_notifications(unread_only: bool = True) -> str:
    """워크플로우 실행 결과 알림 조회"""
    db = _get_db()
    if unread_only:
        rows = db.execute(
            "SELECT id, wf_name, action, result, created FROM wf_notifications WHERE read = 0 ORDER BY created DESC LIMIT 20"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, wf_name, action, result, created FROM wf_notifications ORDER BY created DESC LIMIT 20"
        ).fetchall()
    db.close()
    if not rows:
        return "📭 새 알림이 없습니다."
    lines = [_t("workflow.noti_header", count=len(rows))]
    for r in rows:
        lines.append(f"  [{r[4]}] {r[1]}: {r[3][:100]}")
    return "\n".join(lines)


def mark_notifications_read(ids: list = None):
    """알림을 읽음 처리"""
    db = _get_db()
    if ids:
        for nid in ids:
            db.execute("UPDATE wf_notifications SET read = 1 WHERE id = ?", (nid,))
    else:
        db.execute("UPDATE wf_notifications SET read = 1")
    db.commit()
    db.close()
