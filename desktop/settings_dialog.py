"""
Xoul Desktop Client — 설정 다이얼로그

config.json을 GUI로 편집. 5개 탭:
  🤖 LLM  |  👤 프로필  |  📧 이메일  |  💬 메신저  |  🔧 기타
"""

import json
import os
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QLineEdit, QComboBox, QCheckBox,
    QPushButton, QGroupBox, QMessageBox, QSpinBox, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from styles import COLORS as C
from i18n import t

# ─────────────────────────────────────────────
# config.json 경로
# ─────────────────────────────────────────────
_PROJECT_DIR = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_DIR / "config.json"


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def _save_config(cfg: dict):
    _CONFIG_PATH.write_text(
        json.dumps(cfg, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────
# 공통 스타일
# ─────────────────────────────────────────────
_DIALOG_QSS = f"""
QDialog {{
    background-color: {C['base']};
    color: {C['text']};
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}}
QTabWidget::pane {{
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    background-color: {C['base']};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {C['surface0']};
    color: {C['subtext1']};
    padding: 8px 18px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-size: 13px;
}}
QTabBar::tab:selected {{
    background-color: {C['base']};
    color: {C['text']};
    font-weight: bold;
    border: 1px solid {C['surface1']};
    border-bottom: none;
}}
QTabBar::tab:hover:!selected {{
    background-color: {C['surface1']};
}}
QGroupBox {{
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-weight: bold;
    color: {C['subtext1']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}}
QLineEdit, QSpinBox {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    min-height: 20px;
}}
QLineEdit:focus, QSpinBox:focus {{
    border: 1px solid {C['blue']};
}}
QComboBox {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    min-width: 180px;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    selection-background-color: {C['blue']};
}}
QCheckBox {{
    color: {C['text']};
    spacing: 8px;
    font-size: 13px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid {C['surface2']};
    background-color: {C['surface0']};
}}
QCheckBox::indicator:checked {{
    background-color: {C['blue']};
    border-color: {C['blue']};
}}
QLabel {{
    color: {C['text']};
}}
QPushButton#SaveBtn {{
    background-color: {C['blue']};
    color: {C['crust']};
    border: none;
    border-radius: 8px;
    padding: 10px 32px;
    font-size: 14px;
    font-weight: bold;
}}
QPushButton#SaveBtn:hover {{
    background-color: {C['sapphire']};
}}
QPushButton#CancelBtn {{
    background-color: {C['surface0']};
    color: {C['subtext1']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 14px;
}}
QPushButton#CancelBtn:hover {{
    background-color: {C['surface1']};
    color: {C['text']};
}}
"""

# ─────────────────────────────────────────────
# 모델 定義 (models.json — single source of truth)
# ─────────────────────────────────────────────
_MODELS_PATH = _PROJECT_DIR / "models.json"


def _load_models():
    data = json.loads(_MODELS_PATH.read_text(encoding="utf-8"))

    # Commercial models  →  OrderedDict {label: info_or_None}
    commercial = {}
    for entry in data.get("commercial", []):
        if "group" in entry:
            commercial[f"── {entry['group']} ──"] = None
        else:
            commercial[entry["label"]] = {
                "provider": entry["provider"],
                "base_url": entry["base_url"],
                "model": entry["model"],
            }

    # Local models  →  list[{label, tag}]
    local = []
    for entry in data.get("local", []):
        local.append({
            "label": f"{entry['name']:<28} (~{entry['vram']}GB VRAM)",
            "tag": entry["tag"],
        })

    return commercial, local


COMMERCIAL_MODELS, LOCAL_MODELS = _load_models()



# ─────────────────────────────────────────────
# 설정 다이얼로그
# ─────────────────────────────────────────────
class SettingsDialog(QDialog):
    """config.json 편집 다이얼로그"""

    sig_saved = pyqtSignal()  # 저장 완료 시그널

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.title"))
        self.setFixedSize(580, 640)
        self.setStyleSheet(_DIALOG_QSS)

        self.cfg = _load_config()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 탭
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_llm_tab(), "🤖 LLM")
        self.tabs.addTab(self._build_profile_tab(), t("settings.tab_profile"))
        self.tabs.addTab(self._build_email_tab(), t("settings.tab_email"))
        self.tabs.addTab(self._build_messenger_tab(), t("settings.tab_messenger"))
        self.tabs.addTab(self._build_general_tab(), t("settings.tab_etc"))
        layout.addWidget(self.tabs)

        # 저장/취소 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton(t("settings.cancel"))
        cancel_btn.setObjectName("CancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        save_btn = QPushButton(t("settings.save"))
        save_btn.setObjectName("SaveBtn")
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

    # ═══════════════════════════════════════════
    # Tab 1: LLM
    # ═══════════════════════════════════════════
    def _build_llm_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        # Provider 선택
        provider_group = QGroupBox("Provider")
        pg_layout = QVBoxLayout(provider_group)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(t("settings.mode")))
        self.llm_mode = QComboBox()
        self.llm_mode.addItems(["Local (Ollama)", "Commercial API"])
        mode_row.addWidget(self.llm_mode)
        mode_row.addStretch()
        pg_layout.addLayout(mode_row)

        # Local model
        self.local_widget = QWidget()
        local_layout = QFormLayout(self.local_widget)
        local_layout.setContentsMargins(0, 8, 0, 0)
        self.local_model = QComboBox()
        for m in LOCAL_MODELS:
            self.local_model.addItem(m["label"], m["tag"])
        local_layout.addRow(t("settings.model"), self.local_model)
        pg_layout.addWidget(self.local_widget)

        # Commercial model
        self.commercial_widget = QWidget()
        cm_layout = QFormLayout(self.commercial_widget)
        cm_layout.setContentsMargins(0, 8, 0, 0)
        self.cm_model = QComboBox()
        for label, info in COMMERCIAL_MODELS.items():
            if info is None:
                self.cm_model.addItem(label)
                idx = self.cm_model.count() - 1
                self.cm_model.model().item(idx).setEnabled(False)
            else:
                self.cm_model.addItem(label)
        cm_layout.addRow(t("settings.model"), self.cm_model)
        self.cm_api_key = QLineEdit()
        self.cm_api_key.setPlaceholderText(t("settings.api_key_placeholder"))
        self.cm_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        cm_layout.addRow("API Key:", self.cm_api_key)
        pg_layout.addWidget(self.commercial_widget)

        layout.addWidget(provider_group)
        layout.addStretch()

        # 모드 전환 연결
        self.llm_mode.currentIndexChanged.connect(self._on_llm_mode_changed)

        # 현재 값 로드
        self._load_llm_values()

        return w

    def _on_llm_mode_changed(self, idx):
        self.local_widget.setVisible(idx == 0)
        self.commercial_widget.setVisible(idx == 1)

    def _load_llm_values(self):
        llm = self.cfg.get("llm", {})
        engine = llm.get("engine", "ollama")
        is_commercial = (engine == "commercial")
        self.llm_mode.setCurrentIndex(1 if is_commercial else 0)
        self._on_llm_mode_changed(1 if is_commercial else 0)

        # Local
        local_tag = llm.get("ollama_model", "")
        for i in range(self.local_model.count()):
            if self.local_model.itemData(i) == local_tag:
                self.local_model.setCurrentIndex(i)
                break

        # Commercial
        provider = llm.get("provider", "")
        providers = llm.get("providers", {})
        if is_commercial and provider in providers:
            p = providers[provider]
            model_name = p.get("model_name", "")
            api_key = p.get("api_key", "")
            self.cm_api_key.setText(api_key)
            # 모델 찾기
            for i, (label, info) in enumerate(COMMERCIAL_MODELS.items()):
                if info and info["model"] == model_name:
                    # combobox에서 해당 인덱스 찾기
                    for j in range(self.cm_model.count()):
                        if self.cm_model.itemText(j) == label:
                            self.cm_model.setCurrentIndex(j)
                            break
                    break

    def _save_llm(self):
        llm = self.cfg.setdefault("llm", {})
        providers = llm.setdefault("providers", {})

        if self.llm_mode.currentIndex() == 0:
            # Local
            tag = self.local_model.currentData()
            llm["engine"] = "ollama"
            llm["provider"] = "local"
            llm["ollama_model"] = tag
            local_p = providers.setdefault("local", {})
            local_p["base_url"] = "http://10.0.2.2:11434/v1"
            local_p["api_key"] = "none"
            local_p["model_name"] = tag
        else:
            # Commercial — temperature/top_p/top_k/max_tokens 제거
            for key in ["temperature", "top_p", "top_k", "max_tokens",
                        "model_path", "ollama_model"]:
                llm.pop(key, None)
            # llm_server 제거
            self.cfg.pop("llm_server", None)

            label = self.cm_model.currentText()
            info = COMMERCIAL_MODELS.get(label)
            if info:
                llm["engine"] = "commercial"
                llm["provider"] = info["provider"]
                p = providers.setdefault(info["provider"], {})
                p["base_url"] = info["base_url"]
                p["model_name"] = info["model"]
                api_key = self.cm_api_key.text().strip()
                if api_key:
                    p["api_key"] = api_key

    # ═══════════════════════════════════════════
    # Tab 2: 프로필
    # ═══════════════════════════════════════════
    def _build_profile_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        group = QGroupBox(t("settings.user_info"))
        form = QFormLayout(group)

        user = self.cfg.get("user", {})

        self.user_name = QLineEdit(user.get("name", ""))
        self.user_name.setPlaceholderText(t("settings.name"))
        form.addRow(t("settings.name_label"), self.user_name)

        self.user_location = QLineEdit(user.get("location", ""))
        self.user_location.setPlaceholderText(t("settings.location_placeholder"))
        form.addRow(t("settings.location_label"), self.user_location)

        self.user_timezone = QComboBox()
        _timezones = [
            "Asia/Seoul", "Asia/Tokyo", "Asia/Shanghai", "Asia/Hong_Kong",
            "Asia/Singapore", "Asia/Taipei", "Asia/Bangkok", "Asia/Kolkata",
            "Asia/Dubai", "Asia/Jakarta",
            "US/Eastern", "US/Central", "US/Mountain", "US/Pacific", "US/Hawaii",
            "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Moscow",
            "Australia/Sydney", "Australia/Melbourne", "Pacific/Auckland",
            "America/Sao_Paulo", "America/Mexico_City", "America/Toronto",
            "Africa/Cairo", "Africa/Johannesburg",
        ]
        self.user_timezone.addItems(_timezones)
        current_tz = user.get("timezone", "Asia/Seoul")
        idx = self.user_timezone.findText(current_tz)
        if idx >= 0:
            self.user_timezone.setCurrentIndex(idx)
        else:
            self.user_timezone.addItem(current_tz)
            self.user_timezone.setCurrentIndex(self.user_timezone.count() - 1)
        form.addRow(t("settings.timezone_label"), self.user_timezone)

        layout.addWidget(group)
        layout.addStretch()
        return w

    def _save_profile(self):
        user = self.cfg.setdefault("user", {})
        user["name"] = self.user_name.text().strip()
        user["location"] = self.user_location.text().strip()
        user["timezone"] = self.user_timezone.currentText() or "Asia/Seoul"

    # ═══════════════════════════════════════════
    # Tab 3: Google 연동
    # ═══════════════════════════════════════════
    def _build_email_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        email = self.cfg.get("email", {})
        google = self.cfg.get("google", {})

        # Google 계정
        g_group = QGroupBox(t("settings.google_account"))
        g_form = QFormLayout(g_group)

        self.google_enabled = QCheckBox(t("settings.google_enable"))
        self.google_enabled.setChecked(google.get("enabled", False))
        g_form.addRow(self.google_enabled)

        self.email_address = QLineEdit(email.get("address", ""))
        self.email_address.setPlaceholderText("example@gmail.com")
        g_form.addRow(t("settings.gmail_address"), self.email_address)

        self.email_app_pass = QLineEdit(email.get("app_password", ""))
        self.email_app_pass.setPlaceholderText(t("settings.google_app_password_hint"))
        self.email_app_pass.setEchoMode(QLineEdit.EchoMode.Password)
        g_form.addRow(t("settings.app_password_label"), self.email_app_pass)

        self.email_enabled = QCheckBox(t("settings.email_enable"))
        self.email_enabled.setChecked(email.get("enabled", False))
        g_form.addRow(self.email_enabled)

        layout.addWidget(g_group)
        layout.addStretch()
        return w

    def _save_email(self):
        # Google
        google = self.cfg.setdefault("google", {})
        google["enabled"] = self.google_enabled.isChecked()

        # Email
        email = self.cfg.setdefault("email", {})
        email["enabled"] = self.email_enabled.isChecked()
        email["address"] = self.email_address.text().strip()
        email["app_password"] = self.email_app_pass.text().strip()
        email["imap_host"] = "imap.gmail.com"
        email["smtp_host"] = "smtp.gmail.com"
        # user.email도 동기화
        self.cfg.setdefault("user", {})["email"] = email["address"]

    # ═══════════════════════════════════════════
    # Tab 4: 메신저
    # ═══════════════════════════════════════════
    def _build_messenger_tab(self) -> QWidget:
        # ScrollArea로 감싸서 콘텐츠가 많아도 스크롤 가능
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background-color: {C['base']}; }}")

        w = QWidget()
        w.setStyleSheet(f"background-color: {C['base']};")
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        clients = self.cfg.get("clients", {})

        # Telegram
        tg = clients.get("telegram", {})
        tg_group = QGroupBox("Telegram")
        tg_form = QFormLayout(tg_group)
        tg_form.setSpacing(6)
        self.tg_enabled = QCheckBox(t("settings.enable"))
        self.tg_enabled.setChecked(tg.get("enabled", False))
        tg_form.addRow(self.tg_enabled)
        self.tg_token = QLineEdit(tg.get("bot_token", ""))
        self.tg_token.setPlaceholderText("Bot Token")
        self.tg_token.setEchoMode(QLineEdit.EchoMode.Password)
        tg_form.addRow("Bot Token:", self.tg_token)
        self.tg_chat_id = QLineEdit(tg.get("chat_id", ""))
        self.tg_chat_id.setPlaceholderText(t("settings.chat_id"))
        tg_form.addRow("Chat ID:", self.tg_chat_id)
        layout.addWidget(tg_group)

        # Discord
        dc = clients.get("discord", {})
        dc_group = QGroupBox("Discord")
        dc_form = QFormLayout(dc_group)
        dc_form.setSpacing(6)
        self.dc_enabled = QCheckBox(t("settings.enable"))
        self.dc_enabled.setChecked(dc.get("enabled", False))
        dc_form.addRow(self.dc_enabled)
        self.dc_token = QLineEdit(dc.get("bot_token", ""))
        self.dc_token.setPlaceholderText("Bot Token")
        self.dc_token.setEchoMode(QLineEdit.EchoMode.Password)
        dc_form.addRow("Bot Token:", self.dc_token)
        self.dc_channel = QLineEdit(dc.get("channel_id", ""))
        self.dc_channel.setPlaceholderText("Channel ID")
        dc_form.addRow("Channel ID:", self.dc_channel)
        layout.addWidget(dc_group)

        # Slack
        sl = clients.get("slack", {})
        sl_group = QGroupBox("Slack")
        sl_form = QFormLayout(sl_group)
        sl_form.setSpacing(6)
        self.sl_enabled = QCheckBox(t("settings.enable"))
        self.sl_enabled.setChecked(sl.get("enabled", False))
        sl_form.addRow(self.sl_enabled)
        self.sl_bot_token = QLineEdit(sl.get("bot_token", ""))
        self.sl_bot_token.setPlaceholderText("Bot Token (xoxb-...)")
        self.sl_bot_token.setEchoMode(QLineEdit.EchoMode.Password)
        sl_form.addRow("Bot Token:", self.sl_bot_token)
        self.sl_app_token = QLineEdit(sl.get("app_token", ""))
        self.sl_app_token.setPlaceholderText("App Token (xapp-...)")
        self.sl_app_token.setEchoMode(QLineEdit.EchoMode.Password)
        sl_form.addRow("App Token:", self.sl_app_token)
        self.sl_channel = QLineEdit(sl.get("channel_id", ""))
        self.sl_channel.setPlaceholderText("Channel ID")
        sl_form.addRow("Channel ID:", self.sl_channel)
        layout.addWidget(sl_group)

        # Kakao
        kk = clients.get("kakao", {})
        kk_group = QGroupBox("Kakao")
        kk_form = QFormLayout(kk_group)
        self.kk_enabled = QCheckBox(t("settings.enable"))
        self.kk_enabled.setChecked(kk.get("enabled", False))
        kk_form.addRow(self.kk_enabled)
        layout.addWidget(kk_group)

        scroll.setWidget(w)
        return scroll

    def _save_messenger(self):
        clients = self.cfg.setdefault("clients", {})

        tg = clients.setdefault("telegram", {})
        tg["enabled"] = self.tg_enabled.isChecked()
        tg["bot_token"] = self.tg_token.text().strip()
        tg["chat_id"] = self.tg_chat_id.text().strip()

        dc = clients.setdefault("discord", {})
        dc["enabled"] = self.dc_enabled.isChecked()
        dc["bot_token"] = self.dc_token.text().strip()
        dc["channel_id"] = self.dc_channel.text().strip()

        sl = clients.setdefault("slack", {})
        sl["enabled"] = self.sl_enabled.isChecked()
        sl["bot_token"] = self.sl_bot_token.text().strip()
        sl["app_token"] = self.sl_app_token.text().strip()
        sl["channel_id"] = self.sl_channel.text().strip()

        kk = clients.setdefault("kakao", {})
        kk["enabled"] = self.kk_enabled.isChecked()

    # ═══════════════════════════════════════════
    # Tab 5: 기타
    # ═══════════════════════════════════════════
    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # 언어
        lang_group = QGroupBox(t("settings.language"))
        lang_form = QFormLayout(lang_group)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem(t("settings.lang_ko"), "ko")
        self.lang_combo.addItem("English", "en")
        current_lang = self.cfg.get("assistant", {}).get("language", "ko")
        self.lang_combo.setCurrentIndex(0 if current_lang == "ko" else 1)
        lang_form.addRow(t("settings.language_label"), self.lang_combo)
        layout.addWidget(lang_group)

        # GitHub
        gh_group = QGroupBox("GitHub")
        gh_form = QFormLayout(gh_group)
        gh = self.cfg.get("github", {})
        self.gh_token = QLineEdit(gh.get("token", ""))
        self.gh_token.setPlaceholderText("Personal Access Token")
        self.gh_token.setEchoMode(QLineEdit.EchoMode.Password)
        gh_form.addRow("Token:", self.gh_token)
        self.gh_username = QLineEdit(gh.get("username", ""))
        self.gh_username.setPlaceholderText("GitHub Username")
        gh_form.addRow("Username:", self.gh_username)
        layout.addWidget(gh_group)

        layout.addStretch()
        return w

    def _save_general(self):
        assistant = self.cfg.setdefault("assistant", {})
        assistant["language"] = self.lang_combo.currentData()

        gh = self.cfg.setdefault("github", {})
        gh["token"] = self.gh_token.text().strip()
        gh["username"] = self.gh_username.text().strip()

    # ═══════════════════════════════════════════
    # 저장
    # ═══════════════════════════════════════════
    def _save(self):
        try:
            self._save_llm()
            self._save_profile()
            self._save_email()
            self._save_messenger()
            self._save_general()
            _save_config(self.cfg)
            QMessageBox.information(self, t("settings.save_ok_title"),
                                   t("settings.save_ok_msg"))
            self.accept()
            # 팝업 닫힌 후 무거운 작업 (ollama stop, deploy 동기화) 시작
            self.sig_saved.emit()
        except Exception as e:
            QMessageBox.critical(self, t("settings.save_error_title"), t("settings.save_error_msg", e=str(e)))
