"""
Xoul Desktop Client — 커스텀 팝업 알림

Windows 알림이 꺼져있어도 항상 표시되는 자체 알림 위젯.
화면 우하단에 3초간 표시 후 자동 페이드아웃.
"""

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QApplication, QGraphicsOpacityEffect
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPixmap

from styles import COLORS as C


class NotificationPopup(QWidget):
    """화면 우하단 커스텀 알림 팝업"""

    sig_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setFixedSize(360, 80)
        self.setStyleSheet(f"""
            QWidget#NotifBg {{
                background-color: {C['mantle']};
                border: 1px solid {C['surface1']};
                border-radius: 12px;
            }}
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # 배경 컨테이너
        bg = QWidget(self)
        bg.setObjectName("NotifBg")
        bg.setGeometry(0, 0, 360, 80)

        layout = QHBoxLayout(bg)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        # 아이콘 (xoul.ico)
        import os
        icon = QLabel()
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xoul.ico")
        pixmap = QPixmap(ico_path).scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        icon.setPixmap(pixmap)
        icon.setFixedSize(36, 36)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background: transparent;")
        layout.addWidget(icon)

        # 텍스트
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        self._msg_label = QLabel("")
        self._msg_label.setFont(QFont("Segoe UI", 11))
        self._msg_label.setStyleSheet(f"color: {C['subtext0']}; background: transparent;")
        self._msg_label.setWordWrap(True)
        text_layout.addWidget(self._msg_label)

        layout.addLayout(text_layout, 1)

        # 페이드 효과
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # 애니메이션
        self._fade_in = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_in.setDuration(200)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)

        self._fade_out = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_out.setDuration(400)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.finished.connect(self.hide)

        # 자동 숨김 타이머
        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.timeout.connect(self._start_fade_out)

    def show_notification(self, title: str, message: str, duration_ms: int = 4000):
        """알림 표시"""
        self._msg_label.setText(message[:120] + "..." if len(message) > 120 else message)

        # 화면 우하단 위치
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.right() - self.width() - 16
            y = geo.bottom() - self.height() - 16
            self.move(x, y)

        self._opacity.setOpacity(0.0)
        self.show()
        self.raise_()
        self._fade_in.start()
        self._auto_hide.start(duration_ms)

    def _start_fade_out(self):
        self._fade_out.start()

    def mousePressEvent(self, event):
        """클릭 시 시그널 + 숨김"""
        self._auto_hide.stop()
        self.hide()
        self.sig_clicked.emit()
