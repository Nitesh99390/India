# 📚 NovelTranslator PRO — Telegram Document Translation Bot

**v5.0 · Approval-based access + MongoDB + Telegram Mini App edition, built for the Render.com free tier.**

A single-file Telegram bot (Pyrogram + aiohttp) that translates whole
documents (EPUB / TXT / DOCX / PDF) into 25 languages and delivers the result
as TXT, DOCX or EPUB — split into parts of any size you like.

> 🗄 **MongoDB persistence out of the box.** The bot ships with the project's own
> MongoDB Atlas free-tier (M0, 512 MB) connection string baked in, so users, settings,
> stats, access plans, audit log and job history survive every deploy with zero setup.
> Set `MONGO_URI` to use your own cluster, or `MONGO_URI=off` for the old fully-in-RAM mode.
>
> 💾 **Free-tier safe.** Only *metadata* is stored — uploaded / translated files never
> touch the database. A built-in storage guard (`DB_BUDGET_MB`, default 400 MB) prunes
> old job history (per-user cap → global cap → TTL) so the M0 tier can never fill up.
>
> 🎫 **Approval-based access (v5).** No more shared security code. New users tap
> **🙋 Request Access**, the owner/admins get an inline card and approve with one tap
> for **1 week · 1 month · 3 months · 6 months · 1 year · lifetime** (or any custom
> duration like `45d` / `2026-12-31`). Access expires automatically, users get a reminder
> before it does, everything is written to an audit log.
>
> 📱 **Telegram Mini App.** The same process serves a premium dashboard at
> `/app` — settings, live job progress, history, your access plan and an admin panel
> (pending requests, user management, audit log) — opened straight from the bot's
> menu button / bottom keyboard.

## ✨ Features

- **Input**: `.epub` `.txt` `.docx` `.pdf` (default limit 50 MB, configurable)
- **Output**: TXT · DOCX · EPUB, auto-split into parts (presets or custom 50 KB – 15 MB)
  – split size is *output-aware* (Hindi/Bengali etc. take ~2.6× more bytes than English)
- **25 languages** (Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Urdu, Spanish, French, German, Arabic, Chinese, Japanese …)
- **Fast async translation engine** – direct HTTP to Google's web endpoint via aiohttp, per-line segments so paragraph structure survives 1:1, exponential-backoff retries + thread fallback
- **Live dashboard** – progress bar, speed, ETA, flood-safe message edits
- **Job queue** with position updates, per-user job limit, cancel button, 4-step wizard + ⚡ Quick Start
- **Role-based menus** – persistent bottom reply keyboard + Telegram “/” command list.
  Locked visitors see only `/start` `/request` `/access` `/id`; approved users get the
  user buttons/commands; admins additionally get ⏳ Pending · 👥 Users · ✅ Approve…; the owner
  gets 👑 Owner Panel · 🛡 Admins · 📜 Audit · 📣 Broadcast · 🔗 Links
  (per-chat `BotCommandScope`, so each person sees exactly the commands they can use)
- **Approval-based access** – states `none → pending → approved → expired` (+ `rejected`, `banned`).
  Requests arrive as inline cards (✅ 1w · 1m · 3m · 6m · 1y · ♾ · ❌ Reject); admins can also
  `/approve <id> <duration>`, `/extend`, `/reject`, `/revoke`, `/ban`, `/unban`, `/userinfo`.
  Hourly sweep expires plans, sends reminders `EXPIRY_REMINDER_DAYS` before, and a rejected user
  may re-request after `REJECT_COOLDOWN_H`. Owner can promote admins (`/addadmin`) and read `/audit`.
  v4 users unlocked with the old code are migrated to lifetime access (`LEGACY_USERS=keep`)
- **Optional backup group** – each job gets its own forum topic with all delivered parts (set `BACKUP_GROUP_ID=0` to disable)
- **MongoDB persistence (optional)** – write-through RAM cache + background writer (`motor`), so handlers never block on the DB. Collections: `users`, `stats`, `jobs` (TTL history, `DB_JOB_TTL_DAYS`), `chats`, `meta`.
  **Storage guard**: every 10 min (and after bursts of jobs) the janitor reads `dbStats`; if the DB exceeds 80 % of `DB_BUDGET_MB` or `jobs` exceeds `DB_MAX_JOB_DOCS`, old history is pruned (per-user `HISTORY_LIMIT` → global cap → halve cap until under budget). Usage is shown in `/stats`, the Mini App owner panel and `/health`
