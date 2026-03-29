"""
Xoul 텔레그램 클라이언트
- 양방향 대화: 텔레그램 메시지 → Xoul API → 응답 → 텔레그램
- 알림 전송: send_notification() 함수 제공
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import threading

# ─── 설정 ───
CONFIG_PATH = os.environ.get("XOUL_CONFIG", "/root/xoul/config.json")

def _load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)

def _save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


# ─── Telegram Bot API 헬퍼 ───

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def _call(self, method: str, params: dict = None) -> dict:
        url = f"{self.base_url}/{method}"
        if params:
            data = json.dumps(params).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"}
            )
        else:
            req = urllib.request.Request(url)
        
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    
    def get_me(self) -> dict:
        return self._call("getMe")
    
    def get_updates(self, offset: int = 0, timeout: int = 30) -> list:
        result = self._call("getUpdates", {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"]
        })
        return result.get("result", [])
    
    def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown") -> dict:
        # 텔레그램 메시지 길이 제한: 4096자
        if len(text) > 4000:
            # 긴 메시지는 분할 전송
            chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
            result = None
            for chunk in chunks:
                result = self._call("sendMessage", {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": parse_mode
                })
            return result
        
        try:
            return self._call("sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode
            })
        except Exception:
            # Markdown 파싱 실패 시 plain text로 재시도
            return self._call("sendMessage", {
                "chat_id": chat_id,
                "text": text
            })

    def send_message_with_button(self, chat_id: int, text: str,
                                  button_text: str, callback_data: str,
                                  parse_mode: str = "Markdown") -> dict:
        """인라인 버튼이 포함된 메시지 전송"""
        reply_markup = {
            "inline_keyboard": [[
                {"text": button_text, "callback_data": callback_data}
            ]]
        }
        try:
            return self._call("sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup
            })
        except Exception:
            return self._call("sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup
            })


# ─── Xoul API 클라이언트 ───

class XouldClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.sessions = {}  # chat_id → session_id
    
    def chat(self, chat_id: int, message: str) -> str:
        """Xoul API에 메시지 전송하고 최종 응답 반환"""
        session_id = self.sessions.get(chat_id, f"telegram_{chat_id}")
        
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
        tool_results = []
        
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
                                self.sessions[chat_id] = evt["session_id"]
                        elif evt_type == "tool_result":
                            tool_results.append(f"🔧 {evt.get('tool', '')}")
                        elif evt_type == "error":
                            final_content = f"❌ {evt.get('content', '오류 발생')}"
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            return f"❌ 연결 오류: {e}"
        
        return final_content or "⚠ 응답을 받지 못했습니다."


# ─── 알림 전송 함수 (다른 모듈에서 import하여 사용) ───

def send_notification(message: str, title: str = "") -> str:
    """텔레그램으로 알림 메시지 전송"""
    try:
        config = _load_config()
        tg = config.get("clients", {}).get("telegram", {})
        
        if not tg.get("enabled"):
            return "❌ 텔레그램이 비활성화되어 있습니다."
        
        token = tg.get("bot_token", "")
        chat_id = tg.get("chat_id", "")
        
        if not token or not chat_id:
            return "❌ 텔레그램 설정이 완료되지 않았습니다. (bot_token 또는 chat_id 누락)"
        
        bot = TelegramBot(token)
        text = f"🔔 *{title}*\n\n{message}" if title else f"🔔 {message}"
        bot.send_message(int(chat_id), text)
        return f"✅ 텔레그램으로 알림을 전송했습니다."
    
    except Exception as e:
        return f"❌ 알림 전송 실패: {e}"


# ─── chat_id 자동 감지 ───

def detect_chat_id(token: str, timeout: int = 60) -> str:
    """봇에 메시지를 보내면 chat_id를 자동 감지"""
    bot = TelegramBot(token)
    info = bot.get_me()
    bot_name = info.get("result", {}).get("username", "봇")
    
    print(f"  텔레그램에서 @{bot_name} 에게 아무 메시지를 보내세요...")
    print(f"  ({timeout}초 대기)")
    
    start = time.time()
    offset = 0
    
    while time.time() - start < timeout:
        try:
            updates = bot.get_updates(offset=offset, timeout=10)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                name = msg.get("from", {}).get("first_name", "")
                
                if chat_id:
                    print(f"  ✅ chat_id: {chat_id} (사용자: {name})")
                    return str(chat_id)
        except Exception:
            time.sleep(2)
    
    return ""


# ─── 메인 폴링 루프 ───

def run_polling(config: dict):
    """텔레그램 메시지 폴링 → Xoul 처리 → 응답 전송"""
    tg_cfg = config.get("clients", {}).get("telegram", {})
    srv_cfg = config.get("server", {})
    
    token = tg_cfg.get("bot_token", "")
    allowed_chat_id = tg_cfg.get("chat_id", "")
    
    if not token:
        print("❌ bot_token이 설정되지 않았습니다.")
        sys.exit(1)
    
    bot = TelegramBot(token)
    xoul = XouldClient(
        base_url=f"http://127.0.0.1:{srv_cfg.get('port', 3000)}",
        api_key=srv_cfg.get("api_key", "")
    )
    
    # 봇 정보 확인
    try:
        info = bot.get_me()
        bot_name = info.get("result", {}).get("username", "Unknown")
        print(f"🤖 텔레그램 봇 시작: @{bot_name}")
    except Exception as e:
        print(f"❌ 봇 연결 실패: {e}")
        sys.exit(1)
    
    offset = 0
    print("📨 메시지 대기 중...")
    
    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout=30)
            
            for update in updates:
                offset = update["update_id"] + 1

                # ── callback_query 처리 (상세보기 버튼) ──
                callback = update.get("callback_query")
                if callback:
                    cb_data = callback.get("data", "")
                    cb_chat_id = callback.get("message", {}).get("chat", {}).get("id")
                    # answerCallbackQuery (버튼 로딩 해제)
                    try:
                        bot._call("answerCallbackQuery", {"callback_query_id": callback["id"]})
                    except Exception:
                        pass
                    if cb_data.startswith("wf_result:") and cb_chat_id:
                        try:
                            import sqlite3
                            notif_id = int(cb_data.split(":")[1])
                            db = sqlite3.connect(os.path.expanduser("~/.xoul/workflows.db"))
                            db.row_factory = sqlite3.Row
                            row = db.execute(
                                "SELECT wf_name, result, created FROM wf_notifications WHERE id = ?",
                                (notif_id,)
                            ).fetchone()
                            if row:
                                result_text = row["result"] or "(결과 없음)"
                                if len(result_text) > 3500:
                                    result_text = result_text[:3500] + "\n... (잘림)"
                                header = f"\U0001f4cb *{row['wf_name']}*\n\U0001f550 {row['created']}\n\n"
                                bot.send_message(cb_chat_id, header + result_text)
                                db.execute("UPDATE wf_notifications SET read = 1 WHERE id = ?", (notif_id,))
                                db.commit()
                            else:
                                bot.send_message(cb_chat_id, "\u26a0 결과를 찾을 수 없습니다.")
                            db.close()
                        except Exception as e:
                            print(f"  \u26a0 Callback error: {e}")
                    continue

                # ── 일반 메시지 처리 ──
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                user_name = msg.get("from", {}).get("first_name", "")
                
                if not text or not chat_id:
                    continue
                
                # 보안: 허가된 chat_id만 허용
                if allowed_chat_id and str(chat_id) != str(allowed_chat_id):
                    bot.send_message(chat_id, "\u26d4 인증되지 않은 사용자입니다.")
                    continue
                
                print(f"  \U0001f4e9 [{user_name}] {text[:50]}")
                
                # /start 커맨드 처리
                if text.strip() == "/start":
                    bot.send_message(chat_id,
                        "\U0001f44b 안녕하세요! Xoul 개인 비서입니다.\n"
                        "무엇이든 물어보세요! \U0001f680"
                    )
                    continue
                
                # Xoul에 전달
                bot.send_message(chat_id, "\u23f3 처리 중...")
                response = xoul.chat(chat_id, text)
                bot.send_message(chat_id, response)
                print(f"  \U0001f4e4 응답 전송 완료")
                
        except KeyboardInterrupt:
            print("\n\U0001f6d1 텔레그램 봇 종료")
            break
        except Exception as e:
            print(f"  \u26a0 오류: {e}")
            time.sleep(5)


# ─── 엔트리포인트 ───

if __name__ == "__main__":
    config = _load_config()
    
    if "--detect-chat-id" in sys.argv:
        token = config.get("clients", {}).get("telegram", {}).get("bot_token", "")
        if token:
            cid = detect_chat_id(token)
            if cid:
                config["clients"]["telegram"]["chat_id"] = cid
                _save_config(config)
                print(f"  ✅ config.json에 chat_id 저장 완료")
        else:
            print("❌ bot_token이 없습니다.")
    else:
        run_polling(config)
