"""
3-Tier Memory System — STM / MTM / LTM

STM (Short-Term): 대화 턴을 디스크에 즉시 저장 (서버 크래시 복구용)
MTM (Mid-Term):   세션 요약 (자동 요약, 7~30일 보존)
LTM (Long-Term):  영구 기억 (사용자 프로필, 핵심 사실)

저장소: SQLite /root/.xoul/memory.db
임베딩: bge-m3 (Ollama) → 1024차원
요약:   config summarize_model (Ollama) → think=false
"""

import json
import sqlite3
import struct
import math
import time
import threading
import urllib.request
import os
from datetime import datetime, timedelta
from .system_tools import tool_run_command
from i18n import t as _t


def _get_llm_config() -> dict:
    """config.json에서 LLM base_url, api_key, model_name 반환"""
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    if not os.path.exists(cfg_path):
        cfg_path = "config.json"
    with open(cfg_path, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    llm_cfg = cfg.get("llm", {})
    provider = llm_cfg.get("provider", "local")
    p = llm_cfg.get("providers", {}).get(provider, {})
    model = p.get("model_name", "") or llm_cfg.get("ollama_model", "")
    base_url = p.get("base_url", "http://10.0.2.2:11434/v1")
    api_key = p.get("api_key", "none")
    if not model:
        raise RuntimeError("config.json에 LLM 모델이 설정되지 않았습니다")
    return {"model": model, "base_url": base_url, "api_key": api_key}


def _get_main_model() -> str:
    """config.json에서 선택된 메인 LLM 모델명 반환 (없으면 오류)"""
    return _get_llm_config()["model"]

# ─── 설정 ───
DB_PATH = "/root/.xoul/memory.db"
EMBED_MODEL = "bge-m3"
EMBED_URL = "http://10.0.2.2:11434/api/embeddings"
def _get_summarize_model() -> str:
    """config.json에서 요약용 모델명 반환 (llm.summarize_model, 없으면 메인 모델 사용)"""
    try:
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
        if not os.path.exists(cfg_path):
            cfg_path = "config.json"
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        model = cfg.get("llm", {}).get("summarize_model", "")
        if model:
            return model
    except Exception:
        pass
    return _get_main_model()
KEY_MATCH_THRESHOLD = 0.9   # key normalization cosine threshold

MAX_RESULTS = 5
SIMILARITY_THRESHOLD = 0.6
SESSION_GAP_MINUTES = 10    # 이 시간 이상 공백 → 새 세션
MTM_TTL_DAYS = 30           # MTM 세션요약 만료일
MTM_MEMORY_TTL_DAYS = 7     # MTM 기억 만료일 (미사용 시 삭제)
LTM_PROMOTE_COUNT = 3       # MTM에서 N회 이상 참조 → LTM 승격
STM_CLEANUP_DAYS = 7        # summarized=1 된 STM 보존 일수
_last_cleanup_time = 0.0    # 마지막 전체 정리 실행 시각

# DB 싱글턴
_db_conn = None
_db_lock = threading.RLock()  # RLock: 재진입 가능 (remember_mtm→remember 체인 deadlock 방지)
_last_activity = 0.0  # 마지막 활동 시각 (time.time)


# ─── DB 초기화 ───

def _get_db() -> sqlite3.Connection:
    """SQLite 연결 — 3개 테이블 자동 생성"""
    global _db_conn
    # 닫힌 연결 감지 → 재생성
    if _db_conn is not None:
        try:
            _db_conn.execute("SELECT 1")
        except Exception:
            _db_conn = None
    if _db_conn is None:
        tool_run_command("mkdir -p /root/.xoul")
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        _db_conn.row_factory = sqlite3.Row
        _db_conn.execute("PRAGMA journal_mode=WAL")  # 동시 읽기/쓰기 지원

        # STM: 대화 턴 (서버 크래시 복구용)
        _db_conn.execute("""
            CREATE TABLE IF NOT EXISTS stm (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                summarized INTEGER DEFAULT 0
            )
        """)

        # MTM: 세션 요약 (7~30일 보존)
        _db_conn.execute("""
            CREATE TABLE IF NOT EXISTS mtm (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary TEXT NOT NULL,
                keywords TEXT,
                embedding BLOB,
                created TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count INTEGER DEFAULT 0
            )
        """)

        # LTM: 영구 기억 (기존 memories 호환)
        _db_conn.execute("""
            CREATE TABLE IF NOT EXISTS ltm (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                embedding BLOB,
                created TEXT,
                updated TEXT,
                access_count INTEGER DEFAULT 0,
                importance INTEGER DEFAULT 1
            )
        """)

        # MTM 기억 key-value (MTM→LTM 승격 파이프라인)
        _db_conn.execute("""
            CREATE TABLE IF NOT EXISTS mtm_memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                embedding BLOB,
                created TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count INTEGER DEFAULT 0
            )
        """)

        # 기존 memories → ltm 마이그레이션
        try:
            _db_conn.execute("""
                INSERT OR IGNORE INTO ltm (key, value, embedding, created, updated, access_count)
                SELECT key, value, embedding, created, updated, access_count
                FROM memories
            """)
            _db_conn.commit()
        except Exception:
            pass

        # 스킬 테이블 (기존 유지)
        _db_conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                name TEXT PRIMARY KEY,
                description TEXT,
                trigger_keywords TEXT,
                method TEXT,
                packages TEXT,
                script TEXT,
                embedding BLOB,
                created TEXT,
                last_used TEXT,
                success_count INTEGER DEFAULT 1
            )
        """)
        _db_conn.commit()
    return _db_conn


# ─── 임베딩 유틸 ───

def _get_embedding(text: str) -> bytes | None:
    """Ollama bge-m3 임베딩 → 동적 차원 → bytes"""
    try:
        payload = json.dumps({"model": EMBED_MODEL, "prompt": text, "options": {"num_gpu": 0}}).encode()
        req = urllib.request.Request(
            EMBED_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            vec = json.loads(resp.read().decode())["embedding"]
            if not vec:
                return None
            norm = math.sqrt(sum(x * x for x in vec))
            if norm > 0:
                vec = [x / norm for x in vec]
            dim = len(vec)
            return struct.pack(f'{dim}f', *vec)
    except Exception:
        return None


def _cosine_similarity(a: bytes, b: bytes) -> float:
    """두 임베딩의 코사인 유사도"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dim = len(a) // 4  # float32 = 4 bytes
    va = struct.unpack(f'{dim}f', a)
    vb = struct.unpack(f'{dim}f', b)
    return sum(x * y for x, y in zip(va, vb))


