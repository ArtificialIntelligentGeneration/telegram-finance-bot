"""Sanitized Telegram finance importer.

The private version reads a local env file and writes to personal CSV/SQLite
registers. This public version keeps the parser and persistence shape while
using configurable paths and generic env names.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DATA_DIR = Path("./runtime/finance")
EXPENSES_CSV = DATA_DIR / "expenses.csv"
EXPENSES_DB = DATA_DIR / "expenses.sqlite3"
STATE_PATH = DATA_DIR / "telegram_import_state.json"

CSV_FIELDS = [
    "expense_id",
    "expense_date",
    "logged_at",
    "source",
    "telegram_update_id",
    "telegram_message_id",
    "amount",
    "currency",
    "category",
    "merchant_or_item",
    "description",
    "confidence",
    "status",
    "raw_text",
]

AMOUNT_RE = re.compile(
    r"(?<![\w.])(?P<amount>\d{2,7}(?:[ .]\d{3})*(?:[,.]\d{1,2})?|\d{2,7})"
    r"\s*(?P<currency>rub|rur|руб(?:\.|лей|ля|ль)?|₽|usd|usdt|eur|€|\$)?",
    re.IGNORECASE,
)

CATEGORY_RULES = [
    ("food", ("еда", "продукт", "кофе", "ресторан", "кафе", "доставка", "обед", "ужин")),
    ("transport", ("такси", "метро", "транспорт", "автобус", "самокат", "бензин")),
    ("housing", ("жиль", "аренд", "комнат", "квартир", "коммунал")),
    ("health", ("аптек", "лекар", "врач", "здоров", "анализ")),
    ("digital", ("подписк", "софт", "хостинг", "сервер", "связь", "интернет", "телефон")),
    ("debt_payment", ("долг", "вернул", "отдал", "погасил")),
]

NON_EXPENSE_CONTEXT = ("получил", "получила", "внес", "внесла", "продал", "остаток", "обменял")


def api_request(token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API error {exc.code}: {detail}") from exc
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API returned not ok for {method}: {body}")
    return body


def ensure_outputs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not EXPENSES_CSV.exists():
        with EXPENSES_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.DictWriter(handle, fieldnames=CSV_FIELDS).writeheader()

    with sqlite3.connect(EXPENSES_DB) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                expense_id TEXT PRIMARY KEY,
                expense_date TEXT NOT NULL,
                logged_at TEXT NOT NULL,
                source TEXT NOT NULL,
                telegram_update_id INTEGER,
                telegram_message_id INTEGER,
                amount REAL,
                currency TEXT NOT NULL,
                category TEXT NOT NULL,
                merchant_or_item TEXT,
                description TEXT,
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                raw_text TEXT
            )
            """
        )


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"last_update_id": 0}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_amount(raw: str) -> str:
    clean = raw.replace(" ", "").replace(".", "")
    if "," in clean:
        clean = clean.replace(",", ".")
    return f"{float(clean):.2f}"


def normalize_currency(raw: str | None) -> str:
    if not raw:
        return "RUB"
    value = raw.lower().replace(".", "")
    if value in {"₽", "rub", "rur"} or value.startswith("руб"):
        return "RUB"
    if value in {"$", "usd"}:
        return "USD"
    if value == "usdt":
        return "USDT"
    if value in {"€", "eur"}:
        return "EUR"
    return value.upper()


def categorize(text: str) -> str:
    lower = text.lower()
    for category, needles in CATEGORY_RULES:
        if any(needle in lower for needle in needles):
            return category
    return "uncategorized"


def should_skip_amount_match(text: str, match: re.Match[str]) -> bool:
    before = text[match.start() - 1 : match.start()]
    after = text[match.end() : match.end() + 1]
    if before == ":" or after == ":":
        return True
    local_context = text[max(0, match.start() - 35) : min(len(text), match.end() + 35)].lower()
    return any(marker in local_context for marker in NON_EXPENSE_CONTEXT)


def compact_context(text: str, start: int, end: int) -> str:
    return " ".join(text[max(0, start - 45) : min(len(text), end + 45)].split())


def expense_date_from_text(text: str, logged_at: dt.datetime) -> str:
    lower = text.lower()
    if "вчера" in lower:
        return (logged_at.date() - dt.timedelta(days=1)).isoformat()
    if "позавчера" in lower:
        return (logged_at.date() - dt.timedelta(days=2)).isoformat()
    return logged_at.date().isoformat()


