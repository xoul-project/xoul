"""
Xoul Discord 클라이언트
- 양방향 대화: Discord 메시지 → Xoul API → 응답 → Discord
- 알림 전송: send_notification() 함수 제공
- DM 및 채널 멘션 지원

의존성: pip install discord.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

try:
    import discord
    from discord import Intents
except ImportError:
    print("❌ discord.py가 설치되지 않았습니다. pip install discord.py")
    sys.exit(1)

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

    def chat(self, channel_id: int, message: str) -> str:
        """Xoul API에 메시지 전송하고 최종 응답 반환"""
        session_id = self.sessions.get(channel_id, f"discord_{channel_id}")

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
    """Discord로 알림 메시지 전송"""
    try:
        config = _load_config()
        dc = config.get("clients", {}).get("discord", {})

        if not dc.get("enabled"):
            return "❌ Discord가 비활성화되어 있습니다."

        token = dc.get("bot_token", "")
        channel_id = dc.get("channel_id", "")

        if not token or not channel_id:
            return "❌ Discord 설정이 완료되지 않았습니다. (bot_token 또는 channel_id 누락)"

        text = f"🔔 **{title}**\n\n{message}" if title else f"🔔 {message}"

        # Discord REST API로 메시지 전송
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        # 2000자 제한
        if len(text) > 1900:
            text = text[:1900] + "..."
        data = json.dumps({"content": text}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bot {token}"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return "✅ Discord로 알림을 전송했습니다."

    except Exception as e:
        return f"❌ 알림 전송 실패: {e}"


# ─── 메인 봇 ───

def run_bot(config: dict):
    """Discord 봇 시작"""
    dc_cfg = config.get("clients", {}).get("discord", {})
    srv_cfg = config.get("server", {})

    token = dc_cfg.get("bot_token", "")
    allowed_channel_id = dc_cfg.get("channel_id", "")

    if not token:
        print("❌ Discord bot_token이 설정되지 않았습니다.")
        sys.exit(1)

    xoul = XoulsClient(
        base_url=f"http://127.0.0.1:{srv_cfg.get('port', 3000)}",
        api_key=srv_cfg.get("api_key", "")
    )

    intents = Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"🤖 Discord 봇 시작: {client.user.name}#{client.user.discriminator}")
        print(f"📨 메시지 대기 중...")
        # channel_id 미설정 시 자동 감지 안내
        if not allowed_channel_id:
            print("  ℹ channel_id 미설정: DM으로만 응답합니다.")
            print("  채널에서 사용하려면 config.json에 channel_id를 설정하세요.")

    @client.event
    async def on_message(msg):
        # 봇 자신의 메시지 무시
        if msg.author == client.user:
            return

        # DM 처리
        is_dm = isinstance(msg.channel, discord.DMChannel)

        # 채널 메시지: 멘션된 경우만 응답
        is_mention = client.user in msg.mentions if not is_dm else False

        # 허가된 채널 확인
        is_allowed_channel = (
            str(msg.channel.id) == str(allowed_channel_id) if allowed_channel_id else False
        )

        # 응답 조건: DM이거나, 허가된 채널에서 멘션
        if not is_dm and not (is_allowed_channel and is_mention):
            return

        # 멘션 텍스트 정리
        text = msg.content
        for mention in msg.mentions:
            text = text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        text = text.strip()

        if not text:
            return

        user_name = msg.author.display_name
        print(f"  📩 [{user_name}] {text[:50]}")

        # 처리 중 표시
        async with msg.channel.typing():
            response = xoul.chat(msg.channel.id, text)

        # Discord 메시지 길이 제한: 2000자
        if len(response) > 1900:
            chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
            for chunk in chunks:
                await msg.channel.send(chunk)
        else:
            await msg.channel.send(response)

        print(f"  📤 응답 전송 완료")

    client.run(token, log_handler=None)


# ─── 엔트리포인트 ───

if __name__ == "__main__":
    config = _load_config()
    run_bot(config)
