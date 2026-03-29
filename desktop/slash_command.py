"""
Xoul Desktop Client — 슬래시 커맨드 시스템

/ 입력 시 자동완성 팝업 + 커맨드 인터셉트
"""

from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QAbstractItemView
from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QFont
from styles import COLORS as C
from i18n import t


# ── 슬래시 커맨드 정의 ──
SLASH_COMMANDS = [
    {
        "key": "workflow",
        "aliases": ["/workflow", "/워크플로우"],
        "icon": "⚡",
        "label_key": "slash.workflow",
        "label_fallback": "워크플로우 목록",
        "action": "list_workflow",
    },
    {
        "key": "persona",
        "aliases": ["/personas", "/persona", "/페르소나", "/페르소나 리스트"],
        "icon": "🎭",
        "label_key": "slash.persona",
        "label_fallback": "페르소나 목록",
        "action": "list_persona",
    },
    {
        "key": "code",
        "aliases": ["/code", "/codes", "/코드", "/코드 리스트"],
        "icon": "🐍",
        "label_key": "slash.code",
        "label_fallback": "코드 목록",
        "action": "list_code",
    },
    {
        "key": "cancel",
        "aliases": ["/취소", "/done", "/cancel", "/cancle"],
        "icon": "⏹",
        "label_key": "slash.cancel",
        "label_fallback": "페르소나/워크플로우 종료",
        "action": "cancel",
    },
]


def match_slash_command(text: str):
    """입력 텍스트가 슬래시 커맨드와 일치하면 action 문자열 반환, 아니면 None."""
    text_lower = text.strip().lower()
    for cmd in SLASH_COMMANDS:
        for alias in cmd["aliases"]:
            if text_lower == alias.lower():
                return cmd["action"]
    return None


def _get_label(cmd: dict) -> str:
    """커맨드의 표시 라벨 (i18n → fallback)."""
    try:
        label = t(cmd["label_key"])
        if label != cmd["label_key"]:
            return label
    except Exception:
        pass
    return cmd["label_fallback"]


