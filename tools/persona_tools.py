"""
Persona 시스템 — 대화형 AI 역할 관리

페르소나를 활성화하면 시스템 프롬프트에 주입되어
AI가 해당 역할로 대화합니다.

저장소: /root/.xoul/workflows.db (personas 테이블)
"""

import json
import sqlite3
from datetime import datetime
from i18n import t as _t


# ─────────────────────────────────────────────
# DB 헬퍼 (workflows.db 공유)
# ─────────────────────────────────────────────

PERSONA_DB_PATH = "/root/.xoul/workflows.db"
_persona_db_conn = None


def _get_db():
    """personas 테이블 보장"""
    global _persona_db_conn
    if _persona_db_conn is not None:
        try:
            _persona_db_conn.execute("SELECT 1")
        except Exception:
            _persona_db_conn = None
    if _persona_db_conn is None:
        import os
        from .system_tools import tool_run_command
        tool_run_command("mkdir -p /root/.xoul")
        _persona_db_conn = sqlite3.connect(PERSONA_DB_PATH, check_same_thread=False, timeout=10)
        _persona_db_conn.row_factory = sqlite3.Row
        _persona_db_conn.execute("PRAGMA journal_mode=WAL")

        _persona_db_conn.execute("""
            CREATE TABLE IF NOT EXISTS personas (
                name        TEXT PRIMARY KEY,
                description TEXT DEFAULT '',
                prompt      TEXT DEFAULT '',
                bg_image    TEXT DEFAULT '',
                embedding   BLOB,
                activated   INTEGER DEFAULT 0,
                created     TEXT
            )
        """)
        _persona_db_conn.commit()
        cnt = _persona_db_conn.execute("SELECT COUNT(*) FROM personas").fetchone()[0]
        print(f"[PERSONA_DB] Ready, {cnt} personas", flush=True)

    return _persona_db_conn


def _get_embed(text: str):
    """임베딩 생성 (memory_tools 재사용)"""
    from .memory_tools import _get_embedding
    return _get_embedding(text)


def _cosine_sim(a, b):
    """코사인 유사도"""
    from .memory_tools import _cosine_similarity
    return _cosine_similarity(a, b)


# ─────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────

def create_persona(name: str, prompt: str, description: str = "",
                   bg_image: str = "") -> str:
    """
    페르소나를 생성합니다.

    Args:
        name: 페르소나 이름 (예: "블로그 전문가")
        prompt: 시스템 프롬프트 (AI 역할 정의)
        description: 페르소나 설명
        bg_image: 배경 이미지 URL (선택)

    Returns:
        생성 결과
    """
    if not name or not name.strip():
        return "⚠ 페르소나 이름을 입력하세요."
    if not prompt or not prompt.strip():
        return "⚠ 프롬프트를 입력하세요."

    name = name.strip()
    db = _get_db()

    existing = db.execute("SELECT name FROM personas WHERE name = ?", (name,)).fetchone()
    if existing:
        return f"⚠ '{name}' 페르소나가 이미 존재합니다."

    # 임베딩
    embed_text = f"{name} {description}" if description else name
    try:
        embedding = _get_embed(embed_text)
    except Exception:
        embedding = None

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.execute("""
        INSERT INTO personas (name, description, prompt, bg_image, embedding, activated, created)
        VALUES (?, ?, ?, ?, ?, 0, ?)
    """, (name, description or f"{name} persona", prompt, bg_image, embedding, now))
    db.commit()

    return f"✅ '{name}' 페르소나 생성 완료!"


