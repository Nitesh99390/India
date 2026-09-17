# 📚 NovelTranslator PRO — Telegram Document Translation Bot

**v6.6 · Master + embedded/extra workers · GridFS + approval-based access, built for the Render.com free tier.**

The master process (Pyrogram + aiohttp) accepts Telegram updates and — by default —
also runs a translation worker in-process, so **one free Render service is a complete
deployment**. Extra stateless `worker.py` services simply add parallel capacity.
Workers translate whole documents (EPUB / TXT / DOCX / PDF) into 25 languages and
deliver the result as TXT, DOCX or EPUB — split into parts of any size you like.

> 🚀 **One-variable deploy.** `config.py` is the single source of truth and ships working
> built-in defaults for Telegram `API_ID` / `API_HASH`, `OWNER_ID` and `MONGO_URI`.
> The **only** thing you provide is `BOT_TOKEN` — as an **environment variable**
> (Render dashboard → Environment, Docker `-e BOT_TOKEN=…`, or `.env`). The bot secret is
> never committed to the repository; both `master.py` and `worker.py` refuse to start
> with a clear message until it is set. Set any other env var only to override a default.

> 🗄 **MongoDB persistence out of the box.** The bot ships with the project's own
> MongoDB Atlas free-tier (M0, 512 MB) connection string baked in, so users, settings,
> stats, access plans, audit log and job history survive every deploy with zero setup.
> Set `MONGO_URI` to use your own cluster, or `MONGO_URI=off` for the old fully-in-RAM mode.
>
> 💾 **Free-tier safe.** Uploads are stored temporarily in MongoDB GridFS and deleted
> after delivery; job metadata and history remain subject to the storage guard.
> A built-in storage guard (`DB_BUDGET_MB`, default 400 MB) prunes
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
- **MongoDB persistence** – write-through RAM cache + background writer (`motor`) for access data, plus `jobs_queue`, transient GridFS document storage and worker heartbeats. Collections include `users`, `stats`, `jobs` (TTL history, `DB_JOB_TTL_DAYS`), `chats`, `meta` and `workers_status`.
  **Storage guard**: every 10 min (and after bursts of jobs) the janitor reads `dbStats`; if the DB exceeds 80 % of `DB_BUDGET_MB` or `jobs` exceeds `DB_MAX_JOB_DOCS`, old history is pruned (per-user `HISTORY_LIMIT` → global cap → halve cap until under budget). Usage is shown in `/stats`, the Mini App owner panel and `/health`