class SlashCommandPopup(QListWidget):
    """슬래시 커맨드 자동완성 팝업"""

    sig_command_selected = pyqtSignal(str)  # action 문자열 전달

    def __init__(self, target_input, parent=None):
        super().__init__(parent or target_input.parentWidget())
        self._target = target_input
        # ToolTip 타입: 포커스를 뺏지 않으면서 다른 창 위에 표시
        self.setWindowFlags(
            Qt.WindowType.ToolTip |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(f"""
            QListWidget {{
                background-color: {C['surface0']};
                color: {C['text']};
                border: 2px solid {C['green']}88;
                border-radius: 8px;
                padding: 2px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 1px 10px;
                border-radius: 4px;
            }}
            QListWidget::item:hover {{
                background-color: {C['surface1']};
            }}
            QListWidget::item:selected {{
                background-color: {C['surface2']};
                color: {C['green']};
            }}
        """)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.hide()

        # 클릭으로 선택 (ToolTip은 클릭 이벤트가 다르므로 mouseRelease 사용)
        self.setMouseTracking(True)

        # 입력 변화 감지
        self._target.textChanged.connect(self._on_text_changed)
        self._target.installEventFilter(self)

        # 외부 클릭 감지 → 팝업 닫기
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)

    def _on_text_changed(self, text: str):
        """입력 변경 → 팝업 필터링"""
        text = text.strip()
        if not text.startswith("/"):
            self.hide()
            return
        self._update_filter(text)

    def _update_filter(self, text: str):
        """입력에 따라 항목 필터링 후 표시"""
        self.clear()
        query = text.lower()

        matched = []
        for cmd in SLASH_COMMANDS:
            # / 만 입력 시 전체 표시
            if query == "/":
                matched.append(cmd)
                continue
            # alias 또는 label에 query 포함
            for alias in cmd["aliases"]:
                if alias.lower().startswith(query) or query.startswith(alias.lower()):
                    matched.append(cmd)
                    break
            else:
                # label fallback / label_key 매칭
                label = _get_label(cmd)
                if query.lstrip("/") and query.lstrip("/") in label.lower():
                    matched.append(cmd)

        if not matched:
            self.hide()
            return

        for cmd in matched:
            label = _get_label(cmd)
            # alias 명령어를 표시 (사용자가 어떤 명령어를 써야 하는지 보여줌)
            aliases_str = "  ".join(cmd["aliases"][:2])  # 대표 1~2개
            display = f"{label}    {aliases_str}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, cmd["action"])
            self.addItem(item)

        # 첫 항목 선택
        self.setCurrentRow(0)

        # 팝업 위치 및 크기
        self._position_popup()
        self.show()

    def _position_popup(self):
        """팝업을 입력 필드 위에 위치"""
        if not self.count():
            return
        # 실제 렌더링된 아이템 높이 기반 계산
        item_h = self.sizeHintForRow(0) + 2  # spacing
        popup_h = min(self.count() * item_h + 10, 250)  # border + padding 여유
        popup_w = max(self._target.width(), 280)

        # 입력 필드의 글로벌 좌표 기준 위에 표시
        global_pos = self._target.mapToGlobal(self._target.rect().topLeft())
        x = global_pos.x()
        y = global_pos.y() - popup_h - 4

        self.setFixedSize(popup_w, popup_h)
        self.move(x, y)

    def mouseReleaseEvent(self, event):
        """팝업 항목 클릭 → 커맨드 실행"""
        item = self.itemAt(event.pos())
        if item:
            action = item.data(Qt.ItemDataRole.UserRole)
            if action:
                self.hide()
                self._target.clear()
                self.sig_command_selected.emit(action)
                return
        super().mouseReleaseEvent(event)

    def eventFilter(self, obj, event):
        """입력 필드의 키 이벤트 + 외부 클릭 감지"""
        # 외부 클릭 → 팝업 닫기
        if self.isVisible() and event.type() == QEvent.Type.MouseButtonPress:
            if obj is not self and obj is not self._target:
                self.hide()
                return False

        if obj is self._target and event.type() == QEvent.Type.KeyPress:
            if self.isVisible():
                key = event.key()
                if key == Qt.Key.Key_Down:
                    row = self.currentRow()
                    if row < self.count() - 1:
                        self.setCurrentRow(row + 1)
                    return True
                elif key == Qt.Key.Key_Up:
                    row = self.currentRow()
                    if row > 0:
                        self.setCurrentRow(row - 1)
                    return True
                elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    item = self.currentItem()
                    if item:
                        action = item.data(Qt.ItemDataRole.UserRole)
                        if action:
                            self.hide()
                            self._target.clear()
                            self.sig_command_selected.emit(action)
                            return True
                elif key == Qt.Key.Key_Escape:
                    self.hide()
                    return True
                elif key == Qt.Key.Key_Tab:
                    # Tab → 현재 선택 항목의 alias로 입력 채우기
                    item = self.currentItem()
                    if item:
                        for cmd in SLASH_COMMANDS:
                            if cmd["action"] == item.data(Qt.ItemDataRole.UserRole):
                                self._target.setText(cmd["aliases"][0])
                                break
                    return True
        # 입력 필드 포커스 잃음 → 팝업 닫기
        if obj is self._target and event.type() == QEvent.Type.FocusOut:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, self._check_focus)
        return super().eventFilter(obj, event)

    def _check_focus(self):
        """포커스가 팝업도 아니고 입력도 아니면 닫기"""
        from PyQt6.QtWidgets import QApplication
        focused = QApplication.focusWidget()
        if focused is not self and focused is not self._target:
            self.hide()

    def hideEvent(self, event):
        """팝업 숨김 시 정리"""
        super().hideEvent(event)
