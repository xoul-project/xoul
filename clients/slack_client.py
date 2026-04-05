"""
Xoul Slack 클라이언트
- 양방향 대화: Slack 메시지 → Xoul API → 응답 → Slack
- 알림 전송: send_notification() 함수 제공
- Socket Mode (WebSocket) — 별도 서버 불필요

의존성: pip install slack-bolt slack-sdk
"""
import json
import os
import sys
import urllib.request
import urllib.error

# ─── 설정 ───
CONFIG_PATH = os.environ.get("XOUL_CONFIG", "/root/xoul/config.json")


def _load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def _save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


# ─── Xoul API 클라이언트 ───

class XoulsClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.sessions = {}  # channel_id → session_id

    def chat(self, channel_id: str, message: str) -> str:
        """Xoul API에 메시지 전송하고 최종 응답 반환"""
        session_id = self.sessions.get(channel_id, f"slack_{channel_id}")

        url = f"{self.base_url}/chat/stream"
        payload = {
            "message": message,
            "session_id": session_id
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
        )

        final_content = ""

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for line in resp:
                    line = line.decode("utf-8").strip()
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        evt = json.loads(line[6:])
                        evt_type = evt.get("type", "")

                        if evt_type == "final":
                            final_content = evt.get("content", "")
                            if evt.get("session_id"):
                                self.sessions[channel_id] = evt["session_id"]
                        elif evt_type == "error":
                            final_content = f"❌ {evt.get('content', '오류 발생')}"
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            return f"❌ 연결 오류: {e}"

        return final_content or "⚠ 응답을 받지 못했습니다."


# ─── 알림 전송 함수 (다른 모듈에서 import하여 사용) ───

def send_notification(message: str, title: str = "") -> str:
    """Slack으로 알림 메시지 전송"""
    try:
        config = _load_config()
        sl = config.get("clients", {}).get("slack", {})

        if not sl.get("enabled"):
            return "❌ Slack이 비활성화되어 있습니다."

        bot_token = sl.get("bot_token", "")
        channel_id = sl.get("channel_id", "")

        if not bot_token or not channel_id:
            return "❌ Slack 설정이 완료되지 않았습니다. (bot_token 또는 channel_id 누락)"

        text = f"🔔 *{title}*\n\n{message}" if title else f"🔔 {message}"

        # Slack Web API로 메시지 전송
        url = "https://slack.com/api/chat.postMessage"
        data = json.dumps({
            "channel": channel_id,
            "text": text,
            "mrkdwn": True
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {bot_token}"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if not result.get("ok"):
                return f"❌ Slack 전송 실패: {result.get('error', 'unknown')}"
        return "✅ Slack으로 알림을 전송했습니다."

    except Exception as e:
        return f"❌ 알림 전송 실패: {e}"


# ─── 메인 봇 ───

def run_bot(config: dict):
    """Slack 봇 시작 (Socket Mode)"""
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        print("❌ slack-bolt가 설치되지 않았습니다. pip install slack-bolt slack-sdk")
        sys.exit(1)

    sl_cfg = config.get("clients", {}).get("slack", {})
    srv_cfg = config.get("server", {})

    bot_token = sl_cfg.get("bot_token", "")
    app_token = sl_cfg.get("app_token", "")
    allowed_channel = sl_cfg.get("channel_id", "")

    if not bot_token:
        print("❌ Slack bot_token이 설정되지 않았습니다.")
        sys.exit(1)
    if not app_token:
        print("❌ Slack app_token이 설정되지 않았습니다. (Socket Mode에 필요)")
        sys.exit(1)

    xoul = XoulsClient(
        base_url=f"http://127.0.0.1:{srv_cfg.get('port', 3000)}",
        api_key=srv_cfg.get("api_key", "")
    )

    app = App(token=bot_token)

    @app.event("app_mention")
    def handle_mention(event, say):
        """채널에서 @멘션된 메시지 처리"""
        channel_id = event.get("channel", "")
        text = event.get("text", "")
        user = event.get("user", "")

        # 멘션 텍스트 정리 (<@BOT_ID> 제거)
        import re
        text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if not text:
            return

        # 허가된 채널 확인
        if allowed_channel and channel_id != allowed_channel:
            say("⛔ 이 채널에서는 사용할 수 없습니다.")
            return

        print(f"  📩 [<@{user}>] {text[:50]}")
        response = xoul.chat(channel_id, text)

        # Slack 메시지 길이 제한: ~40000자 (실질적으로 무제한)
        say(response)
        print(f"  📤 응답 전송 완료")

    @app.event("message")
    def handle_dm(event, say):
        """DM 메시지 처리"""
        # DM만 처리 (channel_type == 'im')
        if event.get("channel_type") != "im":
            return
        # 봇 자신의 메시지 무시
        if event.get("bot_id") or event.get("subtype"):
            return

        text = event.get("text", "").strip()
        channel_id = event.get("channel", "")
        user = event.get("user", "")

        if not text:
            return

        print(f"  📩 DM [<@{user}>] {text[:50]}")
        response = xoul.chat(channel_id, text)
        say(response)
        print(f"  📤 DM 응답 전송 완료")

    # Socket Mode 시작
    print(f"🤖 Slack 봇 시작 (Socket Mode)")
    print(f"📨 메시지 대기 중...")
    if not allowed_channel:
        print("  ℹ channel_id 미설정: DM으로만 응답합니다.")

    handler = SocketModeHandler(app, app_token)
    handler.start()


# ─── 엔트리포인트 ───

if __name__ == "__main__":
    config = _load_config()
    run_bot(config)