- **Telegram Mini App** (`/app`) – Telegram-themed dashboard: ⚙️ settings (language / format / split), ▶️ live progress with ETA, 📋 queue with cancel, 🕘 history, 🎫 my access card + expiry banner, 🔒 locked screen with **Request Access** / pending status / withdraw (auto re-checks while pending), 👑 admin panel (pending requests with one-tap plans, user list with status filters, per-user sheet: approve / extend / custom duration / reject / revoke / ban / unban / promote, audit log, broadcast). Uploads can be configured from the Mini App via “📱 Configure in Mini App”. `initData` is HMAC-verified server-side (24 h max age)
- **Mini App UX polish** – skeleton shimmer while loading (no blank splash), ↻ refresh button + pull-to-refresh gesture (`disableVerticalSwipes` so Telegram doesn't collapse the app), animated tab slides / staggered card entrances / number bumps, slide-up bottom sheets with grabber, and rich haptics (`HapticFeedback` impact / selection / notification, `navigator.vibrate` fallback outside Telegram). Honours `prefers-reduced-motion`
- **Mini App reliability (v6.3)** – built for a free-tier host that sleeps: the boot loop keeps the skeleton up and retries with backoff (“Waking up the server…”) instead of dumping users on the *Open from Telegram* screen when the backend is cold, 5xx or unreachable; a ↻ **Retry** button appears only after a hard failure. Every request has a timeout, background polls never overlap, pause while the app is hidden/minimised and back off silently when the server is down (one quiet toast, no error spam). Renders are **DOM-morphed** — polls patch only what changed (rows keyed by id), so nothing flickers, progress bars animate smoothly and taps are never lost; settings taps highlight optimistically and roll back on error. `index.html` is served `no-store` with hashed asset URLs (`/app/app.js?v=<hash>`, `immutable`), so a Telegram WebView can never combine a fresh page with a stale script after a deploy. Fonts and scripts load non-blocking, 401s show a clear *Session expired* screen
- **Hierarchical reply keyboard** – 📱 Mini App · 🛠 Tools · ⚙️ Settings · 👑 Admin (owner) sub-menus. The *📱 Mini App* keyboard button behaves like `/app`: the bot replies with a message carrying an inline **📱 Open Mini App** button (fresh link, visible in chat)
- **Render-ready** – binds `$PORT` with a `/health` endpoint, self keep-alive ping so the free instance doesn't sleep, graceful SIGTERM handling (users are told when a redeploy interrupts their job)
- **Worker monitoring** – the owner Admin tab shows live worker nodes, heartbeat age, current job and online count.

## 🧭 Master-worker architecture

- `master.py` is the only Telegram polling process. It owns all message and callback handlers, the Mini App API, and document intake.
- **Embedded worker (v6.1+)** – with `EMBEDDED_WORKER=1` (default) the master also creates a `TranslationWorker(client=app, db=…)` and runs it as a background task using the *same* Pyrogram client. Set `EMBEDDED_WORKER=0` for a polling-only master when you run dedicated workers.
- `config.py` centralises environment parsing (`.env` supported), validation and deployment constants — no secrets are committed.
- `database.py` owns Motor, GridFS, atomic `jobs_queue` claims, job heartbeats, stale-job recovery (`requeue_stale`), finished-job pruning, queue positions and worker heartbeats.
- `translator.py` contains extraction, chunking, Google Translate/fallback logic and output writers.
- `worker.py` never calls Telegram polling or `app.run()`. Each worker claims one queued job with `find_one_and_update`, downloads the source from GridFS to an ephemeral folder, translates it, sends throttled progress edits (progress bar · part/chunk · speed · ETA) and the output documents, then deletes the GridFS file.

### Reliability (v6.1+)

| Feature | How it works |
|---------|--------------|
| Job heartbeat | The worker refreshes `jobs_queue.heartbeat` on every progress tick and heartbeat interval. |
| Stale recovery | A `running` job whose heartbeat is older than `WORKER_STALE_AFTER` (150 s) is handed back to the queue (max `JOB_MAX_REQUEUES` = 2 times), the user is told, and the GridFS source is kept until the job really finishes. |
| Graceful shutdown | On SIGTERM / redeploy a running job is re-queued (`♻️ Worker restarting`) and picked up by the next node. |
| Cancel anywhere | `/cancel`, the inline 🛑 button and the Mini App cancel both RAM jobs and persisted queue documents — a running worker notices within one progress tick. |
| Per-user cap | `MAX_JOBS_PER_USER` counts RAM *and* persisted queued/running jobs. |
| Queue hygiene | Finished queue documents are pruned after `QUEUE_DONE_KEEP_H` (24 h); history lives in `jobs`. Any GridFS source still attached to a pruned document is deleted with it. |
| GridFS hygiene (v6.4) | Uploads happen *before* the options wizard, so an abandoned wizard, a master restart mid-wizard or a worker crash between delivery and cleanup used to leave the source in `documents.*` forever. Now a wizard that is discarded/expired deletes its upload immediately, and every worker's `recover_loop` (or the master janitor) runs `prune_orphan_files()` each minute: any GridFS file older than `ORPHAN_FILE_AGE_H` (2 h) that no queued/running job references is removed. `/health` reports `gridfs: {files, mb}`. |
| Stats & history sync (v6.6) | **Bug fixed:** jobs finished by a worker (embedded or standalone) were written to `jobs` history but *never* credited to `users.stats` / `stats.global`, so `/mystats`, owner `/stats` and the Mini App showed `Files 0 · Parts 0 · Chars 0` forever — and the master's `replace_one` of a stale RAM user document could even wipe counters. Now the worker calls `MongoDatabase.bump_stats()` (atomic `$inc` on both documents, shared layout with the legacy in-process `Store.bump()`), the master merges history + counters from MongoDB via `Store.sync_from_db()` (throttled, before `/mystats`, `/stats`, `/api/me`, `/api/jobs`, admin views and every janitor tick), the embedded worker's `on_finished` hook mirrors the result instantly, and `Store._save_user()` no longer touches `stats`. Failed/cancelled jobs are listed in history but never credited. |

All master and worker services must share the same `MONGO_URI`, `MONGO_DB` and `BOT_TOKEN`. Add more Render worker services with unique `WORKER_NODE_ID` values; the admin Mini App shows nodes (with an `embedded` badge, jobs done/failed and heartbeat age) whose heartbeat was received within the last 90 seconds. `/health` reports `role: master+worker`, the embedded worker status and online/busy worker counts.

## 🚀 Deploy on Render (free)

1. **Fork / push** this repo to GitHub.
2. Render Dashboard → **New → Blueprint** → select the repo. `render.yaml` creates the master service (which already includes an embedded worker) and one optional extra worker service. Delete the extra worker to stay within a single free instance, or add more for parallel capacity.
3. Render asks for **one** value — `BOT_TOKEN` (declared `sync: false` in `render.yaml`, so it
   is stored only in the dashboard, never in git). Paste the raw token from @BotFather for the
   master **and** the optional worker service (they must share the same bot). Everything else
   is built into `config.py`; override only if you want to:

   | Variable        | Built-in default | Where to get your own                              |
   |-----------------|------------------|----------------------------------------------------|
   | `BOT_TOKEN`     | ❌ **required (env var)** | @BotFather → `/newbot`                     |
   | `API_ID`        | ✅ included      | https://my.telegram.org → API development tools    |
   | `API_HASH`      | ✅ included      | same page                                          |
   | `OWNER_ID`      | ✅ `6069200310`  | your numeric Telegram ID (send `/id` to the bot)   |
   | `MONGO_URI`     | ✅ project Atlas cluster | your own free [MongoDB Atlas](https://www.mongodb.com/atlas) `mongodb+srv://…` (allow `0.0.0.0/0`); `off` = RAM-only |

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
> - `OWNER_ID` defaults to the built-in owner; set it explicitly to make someone else the owner.

## ⚙️ Configuration

| Variable             | Default | Description                                                   |
|----------------------|---------|---------------------------------------------------------------|
| `BOT_TOKEN`          | **required** | Bot token from @BotFather — environment variable only, never in the repo (aliases `TELEGRAM_BOT_TOKEN` / `TG_BOT_TOKEN` also accepted) |
| `API_ID` / `API_HASH` | built-in | Telegram app credentials (my.telegram.org) — override to use another app |
| `OWNER_ID`           | `6069200310` | Permanent owner (approves requests, manages admins). Send `/id` to the bot |
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
| `EMBEDDED_WORKER`    | `1`     | Master also translates in-process. `0` → polling-only master  |
| `SERVICE_ROLE`       | `master`| `master` or `worker` (informational; `worker.py` is always a worker) |
| `WORKER_NODE_ID`     | auto    | Stable node name shown in the Mini App (standalone workers)   |
| `WORKER_HEARTBEAT_INTERVAL` | `30` | Seconds between `workers_status` heartbeats               |
| `WORKER_POLL_INTERVAL` | `3`   | Seconds between queue polls when idle                         |
| `WORKER_STALE_AFTER` | `150`   | Re-queue a running job whose heartbeat is older than this     |
| `JOB_MAX_REQUEUES`   | `2`     | Re-queue attempts before a job is failed as `WorkerLost`      |
| `QUEUE_DONE_KEEP_H`  | `24`    | Hours finished queue documents stay visible in the Mini App   |
| `ORPHAN_FILE_AGE_H`  | `2`     | Delete GridFS sources older than this that no live job references (min 1 h, must exceed the 30 min wizard TTL) |

## 💻 Run locally

```bash
git clone https://github.com/Nitesh99390/India.git && cd India
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then put your @BotFather token in BOT_TOKEN= (the only required value)
python master.py          # Telegram polling + embedded worker + Mini App at http://localhost:10000/health
# Optional — extra capacity, with the same MongoDB and the same BOT_TOKEN:
SERVICE_ROLE=worker WORKER_NODE_ID=worker-1 PORT=10001 python worker.py
# Polling-only master (no in-process translation):
EMBEDDED_WORKER=0 python master.py
```

Test the Mini App UI without Telegram: set `MINIAPP_DEV_USER=<your id>` and open
`http://localhost:10000/app` — the API treats you as that user (only works when
`RENDER_EXTERNAL_URL` is unset, i.e. never in production).

Or use the offline harness (no Telegram connection, fake jobs + seeded users in every access state):

```bash
tests/devctl.sh start owner   8080   # admin panel with pending requests
tests/devctl.sh start locked  8083   # "Request Access" screen
tests/devctl.sh start pending 8081   # waiting-for-approval screen  (also: admin · user · expired · rejected · banned)
python tests/test_api_flow.py 8080 8083   # exercises every /api/admin/users action + the locked-user flow (fresh harnesses!)
python tests/test_miniapp_ui.py 8080 8081 # headless Chromium: asset versioning, boot, DOM morphing, offline/Retry, locked view
                                          # (pip install playwright && python -m playwright install chromium)
tests/devctl.sh stopall
```

### Docker

```bash
docker build -t noveltranslator .
docker run -d -e BOT_TOKEN=123456:your_bot_token -p 10000:10000 noveltranslator
# or keep the token in .env:  docker run -d --env-file .env -p 10000:10000 noveltranslator
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
`index.html` is served with `Cache-Control: no-store` and the literal `__ASSET_V__` in its asset URLs replaced by a
short hash of `app.js` + `style.css` (+ `VERSION`); `/app/<file>?v=<hash>` responses are `immutable` for a year,
bare `/app/<file>` requests get `max-age=300, must-revalidate`. Deploying new front-end files therefore invalidates
every client automatically — no manual version bump needed.

## 🔒 Security

- The bot token is **never** stored in the repository — `BOT_TOKEN` comes exclusively from the environment (`sync: false` in `render.yaml`), and both services refuse to start with an actionable message when it is missing or malformed. Only the Telegram app id/hash, owner id and the MongoDB URI have built-in defaults.
- Nobody can use the bot until the owner or an admin approves them; approvals carry an expiry and every decision is audited (`/audit`, Mini App audit log).
- Mini App requests are authenticated with Telegram's `initData` HMAC (bot-token derived key, 24 h max age); the dev-user bypass is disabled whenever `RENDER_EXTERNAL_URL` is set.
- `.env`, sessions and temp folders are git-ignored. Rotate your bot token if it was ever shared publicly.
