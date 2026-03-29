"""
Xoul Desktop Client — 글로벌 핫키 관리
"""

import threading
import keyboard
from PyQt6.QtCore import QObject, pyqtSignal

# desktop/i18n.py 사용 (루트 i18n과 별개)
import i18n


class HotkeyManager(QObject):
    """글로벌 핫키를 관리합니다."""

    sig_toggle = pyqtSignal()  # Ctrl+Space 눌렸을 때

    def __init__(self, hotkey: str = "ctrl+space", parent=None):
        super().__init__(parent)
        self.hotkey = hotkey
        self._registered = False

    def register(self):
        """핫키 등록"""
        if self._registered:
            return
        try:
            keyboard.add_hotkey(self.hotkey, self._on_hotkey, suppress=True)
            self._registered = True
        except Exception as e:
            print(i18n.t("hotkey.register_fail", error=e))
            # suppress=True 실패 시 재시도
            try:
                keyboard.add_hotkey(self.hotkey, self._on_hotkey, suppress=False)
                self._registered = True
            except Exception as e2:
                print(i18n.t("hotkey.register_final_fail", error=e2))

    def unregister(self):
        """핫키 해제"""
        if self._registered:
            try:
                keyboard.remove_hotkey(self.hotkey)
            except Exception:
                pass
            self._registered = False

    def _on_hotkey(self):
        """핫키 콜백 — Qt 스레드로 시그널 emit"""
        self.sig_toggle.emit()