def list_personas(page: str = "1", per_page: str = "10") -> str:
    """
    저장된 페르소나 목록을 조회합니다.

    Args:
        page: 페이지 번호 (기본 1)
        per_page: 페이지당 항목 수 (기본 10)

    Returns:
        페르소나 목록
    """
    db = _get_db()
    total = db.execute("SELECT COUNT(*) FROM personas").fetchone()[0]
    if total == 0:
        return "📋 저장된 페르소나가 없습니다."

    try:
        p = max(1, int(page))
        pp = max(1, min(50, int(per_page)))
    except (ValueError, TypeError):
        p, pp = 1, 10

    offset = (p - 1) * pp
    rows = db.execute(
        "SELECT name, description, activated, created FROM personas ORDER BY created DESC LIMIT ? OFFSET ?",
        (pp, offset)
    ).fetchall()

    lines = [_t("persona.list_header", total=total)]
    page_items = []
    for i, r in enumerate(rows, offset + 1):
        desc = (r["description"] or "")[:50]
        name = r["name"]
        activated = r["activated"] or 0
        lines.append(f"  {i}. {name}")
        if desc:
            lines.append(f"     {desc}")
        if activated > 0:
            lines.append(_t("persona.activated_count", count=activated))
        page_items.append({
            "name": name,
            "description": desc,
            "activated": activated,
        })

    if total > offset + pp:
        pages = (total + pp - 1) // pp
        lines.append(_t("persona.page_info", pg=p, pages=pages))

    # Desktop UI용 base64 데이터
    import base64
    persona_payload = {
        "_meta": {"page": p, "total_pages": (total + pp - 1) // pp, "total": total},
        "items": page_items
    }
    data_json = json.dumps(persona_payload, ensure_ascii=False)
    data_b64 = base64.b64encode(data_json.encode()).decode()
    lines.append(f"\n<!--PERSONA_DATA:{data_b64}-->")
    lines.append("\n[이 결과를 사용자에게 그대로 전달하세요. 추가 도구 호출 불필요.]")

    return "\n".join(lines)


def activate_persona(name: str) -> str:
    """
    페르소나를 활성화합니다.

    Args:
        name: 활성화할 페르소나 이름

    Returns:
        활성화 결과 (JSON 마커)
    """
    if not name or not name.strip():
        return "⚠ 페르소나 이름을 입력하세요."

    name = name.strip()
    db = _get_db()

    # 1. 정확 매칭
    row = db.execute("SELECT * FROM personas WHERE name = ?", (name,)).fetchone()

    # 2. 부분 매칭
    if not row:
        row = db.execute(
            "SELECT * FROM personas WHERE name LIKE ?", (f"%{name}%",)
        ).fetchone()

    # 3. 임베딩 유사도
    if not row:
        candidates = _fuzzy_find_persona(name)
        if len(candidates) == 1:
            row = db.execute(
                "SELECT * FROM personas WHERE name = ?", (candidates[0][0],)
            ).fetchone()
        elif len(candidates) > 1:
            lines = [f"🎭 '{name}'과(와) 유사한 페르소나가 {len(candidates)}개 있습니다:\n"]
            for i, (cname, score) in enumerate(candidates, 1):
                lines.append(_t("persona.similarity", i=i, name=cname, score=f"{score:.0%}"))
            lines.append("\n번호를 입력하면 해당 페르소나를 활성화합니다.")
            return "\n".join(lines)

    if not row:
        return f"⚠ '{name}' 페르소나를 찾을 수 없습니다. list_personas로 목록을 확인하세요."

    # 활성화 기록
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.execute(
        "UPDATE personas SET activated = activated + 1 WHERE name = ?",
        (row["name"],)
    )
    db.commit()

    bg_image = row["bg_image"] or ""

    # __persona_activate__ 마커 → server.py가 감지
    return json.dumps({
        "__persona_activate__": True,
        "name": row["name"],
        "prompt": row["prompt"],
        "description": row["description"] or "",
        "bg_image": bg_image,
        "icon": "🎭",
    }, ensure_ascii=False)


def update_persona(name: str, prompt: str = "", description: str = "",
                   new_name: str = "") -> str:
    """
    페르소나를 수정합니다.

    Args:
        name: 수정할 페르소나 이름
        prompt: 새 시스템 프롬프트 (빈 문자열이면 유지)
        description: 새 설명 (빈 문자열이면 유지)
        new_name: 새 이름 (빈 문자열이면 유지)

    Returns:
        수정 결과
    """
    if not name or not name.strip():
        return "⚠ 페르소나 이름을 입력하세요."

    db = _get_db()
    row = db.execute("SELECT * FROM personas WHERE name = ?", (name.strip(),)).fetchone()
    if not row:
        return f"⚠ '{name}' 페르소나를 찾을 수 없습니다."

    updates = []
    params = []
    if prompt:
        updates.append("prompt = ?")
        params.append(prompt)
    if description:
        updates.append("description = ?")
        params.append(description)
    if new_name and new_name.strip() != name.strip():
        updates.append("name = ?")
        params.append(new_name.strip())

    if not updates:
        return "⚠ 수정할 내용이 없습니다."

    # 임베딩 갱신
    embed_text = f"{new_name or name} {description or row['description']}"
    try:
        embedding = _get_embed(embed_text)
        updates.append("embedding = ?")
        params.append(embedding)
    except Exception:
        pass

    params.append(name.strip())
    db.execute(f"UPDATE personas SET {', '.join(updates)} WHERE name = ?", params)
    db.commit()

    display_name = new_name.strip() if new_name else name.strip()
    return f"✅ '{display_name}' 페르소나가 수정되었습니다."


def delete_persona(name: str) -> str:
    """
    페르소나를 삭제합니다.

    Args:
        name: 삭제할 페르소나 이름

    Returns:
        삭제 결과
    """
    if not name or not name.strip():
        return "⚠ 페르소나 이름을 입력하세요."

    db = _get_db()
    row = db.execute("SELECT name FROM personas WHERE name = ?", (name.strip(),)).fetchone()
    if not row:
        return f"⚠ '{name}' 페르소나를 찾을 수 없습니다."

    db.execute("DELETE FROM personas WHERE name = ?", (name.strip(),))
    db.commit()
    return f"🗑 '{name}' 페르소나가 삭제되었습니다."


def _fuzzy_find_persona(query: str, threshold: float = 0.5):
    """임베딩 유사도로 페르소나 검색"""
    db = _get_db()
    try:
        q_embed = _get_embed(query)
    except Exception:
        return []

    rows = db.execute("SELECT name, embedding FROM personas WHERE embedding IS NOT NULL").fetchall()
    results = []
    for r in rows:
        if r["embedding"]:
            sim = _cosine_sim(q_embed, r["embedding"])
            if sim >= threshold:
                results.append((r["name"], sim))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:3]
