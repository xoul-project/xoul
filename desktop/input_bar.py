"""
Xoul Desktop Client — Spotlight 스타일 입력바
Ctrl+Space로 화면 상단 중앙에 나타나는 프레임리스 입력 필드.
"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QLineEdit, QTextEdit, QGraphicsDropShadowEffect
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QRect, QEasingCurve
from PyQt6.QtGui import QColor, QFont

from styles import INPUT_BAR_QSS, COLORS as C
from i18n import t
from slash_command import match_slash_command, SlashCommandPopup


class SpotlightInput(QTextEdit):
    """Spotlight용 멀티라인 입력 위젯.
    - Enter → 전송 시그널
    - Shift+Enter → 줄바꿈 (부모 InputBar 리사이즈)
    """
    sig_submit = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._min_height = 36
        self._max_height = 120
        self.setFixedHeight(self._min_height)
        self.document().contentsChanged.connect(self._adjust_height)
        self._placeholder = ""
        self._bar = None  # InputBar 참조 (외부에서 설정)

    def setPlaceholderText(self, text: str):
        self._placeholder = text
        super().setPlaceholderText(text)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str):
        self.setPlainText(text)

    def clear(self):
        super().clear()
        self.setFixedHeight(self._min_height)
        # 부모 InputBar 크기 리셋
        if self._bar and hasattr(self._bar, '_reset_bar_size'):
            self._bar._reset_bar_size()

    def _adjust_height(self):
        doc_height = int(self.document().size().height()) + 12
        new_height = max(self._min_height, min(doc_height, self._max_height))
        if new_height != self.height():
            self.setFixedHeight(new_height)
            # 부모 InputBar 리사이즈
            if self._bar and hasattr(self._bar, '_resize_bar'):
                self._bar._resize_bar(new_height)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.sig_submit.emit()
            return
        super().keyPressEvent(event)


class InputBar(QWidget):
    """Spotlight 스타일 입력바"""

    sig_message_sent = pyqtSignal(str)  # 메시지 전송

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InputBar")
        self.setWindowTitle("Xoul")

        # 창 플래그: 프레임리스, 항상 위, 도구창 (태스크바에 안 뜸)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(560, 56)

        # ── 레이아웃 ──
        self._container = QWidget(self)
        self._container.setObjectName("InputBar")
        self._container.setFixedSize(560, 56)
        self._container.setStyleSheet(INPUT_BAR_QSS)

        layout = QHBoxLayout(self._container)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(10)

        # 아이콘
        icon_label = QLabel("🤖")
        icon_label.setObjectName("SpotlightIcon")
        icon_label.setFixedWidth(28)
        layout.addWidget(icon_label)

        # 입력 필드 (멀티라인)
        self.input_field = SpotlightInput()
        self.input_field.setObjectName("SpotlightInput")
        self.input_field.setPlaceholderText(t("input_bar.placeholder"))
        self.input_field.setFont(QFont("Segoe UI", 14))
        self.input_field.setStyleSheet(f"""
            QTextEdit {{
                background: transparent; border: none;
                color: {C['text']}; font-size: 14px; font-family: 'Segoe UI';
                padding: 0px;
            }}
        """)
        self.input_field.sig_submit.connect(self._on_submit)
        self.input_field._bar = self  # SpotlightInput → InputBar 역참조
        layout.addWidget(self.input_field)

        # ── 슬래시 커맨드 자동완성 팝업 ──
        self._slash_popup = SlashCommandPopup(self.input_field)
        self._slash_popup.sig_command_selected.connect(self._execute_slash_command)

        # 엔터 힌트
        enter_label = QLabel("⏎")
        enter_label.setStyleSheet(f"color: {C['overlay0']}; font-size: 16px;")
        enter_label.setFixedWidth(24)
        layout.addWidget(enter_label)

        # 그림자 효과
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 8)
        self._container.setGraphicsEffect(shadow)

        # 기본 바 높이
        self._base_height = 56

        # 애니메이션
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # 대기 상태 표시
        self._waiting = False
        self._wf_hint = ""

    def toggle(self):
        """표시/숨김 토글"""
        if self.isVisible():
            self.hide_bar()
        else:
            self.show_bar()

    def show_bar(self):
        """입력바 표시 (화면 상단 중앙)"""
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            x = (geo.width() - self.width()) // 2
            y = int(geo.height() * 0.28)
            self.move(x, y)

        self.input_field.clear()
        if self._wf_hint:
            self.input_field.setPlaceholderText(self._wf_hint)
        elif self._waiting:
            self.input_field.setPlaceholderText(t("input_bar.waiting"))
        else:
            self.input_field.setPlaceholderText(t("input_bar.placeholder"))
        self._reset_bar_size()  # 높이 리셋
        self.show()
        self.raise_()
        self.activateWindow()

        # Windows: 강제 포그라운드 + 딜레이 포커스
        self._force_focus()
        QTimer.singleShot(50, self._force_focus)
        QTimer.singleShot(150, self._force_focus)

    def _force_focus(self):
        """Windows API로 강제 포커스 획득"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            # Alt키 시뮬레이션 → Windows가 사용자 입력으로 인식
            VK_MENU = 0x12
            KEYEVENTF_EXTENDEDKEY = 0x0001
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
            user32.SetForegroundWindow(hwnd)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        except Exception:
            pass
        self.activateWindow()
        self.input_field.setFocus(Qt.FocusReason.OtherFocusReason)

    def hide_bar(self):
        """입력바 숨김"""
        self.hide()

    def set_waiting(self, waiting: bool):
        """대기 상태 설정"""
        self._waiting = waiting

    def set_workflow_hint(self, wf_name: str, status: str = "running"):
        """워크플로우 진행 중 힌트 설정"""
        self._wf_hint = f"⚡ '{wf_name}' {'⏸ 입력 대기 중' if status == 'paused' else '실행 중'}..."

    def clear_workflow_hint(self):
        """워크플로우 힌트 제거"""
        self._wf_hint = ""

    def _on_submit(self):
        """Enter 키 → 전송 + 숨김"""
        text = self.input_field.toPlainText().strip()
        if text:
            action = match_slash_command(text)
            if action:
                self._execute_slash_command(action)
            else:
                self.sig_message_sent.emit(text)
        self.hide_bar()

    def _resize_bar(self, input_height: int):
        """입력 필드 높이 변경에 따른 바 리사이즈"""
        new_height = max(self._base_height, input_height + 20)
        self.setFixedHeight(new_height)
        self._container.setFixedHeight(new_height)

    def _reset_bar_size(self):
        """바 크기를 기본으로 리셋"""
        self.setFixedHeight(self._base_height)
        self._container.setFixedHeight(self._base_height)

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
        self.hide_bar()

    def keyPressEvent(self, event):
        """Esc → 숨김"""
        if event.key() == Qt.Key.Key_Escape:
            self.hide_bar()
        else:
            super().keyPressEvent(event)
