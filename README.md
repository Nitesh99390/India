# 📚 NovelTranslator PRO — Telegram Document Translation Bot

**v3.0 · Stateless edition, built for the Render.com free tier.**

A single-file Telegram bot (Pyrogram + aiohttp) that translates whole
documents (EPUB / TXT / DOCX / PDF) into 25 languages and delivers the result
as TXT, DOCX or EPUB — split into parts of any size you like.

> 🗄 **No database. No disk persistence.** Everything (users, settings, stats,
> the bot session itself) lives in RAM and resets on every deploy / restart.
> Permanent access is configured purely through environment variables.

## ✨ Features

- **Input**: `.epub` `.txt` `.docx` `.pdf` (default limit 50 MB, configurable)
- **Output**: TXT · DOCX · EPUB, auto-split into parts (presets or custom 50 KB – 15 MB)
  – split size is *output-aware* (Hindi/Bengali etc. take ~2.6× more bytes than English)
- **25 languages** (Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Urdu, Spanish, French, German, Arabic, Chinese, Japanese …)
- **Fast async translation engine** – direct HTTP to Google's web endpoint via aiohttp, per-line segments so paragraph structure survives 1:1, exponential-backoff retries + thread fallback
- **Live dashboard** – progress bar, speed, ETA, flood-safe message edits
- **Job queue** with position updates, per-user job limit, cancel button, 4-step wizard + ⚡ Quick Start
- **Security-code gate**, owner panel, `/adduser`, `/deluser`, `/broadcast`, `/setcode`, `/id`
- **Optional backup group** – each job gets its own forum topic with all delivered parts (set `BACKUP_GROUP_ID=0` to disable)
- **Render-ready** – binds `$PORT` with a `/health` endpoint, self keep-alive ping so the free instance doesn't sleep, graceful SIGTERM handling (users are told when a redeploy interrupts their job)

## 🚀 Deploy on Render (free)

1. **Fork / push** this repo to GitHub.
2. Render Dashboard → **New → Blueprint** → select the repo. `render.yaml` is picked up automatically
   (or: **New → Web Service**, runtime *Python*, build `pip install -r requirements.txt`, start `python bot.py`).
3. Fill the secret environment variables:

   | Variable        | Where to get it                                             |
   |-----------------|-------------------------------------------------------------|
   | `API_ID`        | https://my.telegram.org → API development tools             |
   | `API_HASH`      | same page                                                   |
   | `BOT_TOKEN`     | @BotFather → `/newbot`                                      |
   | `SECURITY_CODE` | any secret ≥ 6 chars users must send to unlock the bot      |
   | `OWNER_ID`      | your numeric Telegram ID (send `/id` to the bot, or @userinfobot) |

4. Deploy. Open the service URL → you should see “Telegram bot is online”.
5. Send `/start` to your bot. 🎉

> ⚠️ Free-tier notes
> - The instance is spun down after 15 min without HTTP traffic. The built-in
>   keep-alive pings `RENDER_EXTERNAL_URL/health` every 10 min to prevent that
>   (`KEEP_ALIVE=0` to disable). Free tier also has a monthly hour budget.
> - Every deploy/restart wipes memory: users authorised with the code or
>   `/adduser` must re-send the code. Put permanent IDs in `AUTHORIZED_USERS`.
> - Set `OWNER_ID` explicitly; otherwise the first person who sends the code
>   after each restart becomes the owner.

## ⚙️ Configuration

| Variable             | Default | Description                                                   |
|----------------------|---------|---------------------------------------------------------------|
| `API_ID` / `API_HASH` / `BOT_TOKEN` | — | **Required** Telegram credentials                    |
| `SECURITY_CODE`      | —       | Code users send once to unlock (required unless `PUBLIC_MODE=1` or IDs given) |
| `OWNER_ID`           | `0`     | Permanent owner. `0` → first unlocked user becomes owner       |
| `AUTHORIZED_USERS`   | —       | Comma-separated IDs pre-authorised on every start             |
| `PUBLIC_MODE`        | `0`     | `1` → anyone can use the bot, no code needed                  |
| `BACKUP_GROUP_ID`    | `0`     | Forum-enabled supergroup where the bot is admin. `0` = off    |
| `MAX_INPUT_MB`       | `50`    | Max upload size                                               |
| `MAX_JOBS_PER_USER`  | `2`     | Running + queued jobs allowed per user                        |
| `CONCURRENCY`        | `8`     | Parallel translation requests (1–20)                          |
| `CHUNK_SIZE`         | `3500`  | Characters per translation request                            |
| `DEFAULT_LANG` / `DEFAULT_FORMAT` / `DEFAULT_SPLIT_KB` | `hi` / `txt` / `500` | Defaults for new users |
| `KEEP_ALIVE`         | `1`     | Self-ping to stay awake on Render free tier                   |
| `KEEP_ALIVE_INTERVAL`| `600`   | Ping interval in seconds                                      |
| `PORT`               | `10000` | Set by Render automatically                                   |

## 💻 Run locally

```bash
git clone https://github.com/Nitesh99390/India.git && cd India
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in API_ID, API_HASH, BOT_TOKEN, SECURITY_CODE, OWNER_ID
python bot.py             # http://localhost:10000/health
```

### Docker

```bash
docker build -t noveltranslator .
docker run -d --env-file .env -p 10000:10000 noveltranslator
```

## 💬 Commands

**Users**: `/start` `/settings` `/queue` `/cancel` `/mystats` `/id` `/help`
**Owner**: `/stats` `/users` `/adduser <id>` `/deluser <id>` `/broadcast <text>` `/setcode <code>` `/links`

## 🔒 Security

- No credentials are hard-coded; the bot refuses to start if required env vars are missing.
- The security-code message is deleted from the chat after a successful unlock.
- `.env`, sessions and temp folders are git-ignored. Rotate your bot token if it was ever shared publicly.