# ─── STM: 대화 턴 저장/조회 ───

def save_turn(role: str, content: str):
    """매 턴을 STM에 즉시 저장 (서버 크래시 대비)"""
    global _last_activity
    try:
        with _db_lock:
            db = _get_db()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO stm (role, content, timestamp) VALUES (?, ?, ?)",
                (role, content[:5000], now)  # 5000자 제한
            )
            db.commit()
        _last_activity = time.time()
    except Exception:
        pass


def _get_unsummarized_turns() -> list[dict]:
    """아직 요약 안 된 STM 턴 가져오기"""
    try:
        db = _get_db()
        rows = db.execute(
            "SELECT id, role, content, timestamp FROM stm WHERE summarized = 0 ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _mark_turns_summarized(turn_ids: list[int]):
    """요약 완료된 턴 마킹"""
    try:
        db = _get_db()
        placeholders = ",".join("?" * len(turn_ids))
        db.execute(f"UPDATE stm SET summarized = 1 WHERE id IN ({placeholders})", turn_ids)
        db.commit()
    except Exception:
        pass


# ─── MTM: 세션 요약 ───

def _summarize_structured(text: str) -> tuple[str, str]:
    """요약 모델로 대화를 keyword|value 쌍으로 구조화 요약.
    Returns: (summary_text, keywords_csv)
    summary_text: 원래 형식의 요약 (MTM에 저장)
    keywords_csv: 콤마 구분 키워드 (MTM 검색용)
    """
    prompt = (
        "Extract ONLY personal facts about the USER from the conversation below.\n"
        "Output each fact as: keyword|value (one per line).\n"
        "If nothing to extract, output: none\n\n"
        "Extract: name, age, birthday, family, email, job, location, hobbies, preferences.\n"
        "Ignore: workflows, prices, dates, files, commands, API keys, search requests.\n\n"
        "Example:\n"
        "Conversation: '저는 김철수이고 백엔드 개발자예요. 커피를 좋아해요'\n"
        "Output:\n"
        "이름|김철수\n"
        "직업|백엔드 개발자\n"
        "좋아하는 음료|커피\n\n"
        "Conversation: 'Stock Market Summary 워크플로우 실행됨. 비트코인 $70,000'\n"
        "Output: none\n\n"
        f"Conversation:\n{text[:5000]}"
    )
    try:
        url = "http://10.0.2.2:11434/api/chat"
        req = urllib.request.Request(
            url,
            data=json.dumps({
                "model": _get_summarize_model(),
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_gpu": 0, "temperature": 0.1, "num_predict": 4096},
            }).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = (json.loads(resp.read().decode()).get("message", {}).get("content") or "").strip()

        if not raw:
            return "", ""

        # 추출 대상 없음 → 빈 결과
        if raw.strip() in ("없음", "해당 없음", "없음.", "none", "None"):
            return "", ""

        # keyword|value 파싱
        lines = []
        keywords = []
        for line in raw.split("\n"):
            line = line.strip().lstrip("- ")
            if "|" in line:
                parts = line.split("|", 1)
                kw = parts[0].strip()
                val = parts[1].strip() if len(parts) > 1 else ""
                if kw and val and len(kw) >= 2:
                    lines.append(f"{kw}: {val}")
                    keywords.append(kw)
            elif line and len(line) > 5:
                lines.append(line)

        summary = "\n".join(lines)
        keywords_csv = ",".join(dict.fromkeys(keywords))
        return summary, keywords_csv
    except Exception:
        return "", ""


def summarize_and_store():
    """미요약 STM 턴을 요약 모델로 요약 → MTM 저장
    세션 갭(10분+)이 있으면 분리해서 각각 요약 → 별도 MTM 생성"""
    turns = _get_unsummarized_turns()
    if len(turns) < 2:
        return

    # 턴을 세션별로 분리 (10분+ 공백 기준)
    sessions = []
    current_session = [turns[0]]
    for i in range(1, len(turns)):
        prev_ts = datetime.strptime(turns[i-1]["timestamp"], "%Y-%m-%d %H:%M:%S")
        curr_ts = datetime.strptime(turns[i]["timestamp"], "%Y-%m-%d %H:%M:%S")
        gap = (curr_ts - prev_ts).total_seconds()
        if gap > SESSION_GAP_MINUTES * 60:
            sessions.append(current_session)
            current_session = [turns[i]]
        else:
            current_session.append(turns[i])
    sessions.append(current_session)

    all_keywords = []
    for session_turns in sessions:
        if len(session_turns) < 2:
            # 1턴만 있으면 마킹만 하고 스킵
            _mark_turns_summarized([t["id"] for t in session_turns])
            continue

        convo = "\n".join(f"[{t['role']}] {t['content']}" for t in session_turns)
        summary, keywords = _summarize_structured(convo)
        if not summary or len(summary) < 10:
            _mark_turns_summarized([t["id"] for t in session_turns])
            continue

        try:
            db = _get_db()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            embedding = _get_embedding(summary)

            db.execute(
                "INSERT INTO mtm (summary, keywords, embedding, created, last_accessed) VALUES (?, ?, ?, ?, ?)",
                (summary, keywords, embedding, now, now)
            )
            db.commit()
            _mark_turns_summarized([t["id"] for t in session_turns])
            all_keywords.append(keywords)
        except Exception:
            pass

    # 모든 세션 요약 후 LTM 승격 체크 (누적 키워드)
    for kw in all_keywords:
        _try_promote_to_ltm(kw)

    # 통합 정리 (24시간 1회)
    run_full_cleanup()


def check_and_summarize():
    """새 세션 시작 시 이전 대화 자동 요약 (server.py에서 호출)
    Returns: True if summarization was triggered"""
    global _last_activity
    now = time.time()

    if _last_activity == 0:
        _last_activity = now
        return False

    gap = now - _last_activity
    if gap > SESSION_GAP_MINUTES * 60:
        # 비동기로 요약 실행 (사용자 대기 없음)
        t = threading.Thread(target=summarize_and_store, daemon=True)
        t.start()
        _last_activity = now
        return True

    _last_activity = now
    return False


# ─── LTM: 영구 기억 ───

def _try_promote_to_ltm(new_keywords: str):
    """MTM 키워드가 반복 등장하면 LTM으로 승격"""
    if not new_keywords:
        return
    try:
        db = _get_db()
        kw_list = [k.strip() for k in new_keywords.split(",") if k.strip()]

        for kw in kw_list:
            # 이 키워드가 MTM에 몇 번 등장했는지 카운트
            count = db.execute(
                "SELECT COUNT(*) as cnt FROM mtm WHERE keywords LIKE ?",
                (f"%{kw}%",)
            ).fetchone()["cnt"]

            if count >= LTM_PROMOTE_COUNT:
                # 이미 LTM에 있으면 중요도 증가, 없으면 신규
                existing = db.execute("SELECT key FROM ltm WHERE key = ? OR key LIKE ?", (kw, f"%{kw}%")).fetchone()
                if existing:
                    db.execute(
                        "UPDATE ltm SET importance = importance + 1, updated = ? WHERE key = ?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M"), kw)
                    )
                else:
                    # 해당 키워드가 포함된 MTM에서 가장 최근 요약 가져오기
                    latest = db.execute(
                        "SELECT summary FROM mtm WHERE keywords LIKE ? ORDER BY created DESC LIMIT 1",
                        (f"%{kw}%",)
                    ).fetchone()
                    if latest:
                        remember(kw, latest["summary"][:200], category="auto_promoted")
        db.commit()
    except Exception:
        pass


