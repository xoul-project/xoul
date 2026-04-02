"""
Xoul Desktop Client — 메인 채팅 창 (카카오톡 스타일 버블 UI)

QWebEngineView + JavaScript append 방식으로 실시간 업데이트.
프레임리스 창 + 커스텀 리사이즈 핸들.

Note: WA_TranslucentBackground / QGraphicsDropShadowEffect 사용 금지 —
      QWebEngineView (Chromium) 렌더링과 충돌하여 화면 갱신이 안 됨.
"""

import html as html_lib
import os
import webbrowser
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QTextEdit, QPushButton, QApplication, QSizeGrip,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSettings
from PyQt6.QtGui import QColor, QFont, QCursor, QPainter, QBrush, QPainterPath, QRegion
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from styles import CHAT_WINDOW_QSS, BUBBLE_CSS, COLORS as C
from i18n import t
from slash_command import match_slash_command, SlashCommandPopup


import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
from env_config import get_web_config, is_dev

WEB_CFG = get_web_config()

# ── WebEngine 캐시 비활성화 (배포 후 항상 최신 UI 로드) ──
# ⚠ QApplication 생성 이후에만 호출 가능! ChatWindow.__init__()에서 호출됨.
def _disable_webengine_cache():
    try:
        profile = QWebEngineProfile.defaultProfile()
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
    except Exception:
        pass



class ExternalLinkPage(QWebEnginePage):
    """링크 클릭 시 기본 브라우저에서 열기"""

    _chat_window = None  # ChatWindow 참조 (wfaction용)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        url_str = url.toString()
        # JS에서 openurl: 스킴으로 전달된 URL
        if url_str.startswith("openurl:"):
            from urllib.parse import unquote
            real_url = unquote(url_str[8:])
            webbrowser.open(real_url)
            return False
        # Workflow 페이지네이션 — 직접 API 호출 (LLM 우회)
        if url_str.startswith("wfpage:"):
            page_num = url_str[7:].split("#")[0]
            if self._chat_window:
                self._chat_window._fetch_workflow_page(page_num)
            return False
        # Workflow 액션 버튼 클릭 (▶ 실행, 🗑 삭제)
        if url_str.startswith("wfaction:"):
            from urllib.parse import unquote
            action_data = unquote(url_str[9:].split("#")[0])  # #t=timestamp 제거
            if self._chat_window:
                self._chat_window.sig_message_sent.emit(action_data)
            return False
        # Workflow 수정 버튼 클릭 (✏ 로컬 다이얼로그)
        if url_str.startswith("wfedit:"):
            from urllib.parse import unquote
            wf_name = unquote(url_str[7:].split("#")[0])  # #t=timestamp 제거
            if self._chat_window:
                self._chat_window._open_edit_dialog(wf_name)
            return False
        # Persona 수정 버튼 클릭 (✏ 로컬 다이얼로그)
        if url_str.startswith("personaedit:"):
            from urllib.parse import unquote
            p_name = unquote(url_str[12:].split("#")[0])
            if self._chat_window:
                self._chat_window._show_persona_dialog(p_name)
            return False
        # Workflow Store Import (📥 wfimport:base64data)
        if url_str.startswith("wfimport:"):
            b64_data = url_str[9:].split("#")[0]
            if self._chat_window:
                self._chat_window._handle_wf_import(b64_data)
                self._chat_window.activateWindow()
                self._chat_window.raise_()
            return False
        # Persona Store Import (🎭 personaimport:base64data)
        if url_str.startswith("personaimport:"):
            b64_data = url_str[14:].split("#")[0]
            if self._chat_window:
                self._chat_window._handle_persona_import(b64_data)
                self._chat_window.activateWindow()
                self._chat_window.raise_()
            return False
        # Arena 게임 참가 (🎮 arenajoin:base64data)
        if url_str.startswith("arenajoin:"):
            b64_data = url_str[10:].split("#")[0]
            if self._chat_window:
                self._chat_window._handle_arena_join(b64_data)
                self._chat_window.activateWindow()
                self._chat_window.raise_()
            return False
        # 코드 실행 중지 (⏹ stopcode:)
        if url_str.startswith("stopcode:"):
            if self._chat_window:
                self._chat_window._stop_running_code()
            return False
        # 목록 카드 내 생성 버튼 (wfcreate:, personacreate:, codecreate:)
        if url_str.startswith("wfcreate:"):
            action = url_str[9:].split("#")[0]
            if self._chat_window:
                if action == "fromconv":
                    self._chat_window._show_workflow_from_conversation()
                else:
                    self._chat_window._show_workflow_dialog()
            return False
        if url_str.startswith("personacreate:"):
            if self._chat_window:
                self._chat_window._show_persona_dialog()
            return False
        if url_str.startswith("codecreate:"):
            if self._chat_window:
                self._chat_window._show_code_editor()
            return False
        # Code Store Import (🐍 codeimport:base64data)
        if url_str.startswith("codeimport:"):
            b64_data = url_str[11:].split("#")[0]
            if self._chat_window:
                self._chat_window._handle_code_import(b64_data)
                self._chat_window.activateWindow()
                self._chat_window.raise_()
            return False
        # Code 수정 버튼 클릭 (✏ 로컬 다이얼로그)
        if url_str.startswith("codeedit:"):
            from urllib.parse import unquote
            code_name = unquote(url_str[9:].split("#")[0])
            if self._chat_window:
                self._chat_window._show_code_edit_dialog(code_name)
            return False
        # ☁️ Code 공유
        if url_str.startswith("codeshare:"):
            from urllib.parse import unquote
            code_name = unquote(url_str[10:].split("#")[0])
            if self._chat_window:
                self._chat_window._share_item_by_name("code", code_name)
            return False
        # ☁️ Workflow 공유
        if url_str.startswith("wfshare:"):
            from urllib.parse import unquote
            wf_name = unquote(url_str[8:].split("#")[0])
            if self._chat_window:
                self._chat_window._share_item_by_name("workflow", wf_name)
            return False
        # ☁️ Persona 공유
        if url_str.startswith("personashare:"):
            from urllib.parse import unquote
            p_name = unquote(url_str[13:].split("#")[0])
            if self._chat_window:
                self._chat_window._share_item_by_name("persona", p_name)
            return False
        # 일반 링크 클릭도 외부 브라우저에서 열기
        # 단, OAuth 관련 URL은 WebView 안에서 처리 (로그인 흐름 유지)
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            _auth_domains = [
                "accounts.google.com",
                "www.facebook.com",
                "graph.facebook.com",
            ]
            # config.json의 web URL도 auth 도메인으로 허용
            for _wu in (WEB_CFG.get("backend_url", ""), WEB_CFG.get("frontend_url", "")):
                if _wu:
                    _auth_domains.append(_wu.replace("http://", "").replace("https://", ""))
            if any(d in url_str for d in _auth_domains):
                return True  # WebView 안에서 탐색
            webbrowser.open(url_str)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def createWindow(self, window_type):
        """target=_blank 등 새 창 요청도 기본 브라우저로"""
        return None  # 내부 팝업 차단

from md_renderer import render_markdown, PYGMENTS_CSS


# 초기 HTML — 스크롤 가능한 채팅 페이지 (매트릭스 배경 효과 포함)
_INITIAL_HTML = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
{BUBBLE_CSS}
{PYGMENTS_CSS}
html, body {{
    height: 100%;
    margin: 0;
    padding: 0;
    overflow-y: auto;
    overflow-x: hidden;
}}
#matrix-canvas {{
    position: fixed;
    top: 0; left: 0;
    width: 100%; height: 100%;
    z-index: 0;
    pointer-events: none;
    opacity: 0.4;
}}
#chat-container {{
    position: relative;
    z-index: 1;
    padding: 12px 8px 24px 8px;
    min-height: 100%;
    box-sizing: border-box;
}}
::-webkit-scrollbar {{ width: 6px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: {C['surface1']}; border-radius: 3px; }}
::-webkit-scrollbar-thumb:hover {{ background: {C['surface2']}; }}
</style>
</head>
<body>
<canvas id="matrix-canvas"></canvas>
<div id="chat-container"></div>
<script>
// ── Matrix Rain Effect (trail-array — no ghosting) ──
(function() {{
    var canvas = document.getElementById('matrix-canvas');
    var ctx = canvas.getContext('2d');
    var fontSize = 14;
    var trailLen = 15;
    var columns = 0, drops = [], trails = [];
    var chars = 'アイウエオカキクケコ가나다라마바사아자카타파하XOULAGENT01코드워크플로우=+{{}}[]';

    function initMatrix() {{
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        var newCols = Math.floor(canvas.width / fontSize);
        if (newCols !== columns) {{
            columns = newCols;
            var maxRow = Math.ceil(canvas.height / fontSize);
            drops = [];
            trails = [];
            for (var i = 0; i < columns; i++) {{
                drops[i] = Math.floor(Math.random() * maxRow) - maxRow;
                trails[i] = [];
            }}
        }}
    }}
    initMatrix();
    window.addEventListener('resize', initMatrix);

    function drawMatrix() {{
        ctx.fillStyle = '#0d0d0d';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.font = fontSize + 'px monospace';
        for (var i = 0; i < columns; i++) {{
            if (drops[i] >= 0) {{
                var ch = chars[Math.floor(Math.random() * chars.length)];
                trails[i].push({{ c: ch, y: drops[i] * fontSize }});
                if (trails[i].length > trailLen) trails[i].shift();
            }}
            for (var j = 0; j < trails[i].length; j++) {{
                var t = trails[i][j];
                var ratio = j / trails[i].length;
                var alpha = 0.10 + ratio * 0.40;
                ctx.fillStyle = 'rgba(80, 200, 120, ' + alpha + ')';
                ctx.fillText(t.c, i * fontSize, t.y);
            }}
            drops[i]++;
            if (drops[i] * fontSize > canvas.height && Math.random() > 0.985) {{
                drops[i] = -Math.floor(Math.random() * 10);
                trails[i] = [];
            }}
        }}
    }}
    setInterval(drawMatrix, 80);
}})();

