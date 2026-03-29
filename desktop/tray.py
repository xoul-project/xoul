"""
Xoul Desktop Client — 시스템 트레이

알림은 notification.py의 커스텀 팝업으로 처리.
트레이는 아이콘 + 메뉴 + 더블클릭만 담당.
"""

from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QAction
from PyQt6.QtCore import pyqtSignal, QObject
from styles import COLORS as C
from i18n import t


import os

def _create_default_icon() -> QIcon:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(script_dir, "xoul.ico")
    
    # xoul.ico가 존재하는 경우 로드
    if os.path.exists(icon_path):
        return QIcon(icon_path)
    
    # 파일이 없는 경우를 위한 폴백(기존 코드에서 A 대신 X로 변경)
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(C['blue']))
    painter.setPen(QColor(C['blue']))
    painter.drawEllipse(2, 2, size - 4, size - 4)
    painter.setPen(QColor(C['base']))
    painter.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), 0x0084, "X")
    painter.end()
    return QIcon(pixmap)


class TrayManager(QObject):
    sig_show_chat = pyqtSignal()
    sig_settings = pyqtSignal()
    sig_reset_session = pyqtSignal()
    sig_quit = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.icon = _create_default_icon()
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip(t("tray.tooltip"))

        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {C['base']}; color: {C['text']};
                border: 1px solid {C['surface1']}; padding: 4px;
                font-family: 'Segoe UI'; font-size: 13px;
            }}
            QMenu::item {{ padding: 6px 24px; border-radius: 4px; }}
            QMenu::item:selected {{ background-color: {C['surface0']}; }}
            QMenu::separator {{ height: 1px; background-color: {C['surface1']}; margin: 4px 8px; }}
        """)

        for label, signal in [
            (t("tray.open_chat"), self.sig_show_chat),
            (None, None),
            (t("tray.settings"), self.sig_settings),
            (t("tray.reset_session"), self.sig_reset_session),
            (None, None),
            (t("tray.quit"), self.sig_quit),
        ]:
            if label is None:
                menu.addSeparator()
            else:
                act = QAction(label, menu)
                act.triggered.connect(signal.emit)
                menu.addAction(act)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

    def show(self):
        self.tray.show()

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.sig_show_chat.emit()
