"""
Xoul Desktop Client — REST API + SSE 스트리밍 클라이언트 (QThread)
"""

import json
import requests
from PyQt6.QtCore import QThread, pyqtSignal
from i18n import t


class ChatWorker(QThread):
    """
    백그라운드에서 /chat/stream SSE를 소비하는 워커 스레드.
    각 이벤트 타입별 시그널을 emit 합니다.
    """
    # 시그널 정의
    sig_thinking = pyqtSignal(int)                    # attempt
    sig_tool_start = pyqtSignal(str, dict)            # tool_name, args
    sig_tool_result = pyqtSignal(str, str)            # tool_name, result
    sig_code_output = pyqtSignal(str)                  # stdout line from code execution
    sig_browser_frame = pyqtSignal(str, str, int)        # base64 JPEG, source (ddg/url), url_idx
    sig_final = pyqtSignal(str, str, list)            # content, session_id, tool_calls
    sig_persona = pyqtSignal(dict)                     # persona state {active, name, bg_image}
    sig_error = pyqtSignal(str)                       # error message
    sig_needs_input = pyqtSignal(str, list, str)        # code_name, params[{name,desc}], session_id
    sig_wf_progress = pyqtSignal(str, int, int, str)    # wf_name, step, total, status

    def __init__(self, base_url: str, api_key: str, message: str,
                 session_id: str = None, host_context: dict = None, parent=None):
        super().__init__(parent)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.message = message
        self.session_id = session_id
        self.host_context = host_context or {}
        self._cancelled = False

    def run(self):
        """SSE 스트리밍 요청 실행"""
        url = f"{self.base_url}/chat/stream"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "message": self.message,
            "session_id": self.session_id,
            "format": "markdown",
        }
        if self.host_context:
            payload["host_context"] = self.host_context

        try:
            resp = requests.post(
                url, json=payload, headers=headers,
                stream=True, timeout=660,
            )
            resp.raise_for_status()

            for raw_line in resp.iter_lines(decode_unicode=True):
                if self._cancelled:
                    break
                if not raw_line or not raw_line.startswith("data: "):
                    continue

                try:
                    event = json.loads(raw_line[6:])
                except json.JSONDecodeError:
                    continue

                evt_type = event.get("type", "")

                if evt_type == "thinking":
                    self.sig_thinking.emit(event.get("attempt", 0))

                elif evt_type == "tool_start":
                    _args = event.get("args", {})
                    _phase = event.get("phase", "")
                    if _phase:
                        _args["__phase__"] = _phase
                    self.sig_tool_start.emit(
                        event.get("tool", "?"),
                        _args,
                    )

                elif evt_type == "tool_result":
                    self.sig_tool_result.emit(
                        event.get("tool", "?"),
                        event.get("result", ""),
                    )

                elif evt_type == "code_output":
                    self.sig_code_output.emit(event.get("line", ""))

                elif evt_type == "browser_frame":
                    self.sig_browser_frame.emit(event.get("data", ""), event.get("source", "ddg"), event.get("url_idx", 0))

                elif evt_type == "needs_input":
                    _ni_sid = event.get("session_id", "") or self.session_id or ""
                    self.sig_needs_input.emit(
                        event.get("code_name", ""),
                        event.get("params", []),
                        _ni_sid,
                    )

                elif evt_type == "wf_progress":
                    self.sig_wf_progress.emit(
                        event.get("wf_name", ""),
                        event.get("step", 0),
                        event.get("total", 0),
                        event.get("status", ""),
                    )

                elif evt_type == "error":
                    self.sig_error.emit(event.get("content", t("api.connection_error", e="")))

                elif evt_type == "persona_activated":
                    # 페르소나 활성화 안내 (중간 메시지) — LLM 인사말은 이후 final로 도착
                    self.sig_persona.emit({
                        "active": event.get("persona_active", True),
                        "name": event.get("persona_name", ""),
                        "bg_image": event.get("persona_bg_image", ""),
                    })
                    # 활성화 안내를 중간 메시지로 표시
                    self.sig_tool_result.emit("activate_persona", event.get("content", ""))

                elif evt_type == "final":
                    _sid = event.get("session_id", "")
                    if _sid:
                        self.session_id = _sid  # 워커 내부에 즉시 저장
                    # Persona state 전달 (비활성화 시 False/"" 이므로 in 체크)
                    if "persona_active" in event:
                        self.sig_persona.emit({
                            "active": event.get("persona_active", False),
                            "name": event.get("persona_name", ""),
                            "bg_image": event.get("persona_bg_image", ""),
                        })
                    self.sig_final.emit(
                        event.get("content", ""),
                        _sid,
                        event.get("tool_calls", []),
                    )

            resp.close()

        except requests.exceptions.ConnectionError:
            self.sig_error.emit(t("api.connection_error", e="VM not running"))
        except requests.exceptions.Timeout:
            self.sig_error.emit(t("api.timeout"))
        except requests.exceptions.HTTPError as e:
            self.sig_error.emit(f"HTTP {t('api.connection_error', e=str(e.response.status_code))}")
        except Exception as e:
            self.sig_error.emit(t("api.connection_error", e=str(e)[:200]))

    def cancel(self):
        self._cancelled = True


class ApiClient:
    """Xoul REST API 클라이언트"""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session_id = None

    def check_status(self) -> dict | None:
        """서버 상태 확인. 연결 실패 시 None 반환."""
        try:
            resp = requests.get(
                f"{self.base_url}/status",
                timeout=5,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def send_message(self, message: str, host_context: dict = None) -> ChatWorker:
        """
        메시지 전송 (비동기). ChatWorker 스레드를 시작하고 반환.
        호출자가 시그널을 연결한 후 worker.start() 호출.
        """
        worker = ChatWorker(
            base_url=self.base_url,
            api_key=self.api_key,
            message=message,
            session_id=self.session_id,
            host_context=host_context,
        )
        return worker

    def run_code_background(self, name: str, params: dict, timeout: int = 600):
        """백그라운드에서 코드 실행 지시 (채팅 메시지 생성 안 함)"""
        try:
            requests.post(
                f"{self.base_url}/code/run",
                json={"name": name, "params": params, "timeout": timeout},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=5
            )
        except Exception as e:
            print(f"Failed to trigger {name} in background: {e}")

    def reset_session(self):
        """세션 초기화"""
        if self.session_id:
            try:
                requests.delete(
                    f"{self.base_url}/sessions/{self.session_id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=5,
                )
            except Exception:
                pass
        self.session_id = None
