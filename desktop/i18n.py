"""
Desktop 다국어 지원 모듈
config.json의 assistant.language 값에 따라 UI 텍스트를 로드합니다.
"""
import json
import os

_translations = {}
_lang = "ko"  # 기본값


def init(config_path: str = None):
    """config.json에서 언어 설정을 읽고 번역 파일을 로드합니다."""
    global _translations, _lang

    # config.json에서 언어 코드 읽기
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
            _lang = cfg.get("assistant", {}).get("language", "ko")
        except Exception:
            _lang = "ko"

    # 번역 파일 로드
    locale_dir = os.path.join(os.path.dirname(__file__), "locales")
    locale_file = os.path.join(locale_dir, f"{_lang}.json")

    if not os.path.exists(locale_file):
        # fallback to ko
        locale_file = os.path.join(locale_dir, "ko.json")

    try:
        with open(locale_file, "r", encoding="utf-8") as f:
            _translations = json.load(f)
    except Exception:
        _translations = {}


def t(key: str, **kwargs) -> str:
    """
    번역 키에 해당하는 텍스트를 반환합니다.
    키 형식: "section.key" (예: "tray.open_chat")
    kwargs로 포맷팅 가능: t("host.exec_ok", app="Chrome")

    키가 없으면 키 자체를 반환합니다.
    """
    if not _translations:
        # Auto-init: config.json을 찾아서 초기화
        for p in [
            os.path.join(os.path.dirname(__file__), "..", "config.json"),
            os.path.join(os.path.dirname(__file__), "config.json"),
        ]:
            if os.path.exists(p):
                init(p)
                break
        if not _translations:
            init()  # fallback: ko.json 로드
    parts = key.split(".", 1)
    if len(parts) == 2:
        section, subkey = parts
        text = _translations.get(section, {}).get(subkey, key)
    else:
        text = _translations.get(key, key)

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass

    return text


def get_lang() -> str:
    """현재 언어 코드를 반환합니다."""
    return _lang