// ── Chat Functions ──
function appendHtml(htmlStr) {{
    var c = document.getElementById('chat-container');
    c.insertAdjacentHTML('beforeend', htmlStr);
    scrollBottom();
}}
function scrollBottom() {{
    function doScroll() {{
        document.body.scrollTop = 999999;
        document.documentElement.scrollTop = 999999;
        window.scrollTo(0, 999999);
    }}
    doScroll();
    setTimeout(doScroll, 50);
    setTimeout(doScroll, 200);
    setTimeout(doScroll, 500);
}}
// DOM 변경 감지 → 자동 스크롤
var _obs = new MutationObserver(function() {{
    setTimeout(function() {{
        window.scrollTo(0, 999999);
    }}, 100);
}});
_obs.observe(document.getElementById('chat-container'), {{ childList: true, subtree: true }});
function clearChat() {{
    document.getElementById('chat-container').innerHTML = '';
}}
// 모든 링크 클릭 → 외부 브라우저에서 열기
document.addEventListener('click', function(e) {{
    var el = e.target;
    while (el && el.tagName !== 'A') el = el.parentElement;
    if (el && el.href && !el.href.startsWith('data:')) {{
        e.preventDefault();
        e.stopPropagation();
        if (el.href.startsWith('wfaction:') || el.href.startsWith('wfpage:') || el.href.startsWith('wfedit:') || el.href.startsWith('personaedit:') || el.href.startsWith('codeedit:') || el.href.startsWith('codeshare:') || el.href.startsWith('wfshare:') || el.href.startsWith('personashare:') || el.href.startsWith('wfcreate:') || el.href.startsWith('personacreate:') || el.href.startsWith('codecreate:')) {{
            // 타임스탬프 추가 — QWebEngine URL 캐싱 방지
            window.location.href = el.href.split('#')[0] + '#t=' + Date.now();
        }} else {{
            window.location.href = 'openurl:' + el.href;
        }}
    }}
}});
</script>
</body>
</html>"""


_RESIZE_MARGIN = 6


class MultiLineInput(QTextEdit):
    """QTextEdit 기반 멀티라인 입력 위젯.
    - Enter → 전송 시그널 (sig_submit)
    - Shift+Enter → 줄바꿈
    - 자동 높이 조절 (1~5줄)
    """
    sig_submit = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(),
            self.sizePolicy().verticalPolicy(),
        )
        # 초기 높이 (한 줄 기준, QLineEdit과 동일)
        self._min_height = 40
        self._max_height = 140  # ~5줄
        self.setFixedHeight(self._min_height)
        self.document().contentsChanged.connect(self._adjust_height)
        # textChanged → SlashCommandPopup 호환
        self.textChanged.connect(self._on_text_changed)
        self._placeholder = ""

    def setPlaceholderText(self, text: str):
        self._placeholder = text
        super().setPlaceholderText(text)

    def text(self) -> str:
        """QLineEdit 호환: 텍스트 반환"""
        return self.toPlainText()

    def setText(self, text: str):
        """QLineEdit 호환: 텍스트 설정"""
        self.setPlainText(text)

    def clear(self):
        super().clear()
        self.setFixedHeight(self._min_height)

    def _adjust_height(self):
        doc_height = int(self.document().size().height()) + 12  # margin
        new_height = max(self._min_height, min(doc_height, self._max_height))
        self.setFixedHeight(new_height)

    def _on_text_changed(self):
        pass  # SlashCommandPopup 등 외부 연결용

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Enter → 줄바꿈 삽입
                super().keyPressEvent(event)
            else:
                # Enter → 전송
                self.sig_submit.emit()
            return
        super().keyPressEvent(event)


class ChatWindow(QWidget):
    """메인 채팅 창"""

    sig_message_sent = pyqtSignal(str)
    sig_open_settings = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # QApplication 생성 이후이므로 여기서 캐시 비활성화 안전
        _disable_webengine_cache()
        self.setObjectName("ChatWindow")
        self.setWindowTitle("Xoul")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
        )
        # ⚠ WA_TranslucentBackground 사용 금지 — WebEngine 렌더링 충돌
        self.setStyleSheet(f"""
            QWidget#ChatWindow {{
                background-color: {C['base']};
            }}
            QToolTip {{
                background-color: {C['surface0']};
                color: {C['text']};
                border: 1px solid {C['surface2']};
                padding: 4px 8px;
                border-radius: 4px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }}
        """)
        self.setMinimumSize(400, 320)

        # 화면 크기 기반 초기 크기 (가로 1/3, 세로 1/2) + 이전 크기 복원
        self._settings = QSettings("Xoul", "Desktop")
        self._is_maximized = False
        self._normal_geo = None  # 최대화 전 geometry 저장
        saved = self._settings.value("window/geometry")
        if saved:
            self.restoreGeometry(saved)
        else:
            screen = QApplication.primaryScreen()
            if screen:
                sz = screen.availableSize()
                w = sz.width() // 3
                h = sz.height() // 2
            else:
                w, h = 640, 520
            self.resize(w, h)

        # 리사이즈
        self._resize_edge = None
        self._resize_start_pos = None
        self._resize_start_geo = None
        self._drag_start_pos = None          # 수동 드래그 폴백용
        self._drag_start_window_pos = None   # 수동 드래그 폴백용
        self.setMouseTracking(True)

        self._pending_tool_chips = []
        self._page_ready = False
        self._session_tool_turns = []  # Workflow용: (user_msg, [tool_names]) 기록
        self._tool_chip_counter = 0    # tool chip 고유 ID 카운터
        self._current_tool_name = ""   # 현재 실행 중인 도구 이름 (status bar용)
        self._active_chip_id = None    # 현재 active 상태인 chip ID

        # ── 레이아웃 ──
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 타이틀바 ──
        title_bar = QWidget()
        title_bar.setObjectName("TitleBar")
        title_bar.setFixedHeight(44)
        title_bar.setStyleSheet(
            f"background-color: {C['mantle']}; "
            "padding: 8px 12px;"
        )
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(16, 0, 8, 0)

        title_label = QLabel()
        from PyQt6.QtGui import QIcon
        from PyQt6.QtCore import QSize
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xoul.ico")
        if os.path.exists(icon_path):
            from PyQt6.QtGui import QPixmap
            pix = QPixmap(icon_path).scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            title_label.setPixmap(pix)
        else:
            title_label.setText("🤖 Xoul")
            title_label.setStyleSheet(
                f"color: {C['text']}; font-size: 14px; font-weight: bold; font-family: 'Segoe UI';"
            )
        title_layout.addWidget(title_label)

        self._status_label = QLabel(t("chat.status_checking"))
        self._status_label.setStyleSheet(
            f"color: {C['green']}; font-size: 11px; font-family: 'Segoe UI';"
        )
        title_layout.addWidget(self._status_label)

        # 🌿 DEV 환경 표시 (main이 아닌 브랜치에서만)
        if is_dev():
            _dev_badge = QLabel("[DEV]")
            _dev_badge.setStyleSheet(
                f"color: #ff8800; font-size: 10px; font-weight: bold; "
                f"font-family: 'Segoe UI'; background: #331a00; "
                f"border: 1px solid #ff8800; border-radius: 4px; padding: 1px 6px;"
            )
            _dev_badge.setToolTip(f"→ {WEB_CFG['backend_url']}")
            title_layout.addWidget(_dev_badge)

        # Persona blink timer (persona now shown in status bar, not title bar)
        self._persona_blink_timer = QTimer(self)
        self._persona_blink_timer.setInterval(800)
        self._persona_blink_timer.timeout.connect(self._blink_persona)
        self._persona_blink_visible = True
        self._persona_active_name = ""  # 현재 활성 persona 이름

        title_layout.addStretch()

        # 👤 Auth indicator
        self._auth_label = QPushButton("👤")
        self._auth_label.setFixedHeight(28)
        self._auth_label.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C['subtext0']};
                border: none; font-size: 12px; font-family: 'Segoe UI';
                padding: 0 10px; border-radius: 6px;
            }}
            QPushButton:hover {{ background-color: {C['surface0']}; color: {C['text']}; }}
        """)
        self._auth_label.setToolTip("Account")
        self._auth_label.clicked.connect(self._on_auth_clicked)
        self._update_auth_label()
        title_layout.addWidget(self._auth_label)

        # ⚙ 설정 버튼
        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setFixedSize(36, 32)
        self._settings_btn.setToolTip(t("chat.tooltip_settings"))
        self._settings_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C['overlay1']};
                border: none; font-size: 15px; border-radius: 6px;
                padding: 0px;
            }}
            QPushButton:hover {{ background-color: {C['surface0']}; color: {C['text']}; }}
        """)
        self._settings_btn.clicked.connect(lambda: self.sig_open_settings.emit())
        title_layout.addWidget(self._settings_btn)

        _title_btn_qss = f"""
            QPushButton {{
                background: transparent; color: {C['overlay1']};
                border: none; font-size: 14px; border-radius: 6px;
            }}
            QPushButton:hover {{ background-color: {C['surface0']}; color: {C['text']}; }}
        """

        # 최소화
        min_btn = QPushButton("─")
        min_btn.setFixedSize(32, 32)
        min_btn.setStyleSheet(_title_btn_qss)
        min_btn.clicked.connect(self.hide)
        title_layout.addWidget(min_btn)

        # 최대화 / 복원
        self._max_btn = QPushButton("□")
        self._max_btn.setFixedSize(32, 32)
        self._max_btn.setStyleSheet(_title_btn_qss)
        self._max_btn.clicked.connect(self._toggle_maximize)
        title_layout.addWidget(self._max_btn)

        # 닫기
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet(_title_btn_qss)
        close_btn.clicked.connect(self.hide)
        title_layout.addWidget(close_btn)

        main_layout.addWidget(title_bar)

        # ── 워크플로우 진행 배너 (sticky bar) ──
        self._wf_banner = QWidget()
        self._wf_banner.setObjectName("WfBanner")
        self._wf_banner.setFixedHeight(32)
        self._wf_banner.setVisible(False)
        self._wf_banner.setStyleSheet(f"""
            #WfBanner {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0a2a15, stop:0.5 #0d1f12, stop:1 #0a1a10);
                border-bottom: 2px solid {C['green']}88;
                border-top: 1px solid {C['green']}30;
            }}
            #WfBanner QLabel {{ background: transparent; }}
        """)
        _wfb_layout = QHBoxLayout(self._wf_banner)
        _wfb_layout.setContentsMargins(16, 0, 12, 0)
        _wfb_layout.setSpacing(8)

        self._wf_icon = QLabel("⚡")
        self._wf_icon.setStyleSheet(f"color: {C['green']}; font-size: 13px;")
        self._wf_icon.setFixedWidth(18)
        _wfb_layout.addWidget(self._wf_icon)

        self._wf_name_label = QLabel("")
        self._wf_name_label.setStyleSheet(
            f"color: {C['green']}; font-size: 12px; font-weight: bold; font-family: 'Segoe UI';"
        )
        _wfb_layout.addWidget(self._wf_name_label)

        self._wf_step_label = QLabel("")
        self._wf_step_label.setStyleSheet(
            f"color: {C['subtext0']}; font-size: 11px; font-family: 'Segoe UI';"
        )
        _wfb_layout.addWidget(self._wf_step_label)

        _wfb_layout.addStretch()

        self._wf_status_label = QLabel("")
        self._wf_status_label.setStyleSheet(
            f"color: {C['yellow']}; font-size: 11px; font-family: 'Segoe UI';"
        )
        _wfb_layout.addWidget(self._wf_status_label)

        # 배너 깜빡임 타이머
        self._wf_pulse_timer = QTimer(self)
        self._wf_pulse_timer.setInterval(600)
        self._wf_pulse_on = True
        self._wf_pulse_timer.timeout.connect(self._pulse_wf_banner)

        main_layout.addWidget(self._wf_banner)

        # 드래그 (하이브리드: OS 네이티브 → 수동 폴백)
        title_bar.mousePressEvent = self._title_mouse_press
        title_bar.mouseMoveEvent = self._title_mouse_move
        title_bar.mouseReleaseEvent = self._title_mouse_release
        title_bar.mouseDoubleClickEvent = self._title_double_click

        # ── 채팅 영역 ──
        self._page = ExternalLinkPage()
        self._page._chat_window = self  # wfaction: URL 처리용
        self._page.setBackgroundColor(QColor(C['base']))
        self._web_view = QWebEngineView()
        self._web_view.setPage(self._page)
        self._web_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._web_view.loadFinished.connect(self._on_page_loaded)
        self._web_view.setHtml(_INITIAL_HTML)
        main_layout.addWidget(self._web_view, 1)

        # ── 상태 표시 바 (항상 표시: thinking / persona indicator) ──
        self._status_bar = QWidget()
        self._status_bar.setFixedHeight(28)
        self._status_bar.setStyleSheet(f"background-color: {C['mantle']};")
        _sb_layout = QHBoxLayout(self._status_bar)
        _sb_layout.setContentsMargins(16, 0, 16, 0)
        _sb_layout.setSpacing(0)

        # 🎭 Persona indicator (status bar 왼쪽)
        self._persona_label = QPushButton("")
        self._persona_label.setFixedHeight(24)
        self._persona_label.setVisible(False)
        self._persona_label.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C['red']};
                border: none; font-size: 11px; font-weight: bold;
                font-family: 'Segoe UI'; padding: 0 8px; border-radius: 6px;
            }}
            QPushButton:hover {{ background-color: {C['surface0']}; }}
        """)
        self._persona_label.setToolTip("Click to exit Persona Mode")
        self._persona_label.clicked.connect(self._on_persona_clicked)
        _sb_layout.addWidget(self._persona_label)

        self._thinking_label = QLabel("")
        self._thinking_label.setStyleSheet(
            f"color: {C['yellow']}; font-size: 12px; font-family: 'Segoe UI';"
        )
        _sb_layout.addWidget(self._thinking_label)

        _sb_layout.addStretch()

        main_layout.addWidget(self._status_bar)

        # ── 입력 영역 ──
        input_container = QWidget()
        input_container.setStyleSheet(f"background-color: {C['mantle']};")
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(12, 8, 12, 12)

        self._chat_input = MultiLineInput()
        self._chat_input.setPlaceholderText(t("chat.placeholder_enter"))
        self._chat_input.setFont(QFont("Segoe UI", 13))
        self._chat_input.setStyleSheet(f"""
            QTextEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 2px solid {C['surface1']}; border-radius: 12px;
                padding: 10px 16px; font-size: 14px; font-family: 'Segoe UI';
            }}
            QTextEdit:focus {{ border: 2px solid {C['blue']}; }}
        """)
        self._chat_input.sig_submit.connect(self._on_submit)
        input_layout.addWidget(self._chat_input)

        # ── 슬래시 커맨드 자동완성 팝업 ──
        self._slash_popup = SlashCommandPopup(self._chat_input, self)
        self._slash_popup.sig_command_selected.connect(self._execute_slash_command)

        # ── 입력창 오른쪽 버튼 공통 스타일 ──
        _input_btn_qss = lambda color: f"""
            QPushButton {{
                background-color: {C['surface0']}; color: {color};
                border: 2px solid {C['surface1']}; border-radius: 12px;
                font-size: 13px; font-family: 'Segoe UI';
                padding: 0 8px;
            }}
            QPushButton:hover {{ background-color: {C['surface1']}; border-color: {color}; }}
        """

        # ⚡ 워크플로우 버튼 (클릭 → 목록 표시)
        self._workflow_btn = QPushButton(t("chat.btn_workflow"))
        self._workflow_btn.setFixedHeight(40)
        self._workflow_btn.setToolTip(t("chat.tooltip_wf_list"))
        self._workflow_btn.setStyleSheet(_input_btn_qss(C['yellow']))
        self._workflow_btn.clicked.connect(lambda: self.sig_message_sent.emit(t("chat.cmd_list_workflow")))
        input_layout.addWidget(self._workflow_btn)

        # ── 세로 구분선 1 ──
        _div1 = QLabel("│")
        _div1.setFixedWidth(6)
        _div1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _div1.setStyleSheet(f"color: {C['surface2']}; font-size: 16px;")
        input_layout.addWidget(_div1)

        # 🎭 페르소나 버튼 (클릭 → 목록 표시)
        self._persona_list_btn = QPushButton(t("chat.btn_persona"))
        self._persona_list_btn.setFixedHeight(40)
        self._persona_list_btn.setToolTip(t("chat.tooltip_persona_list"))
        self._persona_list_btn.setStyleSheet(_input_btn_qss(C['mauve']))
        self._persona_list_btn.clicked.connect(lambda: self.sig_message_sent.emit(t("chat.cmd_list_persona")))
        input_layout.addWidget(self._persona_list_btn)

        # 🐍 코드 버튼 (클릭 → 목록 표시)
        self._code_list_btn = QPushButton(t("chat.btn_code"))
        self._code_list_btn.setFixedHeight(40)
        self._code_list_btn.setToolTip(t("chat.tooltip_code_list"))
        self._code_list_btn.setStyleSheet(_input_btn_qss(C['green']))
        self._code_list_btn.clicked.connect(lambda: self.sig_message_sent.emit(t("chat.cmd_list_code")))
        input_layout.addWidget(self._code_list_btn)

        # ── 세로 구분선 2 ──
        _div2 = QLabel("│")
        _div2.setFixedWidth(6)
        _div2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _div2.setStyleSheet(f"color: {C['surface2']}; font-size: 16px;")
        input_layout.addWidget(_div2)

        # 🌐 스토어 버튼 (Store + Arena 메뉴)
        self._web_btn = QPushButton(t("chat.btn_store"))
        self._web_btn.setFixedHeight(40)
        self._web_btn.setToolTip(t("chat.tooltip_web"))
        self._web_btn.setStyleSheet(_input_btn_qss(C['teal']))
        self._web_btn.clicked.connect(self._show_workflow_store)
        input_layout.addWidget(self._web_btn)



        main_layout.addWidget(input_container)

        # ── 리사이즈 그립 ──
        grip_bar = QWidget()
        grip_bar.setFixedHeight(16)
        grip_bar.setStyleSheet(f"background-color: {C['mantle']};")
        grip_layout = QHBoxLayout(grip_bar)
        grip_layout.setContentsMargins(0, 0, 4, 4)
        grip_layout.addStretch()
        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip.setStyleSheet(f"background: {C['surface1']}; border-radius: 2px;")
        grip_layout.addWidget(grip)
        main_layout.addWidget(grip_bar)

        # Thinking
        self._think_dots = 0
        self._think_timer = QTimer(self)
        self._think_timer.timeout.connect(self._animate_thinking)

        # JS 큐
        self._pending_js = []

    # ── WebEngine ──


    def _on_page_loaded(self, ok):
        self._page_ready = True
        for js in self._pending_js:
            self._web_view.page().runJavaScript(js)
        self._pending_js.clear()

    def _run_js(self, js: str):
        if self._page_ready:
            self._web_view.page().runJavaScript(js)
        else:
            self._pending_js.append(js)

    def _scroll_to_bottom(self):
        """Python 측에서도 스크롤 보장"""
        self._run_js("scrollBottom();")

    def _append_html(self, html_str: str):
        escaped = html_str.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
        self._run_js(f"appendHtml(`{escaped}`);")

    # ── 리사이즈 ──

    def _edge_at(self, pos):
        r = self.rect()
        m = _RESIZE_MARGIN
        edges = 0
        if pos.x() <= m: edges |= 1
        if pos.x() >= r.width() - m: edges |= 2
        if pos.y() <= m: edges |= 4
        if pos.y() >= r.height() - m: edges |= 8
        return edges

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edge_at(event.position().toPoint())
            if edges:
                self._resize_edge = edges
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geo = self.geometry()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_edge and self._resize_start_pos:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            geo = self._resize_start_geo
            x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
            mw, mh = self.minimumWidth(), self.minimumHeight()
            if self._resize_edge & 2: w = max(mw, geo.width() + delta.x())
            if self._resize_edge & 1:
                nw = max(mw, geo.width() - delta.x())
                x = geo.x() + geo.width() - nw; w = nw
            if self._resize_edge & 8: h = max(mh, geo.height() + delta.y())
            if self._resize_edge & 4:
                nh = max(mh, geo.height() - delta.y())
                y = geo.y() + geo.height() - nh; h = nh
            self.setGeometry(x, y, w, h)
            return

        edges = self._edge_at(event.position().toPoint())
        cursors = {
            1: Qt.CursorShape.SizeHorCursor, 2: Qt.CursorShape.SizeHorCursor,
            4: Qt.CursorShape.SizeVerCursor, 8: Qt.CursorShape.SizeVerCursor,
            5: Qt.CursorShape.SizeFDiagCursor, 10: Qt.CursorShape.SizeFDiagCursor,
            6: Qt.CursorShape.SizeBDiagCursor, 9: Qt.CursorShape.SizeBDiagCursor,
        }
        self.setCursor(QCursor(cursors.get(edges, Qt.CursorShape.ArrowCursor)))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_edge = None
        self._resize_start_pos = None
        self._resize_start_geo = None
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        super().mouseReleaseEvent(event)

    # ── 타이틀바 드래그 (수동 — startSystemMove 절전 복귀 후 먹통 이슈 회피) ──

    def _title_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._is_maximized:
                self._drag_start_pos = event.globalPosition().toPoint()
                self._drag_start_window_pos = self.pos()

    def _title_mouse_move(self, event):
        if self._drag_start_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_start_pos
            self.move(self._drag_start_window_pos + delta)

    def _title_mouse_release(self, event):
        self._drag_start_pos = None
        self._drag_start_window_pos = None

    def _title_double_click(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()

    # ── 최대화 / 복원 ──

    def _toggle_maximize(self):
        if self._is_maximized:
            # 복원
            if self._normal_geo:
                self.setGeometry(self._normal_geo)
            self._is_maximized = False
            self._max_btn.setText("□")
        else:
            # 최대화 — 현재 창이 있는 모니터에서 최대화
            self._normal_geo = self.geometry()
            screen = self.screen() or QApplication.primaryScreen()
            if screen:
                self.setGeometry(screen.availableGeometry())
            self._is_maximized = True
            self._max_btn.setText("❐")

    # ── 크기 저장 / 복원 ──

    def _save_geometry(self):
        if not self._is_maximized:
            self._settings.setValue("window/geometry", self.saveGeometry())

    def hideEvent(self, event):
        self._save_geometry()
        super().hideEvent(event)

    # ── 메시지 ──

    def add_user_message(self, text: str):
        safe = html_lib.escape(text)
        if len(text) > 150:
            uid = f"umsg{id(text) & 0xFFFFFF}"
            self._append_html(
                f'<div class="user-bubble">'
                f'<div id="{uid}" style="max-height:60px;overflow:hidden;word-break:break-all">{safe}</div>'
                f'<a id="{uid}-btn" href="javascript:void(0)" '
                f'style="color:#89b4fa;font-size:11px;display:block;margin-top:4px;cursor:pointer">'
                f'{t("desktop.show_more")}</a></div>'
            )
            self._run_js(
                f"document.getElementById('{uid}-btn').addEventListener('click',function(){{"
                f"var d=document.getElementById('{uid}');"
                f"if(d.style.maxHeight==='60px'){{d.style.maxHeight='none';this.textContent='{t('desktop.collapse')}';}}"
                f"else{{d.style.maxHeight='60px';this.textContent='{t('desktop.show_more')}';}}"
                f"}});"
            )
        else:
            self._append_html(f'<div class="user-bubble">{safe}</div>')
        self._pending_tool_chips = []
        # Workflow용: 새 사용자 턴 시작
        self._session_tool_turns.append({"msg": text, "tools": []})

    def track_tool_call(self, tool_name: str):
        """Workflow용: 현재 턴에 사용된 tool 기록"""
        if self._session_tool_turns:
            self._session_tool_turns[-1]["tools"].append(tool_name)

    def add_bot_message(self, md_text: str):
        self._pending_tool_chips = []
        html_content = render_markdown(md_text)
        self._append_html(f'<div class="bot-bubble">{html_content}</div>')
        self._stop_thinking()

    def add_tool_chip(self, tool_name: str, args: dict = None):
        import json as _json
        safe_name = html_lib.escape(tool_name)
        # 이전 active chip 완료 처리
        if self._active_chip_id is not None:
            self._finish_active_chip()
        # 고유 ID 생성
        self._tool_chip_counter += 1
        chip_id = f"tc-{self._tool_chip_counter}"
        self._active_chip_id = chip_id
        self._current_tool_name = tool_name  # status bar용
        # args 표시 (query 등 핵심 파라미터만)
        detail = ""
        if args:
            # query나 name 등 핵심 파라미터만 간략히 표시
            key_params = {k: v for k, v in args.items() if k in ('query', 'name', 'url', 'location', 'command', 'expression', 'path')}
            if key_params:
                args_str = _json.dumps(key_params, ensure_ascii=False, default=str)
                safe_args = html_lib.escape(args_str)
                detail = f' {safe_args}'
        chip = (
            f'<div id="{chip_id}" class="tool-chip active">'
            f'<span class="tool-spinner">⟳</span> '
            f'🔧 {safe_name}{detail}'
            f'<span id="{chip_id}-timer" class="tool-elapsed">0s</span>'
            f'</div>'
        )
        self._pending_tool_chips.append(chip)
        self._append_html(chip)
        # JS: 경과 시간 카운터 시작
        self._run_js(
            f"(function(){{var s=0;var t=document.getElementById('{chip_id}-timer');"
            f"window['_tc_{chip_id}']=setInterval(function(){{"
            f"s++;if(t){{t.textContent=s+'s';}}}},1000);}})();"
        )
        # status bar 업데이트
        self._thinking_label.setText(f"⏳ {tool_name} ...")

    def _finish_active_chip(self):
        """현재 active chip을 done 상태로 전환"""
        chip_id = self._active_chip_id
        if chip_id is None:
            return
        # JS: 타이머 정지 + 스피너→✓ + 클래스 변경
        self._run_js(
            f"clearInterval(window['_tc_{chip_id}']);"
            f"var el=document.getElementById('{chip_id}');"
            f"if(el){{el.className='tool-chip done';"
            f"var sp=el.querySelector('.tool-spinner');"
            f"if(sp){{sp.style.animation='none';sp.textContent='✓';}}}}"
        )
        self._active_chip_id = None

    def add_tool_result(self, tool_name: str, result: str):
        # active chip 완료 처리
        self._finish_active_chip()
        self._current_tool_name = ""
        safe_name = html_lib.escape(tool_name)
        short = html_lib.escape(result[:200])
        self._pending_tool_chips.append(
            f'<div class="tool-chip-result">📋 {safe_name}: {short}</div>'
        )
        # Code output 완료 시 컨테이너 닫기 + 중지 버튼 숨기기
        if hasattr(self, '_code_output_active') and self._code_output_active:
            self._code_output_active = False
            cid = getattr(self, '_code_output_id', 1)
            self._run_js(
                f"var b=document.getElementById('stop-btn-{cid}');"
                f"if(b){{b.style.display='none';}}"
            )
        # Browser preview 자동 제거 (web_search/browse_url 완료 시)
        self.finish_browser_preview()

    def add_code_output(self, line: str):
        """실시간 코드 출력 라인을 터미널 스타일 스크롤 컨테이너에 추가"""
        safe_line = html_lib.escape(line)
        if not getattr(self, '_code_output_active', False):
            # 첫 줄: 컨테이너 생성 (고유 ID 사용)
            self._code_output_active = True
            self._code_output_id = getattr(self, '_code_output_id', 0) + 1
            stop_btn_html = (
                f'<a id="stop-btn-{self._code_output_id}" href="javascript:void(0)" '
                f'onclick="window.location.href=\'stopcode:#t={self._code_output_id}\'; return false;" '
                'style="display:inline-block; margin:4px 0 8px; '
                'padding:6px 16px; background:#f38ba8; color:#1e1e2e; '
                'border-radius:8px; font-size:12px; font-weight:bold; '
                'text-decoration:none; cursor:pointer; '
                'font-family:Segoe UI,sans-serif;" '
                'onmouseover="this.style.background=\'#eba0ac\'" '
                'onmouseout="this.style.background=\'#f38ba8\'">'
                f'{t("chat.code_stop_btn")}</a>'
            )
            container_html = (
                f'<div id="code-output-terminal-{self._code_output_id}" style="'
                'background:#1e1e2e; color:#cdd6f4; '
                'font-family:Consolas,Monaco,monospace; font-size:12px; '
                'padding:10px; margin:6px 0; border-radius:8px; '
                'max-height:300px; overflow-y:auto; '
                'border:1px solid #313244; white-space:pre-wrap; word-break:break-all;'
                f'">{safe_line}\n</div>'
                f'{stop_btn_html}'
            )
            self._append_html(container_html)
        else:
            # 추가 줄: 현재 컨테이너에 append + auto-scroll
            cid = getattr(self, '_code_output_id', 1)
            js_line = safe_line.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
            self._run_js(
                f"var t=document.getElementById('code-output-terminal-{cid}');"
                f"if(t){{t.textContent+='{js_line}\\n';t.scrollTop=t.scrollHeight;}}"
            )

    def add_browser_frame(self, base64_data: str, source: str = "ddg", url_idx: int = 0):
        """브라우저 스크린캐스트 프레임을 인라인 뷰어에 표시
        
        source == "ddg": DuckDuckGo 검색 결과 → 큰 이미지
        source == "url": URL 탐색 결과 → 2×2 그리드 (url_idx로 슬롯 구분)
        """
        if not base64_data:
            return

        if source == "url":
            # ── URL 프레임: 2×2 그리드 레이아웃 ──
            if not getattr(self, '_url_grid_active', False):
                # 첫 URL 프레임: 그리드 컨테이너 생성 (4칸)
                self._url_grid_active = True
                self._url_grid_id = getattr(self, '_url_grid_id', 0) + 1
                self._url_grid_base_idx = url_idx  # 첫 url_idx 기록
                self._url_grid_slot_map = {}  # url_idx → slot
                gid = self._url_grid_id
                container_html = (
                    f'<div id="url-grid-{gid}" style="'
                    'margin:6px 0; padding:6px; width:70%; '
                    'background:#1e1e2e; border:1px solid #313244; border-radius:8px; '
                    'overflow:hidden; transition:all 0.5s ease;">'
                    '<div style="font-size:10px; color:#a6e3a1; margin-bottom:3px; '
                    'font-family:Segoe UI,sans-serif;">🔍 Search Results</div>'
                    f'<div id="url-grid-inner-{gid}" style="'
                    'display:grid; grid-template-columns:1fr 1fr; gap:3px;">'
                    f'<img id="url-cell-{gid}-0" src="" '
                    'style="width:100%; border-radius:3px; display:none; object-fit:contain; '
                    'background:#181825;" />'
                    f'<img id="url-cell-{gid}-1" src="" '
                    'style="width:100%; border-radius:3px; display:none; object-fit:contain; '
                    'background:#181825;" />'
                    f'<img id="url-cell-{gid}-2" src="" '
                    'style="width:100%; border-radius:3px; display:none; object-fit:contain; '
                    'background:#181825;" />'
                    f'<img id="url-cell-{gid}-3" src="" '
                    'style="width:100%; border-radius:3px; display:none; object-fit:contain; '
                    'background:#181825;" />'
                    '</div></div>'
                )
                self._append_html(container_html)

            gid = self._url_grid_id
            slot_map = self._url_grid_slot_map

            # url_idx → slot 매핑 (처음 보는 url_idx면 다음 빈 슬롯 할당)
            if url_idx not in slot_map:
                next_slot = len(slot_map)
                if next_slot >= 4:
                    return  # 4개 슬롯 모두 사용 중
                slot_map[url_idx] = next_slot

            target_slot = slot_map[url_idx]
            self._run_js(
                f"var img=document.getElementById('url-cell-{gid}-{target_slot}');"
                f"if(img){{img.src='data:image/jpeg;base64,{base64_data}';"
                f"img.style.display='block';}}"
            )
        else:
            # ── DuckDuckGo 프레임: 기존 큰 이미지 ──
            if not getattr(self, '_browser_preview_active', False):
                # 첫 프레임: 컨테이너 생성
                self._browser_preview_active = True
                self._browser_preview_id = getattr(self, '_browser_preview_id', 0) + 1
                bid = self._browser_preview_id
                container_html = (
                    f'<div id="browser-preview-{bid}" style="'
                    'margin:6px 0; padding:6px; width:50%; '
                    'background:#1e1e2e; border:1px solid #313244; border-radius:8px; '
                    'overflow:hidden; transition:all 0.5s ease;">'
                    '<div style="font-size:10px; color:#89b4fa; margin-bottom:3px; '
                    'font-family:Segoe UI,sans-serif;">🌐 Live Browser</div>'
                    f'<img id="browser-img-{bid}" '
                    f'src="data:image/jpeg;base64,{base64_data}" '
                    'style="width:100%; border-radius:4px; display:block; object-fit:contain;" />'
                    '</div>'
                )
                self._append_html(container_html)
            else:
                # 후속 프레임: img src만 업데이트 (DOM 조작 최소화)
                bid = getattr(self, '_browser_preview_id', 1)
                # base64 데이터가 크므로 JS 변수에 직접 대입
                self._run_js(
                    f"var img=document.getElementById('browser-img-{bid}');"
                    f"if(img){{img.src='data:image/jpeg;base64,{base64_data}';}}"
                )

    def finish_browser_preview(self):
        """브라우저 프리뷰 즉시 제거 (DDG + URL 그리드 모두)"""
        # DDG 프리뷰 제거
        if getattr(self, '_browser_preview_active', False):
            self._browser_preview_active = False
            bid = getattr(self, '_browser_preview_id', 1)
            self._run_js(
                f"var el=document.getElementById('browser-preview-{bid}');"
                "if(el){el.style.display='none';el.parentNode.removeChild(el);}"
            )
        # URL 그리드 제거
        if getattr(self, '_url_grid_active', False):
            self._url_grid_active = False
            gid = getattr(self, '_url_grid_id', 1)
            self._run_js(
                f"var el=document.getElementById('url-grid-{gid}');"
                "if(el){el.style.display='none';el.parentNode.removeChild(el);}"
            )

    def add_error_message(self, error_text: str):
        self._stop_thinking()
        safe = html_lib.escape(error_text)
        self._append_html(
            f'<div class="bot-bubble" style="border-left: 3px solid {C["red"]};">⚠️ {safe}</div>'
        )

    # ── Thinking ──

    def start_thinking(self):
        self._think_dots = 0
        self._current_tool_name = ""
        self._thinking_label.setText("⏳ " + t("chat.thinking"))
        self._think_timer.start(400)

    def _animate_thinking(self):
        self._think_dots += 1
        dots = '.' * ((self._think_dots % 3) + 1)
        if self._current_tool_name:
            self._thinking_label.setText(f"⏳ {self._current_tool_name} {dots}")
        else:
            self._thinking_label.setText(f"⏳ " + t("chat.thinking_dots", dots=dots))

    def _stop_thinking(self):
        self._think_timer.stop()
        self._thinking_label.setText("")
        self._finish_active_chip()  # 남은 active chip 정리
        self._current_tool_name = ""

    # ── 상태 ──

    def set_connected(self, connected: bool, info: str = ""):
        if connected:
            self._status_label.setText(t("chat.status_connected") + (f" — {info}" if info else ""))
            self._status_label.setStyleSheet(f"color: {C['green']}; font-size: 11px; font-family: 'Segoe UI';")
        else:
            self._status_label.setText(t("chat.status_disconnected"))
            self._status_label.setStyleSheet(f"color: {C['red']}; font-size: 11px; font-family: 'Segoe UI';")

    def set_workflow_progress(self, wf_name: str, step: int, total: int, status: str):
        """워크플로우 진행 배너 표시/업데이트"""
        self._wf_banner.setVisible(True)
        self._wf_name_label.setText(wf_name)
        if total > 0 and step > 0:
            self._wf_step_label.setText(f"({step}/{total})")
        else:
            self._wf_step_label.setText("")
        if status == "paused":
            self._wf_icon.setText("⏸")
            self._wf_status_label.setText(t("chat.wf_status_paused"))
            self._wf_status_label.setStyleSheet(
                f"color: {C['yellow']}; font-size: 11px; font-family: 'Segoe UI';"
            )
            self._chat_input.setPlaceholderText(t("chat.wf_placeholder_paused", name=wf_name))
        else:
            self._wf_icon.setText("⚡")
            self._wf_status_label.setText(t("chat.wf_status_running"))
            self._wf_status_label.setStyleSheet(
                f"color: {C['green']}; font-size: 11px; font-family: 'Segoe UI';"
            )
            self._chat_input.setPlaceholderText(t("chat.wf_placeholder_running", name=wf_name))
        if not self._wf_pulse_timer.isActive():
            self._wf_pulse_on = True
            self._wf_pulse_timer.start()

    def clear_workflow_progress(self):
        """워크플로우 진행 배너 숨기기"""
        self._wf_banner.setVisible(False)
        self._wf_pulse_timer.stop()
        self._chat_input.setPlaceholderText(t("chat.placeholder_enter"))

    def _pulse_wf_banner(self):
        """배너 배경색 깜빡임"""
        self._wf_pulse_on = not self._wf_pulse_on
        if self._wf_pulse_on:
            bg = "stop:0 #0f3d1e, stop:0.5 #143822, stop:1 #0f3018"
        else:
            bg = "stop:0 #0a2a15, stop:0.5 #0d1f12, stop:1 #0a1a10"
        self._wf_banner.setStyleSheet(f"""
            #WfBanner {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {bg});
                border-bottom: 2px solid {C['green']}88;
                border-top: 1px solid {C['green']}30;
            }}
            #WfBanner QLabel {{ background: transparent; }}
        """)

    # ── 입력 ──

    def _stop_running_code(self):
        """실행 중인 코드 중지 (LLM 경유 없이 직접 서버 API 호출)"""
        import urllib.request, json as _json
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            with open(cfg_path, encoding="utf-8-sig") as f:
                cfg = _json.load(f)
            port = cfg.get("server", {}).get("port", 3000)
            api_key = cfg.get("server", {}).get("api_key", "")
            url = f"http://127.0.0.1:{port}/code/stop"
            data = _json.dumps({}).encode()
            req = urllib.request.Request(url, data=data, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = _json.loads(resp.read())
            msg = result.get("message", "중지 완료")
            self.add_bot_message(msg)
        except Exception as e:
            self.add_bot_message(t("desktop.code_stop_fail", error=str(e)))

    def _on_submit(self):
        text = self._chat_input.toPlainText().strip()
        if text:
            action = match_slash_command(text)
            if action:
                self._chat_input.clear()
                self._execute_slash_command(action)
                return
            self.sig_message_sent.emit(text)
            self._chat_input.clear()

    def _execute_slash_command(self, action: str):
        """슬래시 커맨드 직접 실행 (LLM 미경유)"""
        if action == "list_workflow":
            self.sig_message_sent.emit(t("chat.cmd_list_workflow"))
        elif action == "list_persona":
            self.sig_message_sent.emit(t("chat.cmd_list_persona"))
        elif action == "list_code":
            self.sig_message_sent.emit(t("chat.cmd_list_code"))
        elif action == "cancel":
            self.sig_message_sent.emit("/done")

    def focus_input(self):
        self._chat_input.setFocus()

    def clear_chat(self):
        self._pending_tool_chips = []
        self._session_tool_turns = []
        self._run_js("clearChat();")

    def _update_auth_label(self):
        """타이틀바의 auth 라벨을 ~/.xoul/auth.json 기반으로 갱신"""
        try:
            import json
            from pathlib import Path
            auth_file = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
            if auth_file.exists():
                data = json.loads(auth_file.read_text(encoding="utf-8"))
                token = data.get("token")
                if token:
                    user = data.get("user") or {}
                    name = user.get("name") or user.get("email", "User")
                    if len(name) > 12:
                        name = name[:12] + "…"
                    self._auth_label.setText(f"👤 {name}")
                    return
            self._auth_label.setText("👤 Login")
        except Exception:
            self._auth_label.setText("👤 Login")

    def _on_auth_clicked(self):
        """Auth 버튼 클릭 — 미로그인이면 로그인 다이얼로그, 로그인됐으면 Workflow Store"""
        try:
            import json
            from pathlib import Path
            auth_file = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
            if auth_file.exists():
                data = json.loads(auth_file.read_text(encoding="utf-8"))
                if data.get("token"):
                    self._show_workflow_store()
                    return
        except Exception:
            pass
        self._show_login_dialog()

    def _sync_owned_workflows(self):
        """보유 워크플로우 목록을 web backend에 동기화"""
        try:
            import json
            import urllib.request
            from pathlib import Path

            auth_file = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
            if not auth_file.exists():
                return
            auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
            token = auth_data.get("token")
            if not token:
                return

            # 1. VM 에이전트에서 워크플로우 이름 목록 가져오기
            server_cfg = {}
            try:
                config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
                with open(config_path, "r", encoding="utf-8-sig") as f:
                    server_cfg = json.load(f).get("server", {})
            except Exception:
                pass
            port = server_cfg.get("port", 3000)
            api_key = server_cfg.get("api_key", "")

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/workflows",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())

            # workflows → 이름 리스트 추출
            wf_list = data if isinstance(data, list) else data.get("workflows", [])
            names = []
            for w in wf_list:
                if isinstance(w, dict):
                    names.append(w.get("name", ""))
                elif isinstance(w, str):
                    names.append(w)
            names = [n for n in names if n]

            if not names:
                return

            # 2. web backend에 보유 목록 동기화
            body = json.dumps({"workflows": names}).encode()
            req2 = urllib.request.Request(
                f"{WEB_CFG['backend_url']}/api/workflows/owned",
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="PUT",
            )
            urllib.request.urlopen(req2, timeout=3)
        except Exception:
            pass  # 동기화 실패 시 무시

    def _show_login_dialog(self):
        """로그인 WebView 다이얼로그 — 로그인 완료 시 자동 닫힘"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtCore import QUrl, QTimer
        import json, re
        from datetime import datetime

        log_path = os.path.join(os.path.expanduser("~"), ".xoul", "auth_debug.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        def _log(msg):
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            except Exception:
                pass

        _log("=== Login dialog opened ===")

        dialog = QDialog(self)
        dialog.setWindowTitle("🔐 Login")
        dialog.setMinimumSize(500, 650)
        dialog.setStyleSheet(f"QDialog {{ background-color: {C['base']}; }}")
        _login_done = {"done": False}

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)

        _self = self

        def _save_token_and_close(token):
            """token 저장 → dialog 닫기 → label 갱신"""
            if _login_done["done"]:
                return
            _login_done["done"] = True
            _log(f"Token detected! ({token[:30]}...)")

            # 사용자 정보 가져오기
            user = None
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"{WEB_CFG['backend_url']}/api/auth/me",
                    headers={"Authorization": f"Bearer {token}"}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    user = json.loads(resp.read().decode())
                _log(f"User info OK: {user.get('name', user.get('email', '?'))}")
            except Exception as e:
                _log(f"User info FAIL: {e}")

            # auth.json에 직접 저장 (import 경로 문제 회피)
            try:
                from pathlib import Path
                auth_file = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
                auth_file.parent.mkdir(parents=True, exist_ok=True)
                data = {"token": token}
                if user:
                    data["user"] = user
                auth_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                _log("auth.json SAVED!")
            except Exception as e:
                _log(f"auth.json FAIL: {e}")

            # 라벨 갱신 → 다이얼로그 닫기
            _self._update_auth_label()
            QTimer.singleShot(100, dialog.accept)

        # ExternalLinkPage 사용 — OAuth URL을 WebView 안에서 처리
        page = ExternalLinkPage(web_view := QWebEngineView())
        page._chat_window = self
        web_view.setPage(page)

        # 방법1: urlChanged로 URL에서 token 감지
        def _on_url_changed(url):
            url_str = url.toString()
            _log(f"URL changed: {url_str[:120]}")
            m = re.search(r'[?&#]token=([^&#]+)', url_str)
            if m:
                _save_token_and_close(m.group(1))

        web_view.urlChanged.connect(_on_url_changed)

        # 방법2: loadFinished 후 localStorage 체크 (가장 확실)
        def _on_load_finished(ok):
            if not ok or _login_done["done"]:
                return
            _log("Page loaded, checking localStorage...")

            def _on_ls_result(val):
                _log(f"localStorage token: {val[:30] + '...' if val else 'null'}")
                if val and not _login_done["done"]:
                    _save_token_and_close(val)

            web_view.page().runJavaScript(
                "localStorage.getItem('xoul_token')",
                _on_ls_result
            )

        web_view.loadFinished.connect(_on_load_finished)

        # 방법3: localStorage 폴링 fallback (이메일 로그인 등)
        poll_timer = QTimer(dialog)

        def _check_login():
            if _login_done["done"]:
                poll_timer.stop()
                return
            web_view.page().runJavaScript(
                "localStorage.getItem('xoul_token')",
                lambda val: _save_token_and_close(val) if val and not _login_done["done"] else None
            )

        poll_timer.timeout.connect(_check_login)
        poll_timer.start(2000)

        web_view.load(QUrl(f"{WEB_CFG['frontend_url']}/login"))
        layout.addWidget(web_view)

        # dialog 닫힐 때 마지막 시도: WebView localStorage에서 추출
        def _on_dialog_finished():
            poll_timer.stop()
            if _login_done["done"]:
                return
            # 마지막으로 localStorage 체크
            try:
                web_view.page().runJavaScript(
                    "localStorage.getItem('xoul_token')",
                    lambda val: _save_token_only(val) if val else None
                )
            except Exception:
                pass

        def _save_token_only(token):
            """dialog 닫힌 후 — accept 없이 저장만"""
            if _login_done["done"] or not token:
                return
            _login_done["done"] = True
            _log(f"Token from localStorage on close: {token[:20]}...")
            user = None
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"{WEB_CFG['backend_url']}/api/auth/me",
                    headers={"Authorization": f"Bearer {token}"}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    user = json.loads(resp.read().decode())
            except Exception:
                pass
            try:
                from pathlib import Path
                auth_file = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
                auth_file.parent.mkdir(parents=True, exist_ok=True)
                data = {"token": token}
                if user:
                    data["user"] = user
                auth_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            _self._update_auth_label()

        dialog.finished.connect(_on_dialog_finished)
        dialog.exec()

    def _show_web_menu(self):
        """🌐 Web 버튼 → 컨텍스트 메뉴 (Store + Arena)"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface2']}; border-radius: 8px; padding: 4px;
            }}
            QMenu::item {{ padding: 6px 16px; border-radius: 4px; }}
            QMenu::item:selected {{ background-color: {C['surface1']}; }}
        """)
        act_store = menu.addAction("🛒 Workflow Store")
        act_arena = menu.addAction("🎮 AI Arena")
        act_store.triggered.connect(self._show_workflow_store)
        act_arena.triggered.connect(self._open_arena)
        menu.exec(self._web_btn.mapToGlobal(self._web_btn.rect().topLeft()))

    def _show_workflow_store(self):
        """🛒 Workflow Store — QWebEngineView로 웹 스토어 표시"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtCore import QUrl

        # 보유 워크플로우 동기화 (서버에 알려주기)
        self._sync_owned_workflows()

        dialog = QDialog()  # parent=None — 독립 윈도우로 생성
        dialog.setWindowTitle("🛒 Workflow Store")
        dialog.setWindowFlags(Qt.WindowType.Window)  # 독립 윈도우 (modal 방지)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setMinimumSize(1200, 700)
        dialog.resize(1400, 900)
        dialog.setStyleSheet(f"QDialog {{ background-color: {C['base']}; }}")
        self._store_dialog = dialog  # import 성공 시 닫기 위해 참조 보관

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)

        web_view = QWebEngineView()
        # 캐시 비활성화 — 항상 최신 페이지 로드
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        profile = web_view.page().profile()
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        # ExternalLinkPage 사용 — wfimport: URL을 가로챌 수 있음
        page = ExternalLinkPage(web_view)
        page._chat_window = self
        web_view.setPage(page)

        # Auth token injection: ~/.xoul/auth.json → WebView localStorage
        def _inject_auth():
            try:
                import json, base64, time as _time
                from pathlib import Path
                auth_file = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
                if not auth_file.exists():
                    return
                auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
                token = auth_data.get("token")
                if token:
                    # JWT 만료 체크
                    try:
                        payload = json.loads(base64.b64decode(token.split('.')[1] + '=='))
                        if payload.get('exp') and _time.time() >= payload['exp']:
                            return  # 만료된 토큰은 주입하지 않음
                    except Exception:
                        pass
                    user = auth_data.get("user")
                    user_json = json.dumps(user, ensure_ascii=False) if user else 'null'
                    js = f"""
                        localStorage.setItem('xoul_token', '{token}');
                        if ({user_json} !== null) {{
                            localStorage.setItem('xoul_user', JSON.stringify({user_json}));
                        }}
                    """
                    web_view.page().runJavaScript(js)
            except Exception:
                pass

        # 토큰 주입 순서: 빈 페이지 로드 → token 주입 → /workflows로 이동
        def _on_first_load(ok):
            if not ok:
                return
            web_view.loadFinished.disconnect(_on_first_load)
            _inject_auth()
            # token 주입 후 약간 대기 → 코드 페이지로 이동
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, lambda: web_view.load(QUrl(f"{WEB_CFG['frontend_url']}/workflows")))

        web_view.loadFinished.connect(_on_first_load)
        web_view.load(QUrl(WEB_CFG['frontend_url']))  # 먼저 origin 로드 (localStorage 접근 가능)

        layout.addWidget(web_view)

        # WebView에서 로그인 후 token을 auth.json에 동기화
        def _sync_auth_from_webview():
            """Dialog 닫히기 전에 WebView localStorage에서 token 추출 → auth.json"""
            try:
                from PyQt6.QtCore import QEventLoop, QTimer
                import json
                from pathlib import Path

                loop = QEventLoop()
                result = {}

                def _on_token(val):
                    result['token'] = val
                    loop.quit()

                web_view.page().runJavaScript(
                    "JSON.stringify({token: localStorage.getItem('xoul_token'), user: localStorage.getItem('xoul_user')})",
                    _on_token
                )

                QTimer.singleShot(1000, loop.quit)
                loop.exec()

                if result.get('token'):
                    data = json.loads(result['token'])
                    token = data.get('token')
                    if token:
                        user = None
                        if data.get('user'):
                            try:
                                user = json.loads(data['user'])
                            except Exception:
                                pass
                        auth_file = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
                        auth_file.parent.mkdir(parents=True, exist_ok=True)
                        save_data = {"token": token}
                        if user:
                            save_data["user"] = user
                        auth_file.write_text(json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        dialog.finished.connect(lambda: _sync_auth_from_webview())
        dialog.finished.connect(lambda: self._update_auth_label())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _open_arena(self):
        """🎮 AI Arena — WebView로 아레나 페이지 열기"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtCore import QUrl, QTimer

        dialog = QDialog()  # parent=None — 독립 윈도우로 생성
        dialog.setWindowTitle("🎮 AI Arena")
        dialog.setWindowFlags(Qt.WindowType.Window)  # 독립 윈도우 (modal 방지)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setMinimumSize(1100, 700)
        dialog.resize(1300, 850)
        dialog.setStyleSheet(f"QDialog {{ background-color: {C['base']}; }}")
        self._arena_dialog = dialog  # arenajoin: 처리 시 닫기 위해 참조 보관

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)

        web_view = QWebEngineView()
        # 캐시 비활성화 — 항상 최신 페이지 로드
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        profile = web_view.page().profile()
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        page = ExternalLinkPage(web_view)
        page._chat_window = self
        web_view.setPage(page)

        # Auth token 주입
        def _inject_auth():
            try:
                import json, base64, time as _time
                from pathlib import Path
                auth_file = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
                if not auth_file.exists():
                    return
                auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
                token = auth_data.get("token")
                if token:
                    # JWT 만료 체크
                    try:
                        payload = json.loads(base64.b64decode(token.split('.')[1] + '=='))
                        if payload.get('exp') and _time.time() >= payload['exp']:
                            return  # 만료된 토큰은 주입하지 않음
                    except Exception:
                        pass
                    user = auth_data.get("user")
                    user_json = json.dumps(user, ensure_ascii=False) if user else 'null'
                    js = f"""
                        localStorage.setItem('xoul_token', '{token}');
                        if ({user_json} !== null) {{
                            localStorage.setItem('xoul_user', JSON.stringify({user_json}));
                        }}
                    """
                    web_view.page().runJavaScript(js)
            except Exception:
                pass

        def _on_first_load(ok):
            if not ok:
                return
            web_view.loadFinished.disconnect(_on_first_load)
            _inject_auth()
            QTimer.singleShot(100, lambda: web_view.load(QUrl(f"{WEB_CFG['frontend_url']}/arena")))

        web_view.loadFinished.connect(_on_first_load)
        web_view.load(QUrl(WEB_CFG['frontend_url']))

        layout.addWidget(web_view)

        # WebView에서 로그인 후 token을 auth.json에 동기화
        _self = self
        def _sync_auth_from_webview():
            try:
                from PyQt6.QtCore import QEventLoop, QTimer as QT2
                import json as _json
                from pathlib import Path

                loop = QEventLoop()
                result = {}

                def _on_token(val):
                    result['token'] = val
                    loop.quit()

                web_view.page().runJavaScript(
                    "JSON.stringify({token: localStorage.getItem('xoul_token'), user: localStorage.getItem('xoul_user')})",
                    _on_token
                )

                QT2.singleShot(1000, loop.quit)
                loop.exec()

                if result.get('token'):
                    data = _json.loads(result['token'])
                    token = data.get('token')
                    if token:
                        user = None
                        if data.get('user'):
                            try:
                                user = _json.loads(data['user'])
                            except Exception:
                                pass
                        auth_file = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
                        auth_file.parent.mkdir(parents=True, exist_ok=True)
                        save_data = {"token": token}
                        if user:
                            save_data["user"] = user
                        auth_file.write_text(_json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        dialog.finished.connect(lambda: _sync_auth_from_webview())
        dialog.finished.connect(lambda: self._update_auth_label())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _auto_import_missing_codes(self, steps):
        """code_name 스텝이 참조하는 코드(ID)를 로컬에 없으면 웹 Store에서 자동 import.
        Returns (all_ok: bool, id_to_name: dict) — id_to_name maps code_id → local_name."""
        import json as _json
        import urllib.request

        # code_name(=code ID) 수집
        code_ids = []
        for s in steps:
            if isinstance(s, dict) and s.get("type") == "code" and s.get("code_name"):
                cid = s.get("code_name") or s.get("content", "")
                if cid:
                    code_ids.append(cid)
        if not code_ids:
            return True, {}

        # config.json 읽기
        try:
            from pathlib import Path
            cfg_path = Path(__file__).resolve().parent.parent / "config.json"
            cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            api_key = cfg.get("server", {}).get("api_key", "")
            port = cfg.get("server", {}).get("port", 3000)
            web_url = cfg.get("web", {}).get("backend_url", "")
        except Exception:
            return True, {}

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        # 로컬 코드 목록 (이름 기준)
        local_codes = set()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/codes", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode())
            for cn in data.get("codes", []):
                local_codes.add(cn)
        except Exception:
            pass

        all_ok = True
        id_to_name = {}
        from urllib.parse import quote as _q
        for cid in code_ids:
            # 이미 로컬에 있는지 확인 (이름 or ID로)
            if cid in local_codes:
                continue

            if not web_url:
                self.add_bot_message(t("chat.code_not_local", cid=cid))
                all_ok = False
                continue

            try:
                # ID로 직접 조회
                req = urllib.request.Request(f"{web_url}/api/codes/{_q(cid, safe='')}")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    code_detail = _json.loads(resp.read().decode())

                # 이름 해석 (i18n → 현재 언어)
                raw_name = code_detail.get("name", cid)
                if isinstance(raw_name, str):
                    try:
                        parsed = _json.loads(raw_name)
                        if isinstance(parsed, dict):
                            from desktop.i18n import get_lang
                            _lang = get_lang()
                            raw_name = parsed.get(_lang, parsed.get("en", parsed.get("ko", cid)))
                    except Exception:
                        pass
                local_name = raw_name if isinstance(raw_name, str) else cid

                id_to_name[cid] = local_name

                # 이미 이름으로 존재하는지 재확인
                if local_name in local_codes:
                    continue

                # 로컬에 import
                body = _json.dumps({
                    "name": local_name,
                    "description": code_detail.get("description", ""),
                    "code": code_detail.get("code", ""),
                    "params": _json.dumps(code_detail.get("params", []), ensure_ascii=False),
                }).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/code/import",
                    data=body,
                    headers={**headers, "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    _json.loads(resp.read().decode())
                self.add_bot_message(t("chat.code_auto_import_ok", name=local_name))
                local_codes.add(local_name)

                # 다운로드 카운트
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        f"{web_url}/api/codes/{_q(cid, safe='')}/download",
                        method="POST"
                    ), timeout=5)
                except Exception:
                    pass

            except Exception as e:
                self.add_bot_message(t("chat.code_auto_import_fail", cid=cid, error=str(e)))
                all_ok = False

        return all_ok, id_to_name

    def _handle_wf_import(self, b64_data: str):
        """wfimport: URL 처리 — base64 decode → 편집 다이얼로그 오픈 (수정 후 저장)"""
        import base64
        import json

        try:
            decoded = base64.b64decode(b64_data).decode("utf-8")
            wf = json.loads(decoded)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t("desktop.import_fail"), t("desktop.data_parse_error", error=str(e)))
            return

        name = wf.get("name", "")
        if not name:
            return

        # steps: JSON 문자열로 된 배열 → 파싱
        steps_raw = wf.get("prompts", "[]")
        try:
            steps_list = json.loads(steps_raw) if isinstance(steps_raw, str) else steps_raw
        except Exception:
            # newline-joined 텍스트일 경우 분리
            if isinstance(steps_raw, str) and "\n" in steps_raw:
                steps_list = [s.strip() for s in steps_raw.split("\n") if s.strip()]
            else:
                steps_list = [steps_raw] if steps_raw else []

        # i18n content 해석 (step 구조 보존, prompt는 i18n dict 유지)
        def _resolve(val):
            """Display용 해석 — 현재 언어로 resolve"""
            if isinstance(val, dict) and ("ko" in val or "en" in val):
                from desktop.i18n import get_lang
                lang = get_lang()
                return val.get(lang, val.get("en", val.get("ko", str(val))))
            return str(val) if val else ""

        resolved_steps = []
        for step in (steps_list if isinstance(steps_list, list) else [steps_list]):
            if isinstance(step, dict):
                raw_content = step.get("content", "")
                display_content = _resolve(raw_content)
                stype = step.get("type", "prompt")
                # code_name 타입 → code 타입으로 정규화 (code_name은 필드로만 사용)
                if stype == "code_name":
                    stype = "code"
                is_code_ref = (stype == "code" and not display_content.startswith("def "))
                step_data = {
                    "type": stype,
                    "content": display_content,
                    "code_name": display_content if is_code_ref else step.get("code_name", ""),
                }
                # prompt 스텝은 i18n dict 보존 (DB 저장 시 사용)
                if stype == "prompt" and isinstance(raw_content, dict):
                    step_data["_i18n_content"] = raw_content
                resolved_steps.append(step_data)
            else:
                resolved_steps.append(_resolve(step))

        prefill = {
            "name": name,
            "description": wf.get("description", ""),
            "steps": resolved_steps,
            "schedule": wf.get("schedule", ""),
            "hint_tools": wf.get("hint_tools", ""),
            "is_import": True,
        }

        # code_name 스텝이 참조하는 코드가 로컬에 없으면 자동 import
        all_ok, id_to_name = self._auto_import_missing_codes(resolved_steps)

        if not all_ok:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t("chat.import_fail_title"), t("chat.import_fail_partial"))
            return

        # code_name ID를 로컬 이름으로 변환 (로컬 서버 /code/{name} 호환)
        for step in resolved_steps:
            if isinstance(step, dict) and step.get("type") == "code" and step.get("code_name"):
                cid = step.get("code_name", "")
                if cid in id_to_name:
                    step["code_name"] = id_to_name[cid]
                    step["content"] = id_to_name[cid]

        prefill["steps"] = resolved_steps
        self._show_workflow_dialog(prefill=prefill)

    def _handle_persona_import(self, b64_data: str):
        """personaimport: URL 처리 — base64 decode → /persona/import API 호출"""
        import base64
        import json as _json
        import urllib.request
        import urllib.error

        try:
            decoded = base64.b64decode(b64_data).decode("utf-8")
            p = _json.loads(decoded)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t("chat.import_fail_title"), t("chat.import_data_parse_error", error=str(e)))
            return

        name = p.get("name", "")
        if not name:
            return

        # config.json에서 API 키/포트 읽기
        try:
            from pathlib import Path
            cfg_path = Path(__file__).resolve().parent.parent / "config.json"
            cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            api_key = cfg.get("server", {}).get("api_key", "")
            port = cfg.get("server", {}).get("port", 3000)

            body = _json.dumps({
                "name": name,
                "description": p.get("description", ""),
                "prompt": p.get("prompt", ""),
                "bg_image": p.get("bg_image", ""),
            }).encode()
            req = urllib.request.Request(
                f"http://localhost:{port}/persona/import",
                data=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = _json.loads(resp.read().decode())

            self.add_bot_message(f"🎭 {result.get('message', t('chat.persona_import_ok_fallback'))}")

            # Store 다이얼로그 닫기
            if hasattr(self, '_store_dialog') and self._store_dialog:
                self._store_dialog.accept()

            # Import 후 자동으로 페르소나 목록 표시
            QTimer.singleShot(300, lambda: self.sig_message_sent.emit(t("chat.cmd_list_persona")))

        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode() if e.fp else str(e)
            except Exception:
                detail = str(e)
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t("desktop.import_fail"), t("desktop.server_error", detail=detail))
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t("desktop.import_fail"), t("desktop.persona_import_fail", error=str(e)))

    def _handle_code_import(self, b64_data: str):
        """codeimport: URL 처리 — base64 decode → /code/import API 호출"""
        import base64
        import json as _json
        import urllib.request
        import urllib.error

        try:
            decoded = base64.b64decode(b64_data).decode("utf-8")
            c = _json.loads(decoded)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t("chat.import_fail_title"), t("chat.import_data_parse_error", error=str(e)))
            return

        name = c.get("name", "")
        if not name:
            return

        try:
            from pathlib import Path
            cfg_path = Path(__file__).resolve().parent.parent / "config.json"
            cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            api_key = cfg.get("server", {}).get("api_key", "")
            port = cfg.get("server", {}).get("port", 3000)

            body = _json.dumps({
                "name": name,
                "description": c.get("description", ""),
                "code": c.get("code", ""),
                "params": _json.dumps(c.get("params", []), ensure_ascii=False),
            }).encode()
            req = urllib.request.Request(
                f"http://localhost:{port}/code/import",
                data=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = _json.loads(resp.read().decode())

            self.add_bot_message(f"🐍 {result.get('message', t('chat.code_import_ok_fallback'))}")

            # Import 후 자동으로 코드 목록 갱신 표시
            try:
                list_req = urllib.request.Request(
                    f"http://localhost:{port}/code/list",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                with urllib.request.urlopen(list_req, timeout=5) as lr:
                    list_result = lr.read().decode()
                self.render_code_list(list_result)
            except Exception:
                pass  # 목록 갱신 실패해도 import는 성공

            # Store 다이얼로그 닫기
            if hasattr(self, '_store_dialog') and self._store_dialog:
                self._store_dialog.accept()
            elif hasattr(self, '_arena_dialog') and self._arena_dialog:
                self._arena_dialog.accept()

        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode() if e.fp else str(e)
            except Exception:
                detail = str(e)
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t("desktop.import_fail"), t("desktop.server_error", detail=detail))
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t("desktop.import_fail"), t("desktop.code_import_fail", error=str(e)))

    def _handle_arena_join(self, b64_data: str):
        """arenajoin: URL 처리 — 게임 타입에 따라 분기"""
        import base64
        import json as _json
        import urllib.request
        import urllib.error

        try:
            decoded = base64.b64decode(b64_data).decode("utf-8")
            info = _json.loads(decoded)
        except Exception as e:
            self.add_bot_message(t("desktop.arena_parse_error", error=str(e)))
            return

        game_id = info.get("game_id", "")
        game_type = info.get("game_type", "mafia")
        agent_name = info.get("agent_name", "Xoul에이전트")
        persona = info.get("persona", "분석적이고 논리적인 성격.")

        # config.json에서 API 키/포트 읽기
        try:
            from pathlib import Path
            cfg_path = Path(__file__).resolve().parent.parent / "config.json"
            cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            api_key = cfg.get("server", {}).get("api_key", "")
            port = cfg.get("server", {}).get("port", 3000)
            web_url = cfg.get("web", {}).get("backend_url", "")
        except Exception:
            api_key, port, web_url = "", 3000, ""

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        # Arena 다이얼로그 닫기
        if hasattr(self, '_arena_dialog') and self._arena_dialog:
            self._arena_dialog.accept()

        # ── 게임 타입별 분기 ──

        if game_type == "discussion":
            # Discussion: 코드 실행 불필요 — REST API로 직접 참가
            self._join_discussion_direct(game_id, agent_name, persona, api_key, web_url)
        else:
            # Mafia (및 기타): 기존 방식 — 저장된 코드 실행
            self._join_mafia_via_code(game_id, agent_name, persona, headers, port, web_url)

    def _join_discussion_direct(self, game_id, agent_name, persona, api_key, web_url):
        """Discussion 게임 — 토론 에이전트 코드 자동 import → 실행"""
        import json as _json
        import urllib.request

        port = 3000
        try:
            from pathlib import Path
            cfg_path = Path(__file__).resolve().parent.parent / "config.json"
            cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            port = cfg.get("server", {}).get("port", 3000)
        except Exception:
            pass

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        # 1. 로컬에 토론 에이전트 코드가 있는지 체크 (실제 이름 가져오기)
        actual_code_name = None
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/codes",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode())
            code_list = data.get("codes", [])
            for cn in code_list:
                if "토론" in cn or "discussion" in cn.lower():
                    actual_code_name = cn
                    break
        except Exception:
            pass

        # 2. 없으면 웹 서버에서 자동 import
        if not actual_code_name and web_url:
            try:
                req = urllib.request.Request(f"{web_url}/api/codes/discussion-agent-v1")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    code_data = _json.loads(resp.read().decode())

                imported_name = code_data.get("name", "Discussion Agent")
                body = _json.dumps({
                    "name": imported_name,
                    "description": code_data.get("description", ""),
                    "code": code_data.get("code", ""),
                    "params": _json.dumps(code_data.get("params", []), ensure_ascii=False),
                }).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/code/import",
                    data=body,
                    headers={**headers, "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = _json.loads(resp.read().decode())
                self.add_bot_message(f"💬 {result.get('message', t('chat.discussion_import_ok_fallback'))}")
                actual_code_name = imported_name

                try:
                    urllib.request.urlopen(urllib.request.Request(
                        f"{web_url}/api/codes/discussion-agent-v1/download",
                        method="POST"
                    ), timeout=5)
                except Exception:
                    pass
            except Exception as e:
                self.add_bot_message(t("desktop.agent_import_fail", type=t('desktop.discussion'), error=str(e)))

        # 3. LLM에게 run_stored_code 도구 사용 요청
        code_name = actual_code_name or "Discussion Agent"
        params_json = _json.dumps({
            "game_id": game_id,
            "agent_name": agent_name,
            "persona": persona,
        }, ensure_ascii=False)
        msg = t("chat.llm_run_code", code_name=code_name, params=params_json)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(300, lambda: self.sig_message_sent.emit(msg))

    def _join_mafia_via_code(self, game_id, agent_name, persona, headers, port, web_url):
        """Mafia 게임 — 저장된 코드 자동 import → 실행 요청"""
        import json as _json
        import urllib.request

        # 1. 로컬에 "마피아게임 에이전트" 코드가 있는지 체크
        code_exists = False
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/codes",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode())
            code_list = data.get("codes", [])
            for cn in code_list:
                if "마피아" in cn or "arena" in cn.lower():
                    code_exists = True
                    break
        except Exception:
            pass

        # 2. 없으면 웹 서버에서 자동 import
        if not code_exists and web_url:
            try:
                # 웹 서버에서 코드 다운로드
                req = urllib.request.Request(f"{web_url}/api/codes/mafia-agent-v1")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    code_data = _json.loads(resp.read().decode())

                # 로컬 서버에 import
                body = _json.dumps({
                    "name": code_data.get("name", "마피아게임 에이전트"),
                    "description": code_data.get("description", ""),
                    "code": code_data.get("code", ""),
                    "params": _json.dumps(code_data.get("params", []), ensure_ascii=False),
                }).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/code/import",
                    data=body,
                    headers={**headers, "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = _json.loads(resp.read().decode())
                self.add_bot_message(f"🐍 {result.get('message', t('chat.mafia_import_ok_fallback'))}")

                # 다운로드 카운트 증가
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        f"{web_url}/api/codes/mafia-agent-v1/download",
                        method="POST"
                    ), timeout=5)
                except Exception:
                    pass
            except Exception as e:
                self.add_bot_message(t("desktop.agent_import_fail", type="Arena", error=str(e)))

        # 3. LLM에게 run_stored_code 도구 사용 요청
        params_json = _json.dumps({
            "game_id": game_id,
            "agent_name": agent_name,
            "persona": persona,
        }, ensure_ascii=False)
        msg = t("chat.llm_run_code", code_name="마피아게임 에이전트", params=params_json)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(300, lambda: self.sig_message_sent.emit(msg))

    def _show_code_editor(self, initial_code="", parent=None):
        """🐍 Python 코드 에디터 — 파라미터/본문 분리형, 시그니처 자동 생성"""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QLineEdit, QPushButton, QScrollArea, QWidget,
            QPlainTextEdit, QComboBox
        )
        from PyQt6.QtGui import QFont, QTextCursor, QSyntaxHighlighter, QTextCharFormat, QColor
        from PyQt6.QtCore import Qt, QRegularExpression
        import ast as _ast
        import re as _re
        import textwrap as _tw

        # ── Python Syntax Highlighter ──
        class PythonHighlighter(QSyntaxHighlighter):
            def __init__(self_, parent_doc=None):
                super().__init__(parent_doc)
                self_._rules = []

                # 키워드
                kw_fmt = QTextCharFormat()
                kw_fmt.setForeground(QColor(C['mauve']))
                kw_fmt.setFontWeight(QFont.Weight.Bold)
                keywords = [
                    'False', 'None', 'True', 'and', 'as', 'assert', 'async',
                    'await', 'break', 'class', 'continue', 'def', 'del', 'elif',
                    'else', 'except', 'finally', 'for', 'from', 'global', 'if',
                    'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or',
                    'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
                ]
                for kw in keywords:
                    self_._rules.append((QRegularExpression(rf'\b{kw}\b'), kw_fmt))

                # 내장 함수
                builtin_fmt = QTextCharFormat()
                builtin_fmt.setForeground(QColor(C['teal']))
                builtins_list = [
                    'print', 'len', 'range', 'int', 'str', 'float', 'bool',
                    'list', 'dict', 'set', 'tuple', 'type', 'isinstance',
                    'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed',
                    'open', 'input', 'abs', 'max', 'min', 'sum', 'any', 'all',
                    'hasattr', 'getattr', 'setattr', 'super',
                ]
                for bn in builtins_list:
                    self_._rules.append((QRegularExpression(rf'\b{bn}\b'), builtin_fmt))

                # 숫자
                num_fmt = QTextCharFormat()
                num_fmt.setForeground(QColor(C['peach']))
                self_._rules.append((QRegularExpression(r'\b[0-9]+(\.[0-9]+)?\b'), num_fmt))

                # 데코레이터
                deco_fmt = QTextCharFormat()
                deco_fmt.setForeground(QColor(C['yellow']))
                self_._rules.append((QRegularExpression(r'@\w+'), deco_fmt))

                # self
                self_fmt = QTextCharFormat()
                self_fmt.setForeground(QColor(C['red']))
                self_fmt.setFontItalic(True)
                self_._rules.append((QRegularExpression(r'\bself\b'), self_fmt))

                # 문자열 (싱글/더블 쿼트)
                str_fmt = QTextCharFormat()
                str_fmt.setForeground(QColor(C['green']))
                self_._str_fmt = str_fmt
                self_._rules.append((QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), str_fmt))
                self_._rules.append((QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), str_fmt))

                # 주석
                comment_fmt = QTextCharFormat()
                comment_fmt.setForeground(QColor(C['overlay0']))
                comment_fmt.setFontItalic(True)
                self_._comment_fmt = comment_fmt
                self_._rules.append((QRegularExpression(r'#[^\n]*'), comment_fmt))

            def highlightBlock(self_, text):
                for pattern, fmt in self_._rules:
                    it = pattern.globalMatch(text)
                    while it.hasNext():
                        match = it.next()
                        self_.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        # ── initial_code 파싱 (기존 코드 편집 시) ──
        init_params = []  # [{"name": str, "type": str, "desc": str, "default": str}]
        init_body = "    # 여기에 코드 작성\n    \n    return \"결과\""
        if initial_code and initial_code.strip():
            try:
                tree = _ast.parse(initial_code)
                for node in _ast.walk(tree):
                    if isinstance(node, _ast.FunctionDef):
                        # 파라미터 추출 (default 값 포함)
                        defaults = node.args.defaults
                        num_args = len(node.args.args)
                        num_defaults = len(defaults)
                        for idx, arg in enumerate(node.args.args):
                            p_name = arg.arg
                            p_type = "str"
                            if arg.annotation and isinstance(arg.annotation, _ast.Name):
                                p_type = arg.annotation.id
                            # default 값 추출 (뒤에서부터 매칭)
                            default_idx = idx - (num_args - num_defaults)
                            p_default = ""
                            if default_idx >= 0:
                                dnode = defaults[default_idx]
                                try:
                                    p_default = str(_ast.literal_eval(dnode))
                                except Exception:
                                    p_default = _ast.dump(dnode)
                            init_params.append({"name": p_name, "type": p_type, "desc": "", "default": p_default})
                        # docstring에서 파라미터 설명 추출
                        docstring = _ast.get_docstring(node) or ""
                        for line in docstring.split("\n"):
                            line = line.strip()
                            m = _re.match(r'^(\w+)\s*[:\-–]\s*(.+)$', line)
                            if m:
                                pn, pd = m.group(1), m.group(2)
                                for p in init_params:
                                    if p["name"] == pn:
                                        p["desc"] = pd
                        # 본문 추출 — AST 기반으로 정확히
                        # node.body에서 docstring 제외한 나머지의 시작 줄 찾기
                        body_start_line = node.body[0].end_lineno + 1 if docstring and node.body else node.lineno + 1
                        if docstring and len(node.body) > 1:
                            body_start_line = node.body[1].lineno
                        elif not docstring and node.body:
                            body_start_line = node.body[0].lineno
                        else:
                            body_start_line = node.end_lineno + 1  # 빈 함수

                        src_lines = initial_code.split("\n")
                        body_lines = src_lines[body_start_line - 1:]  # 0-indexed
                        if body_lines:
                            init_body = "\n".join(body_lines)
                        break
            except Exception:
                init_body = initial_code  # 파싱 실패 시 전체를 body로

        dialog = QDialog(parent or self)
        dialog.setWindowTitle("🐍 Code Step 편집기")
        dialog.setMinimumWidth(680)
        dialog.setMinimumHeight(520)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {C['base']}; color: {C['text']}; }}
            QLabel {{ color: {C['text']}; font-family: 'Segoe UI'; }}
            QLineEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 4px;
                padding: 2px 6px; font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {C['blue']}; }}
            QComboBox {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 4px;
                padding: 2px 6px; font-size: 12px; min-width: 60px;
            }}
            QComboBox:focus {{ border-color: {C['blue']}; }}
            QComboBox QAbstractItemView {{
                background-color: {C['surface0']}; color: {C['text']};
                selection-background-color: {C['surface2']};
            }}
        """)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(6)

        # ── 안내 ──
        guide = QLabel("💡 파라미터를 정의하면 LLM이 이전 결과에서 자동 바인딩합니다. return 값이 다음 스텝으로 전달됩니다.")
        guide.setStyleSheet(f"font-size: 11px; color: {C['subtext0']}; padding: 4px 8px; "
                           f"background: {C['surface0']}; border-radius: 6px;")
        guide.setWordWrap(True)
        layout.addWidget(guide)

        # ── 1. 파라미터 영역 ──
        param_header = QLabel("📋 파라미터")
        param_header.setStyleSheet("font-size: 12px; font-weight: bold;")
        layout.addWidget(param_header)

        param_scroll = QScrollArea()
        param_scroll.setWidgetResizable(True)
        param_scroll.setMaximumHeight(120)
        param_scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {C['surface1']}; "
                                   f"border-radius: 6px; background: {C['mantle']}; }}")
        param_container = QWidget()
        param_container.setStyleSheet(f"background: {C['mantle']};")
        param_layout = QVBoxLayout(param_container)
        param_layout.setSpacing(4)
        param_layout.setContentsMargins(6, 6, 6, 6)
        param_inputs = []  # [(container, name_input, type_combo, desc_input, default_input)]

        TYPE_OPTIONS = ["str", "int", "float", "bool"]

        # 시그니처 프리뷰 (읽기 전용, 눈에 잘 보이게)
        sig_preview = QPlainTextEdit()
        sig_preview.setReadOnly(True)
        sig_preview.setFont(QFont("Consolas", 11))
        sig_preview.setMaximumHeight(80)
        sig_preview.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['blue']}; border-radius: 4px;
                padding: 4px 8px; font-family: 'Consolas', monospace;
            }}
        """)
        sig_highlighter = PythonHighlighter(sig_preview.document())

        def _update_sig_preview():
            """파라미터 변경 시 시그니처 프리뷰 갱신"""
            params = []
            for _, n_inp, t_combo, d_inp, df_inp in param_inputs:
                pn = n_inp.text().strip()
                if pn:
                    pt = t_combo.currentText()
                    pd = d_inp.text().strip()
                    pf = df_inp.text().strip()
                    params.append({"name": pn, "type": pt, "desc": pd, "default": pf})
            args_parts = []
            for p in params:
                if p['default']:
                    args_parts.append(f"{p['name']}: {p['type']} = {repr(p['default'])}")
                else:
                    args_parts.append(f"{p['name']}: {p['type']}")
            args_str = ", ".join(args_parts)
            lines = [f"def run({args_str}):"]
            if params:
                lines.append('    """')
                for p in params:
                    desc = p['desc'] or '(설명 필요)'
                    lines.append(f"    {p['name']}: {desc}")
                lines.append('    """')
            sig_preview.setPlainText("\n".join(lines))

        def add_param(name="", ptype="str", desc="", default=""):
            row = QHBoxLayout()
            row.setSpacing(4)

            name_inp = QLineEdit(name)
            name_inp.setFixedHeight(28)
            name_inp.setFixedWidth(100)
            name_inp.setPlaceholderText(t("chat.param_name_placeholder"))
            name_inp.setStyleSheet(f"""
                QLineEdit {{
                    background: {C['surface0']}; color: {C['text']};
                    border: 1px solid {C['surface1']}; border-radius: 3px;
                    padding: 1px 4px; font-size: 12px; font-family: 'Consolas', monospace;
                }}
            """)
            name_inp.textChanged.connect(lambda: _update_sig_preview())
            row.addWidget(name_inp)

            type_combo = QComboBox()
            type_combo.addItems(TYPE_OPTIONS)
            if ptype in TYPE_OPTIONS:
                type_combo.setCurrentText(ptype)
            type_combo.setFixedHeight(28)
            type_combo.setFixedWidth(70)
            type_combo.currentTextChanged.connect(lambda: _update_sig_preview())
            row.addWidget(type_combo)

            default_inp = QLineEdit(default)
            default_inp.setFixedHeight(28)
            default_inp.setFixedWidth(90)
            default_inp.setPlaceholderText("Default")
            default_inp.setStyleSheet(f"""
                QLineEdit {{
                    background: {C['surface0']}; color: {C['peach']};
                    border: 1px solid {C['surface1']}; border-radius: 3px;
                    padding: 1px 4px; font-size: 12px; font-family: 'Consolas', monospace;
                }}
            """)
            default_inp.textChanged.connect(lambda: _update_sig_preview())
            row.addWidget(default_inp)

            desc_inp = QLineEdit(desc)
            desc_inp.setFixedHeight(28)
            desc_inp.setPlaceholderText(t("chat.param_desc_placeholder"))
            desc_inp.setStyleSheet(f"""
                QLineEdit {{
                    background: {C['surface0']}; color: {C['subtext0']};
                    border: 1px solid {C['surface1']}; border-radius: 3px;
                    padding: 1px 4px; font-size: 12px; font-style: italic;
                }}
            """)
            desc_inp.textChanged.connect(lambda: _update_sig_preview())
            row.addWidget(desc_inp)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(26, 26)
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['surface0']}; color: {C['red']};
                    border: none; border-radius: 3px; font-size: 11px;
                }}
                QPushButton:hover {{ background: {C['red']}; color: white; }}
            """)
            row.addWidget(del_btn)

            c = QWidget()
            c.setFixedHeight(34)
            c.setLayout(row)
            c.setStyleSheet(f"background: {C['mantle']};")
            param_layout.addWidget(c)
            param_inputs.append((c, name_inp, type_combo, desc_inp, default_inp))

            def remove():
                param_layout.removeWidget(c)
                c.deleteLater()
                param_inputs[:] = [x for x in param_inputs if x[0] != c]
                _update_sig_preview()

            del_btn.clicked.connect(remove)
            _update_sig_preview()

        # 초기 파라미터 로드
        for p in init_params:
            add_param(p["name"], p.get("type", "str"), p.get("desc", ""), p.get("default", ""))

        param_scroll.setWidget(param_container)
        layout.addWidget(param_scroll)

        # + 파라미터 추가 버튼
        add_param_btn = QPushButton("+ 파라미터 추가")
        add_param_btn.setFixedWidth(120)
        add_param_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['blue']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 3px 8px; font-size: 11px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; border-color: {C['blue']}; }}
        """)
        add_param_btn.clicked.connect(lambda: add_param())
        param_btn_row = QHBoxLayout()
        param_btn_row.addStretch()
        param_btn_row.addWidget(add_param_btn)
        layout.addLayout(param_btn_row)

        # ── 2. 시그니처 프리뷰 (읽기 전용) ──
        sig_label = QLabel("🔒 자동 생성 시그니처 (수정 불가)")
        sig_label.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {C['overlay0']};")
        layout.addWidget(sig_label)
        layout.addWidget(sig_preview)
        _update_sig_preview()  # 초기 프리뷰

        # ── 3. 코드 본문 ──
        body_label = QLabel("📝 함수 본문  (Tab = 4 spaces, 자동 indent)")
        body_label.setStyleSheet("font-size: 12px; font-weight: bold;")
        layout.addWidget(body_label)

        # 커스텀 CodeEditor — Tab→4스페이스, Enter 시 자동 indent
        class CodeEditor(QPlainTextEdit):
            def keyPressEvent(self_, event):
                if event.key() == Qt.Key.Key_Tab:
                    self_.insertPlainText("    ")
                    return
                if event.key() == Qt.Key.Key_Backtab:
                    cursor = self_.textCursor()
                    cursor.movePosition(QTextCursor.MoveOperation.StartOfLine, QTextCursor.MoveMode.KeepAnchor)
                    sel = cursor.selectedText()
                    if sel.startswith("    "):
                        cursor.removeSelectedText()
                        cursor.insertText(sel[4:])
                    return
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    cursor = self_.textCursor()
                    block_text = cursor.block().text()
                    indent = len(block_text) - len(block_text.lstrip())
                    extra = 4 if block_text.rstrip().endswith(":") else 0
                    super(CodeEditor, self_).keyPressEvent(event)
                    self_.insertPlainText(" " * (indent + extra))
                    return
                super(CodeEditor, self_).keyPressEvent(event)

        editor = CodeEditor()
        editor.setFont(QFont("Consolas", 12))
        editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {C['mantle']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 6px;
                padding: 8px; font-family: 'Consolas', 'Courier New', monospace;
                selection-background-color: {C['surface2']};
            }}
        """)
        editor.setTabStopDistance(28)
        editor.setPlainText(init_body)
        editor_highlighter = PythonHighlighter(editor.document())
        layout.addWidget(editor)

        # ── 에러 라벨 ──
        error_label = QLabel("")
        error_label.setStyleSheet(f"color: {C['red']}; font-size: 12px; padding: 2px 4px;")
        error_label.setWordWrap(True)
        error_label.setFixedHeight(0)
        layout.addWidget(error_label)

        # ── 버튼 ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("취소")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['surface1']}; color: {C['text']};
                border: none; border-radius: 6px;
                padding: 8px 20px; font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {C['surface2']}; }}
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("💾 저장")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['green']}; color: white;
                border: none; border-radius: 6px;
                padding: 8px 20px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {C['teal']}; }}
        """)

        def _validate_and_save():
            # 파라미터 검증 — 설명 필수
            params = []
            for _, n_inp, t_combo, d_inp, df_inp in param_inputs:
                pn = n_inp.text().strip()
                pt = t_combo.currentText()
                pd = d_inp.text().strip()
                pf = df_inp.text().strip()
                if not pn:
                    continue
                if not pn.isidentifier():
                    error_label.setFixedHeight(20)
                    error_label.setText(t("desktop.param_invalid_id", name=pn))
                    return
                if not pd:
                    error_label.setFixedHeight(20)
                    error_label.setText(t("desktop.param_desc_required", name=pn))
                    d_inp.setFocus()
                    return
                p_data = {"name": pn, "type": pt, "desc": pd}
                if pf:
                    p_data["default"] = pf
                params.append(p_data)

            # 코드 조립
            args_parts = []
            for p in params:
                if "default" in p:
                    args_parts.append(f"{p['name']}: {p['type']} = {repr(p['default'])}")
                else:
                    args_parts.append(f"{p['name']}: {p['type']}")
            args_str = ", ".join(args_parts)

            body = editor.toPlainText()
            body = body.replace("\t", "    ")

            # 전체 코드 생성
            code_lines = [f"def run({args_str}):"]
            if params:
                code_lines.append('    """')
                for p in params:
                    code_lines.append(f"    {p['name']}: {p['desc']}")
                code_lines.append('    """')
            for line in body.split("\n"):
                code_lines.append(line)
            full_code = "\n".join(code_lines)

            # Python syntax validation
            try:
                _ast.parse(full_code)
            except SyntaxError as e:
                error_label.setFixedHeight(40)
                err_line = e.lineno or "?"
                error_label.setText(t("desktop.syntax_error", line=err_line, msg=e.msg))
                return

            error_label.setFixedHeight(0)
            dialog._result_code = full_code
            dialog.accept()

        save_btn.clicked.connect(_validate_and_save)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            return getattr(dialog, '_result_code', None)
        return None

    def _show_workflow_context_menu(self):
        """⚡ 버튼 클릭 → 컨텍스트 메뉴 (신규 생성 / 이전 대화에서 생성)"""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {C['surface0']};
                color: {C['text']};
                border: 1px solid {C['surface2']};
                border-radius: 8px;
                padding: 6px 0;
                font-family: 'Segoe UI';
                font-size: 13px;
            }}
            QMenu::item {{
                padding: 8px 20px;
            }}
            QMenu::item:selected {{
                background-color: {C['surface1']};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {C['overlay0']};
                margin: 6px 12px;
            }}
        """)

        act_persona = QAction(t("chat.menu_new_persona"), self)
        act_persona.triggered.connect(lambda: self._show_persona_dialog())
        menu.addAction(act_persona)

        act_code = QAction(t("chat.menu_new_code"), self)
        act_code.triggered.connect(lambda: self._show_code_editor())
        menu.addAction(act_code)

        menu.addSeparator()

        act_new = QAction(t("chat.menu_new_workflow"), self)
        act_new.triggered.connect(lambda: self._show_workflow_dialog())
        menu.addAction(act_new)

        act_conv = QAction(t("chat.menu_wf_from_conv"), self)
        has_turns = bool(self._session_tool_turns)
        act_conv.setEnabled(has_turns)
        if not has_turns:
            act_conv.setText(t("chat.menu_wf_from_conv_empty"))
        act_conv.triggered.connect(self._show_workflow_from_conversation)
        menu.addAction(act_conv)



        # 버튼 위쪽에 메뉴 표시
        btn_pos = self._workflow_btn.mapToGlobal(self._workflow_btn.rect().topLeft())
        menu.exec(btn_pos)
    def _share_item_by_name(self, share_type: str, name: str):
        """📤 리스트에서 직접 공유 — LLM에게 share_to_store tool 호출 요청"""
        type_labels = {"code": "코드", "workflow": "워크플로우", "persona": "페르소나"}
        label = type_labels.get(share_type, share_type)
        self.sig_message_sent.emit(f'share_to_store(share_type="{share_type}", name="{name}") 실행해줘')

    def _share_to_store(self, share_type: str, data: dict):
        """📤 웹 서버 /api/share API를 호출하여 GitHub PR 생성"""
        import urllib.request, json as _json
        try:
            url = f"{WEB_CFG['backend_url']}/api/share"
            payload = _json.dumps(data, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read().decode())
            if result.get("ok"):
                pr_url = result.get("pr_url", "")
                self.add_bot_message(
                    f"✅ **{t('chat.share_success')}**\n\n"
                    f"📋 PR: [{pr_url}]({pr_url})\n\n"
                    f"{t('chat.share_pr_hint')}"
                )
            else:
                self.add_bot_message(f"⚠ {t('chat.share_fail')}: {result}")
        except Exception as e:
            self.add_bot_message(f"⚠ {t('chat.share_fail')}: {e}")

    def _show_share_dialog(self, share_type: str):
        """📤 공유 다이얼로그 — 로컬 아이템 목록에서 선택 → Store에 PR 생성"""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QPushButton, QScrollArea, QWidget, QRadioButton, QLineEdit
        )
        from PyQt6.QtCore import Qt
        import urllib.request, json as _json
        from urllib.parse import quote

        # 로컬 서버에서 아이템 목록 가져오기
        _base = getattr(self, '_api_base', 'http://127.0.0.1:3000')
        _key = getattr(self, '_api_key', '')

        items = []
        try:
            if share_type == "workflow":
                url = f"{_base}/workflows"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_key}"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read().decode())
                items = data if isinstance(data, list) else data.get("workflows", [])
            elif share_type == "persona":
                url = f"{_base}/personas"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_key}"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read().decode())
                items = data if isinstance(data, list) else data.get("personas", [])
            elif share_type == "code":
                url = f"{_base}/codes"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_key}"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read().decode())
                items = data if isinstance(data, list) else data.get("codes", [])
        except Exception as e:
            self.add_bot_message(f"⚠ {t('chat.share_load_fail')}: {e}")
            return

        if not items:
            self.add_bot_message(f"ℹ {t('chat.share_empty')}")
            return

        type_labels = {"workflow": "⚡ Workflow", "persona": "🎭 Persona", "code": "🐍 Code"}
        dialog = QDialog(self)
        dialog.setWindowTitle(f"📤 {type_labels.get(share_type, share_type)} → Store")
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(400)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {C['base']}; color: {C['text']}; }}
            QLabel {{ color: {C['text']}; font-family: 'Segoe UI'; }}
            QRadioButton {{
                color: {C['text']}; font-family: 'Segoe UI'; font-size: 13px;
                spacing: 8px; padding: 6px 4px;
            }}
            QRadioButton::indicator {{
                width: 16px; height: 16px;
                border: 2px solid {C['surface2']}; border-radius: 9px;
                background: {C['surface0']};
            }}
            QRadioButton::indicator:checked {{
                background: {C['blue']}; border-color: {C['blue']};
            }}
        """)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(10)

        title = QLabel(f"📤 {t('chat.share_select_title')}")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {C['blue']};")
        main_layout.addWidget(title)

        hint = QLabel(t("chat.share_select_hint"))
        hint.setStyleSheet(f"font-size: 12px; color: {C['subtext0']};")
        hint.setWordWrap(True)
        main_layout.addWidget(hint)

        # 카테고리 입력
        cat_layout = QHBoxLayout()
        cat_label = QLabel(t("chat.share_category"))
        cat_label.setFixedWidth(70)
        cat_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        cat_layout.addWidget(cat_label)
        cat_input = QLineEdit("Other")
        cat_input.setFixedHeight(28)
        cat_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 4px;
                padding: 2px 6px; font-size: 13px;
            }}
        """)
        cat_layout.addWidget(cat_input)
        main_layout.addLayout(cat_layout)

        # 아이템 목록
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {C['surface1']}; background: {C['mantle']}; border-radius: 6px; }}")
        list_widget = QWidget()
        list_widget.setStyleSheet(f"background: {C['mantle']};")
        list_layout = QVBoxLayout(list_widget)
        list_layout.setSpacing(2)
        list_layout.setContentsMargins(8, 8, 8, 8)
        list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        radios = []  # (QRadioButton, item_data)
        for item in items:
            name = item.get("name", "")
            desc = item.get("description", "")
            display = f"{name}"
            if desc:
                short_desc = desc[:60] + "..." if len(desc) > 60 else desc
                display += f"  —  {short_desc}"
            rb = QRadioButton(display)
            rb.setStyleSheet(f"QRadioButton {{ border-bottom: 1px solid {C['surface0']}; }}")
            list_layout.addWidget(rb)
            radios.append((rb, item))

        if radios:
            radios[0][0].setChecked(True)

        scroll.setWidget(list_widget)
        main_layout.addWidget(scroll, 1)

        # 에러/상태 라벨
        status_label = QLabel("")
        status_label.setStyleSheet(f"color: {C['subtext0']}; font-size: 12px;")
        status_label.setFixedHeight(0)
        main_layout.addWidget(status_label)

        # 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton(t("chat.share_cancel"))
        cancel_btn.setFixedSize(80, 36)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['surface1']}; color: {C['text']};
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {C['surface2']}; }}
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        share_btn = QPushButton(f"📤 {t('chat.share_btn')}")
        share_btn.setFixedSize(120, 36)
        share_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['blue']}; color: white;
                border: none; border-radius: 6px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {C['sapphire']}; }}
        """)

        def _do_share():
            selected = None
            for rb, item in radios:
                if rb.isChecked():
                    selected = item
                    break
            if not selected:
                return

            status_label.setFixedHeight(20)
            status_label.setText(f"⏳ {t('chat.share_in_progress')}...")
            share_btn.setEnabled(False)

            import threading
            category = cat_input.text().strip() or "Other"

            def _run():
                try:
                    name = selected.get("name", "")
                    item_id = selected.get("id", name.lower().replace(" ", "-"))
                    share_data = {
                        "type": share_type,
                        "id": item_id,
                        "name": name,
                        "description": selected.get("description", ""),
                        "category": category,
                        "icon": selected.get("icon", "📦"),
                        "lang": "multi",
                    }

                    if share_type == "code":
                        # 코드 전체 가져오기
                        try:
                            code_url = f"{_base}/code/{quote(name, safe='')}"
                            req2 = urllib.request.Request(code_url, headers={"Authorization": f"Bearer {_key}"})
                            with urllib.request.urlopen(req2, timeout=5) as resp2:
                                code_data = _json.loads(resp2.read().decode())
                            share_data["code"] = code_data.get("code", "")
                            share_data["params"] = code_data.get("params", [])
                        except Exception:
                            share_data["code"] = selected.get("code", "")
                            share_data["params"] = selected.get("params", [])

                    elif share_type == "persona":
                        try:
                            p_url = f"{_base}/persona/{quote(name, safe='')}"
                            req2 = urllib.request.Request(p_url, headers={"Authorization": f"Bearer {_key}"})
                            with urllib.request.urlopen(req2, timeout=5) as resp2:
                                p_data = _json.loads(resp2.read().decode())
                            share_data["prompt"] = p_data.get("prompt", "")
                        except Exception:
                            share_data["prompt"] = selected.get("prompt", "")

                    elif share_type == "workflow":
                        try:
                            w_url = f"{_base}/workflow/{quote(name, safe='')}"
                            req2 = urllib.request.Request(w_url, headers={"Authorization": f"Bearer {_key}"})
                            with urllib.request.urlopen(req2, timeout=5) as resp2:
                                w_data = _json.loads(resp2.read().decode())
                            share_data["steps"] = w_data.get("prompts", w_data.get("steps", []))
                            share_data["hint_tools"] = w_data.get("hint_tools", [])
                            share_data["schedule"] = w_data.get("schedule", "")
                        except Exception:
                            share_data["steps"] = selected.get("prompts", selected.get("steps", []))
                            share_data["hint_tools"] = selected.get("hint_tools", [])
                            share_data["schedule"] = selected.get("schedule", "")

                    from PyQt6.QtCore import QMetaObject, Qt as _Qt, Q_ARG
                    QMetaObject.invokeMethod(dialog, "accept", _Qt.ConnectionType.QueuedConnection)
                    self._share_to_store(share_type, share_data)

                except Exception as e:
                    self.add_bot_message(f"⚠ {t('chat.share_fail')}: {e}")
                    from PyQt6.QtCore import QMetaObject, Qt as _Qt
                    QMetaObject.invokeMethod(dialog, "reject", _Qt.ConnectionType.QueuedConnection)

            threading.Thread(target=_run, daemon=True).start()

        share_btn.clicked.connect(_do_share)
        btn_layout.addWidget(share_btn)
        main_layout.addLayout(btn_layout)

        dialog.exec()

    def _show_workflow_from_conversation(self):
        """💬 이전 대화에서 Workflow 생성 — 대화 턴 체크박스 선택"""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QLineEdit, QPushButton, QScrollArea, QWidget, QCheckBox
        )
        from PyQt6.QtCore import Qt

        if not self._session_tool_turns:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("💬 이전 대화에서 Workflow 만들기")
        dialog.setMinimumWidth(650)
        dialog.setMinimumHeight(500)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {C['base']}; color: {C['text']}; }}
            QLabel {{ color: {C['text']}; font-family: 'Segoe UI'; }}
            QLineEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 4px;
                padding: 2px 6px; font-size: 13px; font-family: 'Segoe UI';
            }}
            QLineEdit:focus {{ border-color: {C['blue']}; }}
            QCheckBox {{
                color: {C['text']}; font-family: 'Segoe UI'; font-size: 13px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px; height: 18px;
                border: 2px solid {C['surface2']}; border-radius: 4px;
                background: {C['surface0']};
            }}
            QCheckBox::indicator:checked {{
                background: {C['blue']}; border-color: {C['blue']};
            }}
        """)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(10)

        # 제목
        title = QLabel(t("chat.wf_from_session_title"))
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {C['yellow']};")
        main_layout.addWidget(title)

        hint = QLabel(t("chat.wf_from_session_hint"))
        hint.setStyleSheet(f"font-size: 12px; color: {C['subtext0']}; margin-bottom: 4px;")
        hint.setWordWrap(True)
        main_layout.addWidget(hint)

        # 이름 입력
        name_layout = QHBoxLayout()
        name_label = QLabel(t("chat.wf_name_label"))
        name_label.setFixedWidth(50)
        name_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        name_layout.addWidget(name_label)
        name_input = QLineEdit()
        name_input.setFixedHeight(28)
        name_input.setPlaceholderText(t("chat.wf_name_placeholder"))
        name_layout.addWidget(name_input)
        main_layout.addLayout(name_layout)

        # 설명 입력
        desc_layout = QHBoxLayout()
        desc_label = QLabel(t("chat.wf_desc_label"))
        desc_label.setFixedWidth(50)
        desc_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        desc_layout.addWidget(desc_label)
        desc_input = QLineEdit()
        desc_input.setFixedHeight(28)
        desc_input.setPlaceholderText(t("chat.wf_desc_placeholder"))
        desc_layout.addWidget(desc_input)
        main_layout.addLayout(desc_layout)

        # 대화 기록 체크박스 영역
        conv_label = QLabel(t("chat.wf_from_session_conv_label"))
        conv_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        main_layout.addWidget(conv_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {C['surface1']}; background: {C['mantle']}; border-radius: 6px; }}")
        conv_widget = QWidget()
        conv_widget.setStyleSheet(f"background: {C['mantle']};")
        conv_layout = QVBoxLayout(conv_widget)
        conv_layout.setSpacing(4)
        conv_layout.setContentsMargins(8, 8, 8, 8)
        conv_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        checkboxes = []  # (QCheckBox, turn_data)
        recent_turns = self._session_tool_turns[-20:]  # 최근 20개만
        start_idx = len(self._session_tool_turns) - len(recent_turns) + 1
        for i, turn in enumerate(recent_turns, start_idx):
            msg = turn["msg"]
            tools = turn.get("tools", [])

            # 메시지 텍스트 (길면 잘라서)
            display_msg = msg if len(msg) <= 60 else msg[:57] + "..."
            tool_str = ""
            if tools:
                unique_tools = list(dict.fromkeys(tools))  # 중복 제거, 순서 유지
                tool_str = f"  🔧 {', '.join(unique_tools)}"

            cb = QCheckBox(f"{i}. {display_msg}{tool_str}")
            cb.setToolTip(msg)  # 전체 텍스트 툴팁
            cb.setStyleSheet(f"""
                QCheckBox {{
                    padding: 6px 4px;
                    border-bottom: 1px solid {C['surface0']};
                }}
            """)
            conv_layout.addWidget(cb)
            checkboxes.append((cb, turn))

        scroll.setWidget(conv_widget)
        main_layout.addWidget(scroll, 1)

        # 전체 선택 / 해제
        sel_row = QHBoxLayout()
        sel_all = QPushButton(t("chat.wf_from_session_select_all"))
        sel_all.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['blue']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 4px 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; }}
        """)
        sel_all.clicked.connect(lambda: [cb.setChecked(True) for cb, _ in checkboxes])
        sel_row.addWidget(sel_all)

        sel_none = QPushButton(t("chat.wf_from_session_deselect"))
        sel_none.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['overlay1']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 4px 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; }}
        """)
        sel_none.clicked.connect(lambda: [cb.setChecked(False) for cb, _ in checkboxes])
        sel_row.addWidget(sel_none)
        sel_row.addStretch()
        main_layout.addLayout(sel_row)

        # 스케줄
        sch_layout = QHBoxLayout()
        sch_label = QLabel(t("chat.wf_schedule_label"))
        sch_layout.addWidget(sch_label)
        sch_input = QLineEdit()
        sch_input.setPlaceholderText(t("chat.wf_schedule_placeholder"))
        sch_layout.addWidget(sch_input)
        main_layout.addLayout(sch_layout)

        # 에러 라벨
        error_label = QLabel("")
        error_label.setStyleSheet(f"color: {C['red']}; font-size: 12px;")
        error_label.setFixedHeight(0)
        main_layout.addWidget(error_label)

        # 저장 / 취소
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton(t("chat.wf_from_session_cancel"))
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['surface1']}; color: {C['text']};
                border: none; border-radius: 6px;
                padding: 10px 24px; font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {C['surface2']}; }}
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton(t("chat.wf_from_session_save"))
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['blue']}; color: white;
                border: none; border-radius: 6px;
                padding: 10px 24px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {C['sapphire']}; }}
        """)

        def _validate_and_accept():
            wf_name = name_input.text().strip()
            if not wf_name:
                error_label.setFixedHeight(20)
                error_label.setText(t("chat.wf_from_session_name_req"))
                name_input.setFocus()
                return
            selected = [(cb, t) for cb, t in checkboxes if cb.isChecked()]
            if not selected:
                error_label.setFixedHeight(20)
                error_label.setText(t("chat.wf_from_session_conv_req"))
                return
            error_label.setFixedHeight(0)
            dialog.accept()

        save_btn.clicked.connect(_validate_and_accept)
        btn_layout.addWidget(save_btn)
        main_layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            import json as _json
            wf_name = name_input.text().strip()
            description = desc_input.text().strip()
            schedule = sch_input.text().strip()

            # 선택된 턴 → 스텝 + hint_tools 조립
            steps = []
            all_tools = set()
            for cb, turn in checkboxes:
                if cb.isChecked():
                    steps.append({"type": "prompt", "content": turn["msg"]})
                    for tool_name in turn.get("tools", []):
                        all_tools.add(tool_name)

            if not steps:
                return

            # hint_tools 문자열 조립
            hint_tools = ",".join(sorted(all_tools)) if all_tools else ""

            # LLM에게 workflow 생성 요청
            steps_json = _json.dumps(steps, ensure_ascii=False)
            cmd = f'{t("chat.wf_create_prompt")} "{wf_name}"{t("chat.wf_prompt_label")}\'{steps_json}\''
            if description:
                cmd += f'{t("chat.wf_desc_suffix")} "{description}"'
            if hint_tools:
                cmd += f'{t("chat.wf_tool_hint_suffix")} "{hint_tools}"'
            if schedule:
                cmd += f'{t("chat.wf_schedule_suffix")} "{schedule}"'

            self.sig_message_sent.emit(cmd)

    def _show_workflow_dialog(self, prefill=None):
        """⚡ Workflow 생성/편집 다이얼로그 — prompt/code 스텝 타입 지원
        prefill: Store에서 가져온 워크플로우 데이터 (is_import=True이면 import 모드)
        """
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QLineEdit, QPushButton, QScrollArea, QWidget
        )
        from PyQt6.QtCore import Qt
        is_import = prefill and prefill.get("is_import", False)
        dialog = QDialog(self)
        dialog.setWindowTitle(t("chat.wf_import_title") if is_import else t("chat.wf_create_title"))
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(480)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {C['base']}; color: {C['text']}; }}
            QLabel {{ color: {C['text']}; font-family: 'Segoe UI'; }}
            QLineEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 4px;
                padding: 2px 6px; font-size: 13px; font-family: 'Segoe UI';
            }}
            QLineEdit:focus {{ border-color: {C['blue']}; }}
        """)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(10)

        # 제목
        if is_import:
            title = QLabel(t("chat.wf_import_btn"))
            title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {C['green']};")
            main_layout.addWidget(title)
            hint = QLabel(t("chat.wf_import_hint"))
            hint.setStyleSheet(f"font-size: 12px; color: {C['subtext0']}; margin-bottom: 4px;")
            main_layout.addWidget(hint)
        else:
            title = QLabel(t("chat.wf_new_btn"))
            title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {C['blue']};")
            main_layout.addWidget(title)

        # 이름 입력
        name_layout = QHBoxLayout()
        name_label = QLabel(t("chat.wf_name_label"))
        name_label.setFixedWidth(50)
        name_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        name_layout.addWidget(name_label)
        name_input = QLineEdit()
        name_input.setFixedHeight(28)
        name_input.setPlaceholderText(t("chat.wf_name_placeholder"))
        if prefill:
            name_input.setText(prefill.get("name", ""))
        name_layout.addWidget(name_input)
        main_layout.addLayout(name_layout)

        # 설명 입력
        desc_layout = QHBoxLayout()
        desc_label = QLabel(t("chat.wf_desc_label"))
        desc_label.setFixedWidth(50)
        desc_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        desc_layout.addWidget(desc_label)
        desc_input = QLineEdit()
        desc_input.setFixedHeight(28)
        desc_input.setPlaceholderText(t("chat.wf_desc_placeholder"))
        if prefill:
            desc_input.setText(prefill.get("description", ""))
        desc_layout.addWidget(desc_input)
        main_layout.addLayout(desc_layout)

        # 단계 영역 (스크롤)
        step_label = QLabel(t("chat.wf_steps_label"))
        step_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        main_layout.addWidget(step_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(300)
        scroll.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustToContents)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {C['base']}; padding: 0; margin: 0; }}")
        steps_widget = QWidget()
        steps_widget.setContentsMargins(0, 0, 0, 0)
        steps_widget.setStyleSheet(f"QWidget {{ margin: 0; padding: 0; background: {C['base']}; }}")
        steps_layout = QVBoxLayout(steps_widget)
        steps_layout.setSpacing(0)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # step_inputs: [(container, input_widget, label, step_type, code_content)]
        # step_type: "prompt" or "code"
        # code_content: str (코드 스텝의 경우 전체 코드)
        step_inputs = []

        def _renumber_steps():
            for j, (_, _, lbl, _, _) in enumerate(step_inputs, 1):
                lbl.setText(f"{j}.")

        def add_step_row(text="", idx=None, step_type="prompt", code_content="", code_name="", insert_at=None, step_args=None, i18n_content=None):
            row = QHBoxLayout()
            row.setSpacing(4)
            row.setContentsMargins(0, 1, 0, 1)
            num = idx if idx is not None else len(step_inputs) + 1

            # 스텝 번호
            label = QLabel(f"{num}.")
            label.setFixedWidth(24)
            label.setFixedHeight(26)
            label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {C['overlay0']};")
            row.addWidget(label)



            # 내용 입력 (prompt) 또는 코드 미리보기 (code)
            inp = QLineEdit()
            inp.setFixedHeight(26)
            step_data = {"type": step_type, "code": code_content, "code_name": code_name, "args": step_args or {}, "_i18n": i18n_content}

            if step_type == "code":
                if code_name:
                    display = f"🐍 {code_name}"
                else:
                    first_line = code_content.split("\n")[0] if code_content else "def run():"
                    display = f"🐍 {first_line}"
                inp.setText(display)
                inp.setReadOnly(True)
                inp.setStyleSheet(f"""
                    QLineEdit {{
                        background-color: {C['mantle']}; color: {C['green']};
                        border: 1px solid {C['green']}; border-radius: 4px;
                        padding: 2px 6px; font-size: 12px; font-family: 'Consolas', monospace;
                    }}
                """)
            else:
                inp.setText(text)
                inp.setPlaceholderText(t("chat.wf_step_placeholder", num=num))
            row.addWidget(inp)

            # 코드 편집 버튼 (코드 스텝만)
            edit_code_btn = QPushButton("✏")
            edit_code_btn.setFixedSize(24, 24)
            edit_code_btn.setToolTip(t("chat.wf_code_edit"))
            edit_code_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['surface0']}; color: {C['blue']};
                    border: none; border-radius: 4px; font-size: 12px;
                }}
                QPushButton:hover {{ background: {C['blue']}; color: white; }}
            """)
            edit_code_btn.setVisible(step_type == "code")
            row.addWidget(edit_code_btn)

            # ↑ 위로 이동
            btn_style = f"""
                QPushButton {{
                    background: {C['surface0']}; color: {C['overlay1']};
                    border: none; border-radius: 4px; font-size: 11px;
                }}
                QPushButton:hover {{ background: {C['surface1']}; color: {C['blue']}; }}
            """
            up_btn = QPushButton("▲")
            up_btn.setFixedSize(22, 24)
            up_btn.setStyleSheet(btn_style)
            row.addWidget(up_btn)

            # ↓ 아래로 이동
            down_btn = QPushButton("▼")
            down_btn.setFixedSize(22, 24)
            down_btn.setStyleSheet(btn_style)
            row.addWidget(down_btn)

            # 삭제 버튼
            del_btn = QPushButton("✕")
            del_btn.setFixedSize(24, 24)
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['surface0']}; color: {C['red']};
                    border: none; border-radius: 4px; font-size: 12px;
                }}
                QPushButton:hover {{ background: {C['red']}; color: white; }}
            """)
            row.addWidget(del_btn)

            container = QWidget()
            container.setLayout(row)
            container.setContentsMargins(0, 0, 0, 0)
            container.setStyleSheet(f"background: {C['base']};")

            # ── 코드 스텝: 인자 패널 ──
            args_container = None
            if step_type == "code":
                args_container = QWidget()
                args_layout_inner = QVBoxLayout(args_container)
                args_layout_inner.setSpacing(2)
                args_layout_inner.setContentsMargins(28, 0, 0, 4)
                args_container.setStyleSheet(f"background: {C['base']};")

                def _load_and_show_args(sd, ac):
                    """코드의 파라미터를 조회하여 인자 입력 UI 생성"""
                    import ast as _ast_inner
                    params_list = []
                    # 1) code_name이면 REST API로 조회
                    if sd.get("code_name"):
                        try:
                            import urllib.request, json as _j2
                            from urllib.parse import quote as _q2
                            from pathlib import Path as _P2
                            cfg2 = _j2.loads((_P2(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))
                            _port2 = cfg2.get("server", {}).get("port", 3000)
                            _key2 = cfg2.get("server", {}).get("api_key", "")
                            req2 = urllib.request.Request(
                                f"http://127.0.0.1:{_port2}/code/{_q2(sd['code_name'], safe='')}",
                                headers={"Authorization": f"Bearer {_key2}"}
                            )
                            with urllib.request.urlopen(req2, timeout=5) as resp2:
                                cd2 = _j2.loads(resp2.read().decode())
                            raw_p = cd2.get("params", "[]")
                            if isinstance(raw_p, str):
                                raw_p = _j2.loads(raw_p)
                            if isinstance(raw_p, list):
                                params_list = raw_p
                        except Exception:
                            pass
                    # 2) 직접 작성 코드면 AST에서 추출
                    elif sd.get("code"):
                        try:
                            tree2 = _ast_inner.parse(sd["code"])
                            for nd in _ast_inner.walk(tree2):
                                if isinstance(nd, _ast_inner.FunctionDef):
                                    defs = nd.args.defaults
                                    n_args = len(nd.args.args)
                                    n_defs = len(defs)
                                    for ai, a in enumerate(nd.args.args):
                                        di = ai - (n_args - n_defs)
                                        dv = ""
                                        if di >= 0:
                                            try: dv = str(_ast_inner.literal_eval(defs[di]))
                                            except: pass
                                        params_list.append({"name": a.arg, "type": "str", "default": dv})
                                    break
                        except Exception:
                            pass

                    if not params_list:
                        ac.setVisible(False)
                        return

                    al = ac.layout()
                    # 기존 위젯 제거
                    while al.count():
                        item = al.takeAt(0)
                        if item.widget():
                            item.widget().deleteLater()

                    for p in params_list:
                        pname = p.get("name", "")
                        ptype = p.get("type", "str")
                        pdefault = p.get("default", "")
                        arg_row = QHBoxLayout()
                        arg_row.setContentsMargins(0, 0, 0, 0)
                        arg_row.setSpacing(4)
                        plabel = QLabel(f"  ├ {pname}")
                        plabel.setFixedWidth(120)
                        plabel.setStyleSheet(f"font-size: 11px; color: {C['subtext0']}; font-family: 'Consolas', monospace;")
                        arg_row.addWidget(plabel)
                        type_lbl = QLabel(f"({ptype})")
                        type_lbl.setFixedWidth(40)
                        type_lbl.setStyleSheet(f"font-size: 10px; color: {C['overlay0']};")
                        arg_row.addWidget(type_lbl)
                        val_inp = QLineEdit(sd.get("args", {}).get(pname, ""))
                        val_inp.setFixedHeight(22)
                        ph_text = pdefault if pdefault else "(문맥상 자동 인식)"
                        val_inp.setPlaceholderText(ph_text)
                        val_inp.setStyleSheet(f"""
                            QLineEdit {{
                                background: {C['surface0']}; color: {C['text']};
                                border: 1px solid {C['surface1']}; border-radius: 3px;
                                padding: 1px 4px; font-size: 11px;
                            }}
                        """)
                        # 값 변경 시 step_data["args"]에 반영
                        def _on_val_changed(text, _pn=pname, _sd=sd):
                            if text.strip():
                                _sd.setdefault("args", {})[_pn] = text.strip()
                            else:
                                _sd.get("args", {}).pop(_pn, None)
                        val_inp.textChanged.connect(_on_val_changed)
                        arg_row.addWidget(val_inp)
                        arg_w = QWidget()
                        arg_w.setFixedHeight(26)
                        arg_w.setLayout(arg_row)
                        arg_w.setStyleSheet(f"background: {C['base']};")
                        al.addWidget(arg_w)
                    ac.setVisible(True)

                _load_and_show_args(step_data, args_container)
                container.setFixedHeight(30)
            else:
                container.setFixedHeight(30)

            entry = (container, inp, label, step_data, edit_code_btn)

            if insert_at is not None and insert_at < len(step_inputs):
                steps_layout.insertWidget(insert_at, container)
                if args_container:
                    steps_layout.insertWidget(insert_at + 1, args_container)
                step_inputs.insert(insert_at, entry)
            else:
                steps_layout.addWidget(container)
                if args_container:
                    steps_layout.addWidget(args_container)
                step_inputs.append(entry)
            _renumber_steps()



            def _make_edit_handler(sd, ip, ac_ref):
                def edit_code():
                    initial_code = sd.get("code", "")
                    if sd.get("type") == "code" and sd.get("code_name") and not initial_code:
                        try:
                            import urllib.request, json as _j
                            from urllib.parse import quote as _q
                            from pathlib import Path
                            cfg_path = Path(__file__).resolve().parent.parent / "config.json"
                            cfg = _j.loads(cfg_path.read_text(encoding="utf-8"))
                            _port = cfg.get("server", {}).get("port", 3000)
                            _key = cfg.get("server", {}).get("api_key", "")
                            req = urllib.request.Request(
                                f"http://127.0.0.1:{_port}/code/{_q(sd['code_name'], safe='')}",
                                headers={"Authorization": f"Bearer {_key}"}
                            )
                            with urllib.request.urlopen(req, timeout=5) as resp:
                                code_data = _j.loads(resp.read().decode())
                            initial_code = code_data.get("code", "")
                        except Exception as e:
                            self.add_bot_message(t("desktop.code_load_fail", error=str(e)))
                    code = self._show_code_editor(initial_code, parent=dialog)
                    if code is not None:
                        sd["code"] = code
                        first_line = code.split("\n")[0]
                        ip.setText(f"🐍 {first_line}")
                        if ac_ref:
                            _load_and_show_args(sd, ac_ref)
                return edit_code

            edit_code_btn.clicked.connect(_make_edit_handler(step_data, inp, args_container))

            def remove():
                steps_layout.removeWidget(container)
                container.deleteLater()
                step_inputs[:] = [(c, i, l, d, e) for c, i, l, d, e in step_inputs if c != container]
                _renumber_steps()

            del_btn.clicked.connect(remove)

            def move_up():
                ci = next((j for j, (c, *_) in enumerate(step_inputs) if c == container), -1)
                if ci <= 0:
                    return
                # Swap in list
                step_inputs[ci], step_inputs[ci - 1] = step_inputs[ci - 1], step_inputs[ci]
                # Swap in layout
                steps_layout.removeWidget(container)
                steps_layout.insertWidget(ci - 1, container)
                _renumber_steps()

            def move_down():
                ci = next((j for j, (c, *_) in enumerate(step_inputs) if c == container), -1)
                if ci < 0 or ci >= len(step_inputs) - 1:
                    return
                step_inputs[ci], step_inputs[ci + 1] = step_inputs[ci + 1], step_inputs[ci]
                steps_layout.removeWidget(container)
                steps_layout.insertWidget(ci + 1, container)
                _renumber_steps()

            up_btn.clicked.connect(move_up)
            down_btn.clicked.connect(move_down)

        # prefill이 있으면 가져온 스텝으로 채우기, 없으면 기본 1개 빈 단계
        if prefill and prefill.get("steps"):
            for i, step_text in enumerate(prefill["steps"], 1):
                if isinstance(step_text, dict):
                    # {"type": "code"/"prompt", "content": ..., "code_name": ...} 형식
                    st = step_text.get("type", "prompt")
                    sc = step_text.get("content", "")
                    cn = step_text.get("code_name", "")
                    sa = step_text.get("args", {})
                    if st == "code":
                        add_step_row("", i, step_type="code", code_content=sc, code_name=cn, step_args=sa)
                    else:
                        add_step_row(sc, i, i18n_content=step_text.get("_i18n_content"))
                else:
                    add_step_row(str(step_text), i)
        else:
            add_step_row("", 1)

        scroll.setWidget(steps_widget)
        main_layout.addWidget(scroll)

        # + 프롬프트 추가 / + 코드 추가 / 📥 코드 가져오기 버튼
        add_row = QHBoxLayout()
        add_row.addStretch()

        add_prompt_btn = QPushButton(t("chat.wf_add_prompt"))
        add_prompt_btn.setFixedWidth(110)
        add_prompt_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['green']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 4px 8px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; border-color: {C['green']}; }}
        """)
        add_prompt_btn.clicked.connect(lambda: add_step_row())
        add_row.addWidget(add_prompt_btn)

        add_code_btn = QPushButton(t("chat.wf_add_code"))
        add_code_btn.setFixedWidth(100)
        add_code_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['teal']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 4px 8px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; border-color: {C['teal']}; }}
        """)

        def add_code_step():
            code = self._show_code_editor(parent=dialog)
            if code is not None:
                add_step_row(text="", step_type="code", code_content=code)

        add_code_btn.clicked.connect(add_code_step)
        add_row.addWidget(add_code_btn)

        # 📥 Code Store에서 가져오기 버튼
        import_code_btn = QPushButton(t("chat.wf_import_code"))
        import_code_btn.setFixedWidth(130)
        import_code_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['blue']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 4px 8px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; border-color: {C['blue']}; }}
        """)

        def import_code_from_store():
            selected = self._show_code_picker(parent=dialog)
            if selected:
                # selected = {"name": ..., "code": ..., "description": ...}
                add_step_row(
                    text="",
                    step_type="code",
                    code_content=selected["code"],
                    code_name=selected["name"],
                )

        import_code_btn.clicked.connect(import_code_from_store)
        add_row.addWidget(import_code_btn)

        main_layout.addLayout(add_row)

        # 스케줄
        sch_layout = QHBoxLayout()
        sch_label = QLabel(t("chat.wf_schedule_label"))
        sch_layout.addWidget(sch_label)
        sch_input = QLineEdit()
        sch_input.setPlaceholderText(t("chat.wf_schedule_placeholder"))
        if prefill:
            sch_input.setText(prefill.get("schedule", ""))
        sch_layout.addWidget(sch_input)
        main_layout.addLayout(sch_layout)

        # 에러 라벨 (인라인 validation용)
        error_label = QLabel("")
        error_label.setStyleSheet(f"color: {C['red']}; font-size: 12px; font-family: 'Segoe UI';")
        error_label.setFixedHeight(0)
        main_layout.addWidget(error_label)

        # 저장/취소 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton(t("chat.wf_cancel"))
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['surface1']}; color: {C['text']};
                border: none; border-radius: 6px;
                padding: 10px 24px; font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {C['surface2']}; }}
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton(t("chat.wf_save"))
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['blue']}; color: white;
                border: none; border-radius: 6px;
                padding: 10px 24px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {C['sapphire']}; }}
        """)

        def _validate_and_accept():
            wf_name = name_input.text().strip()
            if not wf_name:
                error_label.setFixedHeight(20)
                error_label.setText(t("chat.wf_name_required"))
                name_input.setFocus()
                return
            has_content = False
            for _, inp, _, data, _ in step_inputs:
                if data["type"] == "code" and (data.get("code", "").strip() or data.get("code_name", "")):
                    has_content = True
                    break
                elif data["type"] == "prompt" and inp.text().strip():
                    has_content = True
                    break
            if not has_content:
                error_label.setFixedHeight(20)
                error_label.setText(t("chat.wf_step_required"))
                return
            error_label.setFixedHeight(0)
            dialog.accept()

        save_btn.clicked.connect(_validate_and_accept)
        btn_layout.addWidget(save_btn)
        main_layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            import json as _json
            wf_name = name_input.text().strip()
            description = desc_input.text().strip()
            schedule = sch_input.text().strip()

            # 스텝을 JSON 배열로 조립
            steps = []
            for _, inp, _, data, _ in step_inputs:
                if data["type"] == "code" and data.get("code_name") and not data.get("code", "").strip():
                    # Code Store 참조 스텝 (code_name 필드 보존)
                    cn = data.get("code_name", "")
                    s = {"type": "code", "code_name": cn, "content": cn}
                    if data.get("args"):
                        s["args"] = data["args"]
                    steps.append(s)
                elif data["type"] == "code":
                    code_name = data.get("code_name", "")
                    code = data.get("code", "").strip()
                    if code_name:
                        s = {"type": "code", "code_name": code_name, "content": code}
                    elif code:
                        s = {"type": "code", "content": code}
                    else:
                        continue
                    if data.get("args"):
                        s["args"] = data["args"]
                    steps.append(s)
                else:
                    text = inp.text().strip()
                    if text:
                        # i18n dict가 있으면 보존 (다국어 지원)
                        i18n = data.get("_i18n")
                        if i18n and isinstance(i18n, dict):
                            steps.append({"type": "prompt", "content": i18n})
                        else:
                            steps.append({"type": "prompt", "content": text})

            if not steps:
                return

            if is_import:
                # Store에서 가져오기: /workflow/import API 직접 호출
                import json
                import urllib.request
                try:
                    from pathlib import Path
                    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    api_key = cfg.get("server", {}).get("api_key", "")
                    port = cfg.get("server", {}).get("port", 3000)

                    # prompt 전용 스텝만 텍스트로, 복합 스텝은 JSON
                    prompts = []
                    for s in steps:
                        if s["type"] == "prompt":
                            prompts.append(s["content"])
                        else:
                            prompts.append(s)
                    wf_data = {
                        "name": wf_name,
                        "description": description,
                        "prompts": _json.dumps(prompts, ensure_ascii=False),
                        "hint_tools": prefill.get("hint_tools", "") if prefill else "",
                        "schedule": schedule,
                    }

                    body = _json.dumps(wf_data).encode()
                    req = urllib.request.Request(
                        f"http://localhost:{port}/workflow/import",
                        data=body,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        result = json.loads(resp.read().decode())

                    self.add_bot_message(t("chat.wf_import_success"))

                    # Store 다이얼로그 닫기
                    if hasattr(self, '_store_dialog') and self._store_dialog:
                        self._store_dialog.accept()

                    # Import 후 자동으로 워크플로우 목록 표시
                    QTimer.singleShot(300, lambda: self.sig_message_sent.emit("워크플로우 목록 보여줘"))

                except urllib.error.HTTPError as e:
                    detail = ""
                    try:
                        detail = e.read().decode() if e.fp else str(e)
                    except Exception:
                        detail = str(e)
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(self, t("chat.wf_import_fail"), t("chat.wf_import_server_error", code=e.code, detail=detail[:300]))
                except Exception as e:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(self, t("chat.wf_import_fail"), t("chat.wf_import_api_error", e=str(e)))
            else:
                # Workflow 생성/수정: API 직접 호출 (LLM 거치지 않음)
                import json
                import urllib.request
                try:
                    from pathlib import Path
                    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    api_key = cfg.get("server", {}).get("api_key", "")
                    port = cfg.get("server", {}).get("port", 3000)

                    wf_data = {
                        "name": wf_name,
                        "description": description,
                        "prompts": _json.dumps(steps, ensure_ascii=False),
                        "schedule": schedule,
                    }
                    body = _json.dumps(wf_data).encode()
                    req = urllib.request.Request(
                        f"http://localhost:{port}/workflow/save",
                        data=body,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        result = json.loads(resp.read().decode())

                    self.add_bot_message(result.get("message", "✅ 저장 완료"))

                    wfdata = result.get("wfdata", "")
                    if wfdata:
                        self.render_workflow_list(wfdata)

                except urllib.error.HTTPError as e:
                    detail = ""
                    try:
                        detail = e.read().decode() if e.fp else str(e)
                    except Exception:
                        detail = str(e)
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(self, t("desktop.save_fail"), t("desktop.save_server_error", code=e.code, detail=detail[:300]))
                except Exception as e:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(self, t("desktop.save_fail"), t("desktop.save_api_error", error=str(e)))

    def _fetch_workflow_page(self, page: str):
        """워크플로우 목록 페이지를 REST API로 직접 가져와 렌더링 (LLM 우회)"""
        import urllib.request, json as _json
        try:
            _base = getattr(self, '_api_base', 'http://127.0.0.1:3000')
            _key = getattr(self, '_api_key', '')
            req = urllib.request.Request(
                f"{_base}/workflows/list?page={page}",
                headers={"Authorization": f"Bearer {_key}"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode())
            wfdata = data.get("wfdata", "")
            if wfdata:
                self.render_workflow_list(wfdata)
            else:
                self.add_bot_message("📭 워크플로우가 없습니다.")
        except Exception as e:
            # API 실패 시 기존 방식(LLM)으로 폴백
            self.sig_message_sent.emit(f'list_workflows(page="{page}") 실행해줘')

    def render_workflow_list(self, result: str):
        """list_workflows 결과를 interactive HTML 테이블로 렌더링"""
        import json as _json
        import base64 as _b64
        from urllib.parse import quote

        # <!--WFDATA:base64--> 파싱
        data = []
        meta = {"page": 1, "total_pages": 1, "total": 0}
        for line in result.split("\n"):
            s = line.strip()
            if s.startswith("<!--WFDATA:") and s.endswith("-->"):
                try:
                    b64 = s[11:-3]
                    payload = _json.loads(_b64.b64decode(b64).decode())
                    if isinstance(payload, dict) and "_meta" in payload:
                        meta = payload["_meta"]
                        data = payload.get("items", [])
                    else:
                        data = payload  # 이전 형식 호환
                        meta["total"] = len(data)
                except Exception:
                    pass
                break

        if not data:
            self.add_bot_message(t("chat.workflow_empty"))
            return

        # 수정 다이얼로그용 데이터 캐시
        self._workflow_cache = {wf["name"]: wf for wf in data}

        rows = ""
        for wf in data:
            name = wf.get("name", "?")
            name_escaped = name.replace("'", "\\'").replace('"', '&quot;')
            sch = wf.get("schedule", "") or "-"
            run_url = quote(t("chat.workflow_run", name=name))
            del_url = quote(t("chat.workflow_delete", name=name))
            edit_url = quote(name)  # wfedit: 용 — 이름만 전달
            desc = (wf.get('description', '') or '')[:25]
            rows += f"""
            <tr>
                <td>{name_escaped}</td>
                <td style="font-size:12px;color:{C['subtext0']}">{desc}</td>
                <td style="text-align:center">{wf.get('steps', 0)}</td>
                <td style="text-align:center">{wf.get('runs', 0)}</td>
                <td>{sch}</td>
                <td style="text-align:right; white-space:nowrap;">
                    <a href="wfaction:{run_url}" class="wf-btn wf-run" title="{t('tooltip.run')}">▶</a>
                    <a href="wfedit:{edit_url}" class="wf-btn wf-edit" title="{t('tooltip.edit')}">✏</a>
                    <a href="wfshare:{edit_url}" class="wf-btn wf-share" title="{t('tooltip.share')}">📤</a>
                    <a href="wfaction:{del_url}" class="wf-btn wf-del" title="{t('tooltip.delete')}">🗑</a>
                </td>
            </tr>"""

        # 페이지 네비게이션 버튼
        pg = meta.get("page", 1)
        total_pages = meta.get("total_pages", 1)
        total_count = meta.get("total", len(data))
        nav_html = ""
        if total_pages > 1:
            prev_btn = ""
            next_btn = ""
            if pg > 1:
                prev_btn = f'<a href="wfpage:{pg-1}#t=0" class="wf-nav-btn">{t("chat.workflow_prev")}</a>'
            if pg < total_pages:
                next_btn = f'<a href="wfpage:{pg+1}#t=0" class="wf-nav-btn">{t("chat.workflow_next")}</a>'
            nav_html = f"""
            <div style="display:flex; justify-content:space-between; align-items:center; margin-top:10px; padding:4px 8px;">
                <div>{prev_btn}</div>
                <div style="font-size:12px; color:{C['subtext0']}">{t('chat.wf_page_indicator', pg=pg, total=total_pages)}</div>
                <div>{next_btn}</div>
            </div>"""

        html = f"""
        <div class="bot-bubble">
            <div style="font-size:14px; font-weight:bold; margin-bottom:8px;">📋 Workflow {total_count}{t('chat.wf_count_suffix')}</div>
            <div style="max-height:350px; overflow-y:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:13px;">
                <tr style="border-bottom:2px solid {C['surface1']};">
                    <th style="text-align:left; padding:6px 8px;">{t('chat.wf_th_name')}</th>
                    <th style="text-align:left; padding:6px 4px;">{t('chat.wf_th_desc')}</th>
                    <th style="text-align:center; padding:6px 4px;">Steps</th>
                    <th style="text-align:center; padding:6px 4px;">{t('chat.wf_th_runs')}</th>
                    <th style="text-align:left; padding:6px 4px;">{t('chat.wf_th_schedule')}</th>
                    <th style="text-align:right; padding:6px 4px;">{t('chat.wf_th_manage')}</th>
                </tr>
                {rows}
            </table>
            </div>{nav_html}
            <div style="display:flex; gap:8px; margin-top:10px; padding-top:8px; border-top:1px solid {C['surface1']};">
                <a href="wfcreate:new#t=0" style="display:inline-block; padding:6px 14px; border-radius:8px;
                    background:{C['surface0']}; color:{C['yellow']}; text-decoration:none;
                    font-size:12px; font-weight:bold; cursor:pointer; transition:background 0.15s;"
                    onmouseover="this.style.background='{C['surface1']}'" onmouseout="this.style.background='{C['surface0']}'"
                >⚡ {t('chat.card_new_workflow')}</a>
                <a href="wfcreate:fromconv#t=0" style="display:inline-block; padding:6px 14px; border-radius:8px;
                    background:{C['surface0']}; color:{C['blue']}; text-decoration:none;
                    font-size:12px; font-weight:bold; cursor:pointer; transition:background 0.15s;"
                    onmouseover="this.style.background='{C['surface1']}'" onmouseout="this.style.background='{C['surface0']}'"
                >💬 {t('chat.card_wf_from_conv')}</a>
            </div>
            <style>
                .wf-btn {{
                    display:inline-block; width:28px; height:28px; line-height:28px;
                    text-align:center; border-radius:6px; text-decoration:none;
                    margin-left:4px; font-size:14px; cursor:pointer;
                    transition: background 0.15s;
                }}
                .wf-run {{ background:{C['surface0']}; }}
                .wf-run:hover {{ background:{C['green']}; }}
                .wf-edit {{ background:{C['surface0']}; }}
                .wf-edit:hover {{ background:{C['yellow']}; }}
                .wf-del {{ background:{C['surface0']}; }}
                .wf-del:hover {{ background:{C['red']}; }}
                .wf-share {{ background:{C['surface0']}; }}
                .wf-share:hover {{ background:{C['blue']}; }}
                .wf-nav-btn {{
                    display:inline-block; padding:6px 16px; border-radius:8px;
                    background:{C['surface0']}; color:{C['text']}; text-decoration:none;
                    font-size:13px; font-weight:bold; cursor:pointer;
                    transition: background 0.15s;
                }}
                .wf-nav-btn:hover {{ background:{C['blue']}; color:white; }}
                table tr {{ border-bottom: 1px solid {C['surface0']}; }}
                table td {{ padding: 8px; }}
            </style>
        </div>"""
        self._append_html(html)
        self._stop_thinking()

    def render_persona_list(self, result: str):
        """list_personas 결과를 interactive HTML 테이블로 렌더링"""
        import json as _json
        import base64 as _b64
        from urllib.parse import quote

        # <!--PERSONA_DATA:base64--> 파싱
        data = []
        meta = {"page": 1, "total_pages": 1, "total": 0}
        for line in result.split("\n"):
            s = line.strip()
            if s.startswith("<!--PERSONA_DATA:") and s.endswith("-->"):
                try:
                    b64 = s[17:-3]
                    payload = _json.loads(_b64.b64decode(b64).decode())
                    if isinstance(payload, dict) and "_meta" in payload:
                        meta = payload["_meta"]
                        data = payload.get("items", [])
                    else:
                        data = payload
                        meta["total"] = len(data)
                except Exception:
                    pass
                break

        if not data:
            self.add_bot_message("📋 저장된 페르소나가 없습니다.")
            return

        # 공유용 데이터 캐시
        self._persona_cache = {p["name"]: p for p in data}

        rows = ""
        for p in data:
            name = p.get("name", "?")
            name_escaped = name.replace("'", "\\'").replace('"', '&quot;')
            desc = (p.get("description", "") or "")[:30]
            activated = p.get("activated", 0)
            activate_url = quote(f"{name} 페르소나 활성화해줘")
            edit_url = quote(name)  # personaedit: 용 — 이름만 전달
            del_url = quote(f"{name} 페르소나 삭제해줘")
            rows += f"""
            <tr>
                <td>{name_escaped}</td>
                <td style="font-size:12px;color:{C['subtext0']}">{desc}</td>
                <td style="text-align:center">{activated}</td>
                <td style="text-align:right; white-space:nowrap;">
                    <a href="wfaction:{activate_url}" class="p-btn p-activate" title="{t('tooltip.activate')}">▶</a>
                    <a href="personaedit:{edit_url}" class="p-btn p-edit" title="{t('tooltip.edit')}">✏</a>
                    <a href="personashare:{edit_url}" class="p-btn p-share" title="{t('tooltip.share')}">📤</a>
                    <a href="wfaction:{del_url}" class="p-btn p-del" title="{t('tooltip.delete')}">🗑</a>
                </td>
            </tr>"""

        total_count = meta.get("total", len(data))

        html = f"""
        <div class="bot-bubble">
            <div style="font-size:14px; font-weight:bold; margin-bottom:8px;">🎭 페르소나 {total_count}개</div>
            <div style="max-height:350px; overflow-y:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:13px;">
                <tr style="border-bottom:2px solid {C['surface1']};">
                    <th style="text-align:left; padding:6px 8px;">이름</th>
                    <th style="text-align:left; padding:6px 4px;">설명</th>
                    <th style="text-align:center; padding:6px 4px;">활성화</th>
                    <th style="text-align:right; padding:6px 4px;">관리</th>
                </tr>
                {rows}
            </table>
            </div>
            <style>
                .p-btn {{
                    display:inline-block; width:28px; height:28px; line-height:28px;
                    text-align:center; border-radius:6px; text-decoration:none;
                    margin-left:4px; font-size:14px; cursor:pointer;
                    transition: background 0.15s;
                }}
                .p-activate {{ background:{C['surface0']}; }}
                .p-activate:hover {{ background:{C['mauve']}; }}
                .p-edit {{ background:{C['surface0']}; }}
                .p-edit:hover {{ background:{C['yellow']}; }}
                .p-del {{ background:{C['surface0']}; }}
                .p-del:hover {{ background:{C['red']}; }}
                .p-share {{ background:{C['surface0']}; }}
                .p-share:hover {{ background:{C['blue']}; }}
                table tr {{ border-bottom: 1px solid {C['surface0']}; }}
                table td {{ padding: 8px; }}
            </style>
            <div style="display:flex; gap:8px; margin-top:10px; padding-top:8px; border-top:1px solid {C['surface1']};">
                <a href="personacreate:new#t=0" style="display:inline-block; padding:6px 14px; border-radius:8px;
                    background:{C['surface0']}; color:{C['mauve']}; text-decoration:none;
                    font-size:12px; font-weight:bold; cursor:pointer; transition:background 0.15s;"
                    onmouseover="this.style.background='{C['surface1']}'" onmouseout="this.style.background='{C['surface0']}'"
                >🎭 {t('chat.card_new_persona')}</a>
            </div>
        </div>"""
        self._append_html(html)
        self._stop_thinking()

    def render_code_list(self, result: str):
        """list_codes 결과를 interactive HTML 테이블로 렌더링"""
        import json as _json
        import base64 as _b64
        from urllib.parse import quote

        # <!--CODE_DATA:base64--> 파싱
        data = []
        meta = {"total": 0}
        for line in result.split("\n"):
            s = line.strip()
            if s.startswith("<!--CODE_DATA:") and s.endswith("-->"):
                try:
                    b64 = s[14:-3]
                    payload = _json.loads(_b64.b64decode(b64).decode())
                    if isinstance(payload, dict) and "_meta" in payload:
                        meta = payload["_meta"]
                        data = payload.get("items", [])
                    else:
                        data = payload
                        meta["total"] = len(data)
                except Exception:
                    pass
                break

        if not data:
            self.add_bot_message("📦 No imported codes.")
            return

        # 공유용 데이터 캐시
        self._code_cache = {c["name"]: c for c in data}

        rows = ""
        for c in data:
            name = c.get("name", "?")
            name_escaped = name.replace("'", "\\'").replace('"', '&quot;')
            desc = (c.get("description", "") or "")[:30]
            category = c.get("category", "Other")
            param_count = c.get("param_count", 0)
            run_url = quote(t("codes.run_prompt", name=name))
            edit_url = quote(name)
            del_url = quote(t("codes.delete_prompt", name=name))
            rows += f"""
            <tr>
                <td>{name_escaped}</td>
                <td style="font-size:12px;color:{C['subtext0']}">{desc}</td>
                <td style="text-align:center">{category}</td>
                <td style="text-align:center">{param_count}</td>
                <td style="text-align:right; white-space:nowrap;">
                    <a href="wfaction:{run_url}" class="c-btn c-run" title="{t('tooltip.run')}">▶</a>
                    <a href="codeedit:{edit_url}" class="c-btn c-edit" title="{t('tooltip.edit')}">✏</a>
                    <a href="codeshare:{edit_url}" class="c-btn c-share" title="{t('tooltip.share')}">📤</a>
                    <a href="wfaction:{del_url}" class="c-btn c-del" title="{t('tooltip.delete')}">🗑</a>
                </td>
            </tr>"""

        total_count = meta.get("total", len(data))

        html = f"""
        <div class="bot-bubble">
            <div style="font-size:14px; font-weight:bold; margin-bottom:8px;">🐍 Code {total_count}{t('chat.wf_count_suffix')}</div>
            <div style="max-height:350px; overflow-y:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:13px;">
                <tr style="border-bottom:2px solid {C['surface1']};">
                    <th style="text-align:left; padding:6px 8px;">{t('chat.wf_th_name')}</th>
                    <th style="text-align:left; padding:6px 4px;">{t('chat.wf_th_desc')}</th>
                    <th style="text-align:center; padding:6px 4px;">Category</th>
                    <th style="text-align:center; padding:6px 4px;">Params</th>
                    <th style="text-align:right; padding:6px 4px;">{t('chat.wf_th_manage')}</th>
                </tr>
                {rows}
            </table>
            </div>
            <style>
                .c-btn {{
                    display:inline-block; width:28px; height:28px; line-height:28px;
                    text-align:center; border-radius:6px; text-decoration:none;
                    margin-left:4px; font-size:14px; cursor:pointer;
                    transition: background 0.15s;
                }}
                .c-run {{ background:{C['surface0']}; }}
                .c-run:hover {{ background:{C['green']}; }}
                .c-edit {{ background:{C['surface0']}; }}
                .c-edit:hover {{ background:{C['yellow']}; }}
                .c-del {{ background:{C['surface0']}; }}
                .c-del:hover {{ background:{C['red']}; }}
                .c-share {{ background:{C['surface0']}; }}
                .c-share:hover {{ background:{C['blue']}; }}
                table tr {{ border-bottom: 1px solid {C['surface0']}; }}
                table td {{ padding: 8px; }}
            </style>
            <div style="display:flex; gap:8px; margin-top:10px; padding-top:8px; border-top:1px solid {C['surface1']};">
                <a href="codecreate:new#t=0" style="display:inline-block; padding:6px 14px; border-radius:8px;
                    background:{C['surface0']}; color:{C['green']}; text-decoration:none;
                    font-size:12px; font-weight:bold; cursor:pointer; transition:background 0.15s;"
                    onmouseover="this.style.background='{C['surface1']}'" onmouseout="this.style.background='{C['surface0']}'"
                >🐍 {t('chat.card_new_code')}</a>
            </div>
        </div>"""
        self._append_html(html)
        self._stop_thinking()

    def _show_code_picker(self, parent=None):
        """📥 Code Store에서 코드를 선택하는 팝업 — REST API로 목록 조회"""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QPushButton,
            QScrollArea, QWidget
        )
        from PyQt6.QtCore import Qt

        dialog = QDialog(parent or self)
        dialog.setWindowTitle(t("chat.wf_import_code"))
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(400)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {C['base']}; color: {C['text']}; }}
            QLabel {{ color: {C['text']}; font-family: 'Segoe UI'; }}
        """)

        layout = QVBoxLayout(dialog)
        title = QLabel(f"📥 {t('chat.wf_import_code')}")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {C['blue']};")
        layout.addWidget(title)

        # REST API로 Code Store 목록 조회
        code_names = []
        try:
            import urllib.request, json as _json
            _base = getattr(self, '_api_base', 'http://127.0.0.1:3000')
            _key = getattr(self, '_api_key', '')
            req = urllib.request.Request(
                f"{_base}/codes",
                headers={"Authorization": f"Bearer {_key}"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode())
                code_names = data.get("codes", []) if isinstance(data, dict) else data
        except Exception as e:
            err = QLabel(f"⚠ {t('codes.error', error=str(e))}")
            err.setStyleSheet(f"color: {C['red']}; font-size: 12px;")
            layout.addWidget(err)

        if not code_names:
            empty = QLabel(t("codes.empty"))
            empty.setStyleSheet(f"color: {C['subtext0']}; font-size: 13px;")
            layout.addWidget(empty)

        selected = [None]

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {C['base']}; }}")
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setSpacing(4)
        list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        for name in code_names:
            btn = QPushButton(f"📦 {name}")
            btn.setFixedHeight(36)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['surface0']}; color: {C['text']};
                    border: 1px solid {C['surface1']}; border-radius: 6px;
                    padding: 4px 12px; font-size: 13px; text-align: left;
                }}
                QPushButton:hover {{ background: {C['surface1']}; border-color: {C['blue']}; }}
            """)

            def on_select(n=name):
                # 코드 본문을 API에서 가져오기
                try:
                    from urllib.parse import quote as _quote
                    code_url = f"{_base}/code/{_quote(str(n), safe='')}"
                    print(f"[code_picker] Fetching: {code_url}")
                    req2 = urllib.request.Request(
                        code_url,
                        headers={"Authorization": f"Bearer {_key}"}
                    )
                    with urllib.request.urlopen(req2, timeout=5) as resp2:
                        detail = _json.loads(resp2.read().decode())
                        selected[0] = {
                            "name": n,
                            "code": detail.get("code", ""),
                            "description": detail.get("description", ""),
                        }
                        dialog.accept()
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(dialog, "Error", t("desktop.code_load_fail_url", error=str(e), url=code_url))
                    # 실패 시 dialog 닫지 않음 — 사용자가 다시 선택 가능

            btn.clicked.connect(lambda checked, n=name: on_select(n))
            list_layout.addWidget(btn)

        scroll.setWidget(list_widget)
        layout.addWidget(scroll)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            return selected[0]
        return None

    def _show_code_edit_dialog(self, code_name: str):
        """🐍 코드 수정 다이얼로그 — REST API로 읽기/쓰기 + 구문 강조"""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QLineEdit, QPushButton, QTextEdit
        )
        from PyQt6.QtCore import Qt, QRegularExpression
        from PyQt6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat, QColor
        import json
        import urllib.request
        import urllib.parse

        # ── Python Syntax Highlighter ──
        class PythonHighlighter(QSyntaxHighlighter):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.rules = []
                # Keywords
                kw_fmt = QTextCharFormat()
                kw_fmt.setForeground(QColor("#cba6f7"))  # mauve
                kw_fmt.setFontWeight(QFont.Weight.Bold)
                keywords = [
                    "False", "None", "True", "and", "as", "assert", "async", "await",
                    "break", "class", "continue", "def", "del", "elif", "else", "except",
                    "finally", "for", "from", "global", "if", "import", "in", "is",
                    "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
                    "try", "while", "with", "yield",
                ]
                for kw in keywords:
                    self.rules.append((QRegularExpression(rf"\b{kw}\b"), kw_fmt))
                # Builtins
                bi_fmt = QTextCharFormat()
                bi_fmt.setForeground(QColor("#fab387"))  # peach
                builtins = ["print", "len", "range", "int", "str", "float", "list", "dict",
                            "set", "tuple", "type", "isinstance", "open", "super", "self",
                            "enumerate", "zip", "map", "filter", "sorted", "reversed"]
                for bi in builtins:
                    self.rules.append((QRegularExpression(rf"\b{bi}\b"), bi_fmt))
                # Decorators
                dec_fmt = QTextCharFormat()
                dec_fmt.setForeground(QColor("#f9e2af"))  # yellow
                self.rules.append((QRegularExpression(r"@\w+"), dec_fmt))
                # Numbers
                num_fmt = QTextCharFormat()
                num_fmt.setForeground(QColor("#fab387"))  # peach
                self.rules.append((QRegularExpression(r"\b\d+\.?\d*\b"), num_fmt))
                # Function defs
                fn_fmt = QTextCharFormat()
                fn_fmt.setForeground(QColor("#89b4fa"))  # blue
                self.rules.append((QRegularExpression(r"\bdef\s+(\w+)"), fn_fmt))
                self.rules.append((QRegularExpression(r"\bclass\s+(\w+)"), fn_fmt))
                # Strings
                self.str_fmt = QTextCharFormat()
                self.str_fmt.setForeground(QColor("#a6e3a1"))  # green
                self.rules.append((QRegularExpression(r'\"\"\".*?\"\"\"'), self.str_fmt))
                self.rules.append((QRegularExpression(r"\'\'\'.*?\'\'\'"), self.str_fmt))
                self.rules.append((QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), self.str_fmt))
                self.rules.append((QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), self.str_fmt))
                # Comments
                self.comment_fmt = QTextCharFormat()
                self.comment_fmt.setForeground(QColor("#6c7086"))  # overlay0
                self.comment_fmt.setFontItalic(True)
                self.rules.append((QRegularExpression(r"#[^\n]*"), self.comment_fmt))

            def highlightBlock(self, text):
                for pattern, fmt in self.rules:
                    it = pattern.globalMatch(text)
                    while it.hasNext():
                        m = it.next()
                        self.setFormat(m.capturedStart(), m.capturedLength(), fmt)

        # ── REST API로 코드 데이터 가져오기 ──
        try:
            _base = getattr(self, '_api_base', 'http://127.0.0.1:3000')
            _key = getattr(self, '_api_key', '')
            encoded_name = urllib.parse.quote(code_name)
            url = f"{_base}/code/{encoded_name}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_key}"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                row = json.loads(resp.read().decode())
        except Exception as e:
            self.add_bot_message(f"⚠️ Code load error: {e}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"✏ {code_name}")
        dlg.setMinimumSize(700, 550)
        dlg.setStyleSheet(f"""
            QDialog {{ background-color: {C['base']}; color: {C['text']}; }}
            QLabel {{ color: {C['subtext1']}; font-size: 12px; }}
            QLineEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 6px 10px; font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {C['blue']}; }}
            QTextEdit {{
                background-color: #1e1e2e; color: {C['text']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 8px; font-family: Consolas, Monaco, monospace; font-size: 12px;
            }}
            QTextEdit:focus {{ border-color: {C['blue']}; }}
        """)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        # Name
        layout.addWidget(QLabel(t('codes.th_name')))
        name_input = QLineEdit(row.get("name", ""))
        layout.addWidget(name_input)

        # Description
        layout.addWidget(QLabel(t('codes.th_desc')))
        desc_input = QLineEdit(row.get("description", ""))
        layout.addWidget(desc_input)

        # Code (with syntax highlighting)
        layout.addWidget(QLabel("Code"))
        code_input = QTextEdit()
        code_input.setPlainText(row.get("code", ""))
        code_font = QFont("Consolas", 11)
        code_input.setFont(code_font)
        code_input._highlighter = PythonHighlighter(code_input.document())
        layout.addWidget(code_input, 1)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton(t('codes.btn_cancel'))
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['subtext1']};
                border: 1px solid {C['surface2']}; border-radius: 8px;
                padding: 8px 20px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; }}
        """)
        cancel_btn.clicked.connect(dlg.reject)
        btn_layout.addWidget(cancel_btn)

        share_btn = QPushButton(f"☁️ {t('codes.btn_share')}")
        share_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['green']}; color: {C['base']};
                border: none; border-radius: 8px;
                padding: 8px 20px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {C['teal']}; }}
        """)

        def _share():
            name = name_input.text().strip()
            desc = desc_input.text().strip()
            code = code_input.toPlainText()
            if not name:
                return
            try:
                _web_base = "https://xoul.app"
                share_data = json.dumps({
                    "type": "code",
                    "id": name.lower().replace(" ", "-"),
                    "name": name,
                    "description": desc,
                    "code": code,
                    "category": row.get("category", "Other"),
                    "icon": "🐍",
                    "lang": "multi",
                }).encode()
                share_req = urllib.request.Request(
                    f"{_web_base}/api/share",
                    data=share_data, method="POST",
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(share_req, timeout=15) as resp:
                    result = json.loads(resp.read().decode())
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(dlg, "✅", f"PR created!\n{result.get('pr_url', '')}")
            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(dlg, "⚠", f"Share failed: {e}")

        share_btn.clicked.connect(_share)
        btn_layout.addWidget(share_btn)

        save_btn = QPushButton(t('codes.btn_save'))
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['blue']}; color: white;
                border: none; border-radius: 8px;
                padding: 8px 24px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {C['mauve']}; }}
        """)

        def _save():
            new_name = name_input.text().strip()
            new_desc = desc_input.text().strip()
            new_code = code_input.toPlainText()
            if not new_name:
                return
            try:
                data = json.dumps({"name": new_name, "description": new_desc, "code": new_code}).encode()
                put_req = urllib.request.Request(
                    f"{_base}/code/{encoded_name}",
                    data=data, method="PUT",
                    headers={"Authorization": f"Bearer {_key}", "Content-Type": "application/json"}
                )
                urllib.request.urlopen(put_req, timeout=5)
                dlg.accept()
                self.add_bot_message(f"✅ Code **{new_name}** updated.")
            except Exception as e:
                self.add_bot_message(f"⚠️ Save error: {e}")

        save_btn.clicked.connect(_save)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        dlg.exec()

    def _show_persona_dialog(self, persona_name: str = None):
        """🎭 페르소나 생성/수정 다이얼로그
        persona_name: None이면 신규 생성, 이름이면 수정 모드
        """
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QLineEdit, QPushButton, QTextEdit
        )
        from PyQt6.QtCore import Qt

        is_edit = persona_name is not None
        prefill = {}

        # 수정 모드: 서버에서 데이터 가져오기
        if is_edit:
            try:
                import urllib.request, json as _json
                from urllib.parse import quote
                _base = getattr(self, '_api_base', 'http://127.0.0.1:3000')
                _key = getattr(self, '_api_key', '')
                url = f"{_base}/persona/{quote(persona_name, safe='')}"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_key}"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    prefill = _json.loads(resp.read().decode())
            except Exception as e:
                self.add_bot_message(t("desktop.persona_info_fail", error=str(e)))
                return

        dialog = QDialog(self)
        dialog.setWindowTitle(t("chat.persona_edit_title") if is_edit else t("chat.persona_new_title"))
        dialog.setMinimumWidth(550)
        dialog.setMinimumHeight(400)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {C['base']}; color: {C['text']}; }}
            QLabel {{ color: {C['text']}; font-family: 'Segoe UI'; }}
            QLineEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 4px;
                padding: 2px 6px; font-size: 13px; font-family: 'Segoe UI';
            }}
            QLineEdit:focus {{ border-color: {C['blue']}; }}
            QTextEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 4px;
                padding: 6px; font-size: 13px; font-family: 'Consolas', 'Segoe UI';
            }}
            QTextEdit:focus {{ border-color: {C['blue']}; }}
        """)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(10)

        # 제목
        title = QLabel("🎭 페르소나 수정" if is_edit else "🎭 새 페르소나")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {C['mauve']};")
        main_layout.addWidget(title)

        # 이름 입력
        name_layout = QHBoxLayout()
        name_label = QLabel("이름")
        name_label.setFixedWidth(50)
        name_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        name_layout.addWidget(name_label)
        name_input = QLineEdit()
        name_input.setFixedHeight(28)
        name_input.setPlaceholderText("페르소나 이름 (예: 영어 튜터)")
        name_input.setText(prefill.get("name", ""))
        if is_edit:
            name_input.setReadOnly(True)
            name_input.setStyleSheet(name_input.styleSheet() + f"color: {C['subtext0']};")
        name_layout.addWidget(name_input)
        main_layout.addLayout(name_layout)

        # 설명 입력
        desc_layout = QHBoxLayout()
        desc_label = QLabel("설명")
        desc_label.setFixedWidth(50)
        desc_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        desc_layout.addWidget(desc_label)
        desc_input = QLineEdit()
        desc_input.setFixedHeight(28)
        desc_input.setPlaceholderText("이 페르소나가 하는 일을 간단히 설명")
        desc_input.setText(prefill.get("description", ""))
        desc_layout.addWidget(desc_input)
        main_layout.addLayout(desc_layout)

        # 프롬프트 입력 (멀티라인)
        prompt_label = QLabel("프롬프트 (시스템 프롬프트)")
        prompt_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        main_layout.addWidget(prompt_label)
        prompt_input = QTextEdit()
        prompt_input.setPlaceholderText("AI에게 줄 역할 지시사항을 입력하세요.\n예: 당신은 친절한 영어 튜터입니다. 사용자가 영어로 질문하면...")
        prompt_input.setPlainText(prefill.get("prompt", ""))
        prompt_input.setMinimumHeight(180)
        main_layout.addWidget(prompt_input)

        # 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("취소")
        cancel_btn.setFixedSize(80, 32)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {C['surface1']}; }}
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("수정" if is_edit else "생성")
        save_btn.setFixedSize(80, 32)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['mauve']}; color: white;
                border: none; border-radius: 6px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {C['blue']}; }}
        """)
        btn_layout.addWidget(save_btn)
        main_layout.addLayout(btn_layout)

        def _on_save():
            n = name_input.text().strip()
            d = desc_input.text().strip()
            p = prompt_input.toPlainText().strip()

            if not n:
                name_input.setStyleSheet(name_input.styleSheet() + f"border-color: {C['red']};")
                return
            if not p:
                prompt_input.setStyleSheet(prompt_input.styleSheet() + f"border-color: {C['red']};")
                return

            dialog.accept()

            if is_edit:
                import json as _json
                cmd = f'update_persona(name="{persona_name}", prompt={_json.dumps(p, ensure_ascii=False)}, description="{d}") 실행해줘'
            else:
                import json as _json
                cmd = f'create_persona(name="{n}", prompt={_json.dumps(p, ensure_ascii=False)}, description="{d}") 실행해줘'
            self.sig_message_sent.emit(cmd)

        save_btn.clicked.connect(_on_save)
        dialog.exec()

    def _open_edit_dialog(self, wf_name: str):
        """✏ Workflow 수정 다이얼로그 — prompt/code 스텝 타입 지원"""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QLineEdit, QPushButton, QScrollArea, QWidget
        )
        from PyQt6.QtCore import Qt

        cache = getattr(self, '_workflow_cache', {})
        wf = cache.get(wf_name, {})

        # 서버 API에서 전체 워크플로우 데이터 조회 (prompts 포함)
        if not wf.get("prompts"):
            try:
                import urllib.request, json as _json
                from urllib.parse import quote as _quote
                _base = getattr(self, '_api_base', 'http://127.0.0.1:3000')
                _key = getattr(self, '_api_key', '')
                url = f"{_base}/workflow/{_quote(wf_name, safe='')}"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_key}"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    wf = _json.loads(resp.read().decode())
            except Exception as e:
                self.add_bot_message(t("chat.wf_edit_fetch_error", e=str(e)))
                return

        if not wf:
            self.add_bot_message(t("chat.wf_edit_not_found"))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(t("chat.wf_edit_title", name=wf_name))
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(480)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {C['base']}; color: {C['text']}; }}
            QLabel {{ color: {C['text']}; font-family: 'Segoe UI'; }}
            QLineEdit {{
                background-color: {C['surface0']}; color: {C['text']};
                border: 1px solid {C['surface1']}; border-radius: 4px;
                padding: 2px 6px; font-size: 13px; font-family: 'Segoe UI';
            }}
            QLineEdit:focus {{ border-color: {C['blue']}; }}
        """)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(10)

        # 제목
        title = QLabel(f"⚡ {wf_name}")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {C['blue']};")
        main_layout.addWidget(title)

        # 단계 영역 (스크롤)
        step_label = QLabel(t("chat.wf_steps_label"))
        step_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        main_layout.addWidget(step_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(300)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {C['base']}; padding: 0; margin: 0; }}")
        steps_widget = QWidget()
        steps_widget.setContentsMargins(0, 0, 0, 0)
        steps_widget.setStyleSheet(f"QWidget {{ margin: 0; padding: 0; background: {C['base']}; }}")
        steps_layout = QVBoxLayout(steps_widget)
        steps_layout.setSpacing(0)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        step_inputs = []
        prompts = wf.get("prompts", [])

        def _renumber_steps():
            for j, (_, _, lbl, _, _) in enumerate(step_inputs, 1):
                lbl.setText(f"{j}.")

        def add_step_row(text="", idx=None, step_type="prompt", code_content="", code_name="", insert_at=None, step_args=None):
            row = QHBoxLayout()
            row.setSpacing(4)
            row.setContentsMargins(0, 1, 0, 1)
            num = idx if idx is not None else len(step_inputs) + 1
            label = QLabel(f"{num}.")
            label.setFixedWidth(24)
            label.setFixedHeight(26)
            label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {C['overlay0']};")
            row.addWidget(label)



            inp = QLineEdit()
            inp.setFixedHeight(26)
            step_data = {"type": step_type, "code": code_content, "code_name": code_name, "args": step_args or {}}

            if step_type == "code":
                if code_name:
                    inp.setText(f"🐍 {code_name}")
                else:
                    first_line = code_content.split("\n")[0] if code_content else "def run():"
                    inp.setText(f"🐍 {first_line}")
                inp.setReadOnly(True)
                inp.setStyleSheet(f"""
                    QLineEdit {{
                        background-color: {C['mantle']}; color: {C['green']};
                        border: 1px solid {C['green']}; border-radius: 4px;
                        padding: 2px 6px; font-size: 12px; font-family: 'Consolas', monospace;
                    }}
                """)
            else:
                inp.setText(text)
                inp.setPlaceholderText(t("chat.wf_step_placeholder", num=num))
            row.addWidget(inp)

            edit_code_btn = QPushButton("✏")
            edit_code_btn.setFixedSize(24, 24)
            edit_code_btn.setToolTip(t("chat.wf_code_edit"))
            edit_code_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['surface0']}; color: {C['blue']};
                    border: none; border-radius: 4px; font-size: 12px;
                }}
                QPushButton:hover {{ background: {C['blue']}; color: white; }}
            """)
            edit_code_btn.setVisible(step_type == "code")
            row.addWidget(edit_code_btn)

            # ▲▼ 이동 버튼
            btn_style = f"""
                QPushButton {{
                    background: {C['surface0']}; color: {C['overlay1']};
                    border: none; border-radius: 4px; font-size: 11px;
                }}
                QPushButton:hover {{ background: {C['surface1']}; color: {C['blue']}; }}
            """
            up_btn = QPushButton("▲")
            up_btn.setFixedSize(22, 24)
            up_btn.setStyleSheet(btn_style)
            row.addWidget(up_btn)

            down_btn = QPushButton("▼")
            down_btn.setFixedSize(22, 24)
            down_btn.setStyleSheet(btn_style)
            row.addWidget(down_btn)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(24, 24)
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['surface0']}; color: {C['red']};
                    border: none; border-radius: 4px; font-size: 12px;
                }}
                QPushButton:hover {{ background: {C['red']}; color: white; }}
            """)
            row.addWidget(del_btn)

            container = QWidget()
            container.setLayout(row)
            container.setContentsMargins(0, 0, 0, 0)
            container.setStyleSheet(f"background: {C['base']};")

            # ── 코드 스텝: 인자 패널 ──
            args_container = None
            if step_type == "code":
                args_container = QWidget()
                args_layout_inner = QVBoxLayout(args_container)
                args_layout_inner.setSpacing(2)
                args_layout_inner.setContentsMargins(28, 0, 0, 4)
                args_container.setStyleSheet(f"background: {C['base']};")

                def _load_and_show_args(sd, ac):
                    import ast as _ast_inner
                    params_list = []
                    if sd.get("code_name"):
                        try:
                            import urllib.request, json as _j2
                            from urllib.parse import quote as _q2
                            from pathlib import Path as _P2
                            cfg2 = _j2.loads((_P2(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))
                            _port2 = cfg2.get("server", {}).get("port", 3000)
                            _key2 = cfg2.get("server", {}).get("api_key", "")
                            req2 = urllib.request.Request(
                                f"http://127.0.0.1:{_port2}/code/{_q2(sd['code_name'], safe='')}",
                                headers={"Authorization": f"Bearer {_key2}"}
                            )
                            with urllib.request.urlopen(req2, timeout=5) as resp2:
                                cd2 = _j2.loads(resp2.read().decode())
                            raw_p = cd2.get("params", "[]")
                            if isinstance(raw_p, str):
                                raw_p = _j2.loads(raw_p)
                            if isinstance(raw_p, list):
                                params_list = raw_p
                        except Exception:
                            pass
                    elif sd.get("code"):
                        try:
                            tree2 = _ast_inner.parse(sd["code"])
                            for nd in _ast_inner.walk(tree2):
                                if isinstance(nd, _ast_inner.FunctionDef):
                                    defs = nd.args.defaults
                                    n_args = len(nd.args.args)
                                    n_defs = len(defs)
                                    for ai, a in enumerate(nd.args.args):
                                        di = ai - (n_args - n_defs)
                                        dv = ""
                                        if di >= 0:
                                            try: dv = str(_ast_inner.literal_eval(defs[di]))
                                            except: pass
                                        params_list.append({"name": a.arg, "type": "str", "default": dv})
                                    break
                        except Exception:
                            pass

                    if not params_list:
                        ac.setVisible(False)
                        return

                    al = ac.layout()
                    while al.count():
                        item = al.takeAt(0)
                        if item.widget():
                            item.widget().deleteLater()

                    for p in params_list:
                        pname = p.get("name", "")
                        ptype = p.get("type", "str")
                        pdefault = p.get("default", "")
                        arg_row = QHBoxLayout()
                        arg_row.setContentsMargins(0, 0, 0, 0)
                        arg_row.setSpacing(4)
                        plabel = QLabel(f"  ├ {pname}")
                        plabel.setFixedWidth(120)
                        plabel.setStyleSheet(f"font-size: 11px; color: {C['subtext0']}; font-family: 'Consolas', monospace;")
                        arg_row.addWidget(plabel)
                        type_lbl = QLabel(f"({ptype})")
                        type_lbl.setFixedWidth(40)
                        type_lbl.setStyleSheet(f"font-size: 10px; color: {C['overlay0']};")
                        arg_row.addWidget(type_lbl)
                        val_inp = QLineEdit(sd.get("args", {}).get(pname, ""))
                        val_inp.setFixedHeight(22)
                        ph_text = pdefault if pdefault else t("chat.param_auto_detect")
                        val_inp.setPlaceholderText(ph_text)
                        val_inp.setStyleSheet(f"""
                            QLineEdit {{
                                background: {C['surface0']}; color: {C['text']};
                                border: 1px solid {C['surface1']}; border-radius: 3px;
                                padding: 1px 4px; font-size: 11px;
                            }}
                        """)
                        def _on_val_changed(text, _pn=pname, _sd=sd):
                            if text.strip():
                                _sd.setdefault("args", {})[_pn] = text.strip()
                            else:
                                _sd.get("args", {}).pop(_pn, None)
                        val_inp.textChanged.connect(_on_val_changed)
                        arg_row.addWidget(val_inp)
                        arg_w = QWidget()
                        arg_w.setFixedHeight(26)
                        arg_w.setLayout(arg_row)
                        arg_w.setStyleSheet(f"background: {C['base']};")
                        al.addWidget(arg_w)
                    ac.setVisible(True)

                _load_and_show_args(step_data, args_container)
                container.setFixedHeight(30)
            else:
                container.setFixedHeight(30)

            entry = (container, inp, label, step_data, edit_code_btn)

            if insert_at is not None and insert_at < len(step_inputs):
                steps_layout.insertWidget(insert_at, container)
                if args_container:
                    steps_layout.insertWidget(insert_at + 1, args_container)
                step_inputs.insert(insert_at, entry)
            else:
                steps_layout.addWidget(container)
                if args_container:
                    steps_layout.addWidget(args_container)
                step_inputs.append(entry)
            _renumber_steps()



            def _make_edit_handler(sd, ip, ac_ref):
                def edit_code():
                    initial_code = sd.get("code", "")
                    if sd.get("type") == "code" and sd.get("code_name") and not initial_code:
                        try:
                            import urllib.request, json as _j
                            from urllib.parse import quote as _q
                            from pathlib import Path
                            cfg_path = Path(__file__).resolve().parent.parent / "config.json"
                            cfg = _j.loads(cfg_path.read_text(encoding="utf-8"))
                            _port = cfg.get("server", {}).get("port", 3000)
                            _key = cfg.get("server", {}).get("api_key", "")
                            req = urllib.request.Request(
                                f"http://127.0.0.1:{_port}/code/{_q(sd['code_name'], safe='')}",
                                headers={"Authorization": f"Bearer {_key}"}
                            )
                            with urllib.request.urlopen(req, timeout=5) as resp:
                                code_data = _j.loads(resp.read().decode())
                            initial_code = code_data.get("code", "")
                        except Exception as e:
                            self.add_bot_message(t("chat.code_load_fail", error=str(e)))
                    code = self._show_code_editor(initial_code, parent=dialog)
                    if code is not None:
                        sd["code"] = code
                        ip.setText(f"🐍 {code.split(chr(10))[0]}")
                        if ac_ref:
                            _load_and_show_args(sd, ac_ref)
                return edit_code

            edit_code_btn.clicked.connect(_make_edit_handler(step_data, inp, args_container))

            def remove():
                steps_layout.removeWidget(container)
                container.deleteLater()
                step_inputs[:] = [(c, i, l, d, e) for c, i, l, d, e in step_inputs if c != container]
                _renumber_steps()

            del_btn.clicked.connect(remove)

            def move_up():
                ci = next((j for j, (c, *_) in enumerate(step_inputs) if c == container), -1)
                if ci <= 0:
                    return
                step_inputs[ci], step_inputs[ci - 1] = step_inputs[ci - 1], step_inputs[ci]
                steps_layout.removeWidget(container)
                steps_layout.insertWidget(ci - 1, container)
                _renumber_steps()

            def move_down():
                ci = next((j for j, (c, *_) in enumerate(step_inputs) if c == container), -1)
                if ci < 0 or ci >= len(step_inputs) - 1:
                    return
                step_inputs[ci], step_inputs[ci + 1] = step_inputs[ci + 1], step_inputs[ci]
                steps_layout.removeWidget(container)
                steps_layout.insertWidget(ci + 1, container)
                _renumber_steps()

            up_btn.clicked.connect(move_up)
            down_btn.clicked.connect(move_down)

        # 기존 스텝 로드 — 새 포맷(dict)과 구 포맷(str) 모두 처리
        for i, p in enumerate(prompts, 1):
            if isinstance(p, dict):
                stype = p.get("type", "prompt")
                content = p.get("content", "")
                code_name = p.get("code_name", "")
                sa = p.get("args", {})
                # code_name 타입 → code 타입으로 정규화 (녹색 표시)
                if stype == "code_name":
                    add_step_row("", i, step_type="code", code_content="", code_name=code_name or content, step_args=sa)
                elif stype == "code":
                    add_step_row("", i, step_type="code", code_content=content, code_name=code_name, step_args=sa)
                else:
                    add_step_row(content, i)
            else:
                add_step_row(str(p), i)

        scroll.setWidget(steps_widget)
        main_layout.addWidget(scroll)

        # + 프롬프트 / + 코드 / 📥 코드 가져오기 버튼
        add_row = QHBoxLayout()
        add_row.addStretch()

        add_prompt_btn = QPushButton(t("chat.wf_add_prompt"))
        add_prompt_btn.setFixedWidth(110)
        add_prompt_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['green']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 4px 8px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; border-color: {C['green']}; }}
        """)
        add_prompt_btn.clicked.connect(lambda: add_step_row())
        add_row.addWidget(add_prompt_btn)

        add_code_btn = QPushButton(t("chat.wf_add_code"))
        add_code_btn.setFixedWidth(100)
        add_code_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['teal']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 4px 8px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; border-color: {C['teal']}; }}
        """)

        def add_code_step():
            code = self._show_code_editor(parent=dialog)
            if code is not None:
                add_step_row(text="", step_type="code", code_content=code)

        add_code_btn.clicked.connect(add_code_step)
        add_row.addWidget(add_code_btn)

        # 📥 Code Store에서 가져오기 버튼
        import_code_btn = QPushButton(t("chat.wf_import_code"))
        import_code_btn.setFixedWidth(130)
        import_code_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['surface0']}; color: {C['blue']};
                border: 1px solid {C['surface2']}; border-radius: 6px;
                padding: 4px 8px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {C['surface1']}; border-color: {C['blue']}; }}
        """)

        def import_code_from_store():
            selected = self._show_code_picker(parent=dialog)
            if selected:
                add_step_row(text="", step_type="code", code_content=selected["code"], code_name=selected["name"])

        import_code_btn.clicked.connect(import_code_from_store)
        add_row.addWidget(import_code_btn)

        main_layout.addLayout(add_row)

        # 스케줄
        sch_layout = QHBoxLayout()
        sch_label = QLabel(t("chat.wf_schedule_label"))
        sch_layout.addWidget(sch_label)
        sch_input = QLineEdit()
        sch_input.setText(wf.get("schedule", "") or "")
        sch_input.setPlaceholderText(t("chat.wf_schedule_placeholder"))
        sch_layout.addWidget(sch_input)
        main_layout.addLayout(sch_layout)

        # 저장/취소 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton(t("chat.wf_cancel"))
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['surface1']}; color: {C['text']};
                border: none; border-radius: 6px;
                padding: 10px 24px; font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {C['surface2']}; }}
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton(t("chat.wf_save"))
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['blue']}; color: white;
                border: none; border-radius: 6px;
                padding: 10px 24px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {C['sapphire']}; }}
        """)
        save_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(save_btn)
        main_layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            import json as _json

            # 스텝을 JSON 배열로 조립
            steps = []
            for _, inp, _, data, _ in step_inputs:
                if data["type"] == "code" and data.get("code_name") and not data.get("code", "").strip():
                    cn = data.get("code_name", "")
                    s = {"type": "code", "code_name": cn, "content": cn}
                    if data.get("args"):
                        s["args"] = data["args"]
                    steps.append(s)
                elif data["type"] == "code":
                    code_name = data.get("code_name", "")
                    code = data.get("code", "").strip()
                    if code_name:
                        s = {"type": "code", "code_name": code_name, "content": code}
                    elif code:
                        s = {"type": "code", "content": code}
                    else:
                        continue
                    if data.get("args"):
                        s["args"] = data["args"]
                    steps.append(s)
                else:
                    text = inp.text().strip()
                    if text:
                        steps.append({"type": "prompt", "content": text})

            if not steps:
                self.add_bot_message(t("chat.wf_step_required"))
                return

            new_schedule = sch_input.text().strip()
            steps_json = _json.dumps(steps, ensure_ascii=False)

            # API 직접 호출 (LLM 거치지 않음)
            import urllib.request
            try:
                from pathlib import Path
                cfg_path = Path(__file__).resolve().parent.parent / "config.json"
                cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
                api_key = cfg.get("server", {}).get("api_key", "")
                port = cfg.get("server", {}).get("port", 3000)

                wf_data = {
                    "name": wf_name,
                    "prompts": steps_json,
                    "description": wf.get("description", ""),
                    "schedule": new_schedule,
                }
                body = _json.dumps(wf_data).encode()
                req = urllib.request.Request(
                    f"http://localhost:{port}/workflow/save",
                    data=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = _json.loads(resp.read().decode())

                self.add_bot_message(result.get("message", t("chat.save_ok_fallback")))

                # 워크플로우 목록 자동 갱신
                wfdata = result.get("wfdata", "")
                if wfdata:
                    self.render_workflow_list(wfdata)

            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, t("desktop.save_fail"), t("desktop.save_error", error=str(e)))

    # ── 표시 ──

    def show_window(self):
        self._update_auth_label()
        if not self.isVisible():
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.geometry()
                self.move((geo.width() - self.width()) // 2, (geo.height() - self.height()) // 2)
        self.show()
        self.raise_()
        self.activateWindow()

        # Windows 포커스 강제 — 최소화→복원 트릭 (가장 확실)
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            SW_MINIMIZE = 6
            SW_RESTORE = 9
            user32.ShowWindow(hwnd, SW_MINIMIZE)
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

        # 약간의 딜레이 후 입력 포커스 (창 애니메이션 후)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, self._force_focus)

    def _force_focus(self):
        self._chat_input.setFocus(Qt.FocusReason.OtherFocusReason)
        self._chat_input.activateWindow()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    # ── Persona Mode UI ──

    def set_persona_mode(self, name: str, bg_image: str = ""):
        """페르소나 모드 활성화 — status bar에 인디케이터 표시 + 배경 변경"""
        self._persona_active_name = name
        self._persona_label.setText(f"🎭 {name}")
        self._persona_label.setVisible(True)
        self._persona_blink_timer.start()
        self._persona_blink_visible = True

        # WebEngineView 배경색 변경 (어두운 레드 톤)
        self._run_js("""
            document.body.style.transition = 'background-color 0.5s';
            document.body.style.backgroundColor = '#1a1520';
        """)

    def clear_persona_mode(self):
        """페르소나 모드 해제 — UI 복원"""
        self._persona_active_name = ""
        self._persona_label.setVisible(False)
        self._persona_label.setText("")
        self._persona_blink_timer.stop()

        # 배경색 복원
        self._run_js(f"""
            document.body.style.transition = 'background-color 0.5s';
            document.body.style.backgroundColor = '{C['base']}';
        """)

    def _blink_persona(self):
        """페르소나 레이블 깜빡임 (status bar)"""
        self._persona_blink_visible = not self._persona_blink_visible
        if self._persona_blink_visible:
            self._persona_label.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C['red']};
                    border: none; font-size: 11px; font-weight: bold;
                    font-family: 'Segoe UI'; padding: 0 8px; border-radius: 6px;
                }}
                QPushButton:hover {{ background-color: {C['surface0']}; }}
            """)
        else:
            self._persona_label.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: transparent;
                    border: none; font-size: 11px; font-weight: bold;
                    font-family: 'Segoe UI'; padding: 0 8px; border-radius: 6px;
                }}
            """)

    def _on_persona_clicked(self):
        """페르소나 레이블 클릭 → 종료 확인 다이얼로그"""
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("🎭 Persona Mode")
        msg.setText(t("chat.persona_exit_confirm"))
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self.sig_message_sent.emit("/done")