- **Telegram Mini App** (`/app`) – Telegram-themed dashboard: ⚙️ settings (language / format / split), ▶️ live progress with ETA, 📋 queue with cancel, 🕘 history, 🎫 my access card + expiry banner, 🔒 locked screen with **Request Access** / pending status / withdraw (auto re-checks while pending), 👑 admin panel (pending requests with one-tap plans, user list with status filters, per-user sheet: approve / extend / custom duration / reject / revoke / ban / unban / promote, audit log, broadcast). Uploads can be configured from the Mini App via “📱 Configure in Mini App”. `initData` is HMAC-verified server-side (24 h max age)
- **Mini App UX polish** – skeleton shimmer while loading (no blank splash), ↻ refresh button + pull-to-refresh gesture (`disableVerticalSwipes` so Telegram doesn't collapse the app), animated tab slides / staggered card entrances / number bumps, slide-up bottom sheets with grabber, and rich haptics (`HapticFeedback` impact / selection / notification, `navigator.vibrate` fallback outside Telegram). Honours `prefers-reduced-motion`
- **Hierarchical reply keyboard** – 📱 Mini App · 🛠 Tools · ⚙️ Settings · 👑 Admin (owner) sub-menus. The *📱 Mini App* keyboard button behaves like `/app`: the bot replies with a message carrying an inline **📱 Open Mini App** button (fresh link, visible in chat)
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
   | `OWNER_ID`      | your numeric Telegram ID (send `/id` to the bot, or @userinfobot) — **required**, the owner approves everyone else |

   Optional: `MONGO_URI` → your own free [MongoDB Atlas](https://www.mongodb.com/atlas) cluster
   connection string (`mongodb+srv://…`). Allow access from `0.0.0.0/0` in Atlas Network Access.
   Leave it empty to use the built-in project cluster, or set `MONGO_URI=off` for RAM-only.

4. Deploy. Open the service URL → you should see “Telegram bot is online”.
5. Send `/start` to your bot. The **📱 App** menu button opens the dashboard directly and the
   *📱 Mini App* keyboard button (or `/app`) sends an inline **Open Mini App** link
   (`RENDER_EXTERNAL_URL/app` is wired automatically). 🎉

> ⚠️ Free-tier notes
> - The instance is spun down after 15 min without HTTP traffic. The built-in
>   keep-alive pings `RENDER_EXTERNAL_URL/health` every 10 min to prevent that
>   (`KEEP_ALIVE=0` to disable). Free tier also has a monthly hour budget.
> - With `MONGO_URI=off` every deploy/restart wipes memory: approvals, pending requests and the
>   audit log are lost. Put permanent IDs in `AUTHORIZED_USERS` / `ADMIN_USERS` — or keep MongoDB on.
> - Set `OWNER_ID` explicitly — without an owner nobody can approve requests.

## ⚙️ Configuration

| Variable             | Default | Description                                                   |
|----------------------|---------|---------------------------------------------------------------|
| `API_ID` / `API_HASH` / `BOT_TOKEN` | — | **Required** Telegram credentials                    |
| `OWNER_ID`           | `0`     | Permanent owner (approves requests, manages admins)            |
| `ADMIN_USERS`        | —       | Comma-separated IDs that are admins on every start (can approve users) |
| `AUTHORIZED_USERS`   | —       | Comma-separated IDs with lifetime access on every start        |
| `PUBLIC_MODE`        | `0`     | `1` → everyone is approved automatically                       |
| `DEFAULT_APPROVAL`   | `1m`    | Duration used by `/approve <id>` / `/adduser` without a duration (`1w` `1m` `1y` `forever` `45d`) |
| `EXPIRY_REMINDER_DAYS` | `3`   | Remind users N days before their access expires (0 = off)      |
| `REJECT_COOLDOWN_H`  | `24`    | Hours a rejected user must wait before requesting again (0 = none) |
| `LEGACY_USERS`       | `keep`  | v4 users unlocked with the old code: `keep` (lifetime) or `reapprove` |
| `MONGO_URI`          | built-in Atlas M0 | MongoDB connection string. `off` → in-memory only      |
| `MONGO_DB`           | `noveltranslator` | Database name                                        |
| `HISTORY_LIMIT`      | `30`    | Jobs kept per user in history (5–100)                          |
| `DB_BUDGET_MB`       | `400`   | Storage budget (50–512). Above 80 % → old history is pruned    |
| `DB_MAX_JOB_DOCS`    | `3000`  | Global cap for the `jobs` collection                           |
| `DB_JOB_TTL_DAYS`    | `60`    | Job history expiry via MongoDB TTL index (7–365)               |
| `PUBLIC_URL`         | `RENDER_EXTERNAL_URL` | Public HTTPS base URL (needed for the Mini App)  |
| `MINI_APP_URL`       | `PUBLIC_URL/app` | Override the Mini App URL                               |
| `MINIAPP_DEV_USER`   | `0`     | Local dev only: fake Telegram user id for `http://localhost/app` |
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
cp .env.example .env      # fill in API_ID, API_HASH, BOT_TOKEN, OWNER_ID
python bot.py             # http://localhost:10000/health
```

Test the Mini App UI without Telegram: set `MINIAPP_DEV_USER=<your id>` and open
`http://localhost:10000/app` — the API treats you as that user (only works when
`RENDER_EXTERNAL_URL` is unset, i.e. never in production).

Or use the offline harness (no Telegram connection, fake jobs + seeded users in every access state):

```bash
tests/devctl.sh start owner   8080   # admin panel with pending requests
tests/devctl.sh start locked  8083   # "Request Access" screen
tests/devctl.sh start pending 8081   # waiting-for-approval screen  (also: admin · user · expired · rejected · banned)
python tests/test_api_flow.py 8080   # exercises every /api/admin/users action + the locked-user flow
tests/devctl.sh stopall
```

### Docker

```bash
docker build -t noveltranslator .
docker run -d --env-file .env -p 10000:10000 noveltranslator
```

## 💬 Commands

**Locked visitors**: `/start` `/request [note]` `/access` `/id`
**Users**: `/start` `/app` `/settings` `/queue` `/cancel` `/mystats` `/access` `/id` `/help`
**Admins**: `/pending` `/users [filter]` `/userinfo <id>` `/approve <id> [1w|1m|3m|6m|1y|forever|45d|2026-12-31]` `/extend <id> <duration>` `/reject <id> [reason]` `/revoke <id> [reason]` `/ban <id> [reason]` `/unban <id>`
**Owner**: `/stats` `/admins` `/addadmin <id>` `/deladmin <id>` `/audit` `/broadcast <text>` `/links`
(`/adduser` and `/deluser` still work as aliases of `/approve` and `/revoke`.)

## 📱 Mini App API (served by `bot.py`)

All endpoints expect `Authorization: tma <initData>` (Telegram WebApp `initData`, HMAC-verified).

| Method | Path                    | Purpose                                   |
|--------|-------------------------|-------------------------------------------|
| GET    | `/api/me`               | Profile, prefs, stats, access + public config. Locked users get `403 {code:"locked", me, config}` |
| GET    | `/api/access`           | Own access card — works while locked        |
| POST   | `/api/access/request`   | `{note?}` → ask the owner for approval      |
| POST   | `/api/access/withdraw`  | Cancel a pending request                    |
| POST   | `/api/settings`         | `{lang?, fmt?, split?}`                    |
| GET    | `/api/jobs`             | Active / queue / pending uploads / history |
| POST   | `/api/jobs/start`       | `{job_id, lang, fmt, split}` finish wizard |
| POST   | `/api/jobs/cancel`      | `{job_id?}` cancel own (owner: any) jobs   |
| GET    | `/api/admin/overview`   | Admins: users, pending requests, counts, stats (+ audit & recent jobs for the owner) |
| GET    | `/api/admin/user/{id}`  | Admins: one user + job history             |
| POST   | `/api/admin/users`      | Admins: `{action: approve\|extend\|reject\|revoke\|ban\|unban\|promote\|demote, id, duration?, reason?}` (`add`/`remove` still accepted) |
| POST   | `/api/admin/broadcast`  | Owner: `{text}` → all approved users        |

Static files live in `miniapp/` (`index.html`, `style.css`, `app.js` — no build step).

## 🔒 Security

- No credentials are hard-coded; the bot refuses to start if required env vars are missing.
- Nobody can use the bot until the owner or an admin approves them; approvals carry an expiry and every decision is audited (`/audit`, Mini App audit log).
- Mini App requests are authenticated with Telegram's `initData` HMAC (bot-token derived key, 24 h max age); the dev-user bypass is disabled whenever `RENDER_EXTERNAL_URL` is set.
- `.env`, sessions and temp folders are git-ignored. Rotate your bot token if it was ever shared publicly.
