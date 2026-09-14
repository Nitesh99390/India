# 📚 NovelTranslator PRO — Telegram Document Translation Bot

A single-file, production-ready Telegram bot (Pyrogram) that translates whole
documents (EPUB / TXT / DOCX / PDF) into 16 languages and delivers the result
as TXT, DOCX or EPUB — split into parts of any size you like.

## ✨ Features

- **Input**: `.epub` `.txt` `.docx` `.pdf` (up to 100 MB)
- **Output**: TXT · DOCX · EPUB, auto-split into parts (presets or custom 50 KB – 15 MB)
- **16 languages** (Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Urdu, Spanish, French, Arabic …)
- **Live dashboard** — progress bar, speed, ETA, flood-safe message edits
- **Job queue** with position updates, per-user cancel, 4-step wizard + ⚡ Quick Start
- **Concurrent translation engine** (10 parallel chunks, exponential-backoff retries)
- **Security-code gate**, owner panel, `/adduser`, `/deluser`, `/broadcast`, `/setcode`
- **Backup group** — every job gets its own forum topic with all delivered parts
- Persistent JSON store (users, prefs, stats) with legacy-file migration

## 🚀 Quick start

```bash
git clone https://github.com/Nitesh99390/India.git
cd India
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then fill in API_ID, API_HASH, BOT_TOKEN, …
python bot.py
```

The first person to send the `SECURITY_CODE` to the bot becomes the **owner**.

### Docker

```bash
docker build -t noveltranslator .
docker run -d --env-file .env -v $(pwd)/data:/app/workspace_textbot noveltranslator
```

## ⚙️ Configuration

| Variable          | Description                                            |
|-------------------|--------------------------------------------------------|
| `API_ID`          | Telegram API ID from https://my.telegram.org           |
| `API_HASH`        | Telegram API hash                                      |
| `BOT_TOKEN`       | Token from @BotFather                                  |
| `SECURITY_CODE`   | Code users must send to unlock the bot                 |
| `OWNER_ID`        | Force a specific owner (0 = first unlocked user)       |
| `BACKUP_GROUP_ID` | Forum-enabled supergroup where the bot is admin        |

Runtime data (session, DB, logs, temp files) lives in `workspace_textbot/`.

## 💬 Commands

**Users**: `/start` `/settings` `/queue` `/cancel` `/mystats` `/help`
**Owner**: `/stats` `/users` `/adduser <id>` `/deluser <id>` `/broadcast <text>` `/setcode <code>` `/links`

## 🔒 Security note

Never commit real credentials. `.env`, sessions and the workspace folder are
git-ignored. Rotate your bot token if it has ever been shared publicly.