def _cleanup_expired_mtm():
    """30일 이상 미참조 MTM 삭제"""
    try:
        db = _get_db()
        cutoff = (datetime.now() - timedelta(days=MTM_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        deleted = db.execute("DELETE FROM mtm WHERE last_accessed < ?", (cutoff,)).rowcount
        if deleted > 0:
            db.commit()
            print(f"[cleanup_mtm] {deleted}개 MTM 요약 삭제 (30일 만료)", flush=True)
    except Exception:
        pass


def _cleanup_summarized_stm():
    """요약 완료된 STM 턴 삭제 (STM_CLEANUP_DAYS일 이상 경과분)"""
    try:
        db = _get_db()
        cutoff = (datetime.now() - timedelta(days=STM_CLEANUP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        deleted = db.execute(
            "DELETE FROM stm WHERE summarized = 1 AND timestamp < ?", (cutoff,)
        ).rowcount
        if deleted > 0:
            db.commit()
            print(f"[cleanup_stm] {deleted}개 요약완료 STM 삭제 ({STM_CLEANUP_DAYS}일 경과)", flush=True)
    except Exception:
        pass


def run_full_cleanup():
    """STM + MTM 통합 정리. summarize_and_store() 완료 후 자동 호출."""
    global _last_cleanup_time
    now = time.time()
    # 24시간에 1회만 실행
    if now - _last_cleanup_time < 86400:
        return
    _last_cleanup_time = now
    _cleanup_summarized_stm()
    _cleanup_expired_mtm()
    _cleanup_mtm_memory()
    print("[run_full_cleanup] 완료", flush=True)


# ─── 공개 API (LLM 도구) ───

def _normalize_ltm_key(new_key: str, new_emb: bytes | None, db) -> str:
    """LTM 저장 전에 기존 key와 유사도 비교 → 중복이면 기존 key 반환.
    1차: cosine similarity >= KEY_MATCH_THRESHOLD
    2차: substring + 길이비율 >= 60%
    """
    existing = db.execute("SELECT key, embedding FROM ltm").fetchall()
    if not existing:
        return new_key

    if new_emb:
        best_sim, best_key = 0.0, None
        for row in existing:
            if not row["embedding"] or len(row["embedding"]) != len(new_emb):
                continue
            sim = _cosine_similarity(new_emb, row["embedding"])
            if sim > best_sim:
                best_sim, best_key = sim, row["key"]
        if best_key and best_sim >= KEY_MATCH_THRESHOLD:
            print(f"[ltm_dedup] '{new_key}' → '{best_key}' (sim={best_sim:.3f})", flush=True)
            return best_key

    # substring fallback
    new_lower = new_key.replace(" ", "").replace("_", "")
    for row in existing:
        ex_lower = row["key"].replace(" ", "").replace("_", "")
        shorter = min(len(ex_lower), len(new_lower))
        longer = max(len(ex_lower), len(new_lower))
        if longer == 0:
            continue
        if shorter / longer >= 0.6 and (ex_lower in new_lower or new_lower in ex_lower):
            print(f"[ltm_dedup] '{new_key}' → '{row['key']}' (substring)", flush=True)
            return row["key"]
    return new_key


def remember(key: str, value: str, category: str = "general") -> str:
    """
    기억을 LTM에 저장합니다. key:value 형태로 간결하게.
    저장 전 기존 key와 유사도를 자동 비교해 중복이면 기존 key에 덮어씁니다.

    Args:
        key: 기억의 키 (예: user_name, 좋아하는_음식)
        value: 기억할 값 (간결하게)

    Returns:
        저장 결과 메시지
    """
    try:
        embed = _get_embedding(key)
        with _db_lock:
            db = _get_db()
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            # 자동 key 정규화 — 기존 key와 유사하면 기존 key 재사용
            normalized_key = _normalize_ltm_key(key, embed, db)
            if normalized_key != key:
                print(f"[remember] key merged: '{key}' → '{normalized_key}'", flush=True)
                key = normalized_key
            # MTM에 같은 key가 있으면 삭제 (LTM으로 승격된 셈)
            db.execute("DELETE FROM mtm_memory WHERE key = ?", (key,))
            db.execute("""
                INSERT INTO ltm (key, value, category, embedding, created, updated, access_count, importance)
                VALUES (?, ?, ?, ?, ?, ?, 0, 1)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    category = excluded.category,
                    embedding = excluded.embedding,
                    updated = excluded.updated
            """, (key, value, category, embed, now, now))
            db.commit()
        return f"✅ 기억 저장됨 [{key}]: {value[:80]}"
    except Exception as e:
        return f"⚠ 기억 저장 실패: {e}"


def remember_mtm(key: str, value: str, category: str = "general") -> str:
    """
    기억을 MTM에 저장합니다 (승격 대기).
    이미 LTM에 같은 key가 있으면 LTM을 직접 업데이트.
    MTM에 같은 key가 있으면 value를 업데이트.
    """
    try:
        with _db_lock:
            db = _get_db()
            # 이미 LTM에 있으면 → LTM 직접 업데이트 (이미 영구화된 기억)
            existing_ltm = db.execute("SELECT key FROM ltm WHERE key = ?", (key,)).fetchone()
            if existing_ltm:
                return remember(key, value, category)  # LTM 업데이트

            embed = _get_embedding(key)
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            db.execute("""
                INSERT INTO mtm_memory (key, value, category, embedding, created, last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    category = excluded.category,
                    embedding = excluded.embedding,
                    last_accessed = excluded.last_accessed
            """, (key, value, category, embed, now, now))
            db.commit()
        return f"📝 MTM 저장됨 [{key}]: {value[:80]}"
    except Exception as e:
        return f"⚠ MTM 저장 실패: {e}"


def recall(query: str = "") -> str:
    """
    저장된 기억을 시맨틱 검색합니다. LTM + MTM 통합 검색.

    Args:
        query: 검색어 (자연어 가능)

    Returns:
        관련 기억 내용 (유사도 순)
    """
    try:
        db = _get_db()
        parts = []

        # --- LTM 검색 ---
        ltm_rows = db.execute("SELECT key, value, category, embedding, COALESCE(updated, created) as ts FROM ltm ORDER BY COALESCE(updated, created) ASC").fetchall()
        if not query:
            if ltm_rows:
                lines = [f"- **{r['key']}** ({r['category']}): {r['value']} [{r['ts'] or ''}]" for r in ltm_rows]
                parts.append(f"🧠 LTM ({len(ltm_rows)}개, ⏱ 오래된→최신 순, 같은 주제면 최신 정보를 우선):\n" + "\n".join(lines))
            # MTM key-value도 표시
            mtm_mem_rows = db.execute("SELECT key, value, category, created, access_count FROM mtm_memory ORDER BY created ASC").fetchall()
            if mtm_mem_rows:
                lines = [f"- **{r['key']}**: {r['value']} [MTM, 접근:{r['access_count']}]" for r in mtm_mem_rows]
                parts.append(f"📝 MTM 기억 ({len(mtm_mem_rows)}개):\n" + "\n".join(lines))
            mtm_rows = db.execute("SELECT summary, created FROM mtm ORDER BY created DESC LIMIT 5").fetchall()
            if mtm_rows:
                lines = [f"- [{r['created'][:10]}] {r['summary'][:100]}" for r in mtm_rows]
                parts.append(f"📋 MTM 요약 ({len(mtm_rows)}개):\n" + "\n".join(lines))
            return "\n\n".join(parts) if parts else "📭 저장된 기억이 없습니다."

        q_emb = _get_embedding(query)
        q_lower = query.lower()

        # LTM 시맨틱 + 키워드 검색
        ltm_scored = []
        for r in ltm_rows:
            score = 0.0
            if q_emb and r["embedding"]:
                score = _cosine_similarity(q_emb, r["embedding"])
            # 키워드 가산
            if any(w in r["key"].lower() or w in r["value"].lower()
                   for w in q_lower.split() if len(w) >= 2):
                score = max(score, 0.5)
            if score >= SIMILARITY_THRESHOLD:
                ltm_scored.append((score, r))

        if ltm_scored:
            ltm_scored.sort(key=lambda x: x[0], reverse=True)
            for _, r in ltm_scored[:MAX_RESULTS]:
                db.execute("UPDATE ltm SET access_count = access_count + 1 WHERE key = ?", (r["key"],))
            lines = [f"- **{r['key']}**: {r['value']} [{r['ts'] or ''}] ({sim:.2f})" for sim, r in ltm_scored[:MAX_RESULTS]]
            parts.append("🧠 LTM (⏱ 같은 주제면 최신 정보를 우선):\n" + "\n".join(lines))

        # --- MTM 검색 ---
        mtm_rows = db.execute("SELECT id, summary, keywords, embedding, created FROM mtm").fetchall()
        mtm_scored = []
        for r in mtm_rows:
            score = 0.0
            if q_emb and r["embedding"]:
                score = _cosine_similarity(q_emb, r["embedding"])
            if r["keywords"] and any(w in r["keywords"].lower()
                                     for w in q_lower.split() if len(w) >= 2):
                score = max(score, 0.5)
            if score >= SIMILARITY_THRESHOLD:
                mtm_scored.append((score, r))

        if mtm_scored:
            mtm_scored.sort(key=lambda x: x[0], reverse=True)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for _, r in mtm_scored[:3]:
                db.execute("UPDATE mtm SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                          (now, r["id"]))
            lines = [f"- [{r['created'][:10]}] {r['summary'][:120]} ({sim:.2f})"
                     for sim, r in mtm_scored[:3]]
            parts.append("📝 MTM 요약:\n" + "\n".join(lines))

        # --- MTM key-value 검색 ---
        mtm_mem_rows = db.execute("SELECT key, value, category, embedding, created, access_count FROM mtm_memory").fetchall()
        mtm_mem_scored = []
        for r in mtm_mem_rows:
            score = 0.0
            if q_emb and r["embedding"]:
                score = _cosine_similarity(q_emb, r["embedding"])
            if any(w in r["key"].lower() or w in r["value"].lower()
                   for w in q_lower.split() if len(w) >= 2):
                score = max(score, 0.5)
            if score >= SIMILARITY_THRESHOLD:
                mtm_mem_scored.append((score, r))

        if mtm_mem_scored:
            mtm_mem_scored.sort(key=lambda x: x[0], reverse=True)
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            for _, r in mtm_mem_scored[:MAX_RESULTS]:
                new_count = r["access_count"] + 1
                if new_count >= LTM_PROMOTE_COUNT:
                    _promote_mtm_to_ltm(r["key"], r)
                else:
                    db.execute(
                        "UPDATE mtm_memory SET access_count = ?, last_accessed = ? WHERE key = ?",
                        (new_count, now, r["key"])
                    )
            lines = [f"- **{r['key']}**: {r['value']} [MTM] ({sim:.2f})" for sim, r in mtm_mem_scored[:MAX_RESULTS]]
            parts.append("📝 MTM 기억:\n" + "\n".join(lines))

        db.commit()
        if parts:
            return "\n\n".join(parts)
        return f"📭 '{query}' 관련 기억이 없습니다."
    except Exception as e:
        return f"⚠ 기억 검색 실패: {e}"


def forget(key: str) -> str:
    """
    특정 LTM 기억을 삭제합니다.

    Args:
        key: 삭제할 기억의 key

    Returns:
        삭제 결과
    """
    try:
        with _db_lock:
            db = _get_db()
            cur_ltm = db.execute("DELETE FROM ltm WHERE key = ?", (key,))
            cur_mtm = db.execute("DELETE FROM mtm_memory WHERE key = ?", (key,))
            db.commit()
            deleted = cur_ltm.rowcount > 0 or cur_mtm.rowcount > 0
        if deleted:
            return f"✅ '{key}' 기억 삭제됨"
        return f"⚠ '{key}' 기억을 찾을 수 없습니다."
    except Exception as e:
        return f"⚠ 기억 삭제 실패: {e}"


# ─── 기억 추출 (후처리 단계) ───

def extract_and_remember(user_message: str) -> dict:
    """
    대화 완료 후 사용자 메시지에서 기억할 사실을 추출.
    Priority 기반: HIGH→LTM 직행, MID→MTM, LOW→추출 안 함.
    Returns: {"raw": str, "saved": [(key, value, priority), ...]}
    """
    if not user_message:
        return {"raw": "", "saved": []}

    # 짧은 인사/질문은 스킵
    if len(user_message) < 10 and not any(w in user_message for w in ["기억", "이름", "생일", "좋아"]):
        return {"raw": "", "saved": []}

    prompt = f"""Extract personal facts about the user. Output: Key|Value|Priority
If nothing to extract, output: none

Priority: HIGH = name, age, birthday, email, family. MID = job, hobby, preference, location.
Ignore: commands, file paths, search requests, workflows, prices, tasks.

Example:
Input: 나는 남현이고 개발자야
Output:
이름|남현|HIGH
직업|개발자|MID

Input: 서버 점검해줘
Output: none

Input: {user_message[:2000]}
Output:"""


    try:
        url = "http://10.0.2.2:11434/api/chat"
        req = urllib.request.Request(
            url,
            data=json.dumps({
                "model": _get_summarize_model(),
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_gpu": 0, "temperature": 0.1, "num_predict": 1024},
            }).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = (json.loads(resp.read().decode()).get("message", {}).get("content") or "").strip()

        print(f"\033[95m[extract_and_remember] INPUT: {user_message[:200]}\nOUTPUT: {raw}\033[0m", flush=True)

        if not raw or "없음" in raw:
            return {"raw": raw, "saved": []}

        # key|value|priority 파싱
        saved = []
        db = _get_db()

        # 기존 LTM/MTM 키 + 임베딩 로드 (key-only embedding이므로 DB에서 바로 사용)
        existing_keys_with_emb = []
        for r in db.execute("SELECT key, embedding FROM ltm").fetchall():
            existing_keys_with_emb.append((r["key"], r["embedding"]))
        for r in db.execute("SELECT key, embedding FROM mtm_memory").fetchall():
            existing_keys_with_emb.append((r["key"], r["embedding"]))

        def _normalize_key(new_key: str) -> str:
            """기존 키와 유사하면 기존 키 재사용 (BGE-M3 embedding + substring fallback)
            1차: cosine similarity >= 0.9 → 매칭
            2차: substring 포함 + 길이비율 >= 60% → 매칭"""
            if not existing_keys_with_emb:
                return new_key

            # 1차: embedding 기반 (BGE-M3, threshold 0.9)
            new_emb = _get_embedding(new_key)
            if new_emb:
                best_sim = 0.0
                best_key = None
                for ex_key, ex_emb in existing_keys_with_emb:
                    if not ex_emb or len(ex_emb) != len(new_emb):
                        continue
                    sim = _cosine_similarity(new_emb, ex_emb)
                    if sim > best_sim:
                        best_sim = sim
                        best_key = ex_key
                if best_key and best_sim >= KEY_MATCH_THRESHOLD:
                    print(f"[normalize_key] '{new_key}' → '{best_key}' (embed={best_sim:.3f})", flush=True)
                    return best_key

            # 2차: substring fallback (embedding 실패 또는 threshold 미달)
            new_lower = new_key.replace(" ", "").replace("_", "")
            for ex_key, _ in existing_keys_with_emb:
                ex_lower = ex_key.replace(" ", "").replace("_", "")
                shorter = min(len(ex_lower), len(new_lower))
                longer = max(len(ex_lower), len(new_lower))
                if longer == 0:
                    continue
                ratio = shorter / longer
                if ratio >= 0.6 and (ex_lower in new_lower or new_lower in ex_lower):
                    print(f"[normalize_key] '{new_key}' → '{ex_key}' (substring, ratio={ratio:.2f})", flush=True)
                    return ex_key
            return new_key
        for line in raw.split("\n"):
            line = line.strip().lstrip("- ")
            if "|" not in line:
                continue
            parts = line.split("|")
            key = parts[0].strip()
            val = parts[1].strip() if len(parts) > 1 else ""
            priority = parts[2].strip().upper() if len(parts) > 2 else "MID"
            if not key or not val or len(val) < 2:
                continue
            # value 길이 제한 — 기억은 간결해야 함 (토큰 절약)
            if len(val) > 80:
                val = val[:77] + "..."
            if priority == "LOW":
                continue

            # key 정규화 — 기존 키와 유사하면 기존 키 재사용
            original_key = key
            key = _normalize_key(key)
            if key != original_key:
                print(f"[extract] key normalized: '{original_key}' → '{key}'", flush=True)

            if priority == "HIGH":
                # 이미 동일 key+value가 LTM에 있으면 스킵
                existing = db.execute("SELECT value FROM ltm WHERE key = ?", (key,)).fetchone()
                if existing and existing[0] == val:
                    continue
                remember(key, val)
            else:  # MID
                # 이미 동일 key+value가 MTM 또는 LTM에 있으면 스킵
                existing_ltm = db.execute("SELECT value FROM ltm WHERE key = ?", (key,)).fetchone()
                if existing_ltm and existing_ltm[0] == val:
                    continue
                existing_mtm = db.execute("SELECT value FROM mtm_memory WHERE key = ?", (key,)).fetchone()
                if existing_mtm and existing_mtm[0] == val:
                    continue
                remember_mtm(key, val)
            saved.append((key, val, priority))

        return {"raw": raw, "saved": saved}
    except Exception as e:
        print(f"[extract_and_remember] ERROR: {e}", flush=True)
        return {"raw": "", "saved": []}


# ─── 자동 검색/주입 (server.py에서 매 메시지 호출) ───

def _cleanup_mtm_memory():
    """last_accessed가 7일 이전인 MTM 기억 삭제"""
    try:
        db = _get_db()
        cutoff = (datetime.now() - timedelta(days=MTM_MEMORY_TTL_DAYS)).strftime("%Y-%m-%d %H:%M")
        deleted = db.execute("DELETE FROM mtm_memory WHERE last_accessed < ?", (cutoff,)).rowcount
        if deleted > 0:
            db.commit()
            print(f"[cleanup_mtm_memory] {deleted}개 MTM 기억 삭제 (7일 미사용)", flush=True)
    except Exception:
        pass


def _promote_mtm_to_ltm(key: str, row):
    """MTM 기억을 LTM으로 승격"""
    try:
        remember(key, row["value"], row["category"])
        print(f"[promote] MTM→LTM: {key}={row['value'][:50]}", flush=True)
    except Exception:
        pass


def auto_retrieve(user_message: str) -> str:
    """
    사용자 메시지에 관련된 기억을 자동 검색하여 시스템 프롬프트에 주입.
    MTM + LTM 통합 검색. MTM access_count 추적 + LTM 승격.
    """
    parts = []
    q_emb = _get_embedding(user_message)

    try:
        db = _get_db()

        # MTM 만료 정리 (lazy cleanup)
        _cleanup_mtm_memory()

        # ① LTM 전체 조회 (시간순 오름차순 — 오래된 것부터)
        all_ltm = db.execute(
            "SELECT key, value, category, embedding, COALESCE(updated, created) as ts FROM ltm ORDER BY COALESCE(updated, created) ASC"
        ).fetchall()

        # ② MTM 기억 조회
        all_mtm_mem = db.execute(
            "SELECT key, value, category, embedding, created, last_accessed, access_count FROM mtm_memory ORDER BY created ASC"
        ).fetchall()

        # LTM + MTM 합산 개수로 전체주입 여부 판단
        total_count = len(all_ltm) + len(all_mtm_mem)

        if total_count <= 100:
            # 항목이 적으면 전부 주입
            lines = []
            if all_ltm:
                lines += [f"- {r['key']}: {r['value'][:80]} [{r['ts'] or ''}]" for r in all_ltm]
            if all_mtm_mem:
                lines += [f"- {r['key']}: {r['value'][:80]} [{r['created'] or ''}]" for r in all_mtm_mem]
                # MTM 항목이 주입되었으므로 access_count++ 및 승격 체크
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                for r in all_mtm_mem:
                    new_count = r["access_count"] + 1
                    if new_count >= LTM_PROMOTE_COUNT:
                        _promote_mtm_to_ltm(r["key"], r)
                    else:
                        db.execute(
                            "UPDATE mtm_memory SET access_count = ?, last_accessed = ? WHERE key = ?",
                            (new_count, now, r["key"])
                        )
                db.commit()
            if lines:
                parts.append("📌 저장된 기억 (⏱ 오래된→최신 순, 같은 주제면 최신 정보를 우선):\n" + "\n".join(lines))
        else:
            # 항목이 많으면 시맨틱 매칭으로 필터링
            if q_emb:
                hits = []
                for r in all_ltm:
                    if r["embedding"]:
                        sim = _cosine_similarity(q_emb, r["embedding"])
                        if sim >= SIMILARITY_THRESHOLD:
                            hits.append((sim, r, "ltm"))
                    elif any(w in r["key"] or w in r["value"]
                             for w in user_message.split() if len(w) >= 2):
                        hits.append((0.5, r, "ltm"))
                for r in all_mtm_mem:
                    if r["embedding"]:
                        sim = _cosine_similarity(q_emb, r["embedding"])
                        if sim >= SIMILARITY_THRESHOLD:
                            hits.append((sim, r, "mtm"))
                    elif any(w in r["key"] or w in r["value"]
                             for w in user_message.split() if len(w) >= 2):
                        hits.append((0.5, r, "mtm"))
                if hits:
                    hits.sort(key=lambda x: x[0], reverse=True)
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    lines = []
                    for _, r, src in hits[:10]:
                        lines.append(f"- {r['key']}: {r['value'][:80]}")
                        if src == "mtm":
                            new_count = r["access_count"] + 1
                            if new_count >= LTM_PROMOTE_COUNT:
                                _promote_mtm_to_ltm(r["key"], r)
                            else:
                                db.execute(
                                    "UPDATE mtm_memory SET access_count = ?, last_accessed = ? WHERE key = ?",
                                    (new_count, now, r["key"])
                                )
                    db.commit()
                    parts.append("🧠 관련 기억:\n" + "\n".join(lines))

        # ③ 새 세션이면 최근 MTM 요약 주입
        global _last_activity
        gap = time.time() - _last_activity if _last_activity > 0 else 0
        if gap > SESSION_GAP_MINUTES * 60:
            recent = db.execute(
                "SELECT summary, created FROM mtm ORDER BY created DESC LIMIT 1"
            ).fetchone()
            if recent:
                parts.append(f"📋 이전 대화 ({recent['created'][:16]}):\n{recent['summary'][:200]}")

    except Exception:
        pass

    # 스킬 검색 (기존 유지)
    try:
        db = _get_db()
        skills = db.execute("SELECT name, method, embedding FROM skills").fetchall()
        if skills and q_emb:
            scored = []
            for s in skills:
                if s["embedding"]:
                    sim = _cosine_similarity(q_emb, s["embedding"])
                    if sim >= SIMILARITY_THRESHOLD:
                        scored.append((sim, s))
            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                lines = [f"- {s['name']}: {s['method'][:200]}" for _, s in scored[:2]]
                parts.append("[관련 스킬]\n" + "\n".join(lines))
    except Exception:
        pass

    return "\n\n".join(parts)
