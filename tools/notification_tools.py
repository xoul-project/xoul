"""
알림 도구 - 활성화된 모든 클라이언트(Telegram, Discord, Slack)로 메시지 전송
"""
from i18n import t


def tool_send_notification(message: str, title: str = "") -> str:
    """활성화된 모든 메신저로 알림 메시지를 전송합니다."""
    results = []

    # Telegram
    try:
        from telegram_client import send_notification as tg_send
        r = tg_send(message, title)
        if "✅" in r:
            results.append("Telegram ✅")
    except Exception:
        pass

    # Discord
    try:
        from discord_client import send_notification as dc_send
        r = dc_send(message, title)
        if "✅" in r:
            results.append("Discord ✅")
    except Exception:
        pass

    # Slack
    try:
        from slack_client import send_notification as sl_send
        r = sl_send(message, title)
        if "✅" in r:
            results.append("Slack ✅")
    except Exception:
        pass

    if results:
        return t("notification.sent", results=", ".join(results))
    return "❌ 활성화된 알림 채널이 없습니다. (config.json에서 clients 설정 확인)"
