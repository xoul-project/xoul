"""
i18n — Xoul 다국어 지원 모듈

사용법:
    from i18n import t, set_language

    set_language("en")      # config.json의 assistant.language에서 자동 로드
    print(t("setup.title")) # 번역된 문자열 반환
    print(t("setup.greeting", name="Dextor"))  # 포맷 변수 지원
"""

import json
import os

_locale_data: dict = {}
_current_lang: str = "ko"
_locales_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")


def load_locale(lang: str) -> dict:
    """Load locale JSON file for the given language."""
    path = os.path.join(_locales_dir, f"{lang}.json")
    if not os.path.isfile(path):
        # Fallback to Korean
        path = os.path.join(_locales_dir, "ko.json")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def set_language(lang: str):
    """Set the global language and load corresponding locale data."""
    global _locale_data, _current_lang
    _current_lang = lang
    _locale_data = load_locale(lang)


def get_language() -> str:
    """Return the current language code."""
    return _current_lang


def t(key: str, **kwargs) -> str:
    """
    Get translated string by dot-notation key.

    Examples:
        t("setup.title")
        t("setup.greeting", name="Dextor")
        t("agent.tool_running", tool="web_search")
    """
    parts = key.split(".")
    value = _locale_data
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = None
            break

    if value is None:
        # Key not found — return the key itself as fallback
        return key

    if isinstance(value, str) and kwargs:
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError):
            return value

    return value if isinstance(value, str) else key


def init_from_config(config: dict = None):
    """
    Initialize language from config dict or config.json file.
    Call this once at startup.
    """
    if config:
        lang = config.get("assistant", {}).get("language", "ko")
    else:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
            lang = cfg.get("assistant", {}).get("language", "ko")
        else:
            lang = "ko"
    set_language(lang)
