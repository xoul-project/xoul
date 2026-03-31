"""
Xoul Desktop Client — 엔트리포인트

Spotlight 스타일 입력바 + 채팅 창 + 시스템 트레이

사용법:
    python desktop/main.py
    (프로젝트 루트에서 실행)
"""

import json
import os
import sys

# ── Windows 초기화 (QApplication 생성 전) ──
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
try:
    import ctypes
    # Per-Monitor DPI Aware v2
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    # AppUserModelID — Windows 10/11 알림(토스트) 표시에 필수
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("xoul.desktop.client")
except Exception:
    pass

# 프로젝트 루트를 경로에 추가 (desktop/ 내부에서 실행해도 동작)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# WebEngine must be imported BEFORE QApplication is created
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from api_client import ApiClient
from chat_window import ChatWindow
from input_bar import InputBar
from tray import TrayManager
from hotkey import HotkeyManager
from notification import NotificationPopup
from settings_dialog import SettingsDialog
import i18n


def load_config() -> dict:
    """config.json 로딩"""
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    i18n.init(config_path)  # i18n 초기화
    if not os.path.isfile(config_path):
        # desktop/ 안에서도 찾기
        config_path = os.path.join(SCRIPT_DIR, "..", "config.json")
    if not os.path.isfile(config_path):
        print(i18n.t("main.config_not_found"))
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


