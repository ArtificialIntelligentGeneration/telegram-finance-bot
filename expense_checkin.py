"""Scheduled Telegram check-in prompt for expense logging."""

from __future__ import annotations

import json
import os
import urllib.request


DEFAULT_TEXT = (
    "Evening expense check. Send what you spent today: text is fine, "
    "voice is fine. The importer will parse it into the cashflow register."
)


def send_checkin(token: str, chat_id: str, text: str = DEFAULT_TEXT) -> dict[str, object]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(
            {
                "chat_id": int(chat_id),
                "text": text,
                "disable_web_page_preview": True,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError("Telegram sendMessage failed")
    return payload


def main() -> int:
    token = os.environ["TELEGRAM_FINANCE_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_FINANCE_CHAT_ID"]
    send_checkin(token, chat_id)
    print(json.dumps({"ok": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
