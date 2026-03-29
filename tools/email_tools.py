"""
이메일 도구 - IMAP/SMTP + Gmail App Password

Gmail App Password 기반으로 메일을 보내고/읽고/검색합니다.
OAuth 없이 16자리 앱 비밀번호만 있으면 됩니다.

설정: config.json의 email 섹션
"""

import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from datetime import datetime, timedelta
import json
import os


def _get_email_config():
    """config.json에서 이메일 설정 로드"""
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    try:
        with open(config_path, encoding="utf-8-sig") as f:
            config = json.load(f)
        em = config.get("email", {})
        if not em.get("enabled"):
            return None, "❌ 이메일이 설정되지 않았습니다. setup_env.ps1을 실행하세요."
        if not em.get("app_password"):
            return None, "❌ App Password가 설정되지 않았습니다."
        return em, None
    except Exception as e:
        return None, f"❌ 설정 파일 로드 실패: {e}"


def _decode_header_value(value):
    """이메일 헤더 디코딩"""
    if not value:
        return ""
    decoded = decode_header(value)
    parts = []
    for text, charset in decoded:
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


def send_email(to: str, subject: str, body: str, attachments: list = None) -> str:
    """
    이메일을 발송합니다.

    Args:
        to: 받는 사람 이메일 주소
        subject: 제목
        body: 본문
        attachments: 첨부파일 경로 리스트 (선택)

    Returns:
        발송 결과 메시지
    """
    cfg, err = _get_email_config()
    if err:
        return err

    try:
        msg = MIMEMultipart()
        msg["From"] = cfg["address"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # 첨부파일 처리
        attached_names = []
        if attachments:
            import mimetypes
            from email.mime.base import MIMEBase
            from email import encoders

            if isinstance(attachments, str):
                attachments = [attachments]

            for filepath in attachments:
                filepath = filepath.strip()
                if not os.path.isfile(filepath):
                    return f"❌ 첨부파일을 찾을 수 없습니다: {filepath}"

                filename = os.path.basename(filepath)
                mime_type, _ = mimetypes.guess_type(filepath)
                if mime_type is None:
                    mime_type = "application/octet-stream"
                main_type, sub_type = mime_type.split("/", 1)

                with open(filepath, "rb") as f:
                    file_data = f.read()
                # 텍스트 파일에 UTF-8 BOM 추가 (이메일 클라이언트 한글 인코딩 감지용)
                text_exts = ('.txt', '.md', '.csv', '.log', '.json')
                if any(filepath.lower().endswith(ext) for ext in text_exts):
                    if not file_data.startswith(b'\xef\xbb\xbf'):
                        file_data = b'\xef\xbb\xbf' + file_data
                    part = MIMEBase(main_type, sub_type)
                    part.set_payload(file_data)
                else:
                    part = MIMEBase(main_type, sub_type)
                    part.set_payload(file_data)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment", filename=filename)
                msg.attach(part)
                attached_names.append(filename)

        with smtplib.SMTP(cfg.get("smtp_host", "smtp.gmail.com"), 587) as server:
            server.starttls()
            server.login(cfg["address"], cfg["app_password"])
            server.send_message(msg)

        result = f"✅ 메일 발송 완료: {to} (제목: {subject})"
        if attached_names:
            result += f"\n   📎 첨부: {', '.join(attached_names)}"
        return result
    except Exception as e:
        return f"❌ 메일 발송 실패: {e}"


def list_emails(query: str = "UNSEEN", max_results: int = 5) -> str:
    """
    메일 목록을 조회합니다.

    Args:
        query: IMAP 검색 조건 (UNSEEN, ALL, FROM "xxx", SUBJECT "xxx" 등)
        max_results: 최대 결과 수

    Returns:
        메일 목록
    """
    cfg, err = _get_email_config()
    if err:
        return err

    try:
        with imaplib.IMAP4_SSL(cfg.get("imap_host", "imap.gmail.com"), 993) as mail:
            mail.login(cfg["address"], cfg["app_password"])
            mail.select("INBOX")

            # 검색
            status, data = mail.search(None, query)
            if status != "OK":
                return f"❌ 검색 실패: {status}"

            msg_ids = data[0].split()
            if not msg_ids:
                return f"📭 '{query}' 검색 결과 없음"

            # 최신 것부터
            msg_ids = msg_ids[-max_results:]
            msg_ids.reverse()

            output = []
            for mid in msg_ids:
                status, msg_data = mail.fetch(mid, "(RFC822)")
                if status != "OK":
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                from_addr = _decode_header_value(msg["From"])
                subject = _decode_header_value(msg["Subject"])
                date_str = msg["Date"] or "?"

                # 미리보기 (본문 첫 100자)
                preview = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                preview = payload.decode("utf-8", errors="replace")[:100]
                            break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        preview = payload.decode("utf-8", errors="replace")[:100]

                output.append(
                    f"- [ID:{mid.decode()}] {date_str}\n"
                    f"  보낸이: {from_addr}\n"
                    f"  제목: {subject}\n"
                    f"  미리보기: {preview.strip()}"
                )

            return f"📬 메일 {len(output)}건:\n\n" + "\n\n".join(output)
    except Exception as e:
        return f"❌ 메일 조회 실패: {e}"


def read_email(email_id: str) -> str:
    """
    메일 본문을 읽습니다.

    Args:
        email_id: 메일 ID (list_emails에서 확인)

    Returns:
        메일 상세 내용
    """
    cfg, err = _get_email_config()
    if err:
        return err

    try:
        with imaplib.IMAP4_SSL(cfg.get("imap_host", "imap.gmail.com"), 993) as mail:
            mail.login(cfg["address"], cfg["app_password"])
            mail.select("INBOX")

            status, msg_data = mail.fetch(email_id.encode(), "(RFC822)")
            if status != "OK":
                return f"❌ 메일 ID {email_id}를 찾을 수 없습니다."

            msg = email.message_from_bytes(msg_data[0][1])
            from_addr = _decode_header_value(msg["From"])
            subject = _decode_header_value(msg["Subject"])
            date_str = msg["Date"] or "?"

            # 본문 추출
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", errors="replace")
                        break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")

            return (
                f"📧 메일 상세\n"
                f"보낸이: {from_addr}\n"
                f"제목: {subject}\n"
                f"날짜: {date_str}\n"
                f"\n{body[:3000]}"
            )
    except Exception as e:
        return f"❌ 메일 읽기 실패: {e}"