def stable_id(update_id: int, message_id: int, index: int, amount: str, text: str) -> str:
    digest = hashlib.sha1(f"{update_id}:{message_id}:{index}:{amount}:{text}".encode("utf-8")).hexdigest()[:10]
    return f"EXP-{digest}"


def rows_from_update(update: dict[str, Any], bot_username: str, allowed_chat_id: int | None) -> list[dict[str, str]]:
    message = update.get("message") or update.get("edited_message") or {}
    if not message:
        return []

    chat = message.get("chat") or {}
    if allowed_chat_id is not None and int(chat.get("id", 0)) != allowed_chat_id:
        return []

    update_id = int(update["update_id"])
    message_id = int(message["message_id"])
    logged_at_dt = dt.datetime.fromtimestamp(message["date"], tz=dt.timezone.utc).astimezone()
    logged_at = logged_at_dt.isoformat(timespec="seconds")
    text = message.get("text") or message.get("caption") or ""
    if text.startswith("/"):
        return []

    expense_date = expense_date_from_text(text, logged_at_dt)
    category = categorize(text)
    matches = [match for match in AMOUNT_RE.finditer(text) if not should_skip_amount_match(text, match)]
    rows: list[dict[str, str]] = []

    for index, match in enumerate(matches, start=1):
        amount = normalize_amount(match.group("amount"))
        amount_float = float(amount)
        if 2020 <= amount_float <= 2035 and not match.group("currency"):
            continue
        rows.append(
            {
                "expense_id": stable_id(update_id, message_id, index, amount, text),
                "expense_date": expense_date,
                "logged_at": logged_at,
                "source": f"telegram:{bot_username}",
                "telegram_update_id": str(update_id),
                "telegram_message_id": str(message_id),
                "amount": amount,
                "currency": normalize_currency(match.group("currency")),
                "category": category,
                "merchant_or_item": compact_context(text, match.start(), match.end()),
                "description": text,
                "confidence": "0.75" if match.group("currency") else "0.55",
                "status": "parsed",
                "raw_text": text,
            }
        )

    if not rows and text:
        rows.append(
            {
                "expense_id": stable_id(update_id, message_id, 0, "0", text),
                "expense_date": expense_date,
                "logged_at": logged_at,
                "source": f"telegram:{bot_username}",
                "telegram_update_id": str(update_id),
                "telegram_message_id": str(message_id),
                "amount": "",
                "currency": "RUB",
                "category": category,
                "merchant_or_item": "",
                "description": text,
                "confidence": "0.00",
                "status": "needs_review",
                "raw_text": text,
            }
        )
    return rows


def append_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    ensure_outputs()
    with EXPENSES_CSV.open("r", encoding="utf-8", newline="") as handle:
        seen = {row["expense_id"] for row in csv.DictReader(handle) if row.get("expense_id")}
    new_rows = [row for row in rows if row["expense_id"] not in seen]
    if not new_rows:
        return []

    with EXPENSES_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.DictWriter(handle, fieldnames=CSV_FIELDS).writerows(new_rows)

    with sqlite3.connect(EXPENSES_DB) as db:
        for row in new_rows:
            db.execute(
                "INSERT OR IGNORE INTO expenses VALUES (:expense_id, :expense_date, :logged_at, :source, :telegram_update_id, :telegram_message_id, :amount, :currency, :category, :merchant_or_item, :description, :confidence, :status, :raw_text)",
                {**row, "amount": float(row["amount"]) if row["amount"] else None},
            )
    return new_rows


def import_updates(token: str, bot_username: str, allowed_chat_id: int | None = None) -> list[dict[str, str]]:
    state = load_state()
    payload = {"offset": int(state.get("last_update_id", 0)) + 1, "timeout": 0}
    updates = api_request(token, "getUpdates", payload)["result"]

    rows: list[dict[str, str]] = []
    for update in updates:
        rows.extend(rows_from_update(update, bot_username=bot_username, allowed_chat_id=allowed_chat_id))
        state["last_update_id"] = max(int(state.get("last_update_id", 0)), int(update["update_id"]))

    saved = append_rows(rows)
    save_state(state)
    return saved
