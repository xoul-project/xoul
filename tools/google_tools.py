"""
Google API 도구 - Gmail 발송/읽기, Calendar 일정 관리
"""
import sys
import os
import base64
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from i18n import t as _t

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from google_auth import build_service, get_credentials


def _check_auth():
    """인증 상태 확인"""
    creds = get_credentials()
    if not creds:
        return "❌ Google 인증이 필요합니다. setup_env.ps1을 다시 실행하거나, python google_auth.py를 실행하세요."
    return None


# ─────────────────────────────────────────────
# Gmail 도구
# ─────────────────────────────────────────────

def send_email(to: str, subject: str, body: str) -> str:
    """Gmail로 이메일 발송"""
    err = _check_auth()
    if err:
        return err

    try:
        service = build_service("gmail", "v1")
        if not service:
            return "❌ Gmail 서비스 초기화 실패"

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        result = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        return f"✅ 메일 발송 완료 (ID: {result['id']})"
    except Exception as e:
        return f"❌ 메일 발송 실패: {e}"


def list_emails(query: str = "is:unread", max_results: int = 5) -> str:
    """Gmail 메일 검색/목록"""
    err = _check_auth()
    if err:
        return err

    try:
        service = build_service("gmail", "v1")
        if not service:
            return "❌ Gmail 서비스 초기화 실패"

        results = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return f"📭 '{query}' 검색 결과 없음"

        output = []
        for msg in messages:
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            snippet = detail.get("snippet", "")[:100]
            output.append(
                f"- [{headers.get('Date', '?')}] {headers.get('From', '?')}\n"
                f"  {_t('google.email_subject', subject=headers.get('Subject', _t('google.no_subject')))}\n"
                f"  미리보기: {snippet}"
            )

        return f"📬 메일 {len(messages)}건:\n\n" + "\n\n".join(output)
    except Exception as e:
        return f"❌ 메일 조회 실패: {e}"


def read_email(email_id: str) -> str:
    """Gmail 메일 본문 읽기"""
    err = _check_auth()
    if err:
        return err

    try:
        service = build_service("gmail", "v1")
        if not service:
            return "❌ Gmail 서비스 초기화 실패"

        detail = service.users().messages().get(
            userId="me", id=email_id, format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}

        # 본문 추출
        body = ""
        payload = detail.get("payload", {})
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    break
        else:
            data = payload.get("body", {}).get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        return (
            f"📧 메일 상세\n"
            f"보낸이: {headers.get('From', '?')}\n"
            f"제목: {headers.get('Subject', '?')}\n"
            f"날짜: {headers.get('Date', '?')}\n"
            f"\n{body[:3000]}"
        )
    except Exception as e:
        return f"❌ 메일 읽기 실패: {e}"


# ─────────────────────────────────────────────
# Calendar 도구
# ─────────────────────────────────────────────

def list_events(date: str = "", days: int = 1) -> str:
    """Google Calendar 일정 조회 (기본: 오늘)"""
    err = _check_auth()
    if err:
        return err

    try:
        service = build_service("calendar", "v3")
        if not service:
            return "❌ Calendar 서비스 초기화 실패"

        if date:
            start = datetime.strptime(date, "%Y-%m-%d")
        else:
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        end = start + timedelta(days=days)

        events_result = service.events().list(
            calendarId="primary",
            timeMin=start.isoformat() + "Z",
            timeMax=end.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return f"📅 {start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}: 일정 없음"

        output = []
        for e in events:
            s = e["start"].get("dateTime", e["start"].get("date", "?"))
            if "T" in s:
                s = s[11:16]  # HH:MM만
            output.append(f"- {s} {e.get('summary', _t('google.no_title'))}")

        return f"📅 일정 {len(events)}건:\n" + "\n".join(output)
    except Exception as e:
        return f"❌ 일정 조회 실패: {e}"


def create_event(title: str, start_time: str, end_time: str = "", description: str = "") -> str:
    """Google Calendar 일정 생성
    start_time: "2024-01-15T14:00:00" 또는 "2024-01-15" (종일)
    """
    err = _check_auth()
    if err:
        return err

    try:
        service = build_service("calendar", "v3")
        if not service:
            return "❌ Calendar 서비스 초기화 실패"

        # 종일 일정 vs 시간 지정
        if "T" in start_time:
            event = {
                "summary": title,
                "start": {"dateTime": start_time, "timeZone": "Asia/Seoul"},
                "end": {
                    "dateTime": end_time or (datetime.fromisoformat(start_time) + timedelta(hours=1)).isoformat(),
                    "timeZone": "Asia/Seoul",
                },
            }
        else:
            event = {
                "summary": title,
                "start": {"date": start_time},
                "end": {"date": end_time or start_time},
            }

        if description:
            event["description"] = description

        result = service.events().insert(calendarId="primary", body=event).execute()
        return f"✅ 일정 생성: {title} ({result.get('htmlLink', '')})"
    except Exception as e:
        return f"❌ 일정 생성 실패: {e}"