class App:
    """Xoul Desktop Client 메인 앱"""

    def __init__(self):
        self.config = load_config()

        # API 클라이언트
        server_cfg = self.config.get("server", {})
        port = server_cfg.get("port", 3000)
        api_key = server_cfg.get("api_key", "")
        self.api = ApiClient(f"http://127.0.0.1:{port}", api_key)

        # UI 컴포넌트 생성
        self.chat_window = ChatWindow()
        self.chat_window._api_base = f"http://127.0.0.1:{port}"
        self.chat_window._api_key = api_key
        self.input_bar = InputBar()
        self.tray = TrayManager()
        self.hotkey = HotkeyManager()
        self.popup = NotificationPopup()

        # 현재 워커
        self._worker = None
        self._pending_question = ""
        self._suppress_next_final = False  # list_workflows intercept

        self._connect_signals()
        self._check_server()

    def _connect_signals(self):
        """시그널 연결"""
        # Spotlight 입력바 → 메시지 전송
        self.input_bar.sig_message_sent.connect(self._send_message)

        # 채팅 창 → 메시지 전송
        self.chat_window.sig_message_sent.connect(self._send_message)

        # 채팅 창 → 설정 열기
        self.chat_window.sig_open_settings.connect(self._open_settings)

        # 핫키 → 입력바 토글
        self.hotkey.sig_toggle.connect(self._on_hotkey)

        # 트레이 시그널
        self.tray.sig_show_chat.connect(self._show_chat)
        self.tray.sig_settings.connect(self._open_settings)
        self.tray.sig_reset_session.connect(self._reset_session)
        self.tray.sig_quit.connect(self._quit)

        # 팝업 알림 클릭 → 채팅 창 열기
        self.popup.sig_clicked.connect(self._show_chat)

    def start(self):
        """앱 시작"""
        self.tray.show()
        self.hotkey.register()
        self.chat_window.show_window()  # 첫 실행 시 채팅 창 표시
        print(i18n.t("main.started"))

    def _check_server(self):
        """서버 연결 확인"""
        status = self.api.check_status()
        if status:
            provider = status.get("llm_provider", "?")
            self.chat_window.set_connected(True, provider)
            # 서버 연결 성공 시 자동 인사 트리거
            QTimer.singleShot(1500, self._send_greeting)
        else:
            self.chat_window.set_connected(False)

    # ── 자동 인사 ──

    def _send_greeting(self):
        """앱 시작 시 자동 인사 (사용자 메시지 버블 없이)"""
        if self._worker and self._worker.isRunning():
            return

        self.chat_window.start_thinking()

        from host_tools import get_installed_app_names
        host_ctx = {"installed_apps": get_installed_app_names()}
        worker = self.api.send_message(
            i18n.t("main.auto_greeting"),
            host_context=host_ctx,
        )
        worker.sig_thinking.connect(self._on_thinking)
        worker.sig_tool_start.connect(self._on_tool_start)
        worker.sig_tool_result.connect(self._on_tool_result)
        worker.sig_code_output.connect(self._on_code_output)
        worker.sig_browser_frame.connect(self._on_browser_frame)
        worker.sig_final.connect(self._on_final)
        worker.sig_persona.connect(self._on_persona)
        worker.sig_error.connect(self._on_error)
        worker.sig_needs_input.connect(self._on_needs_input)
        worker.sig_wf_progress.connect(self._on_wf_progress)
        worker.start()
        self._worker = worker

    # ── 메시지 전송 ──

    def _send_message(self, text: str):
        """메시지 전송 (Spotlight 또는 채팅 창에서)"""
        if self._worker and self._worker.isRunning():
            # 이전 요청 진행 중
            return
        self._pending_question = text
        self.chat_window.add_user_message(text)
        self.chat_window.start_thinking()
        self.input_bar.set_waiting(True)

        # API 워커 시작
        from host_tools import get_installed_app_names
        host_ctx = {"installed_apps": get_installed_app_names()}
        worker = self.api.send_message(text, host_context=host_ctx)
        worker.sig_thinking.connect(self._on_thinking)
        worker.sig_tool_start.connect(self._on_tool_start)
        worker.sig_tool_result.connect(self._on_tool_result)
        worker.sig_code_output.connect(self._on_code_output)
        worker.sig_browser_frame.connect(self._on_browser_frame)
        worker.sig_final.connect(self._on_final)
        worker.sig_persona.connect(self._on_persona)
        worker.sig_error.connect(self._on_error)
        worker.sig_needs_input.connect(self._on_needs_input)
        worker.sig_wf_progress.connect(self._on_wf_progress)
        worker.start()
        self._worker = worker

    def _on_thinking(self, attempt: int):
        """LLM이 생각 중"""
        self.chat_window.start_thinking()

    def _on_tool_start(self, tool_name: str, args: dict):
        """도구 실행 시작"""
        phase = args.pop("__phase__", "")
        display_name = f"[{phase}] {tool_name}" if phase else tool_name
        self.chat_window.add_tool_chip(display_name, args)
        self.chat_window.track_tool_call(tool_name)  # Workflow용 기록

        # host_ 도구 → 데스크톱에서 로컬 실행
        if tool_name.startswith("host_"):
            from datetime import datetime
            log_path = os.path.join(os.path.expanduser("~"), "xoul_host_debug.log")
            def _log(msg):
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")

            _log(f"TOOL_START: {tool_name} args={args}")
            try:
                from host_tools import is_tier2, execute_host_tool

                if is_tier2(tool_name):
                    from PyQt6.QtWidgets import QMessageBox
                    detail = ", ".join(f"{k}={v}" for k, v in args.items())
                    msg = QMessageBox(self.chat_window)
                    msg.setWindowTitle(i18n.t("main.host_tool_confirm_title"))
                    msg.setText(i18n.t("main.host_tool_confirm_body")
                                + i18n.t("main.host_tool_confirm_detail", tool_name=tool_name, detail=detail))
                    msg.setIcon(QMessageBox.Icon.Warning)
                    msg.setStandardButtons(
                        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
                    )
                    msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
                    if msg.exec() != QMessageBox.StandardButton.Ok:
                        self.chat_window.add_tool_result(tool_name, i18n.t("main.user_cancelled"))
                        _log(f"CANCELLED by user")
                        return

                result = execute_host_tool(tool_name, args)
                _log(f"RESULT: {result}")
                self.chat_window.add_tool_result(tool_name, result)
            except Exception as e:
                import traceback
                traceback.print_exc()
                _log(f"ERROR: {e}")
                self.chat_window.add_tool_result(tool_name, i18n.t("main.error", e=str(e)))

    def _on_tool_result(self, tool_name: str, result: str):
        """도구 결과 (host_ 도구는 이미 로컬에서 처리됨)"""
        if tool_name == "list_workflows" and "<!--WFDATA:" in result:
            # Desktop 전용: interactive workflow 테이블 렌더링
            self.chat_window.render_workflow_list(result)
            self._suppress_next_final = True
            return
        if tool_name == "list_personas" and "<!--PERSONA_DATA:" in result:
            # Desktop 전용: interactive persona 테이블 렌더링
            self.chat_window.render_persona_list(result)
            self._suppress_next_final = True
            return
        if tool_name == "list_codes" and "<!--CODE_DATA:" in result:
            # Desktop 전용: interactive code 테이블 렌더링
            self.chat_window.render_code_list(result)
            self._suppress_next_final = True
            return
        if not tool_name.startswith("host_"):
            self.chat_window.add_tool_result(tool_name, result)

    def _on_code_output(self, line: str):
        """실시간 코드 실행 출력"""
        self.chat_window.add_code_output(line)

    def _on_browser_frame(self, data: str, source: str = "ddg", url_idx: int = 0):
        """브라우저 스크린캐스트 프레임"""
        self.chat_window.add_browser_frame(data, source, url_idx)

    def _on_final(self, content: str, session_id: str, tool_calls: list):
        """최종 응답"""
        self.api.session_id = session_id
        self.input_bar.set_waiting(False)

        # list_workflows 인터셉트 후 LLM 중복 응답 숨김
        if getattr(self, '_suppress_next_final', False):
            self._suppress_next_final = False
            self.chat_window._stop_thinking()
            return

        if content:
            self.chat_window.add_bot_message(content)
        else:
            self.chat_window.add_bot_message(i18n.t("main.no_response"))

        # 커스텀 팝업 알림
        preview = content[:100] if content else i18n.t("main.response_arrived")
        self.popup.show_notification("🤖 Xoul", preview)
        self._worker = None

    def _on_wf_progress(self, wf_name: str, step: int, total: int, status: str):
        """워크플로우 진행 상태 UI 업데이트"""
        if status == "done":
            self.chat_window.clear_workflow_progress()
            self.input_bar.clear_workflow_hint()
        else:
            self.chat_window.set_workflow_progress(wf_name, step, total, status)
            self.input_bar.set_workflow_hint(wf_name, status)

    def _on_persona(self, state: dict):
        """페르소나 모드 상태 변경"""
        if state.get("active"):
            self.chat_window.set_persona_mode(state.get("name", ""), state.get("bg_image", ""))
        else:
            self.chat_window.clear_persona_mode()

    def _on_error(self, error_msg: str):
        """오류"""
        self.input_bar.set_waiting(False)
        self.chat_window.add_error_message(error_msg)
        self.popup.show_notification("⚠️ Xoul", error_msg[:100])
        self._worker = None

    def _on_needs_input(self, code_name: str, params: list, session_id: str = ""):
        """코드 실행에 필요한 파라미터 입력 팝업"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout
        from PyQt6.QtCore import Qt

        # session_id: SSE 이벤트에서 직접 전달 (시그널 타이밍 무관)
        if session_id:
            self.api.session_id = session_id
        print(f"[NEEDS_INPUT] code={code_name}, session_id={self.api.session_id}", flush=True)

        # 원본 워커 시그널 해제 — 팝업 중 queued sig_final이 간섭하는 것 방지
        if self._worker:
            try:
                self._worker.sig_final.disconnect(self._on_final)
                self._worker.sig_tool_result.disconnect(self._on_tool_result)
            except Exception:
                pass
            self._worker = None
        self.input_bar.set_waiting(False)

        dlg = QDialog(self.chat_window)
        dlg.setWindowTitle(f"🔑 {code_name}")
        dlg.setMinimumWidth(400)
        dlg.setStyleSheet("""
            QDialog { background: #1a1a1a; color: #e0e0e0; font-family: 'Segoe UI'; }
            QLabel { color: #b0b0b0; font-size: 13px; }
            QLabel#title { color: #50c878; font-size: 15px; font-weight: bold; }
            QLineEdit {
                background: #2a2a2a; color: #fff; border: 1px solid #444;
                border-radius: 6px; padding: 8px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #50c878; }
            QPushButton {
                background: #50c878; color: #000; border: none; border-radius: 6px;
                padding: 8px 24px; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background: #6ade96; }
            QPushButton#cancel { background: #333; color: #aaa; }
            QPushButton#cancel:hover { background: #444; }
        """)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        title = QLabel(i18n.t("main.param_dialog_title", code_name=code_name))
        title.setObjectName("title")
        layout.addWidget(title)

        inputs = {}
        for p in params:
            pname = p.get("name", "?")
            pdesc = p.get("desc", pname)
            lbl = QLabel(f"{pdesc} ({pname})")
            layout.addWidget(lbl)
            inp = QLineEdit()
            inp.setPlaceholderText(i18n.t("main.param_placeholder", pname=pname))
            if "secret" in pname.lower() or "password" in pname.lower():
                inp.setEchoMode(QLineEdit.EchoMode.Password)
            layout.addWidget(inp)
            inputs[pname] = inp

        from PyQt6.QtWidgets import QCheckBox
        remember_chk = QCheckBox(i18n.t("main.param_remember"))
        remember_chk.setStyleSheet("QCheckBox { color: #b0b0b0; font-size: 12px; }")
        remember_chk.setChecked(False)
        layout.addWidget(remember_chk)

        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton(i18n.t("main.param_cancel"))
        cancel_btn.setObjectName("cancel")
        cancel_btn.clicked.connect(dlg.reject)
        submit_btn = QPushButton(i18n.t("main.param_submit"))
        submit_btn.setDefault(True)          # Enter 키 → OK
        submit_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(submit_btn)
        layout.addLayout(btn_layout)

        # QLineEdit에서 Enter 치면 바로 제출
        for inp in inputs.values():
            inp.returnPressed.connect(dlg.accept)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._cancel_workflow(session_id)
            return

        param_dict = {}
        for pname, inp in inputs.items():
            val = inp.text().strip()
            if val:
                param_dict[pname] = val
        if not param_dict:
            self._cancel_workflow(session_id)
            return

        # 기억하기 체크 시 → /memory/save API 직접 호출 (LLM 미경유)
        if remember_chk.isChecked() and param_dict:
            try:
                import threading
                def _save_memory():
                    import requests as _req
                    _headers = {"Authorization": f"Bearer {self.api.api_key}"}
                    for k, v in param_dict.items():
                        try:
                            resp = _req.post(
                                f"{self.api.base_url}/memory/save",
                                json={"key": f"{code_name} {k}", "value": v,
                                      "category": "credentials"},
                                headers=_headers,
                                timeout=10
                            )
                            print(f"[REMEMBER] {k}: status={resp.status_code}", flush=True)
                        except Exception as e:
                            print(f"[REMEMBER] {k}: error={e}", flush=True)
                threading.Thread(target=_save_memory, daemon=True).start()
                print(f"[REMEMBER] Saving {len(param_dict)} params for {code_name}", flush=True)
            except Exception:
                pass

        # 채팅 메시지로 전송 → chat_stream → session.pending_wf 감지 → 워크플로우 자동 재개
        # ⚠️ session_id를 다시 강제 설정 (팝업 중 sig_final이 덮어썼을 수 있음)
        if session_id:
            self.api.session_id = session_id
        parts = [f"{pname}: {val}" for pname, val in param_dict.items()]
        msg = "\n".join(parts)
        print(f"[NEEDS_INPUT] Sending to session_id={self.api.session_id}", flush=True)
        self._send_message(msg)

    def _cancel_workflow(self, session_id: str = ""):
        """ask_user 팝업 취소 시 워크플로우 상태 정리"""
        _sid = session_id or self.api.session_id
        if _sid:
            try:
                import requests as _req
                _req.post(
                    f"{self.api.base_url}/workflow/cancel",
                    json={"session_id": _sid},
                    headers={"Authorization": f"Bearer {self.api.api_key}"},
                    timeout=5,
                )
            except Exception as e:
                print(f"[WF-CANCEL] error: {e}", flush=True)
        self.chat_window._stop_thinking()
        self.chat_window.clear_workflow_progress()
        self.input_bar.clear_workflow_hint()
        self.chat_window.add_bot_message("⏹ 워크플로우가 취소되었습니다.")
        self._worker = None
        print(f"[WF-CANCEL] Workflow cancelled, session_id={_sid}", flush=True)

    def _resume_workflow(self):
        """일시정지된 워크플로우 재개 — /workflow/resume SSE 소비"""
        print(f"[RESUME] _resume_workflow called, session_id={self.api.session_id}, _worker={self._worker}", flush=True)
        if self._worker and self._worker.isRunning():
            print("[RESUME] Worker still running, retrying in 1s", flush=True)
            QTimer.singleShot(1000, self._resume_workflow)
            return

        if not self.api.session_id:
            print("[RESUME] No session_id, abort", flush=True)
            return

        from api_client import ChatWorker

        class ResumeWorker(ChatWorker):
            """워크플로우 재개 전용 워커 (POST /workflow/resume SSE)"""
            def run(self_w):
                import requests
                url = f"{self_w.base_url}/workflow/resume"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self_w.api_key}",
                }
                payload = {"session_id": self_w.session_id}
                try:
                    resp = requests.post(url, json=payload, headers=headers,
                                         stream=True, timeout=660)
                    if resp.status_code != 200:
                        # 재개할 워크플로우 없음 — 정상 종료
                        return
                    # ChatWorker.run()과 동일한 SSE 파싱 (부모 클래스 시그널 재사용)
                    for raw_line in resp.iter_lines(decode_unicode=True):
                        if self_w._cancelled:
                            break
                        if not raw_line or not raw_line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(raw_line[6:])
                        except json.JSONDecodeError:
                            continue
                        evt_type = event.get("type", "")
                        if evt_type == "thinking":
                            self_w.sig_thinking.emit(event.get("attempt", 0))
                        elif evt_type == "tool_start":
                            _args = event.get("args", {})
                            _phase = event.get("phase", "")
                            if _phase:
                                _args["__phase__"] = _phase
                            self_w.sig_tool_start.emit(event.get("tool", "?"), _args)
                        elif evt_type == "tool_result":
                            self_w.sig_tool_result.emit(event.get("tool", "?"), event.get("result", ""))
                        elif evt_type == "code_output":
                            self_w.sig_code_output.emit(event.get("line", ""))
                        elif evt_type == "needs_input":
                            self_w.sig_needs_input.emit(event.get("code_name", ""), event.get("params", []))
                        elif evt_type == "wf_progress":
                            self_w.sig_wf_progress.emit(
                                event.get("wf_name", ""), event.get("step", 0),
                                event.get("total", 0), event.get("status", ""),
                            )
                        elif evt_type == "final":
                            self_w.sig_final.emit(event.get("content", ""), event.get("session_id", ""), event.get("tool_calls", []))
                        elif evt_type == "error":
                            self_w.sig_error.emit(event.get("content", ""))
                    resp.close()
                except requests.exceptions.ConnectionError:
                    pass
                except Exception as e:
                    self_w.sig_error.emit(str(e)[:200])

        worker = ResumeWorker(
            base_url=self.api.base_url,
            api_key=self.api.api_key,
            message="",
            session_id=self.api.session_id,
        )
        worker.sig_thinking.connect(self._on_thinking)
        worker.sig_tool_start.connect(self._on_tool_start)
        worker.sig_tool_result.connect(self._on_tool_result)
        worker.sig_code_output.connect(self._on_code_output)
        worker.sig_final.connect(self._on_final)
        worker.sig_error.connect(self._on_error)
        worker.sig_needs_input.connect(self._on_needs_input)
        worker.sig_wf_progress.connect(self._on_wf_progress)
        worker.start()
        self._worker = worker

    # ── UI 제어 ──

    def _on_hotkey(self):
        """Ctrl+Space 핫키"""
        self.input_bar.toggle()

    def _show_chat(self):
        """채팅 창 표시"""
        self.chat_window.show_window()

    def _open_settings(self):
        """설정 다이얼로그 표시"""
        dlg = SettingsDialog(self.chat_window)
        dlg.sig_saved.connect(self._on_settings_saved)
        dlg.exec()

    def _on_settings_saved(self):
        """설정 저장 후 config 리로드 + VM 동기화"""
        old_config = self.config
        self.config = load_config()
        server_cfg = self.config.get("server", {})
        port = server_cfg.get("port", 3000)
        api_key = server_cfg.get("api_key", "")
        self.api = ApiClient(f"http://127.0.0.1:{port}", api_key)

        # 모델 변경 감지
        old_model = old_config.get("llm", {}).get("ollama_model", "")
        new_model = self.config.get("llm", {}).get("ollama_model", "")
        model_changed = old_model and new_model and old_model != new_model

        # 동기화 시작 메시지 (메인 스레드에서 안전하게 호출)
        self.chat_window.add_bot_message(
            f"🔄 {i18n.t('main.config_syncing')}"
        )

        # 모든 무거운 작업을 백그라운드 스레드에서 실행
        import threading

        def _sync():
            import subprocess

            # 이전 모델 명시적 언로드 (VRAM 확보)
            if model_changed:
                try:
                    subprocess.run(
                        ["ollama", "stop", old_model],
                        capture_output=True, timeout=10,
                    )
                    print(f"[SETTINGS] Unloaded old model: {old_model}", flush=True)
                except Exception as e:
                    print(f"[SETTINGS] Failed to unload {old_model}: {e}", flush=True)

            # VM에 config 동기화 (deploy -ConfigOnly)
            try:
                deploy_script = os.path.join(PROJECT_ROOT, "scripts", "deploy.ps1")
                result = subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass",
                     "-File", deploy_script, "-ConfigOnly"],
                    capture_output=True, text=True, timeout=60,
                    cwd=PROJECT_ROOT,
                )
                if result.returncode == 0:
                    QTimer.singleShot(0, lambda: self.chat_window.add_bot_message(
                        f"✅ {i18n.t('main.config_sync_done')}"
                    ))
                else:
                    QTimer.singleShot(0, lambda: self.chat_window.add_bot_message(
                        f"⚠️ {i18n.t('main.config_sync_fail')}"
                    ))
            except Exception as e:
                QTimer.singleShot(0, lambda: self.chat_window.add_bot_message(
                    f"⚠️ deploy error: {e}"
                ))

            import time
            time.sleep(3)
            QTimer.singleShot(0, self._check_server)

        threading.Thread(target=_sync, daemon=True).start()

    def _reset_session(self):
        """세션 초기화"""
        self.api.reset_session()
        self.chat_window.clear_chat()
        self.chat_window.add_bot_message(i18n.t("main.session_reset_done"))

    def _quit(self):
        """종료"""
        self.hotkey.unregister()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        QApplication.quit()


def main():
    # ── 싱글턴 체크 (Windows Mutex) ──
    try:
        import ctypes
        mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "XoulsDesktopClient_Mutex")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            print(i18n.t("main.already_running"))
            ctypes.windll.kernel32.CloseHandle(mutex)
            sys.exit(0)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Xoul")
    
    # ── 앱 및 작업표시줄 아이콘 설정 ──
    from PyQt6.QtGui import QIcon
    icon_path = os.path.join(SCRIPT_DIR, "xoul.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    xoul = App()
    xoul.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
