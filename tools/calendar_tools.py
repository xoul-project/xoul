"""
로컬 캘린더 시스템 — JSON 기반 일정 관리

저장소: /root/.xoul/calendar/events.json
"""

import json
import uuid
from datetime import datetime, timedelta
from .system_tools import tool_run_command


CALENDAR_DIR = "/root/.xoul/calendar"
EVENTS_FILE = f"{CALENDAR_DIR}/events.json"


def _ensure_dir():
    tool_run_command(f"mkdir -p {CALENDAR_DIR}")


def _load_events() -> list:
    raw = tool_run_command(f"cat {EVENTS_FILE} 2>/dev/null || echo '[]'")
    try:
        result = json.loads(raw.strip())
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"[calendar] _load_events parse error: {e}, raw={raw[:100]!r}")
        return []


def _save_events(events: list):
    data = json.dumps(events, ensure_ascii=False, indent=2)
    tool_run_command(f"cat > {EVENTS_FILE} << 'CAL_EOF'\n{data}\nCAL_EOF")


def _resolve_date(date_str: str) -> str:
    """상대 날짜 표현을 실제 날짜로 변환 + 비현실적 날짜 보정"""
    today = datetime.now().date()
    
    # 한국어 상대 날짜 처리
    relative_map = {
        "오늘": 0, "today": 0,
        "내일": 1, "tomorrow": 1,
        "모레": 2, "내일모레": 2,
        "글피": 3,
        "어제": -1, "yesterday": -1,
        "그제": -2, "그저께": -2,
    }
    
    clean = date_str.strip()
    if clean in relative_map:
        result = today + timedelta(days=relative_map[clean])
        return result.strftime("%Y-%m-%d")
    
    # 요일 처리 (이번주/다음주 + 요일)
    weekday_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
    for day_name, day_num in weekday_map.items():
        if day_name in clean and ("요일" in clean or len(clean) <= 5):
            # 이번 주 해당 요일
            days_ahead = day_num - today.weekday()
            if "다음" in clean or "다음주" in clean:
                days_ahead += 7
            elif days_ahead <= 0:
                days_ahead += 7
            result = today + timedelta(days=days_ahead)
            return result.strftime("%Y-%m-%d")
    
    # YYYY-MM-DD 형식 검증
    try:
        parsed = datetime.strptime(clean, "%Y-%m-%d").date()
        # 현재 연도와 3년 이상 차이나면 → LLM 환각으로 판단, 올해로 보정
        if abs(parsed.year - today.year) >= 3:
            parsed = parsed.replace(year=today.year)
            # 이미 지난 날짜면 다음해로
            if parsed < today:
                parsed = parsed.replace(year=today.year + 1)
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        pass
    
    # 파싱 실패 → 오늘 날짜 사용
    return today.strftime("%Y-%m-%d")


def create_event(title: str, date: str, time: str = "", duration: str = "60", description: str = "") -> str:
    """
    일정을 생성합니다.

    Args:
        title: 일정 제목 (예: "치과 예약")
        date: 날짜 (예: "2026-02-20", "오늘", "내일", "금요일")
        time: 시간 (예: "14:00", 생략 시 종일 일정)
        duration: 소요 시간(분), 기본 60분
        description: 설명 (선택)

    Returns:
        생성 결과
    """
    _ensure_dir()
    events = _load_events()

    # 날짜 자동 변환 (상대 날짜 → 절대 날짜, 환각 보정)
    resolved_date = _resolve_date(date)

    evt_id = f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"

    event = {
        "id": evt_id,
        "title": title,
        "date": resolved_date,
        "time": time or "종일",
        "duration_min": int(duration) if time else 0,
        "description": description,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    events.append(event)
    _save_events(events)

    time_str = f" {time}" if time else " (종일)"
    return f"✅ 일정 생성: {resolved_date}{time_str} — {title} (ID: {evt_id})"


def list_events(date: str = "", days: int = 90) -> str:
    """
    일정을 조회합니다.

    Args:
        date: 시작 날짜 (기본: 오늘). 형식: "2026-02-18"
        days: 조회할 일수 (기본: 90일)

    Returns:
        일정 목록
    """
    events = _load_events()
    if not events:
        return "📅 등록된 일정이 없습니다."

    # 날짜 범위 계산
    if date:
        try:
            start = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return f"❌ 날짜 형식이 올바르지 않습니다: {date} (예: 2026-02-18)"
    else:
        start = datetime.now().date()

    end = start + timedelta(days=days)

    # 필터링
    filtered = []
    for e in events:
        try:
            evt_date = datetime.strptime(e["date"], "%Y-%m-%d").date()
            if start <= evt_date < end:
                filtered.append(e)
        except ValueError:
            continue

    if not filtered:
        if days == 1:
            return f"📅 {start.strftime('%Y-%m-%d')}: 일정 없음"
        else:
            return f"📅 {start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}: 일정 없음"

    # 날짜/시간순 정렬
    filtered.sort(key=lambda e: (e["date"], e.get("time", "99:99")))

    output = []
    for e in filtered:
        time_str = e.get("time", "종일")
        desc = f" — {e['description']}" if e.get("description") else ""
        output.append(f"- {e['date']} {time_str} | {e['title']}{desc} [ID:{e['id']}]")

    header = f"📅 일정 {len(filtered)}건"
    if days == 1:
        header += f" ({start.strftime('%Y-%m-%d')})"
    else:
        header += f" ({start.strftime('%m/%d')}~{end.strftime('%m/%d')})"

    return header + ":\n" + "\n".join(output)


def delete_event(event_id: str) -> str:
    """
    일정을 삭제합니다.

    Args:
        event_id: 삭제할 일정 ID

    Returns:
        삭제 결과
    """
    events = _load_events()
    before = len(events)
    events = [e for e in events if e.get("id") != event_id]

    if len(events) == before:
        return f"⚠ ID '{event_id}' 일정을 찾을 수 없습니다."

    _save_events(events)
    return f"✅ 일정 삭제됨 (ID: {event_id})"


def update_event(event_id: str, title: str = "", date: str = "", time: str = "", description: str = "") -> str:
    """
    일정을 수정합니다.

    Args:
        event_id: 수정할 일정 ID
        title: 새 제목 (빈 문자열이면 변경 안 함)
        date: 새 날짜
        time: 새 시간
        description: 새 설명

    Returns:
        수정 결과
    """
    events = _load_events()
    found = None
    for e in events:
        if e.get("id") == event_id:
            found = e
            break

    if not found:
        return f"⚠ ID '{event_id}' 일정을 찾을 수 없습니다."

    changes = []
    if title:
        found["title"] = title
        changes.append(f"제목→{title}")
    if date:
        found["date"] = date
        changes.append(f"날짜→{date}")
    if time:
        found["time"] = time
        changes.append(f"시간→{time}")
    if description:
        found["description"] = description
        changes.append(f"설명→{description}")

    if not changes:
        return "⚠ 변경할 내용이 없습니다."

    _save_events(events)
    return f"✅ 일정 수정됨: {', '.join(changes)} (ID: {event_id})"
