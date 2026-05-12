# Telegram Finance Bot Showcase

This is a sanitized public excerpt from a personal cashflow automation. The private version imports messages from a Telegram bot, parses expenses from text/voice/media, stores rows in CSV + SQLite, and sends daily check-in prompts.

## Flow

```text
Telegram Bot API getUpdates
  -> allowed chat filter
  -> text/media extraction
  -> optional voice transcription
  -> amount/category parser
  -> CSV + SQLite cashflow register
  -> clarification prompt for ambiguous rows
```

## Included Files

- [`expense_importer.py`](./expense_importer.py) - parsing, dedupe, CSV/SQLite persistence, and Telegram update processing.
- [`expense_checkin.py`](./expense_checkin.py) - short scheduled Bot API prompt asking the operator to log daily expenses.

## Sanitization

Removed from the public version:

- real `.env` path and bot token names;
- real chat IDs;
- personal expense CSV/database contents;
- downloaded media attachments;
- debt registry, reports, and private finance notes.
