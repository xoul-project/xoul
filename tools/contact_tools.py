"""
로컬 연락처 시스템 — JSON 기반

저장소: /root/.xoul/contacts/contacts.json
"""

import json
from datetime import datetime
from .system_tools import tool_run_command


CONTACTS_DIR = "/root/.xoul/contacts"
CONTACTS_FILE = f"{CONTACTS_DIR}/contacts.json"


def _ensure_dir():
    tool_run_command(f"mkdir -p {CONTACTS_DIR}")


def _load_contacts() -> list:
    raw = tool_run_command(f"cat {CONTACTS_FILE} 2>/dev/null || echo '[]'")
    try:
        return json.loads(raw)
    except Exception:
        return []


def _save_contacts(contacts: list):
    data = json.dumps(contacts, ensure_ascii=False, indent=2)
    tool_run_command(f"cat > {CONTACTS_FILE} << 'CON_EOF'\n{data}\nCON_EOF")


def add_contact(name: str, phone: str = "", email: str = "", memo: str = "") -> str:
    """
    연락처를 추가합니다.

    Args:
        name: 이름 (필수)
        phone: 전화번호
        email: 이메일
        memo: 메모 (관계, 특이사항 등)

    Returns:
        추가 결과
    """
    _ensure_dir()
    contacts = _load_contacts()

    # 같은 이름 있으면 업데이트
    updated = False
    for c in contacts:
        if c.get("name") == name:
            if phone:
                c["phone"] = phone
            if email:
                c["email"] = email
            if memo:
                c["memo"] = memo
            c["updated"] = datetime.now().strftime("%Y-%m-%d")
            updated = True
            break

    if not updated:
        contacts.append({
            "name": name,
            "phone": phone,
            "email": email,
            "memo": memo,
            "created": datetime.now().strftime("%Y-%m-%d"),
        })

    _save_contacts(contacts)
    status = "업데이트" if updated else "추가"
    return f"✅ 연락처 {status}: {name}" + (f" ({phone})" if phone else "")


def find_contact(query: str) -> str:
    """
    연락처를 검색합니다. 이름, 전화번호, 메모에서 검색합니다.

    Args:
        query: 검색어 (이름, 번호, 키워드)

    Returns:
        검색 결과
    """
    contacts = _load_contacts()
    if not contacts:
        return "📇 등록된 연락처가 없습니다."

    q = query.lower()
    results = []
    for c in contacts:
        if (q in c.get("name", "").lower() or
            q in c.get("phone", "") or
            q in c.get("email", "").lower() or
            q in c.get("memo", "").lower()):
            results.append(c)

    if not results:
        return f"📇 '{query}' 관련 연락처가 없습니다."

    output = []
    for c in results:
        parts = [f"📇 {c['name']}"]
        if c.get("phone"):
            parts.append(f"  📱 {c['phone']}")
        if c.get("email"):
            parts.append(f"  📧 {c['email']}")
        if c.get("memo"):
            parts.append(f"  📝 {c['memo']}")
        output.append("\n".join(parts))

    return "\n\n".join(output)


def list_contacts() -> str:
    """
    전체 연락처 목록을 봅니다.

    Returns:
        연락처 목록
    """
    contacts = _load_contacts()
    if not contacts:
        return "📇 등록된 연락처가 없습니다."

    output = []
    for c in sorted(contacts, key=lambda x: x.get("name", "")):
        phone = f" ({c['phone']})" if c.get("phone") else ""
        output.append(f"- {c['name']}{phone}")

    return f"📇 연락처 {len(contacts)}명:\n" + "\n".join(output)


def delete_contact(name: str) -> str:
    """
    연락처를 삭제합니다.

    Args:
        name: 삭제할 연락처 이름

    Returns:
        삭제 결과
    """
    contacts = _load_contacts()
    before = len(contacts)
    contacts = [c for c in contacts if c.get("name") != name]

    if len(contacts) == before:
        return f"⚠ '{name}' 연락처를 찾을 수 없습니다."

    _save_contacts(contacts)
    return f"✅ '{name}' 연락처 삭제됨"
