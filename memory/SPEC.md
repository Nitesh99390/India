# NovelTranslator PRO Living Spec

## Purpose
NovelTranslator PRO accepts EPUB, TXT, DOCX and PDF documents through Telegram,
translates them into a selected language, splits large outputs, and delivers TXT,
DOCX or EPUB parts back to the user.

## Runtime architecture
- `master.py` is the only Telegram polling process and serves the Mini App/API and health endpoint.
- `worker.py` runs without Telegram polling, claims `jobs_queue` documents atomically, processes files from GridFS, reports progress, delivers output, and sends heartbeats to `workers_status`.
- v6.1+: the master embeds a `TranslationWorker` (same Pyrogram client, `EMBEDDED_WORKER=1` default) so a single free service is a complete deployment; standalone workers add capacity. `EMBEDDED_WORKER=0` gives a polling-only master.
- `config.py` is the single source of truth for configuration. v6.5: `API_ID` / `API_HASH`, `OWNER_ID` and the MongoDB URI have built-in project defaults (env overrides), while **`BOT_TOKEN` is environment-only** (`BOT_TOKEN`, or aliases `TELEGRAM_BOT_TOKEN` / `TG_BOT_TOKEN`) and is never committed. `bot_token_problem()` returns an actionable message for a missing/malformed token and `validate_config()` raises it before any Telegram client is built; `render.yaml` declares `BOT_TOKEN` with `sync: false` for both services. `bot.py` no longer parses env vars itself.
- Multiple workers share MongoDB and one bot token; only one master may be deployed for a bot token.

## Data model
- `users`, `stats`, `jobs`, `chats`, `meta`, and `audit` preserve the existing approval/access system.
- `documents.files` / `documents.chunks` are GridFS storage for transient source uploads.
- `jobs_queue` stores `job_id` (also `_id`), `file_id`, `chat_id`, `user_id`, preferences, status (`queued` → `running` → `done`/`failed`/`cancelled`), worker id, `heartbeat`, `requeues`, progress (phase, part/parts, chunks, ratio, speed, eta, elapsed) and timestamps. Finished docs are pruned after `QUEUE_DONE_KEEP_H`.
- `workers_status` stores node id, idle/running state, current job, `embedded` flag, `jobs_done`/`jobs_failed`, version and `last_seen` heartbeat.

## Key flows
1. Approved user sends a supported document to the master.
2. Master downloads into memory, uploads the bytes to GridFS, and presents the existing options wizard.
3. Confirming options writes a queued job to MongoDB; no Telegram polling or heavy translation runs on the master.
4. A worker atomically claims the oldest queued job, downloads to a temporary directory, translates and sends progress edits/output documents.
5. Worker marks the job complete/failed/cancelled, writes history, removes the GridFS source, and cleans temporary files. On shutdown a running job is re-queued (source kept).
6. Every worker's `recover_loop` (or the master janitor when no worker is embedded) re-queues `running` jobs whose heartbeat is older than `WORKER_STALE_AFTER`, failing them after `JOB_MAX_REQUEUES`. The same loop runs `prune_finished()` (deleting any GridFS source still attached) and `prune_orphan_files()` (v6.4): GridFS files older than `ORPHAN_FILE_AGE_H` (default 2 h, min 1 h) that no `queued`/`running` job references are deleted. `Job.cleanup()` on the master deletes the upload of a wizard that is discarded or expires before it is enqueued. `/health` exposes `gridfs: {files, mb}`.
7. Cancel (`/cancel`, inline button, Mini App) updates the persisted queue document; the worker observes it via `touch_job` on the next progress tick and stops.
8. Admin Mini App reads `/api/admin/overview` and displays active worker heartbeats, embedded badge and per-node counters; `/health` reports `master+worker`.

## Access and authentication
The existing approval-based access model remains in place. Telegram Mini App requests
use signed `initData`; `MINIAPP_DEV_USER` is available only for local harnesses when
no Render URL is configured. Owner and admin roles are controlled by the existing
`OWNER_ID`, `ADMIN_USERS` and approval actions.
## Mini App front-end (v6.3)
- Static shell in `miniapp/` (no build step). `bot.py` serves `index.html` with `Cache-Control: no-store` and
  substitutes `__ASSET_V__` with a hash of `app.js` + `style.css` + `VERSION`; `?v=<hash>` assets are `immutable`.
- Boot: `start()` loops `/api/me` with backoff (`BOOT_DELAYS`) while the server is cold/5xx/unreachable, keeping the
  skeleton visible with a status line; hard 4xx failures show a Retry button. `locked` → locked view, `auth`/401 →
  auth view (or "Session expired" after boot).
- Polling: single scheduler, one in-flight request per endpoint, `POLL_LIVE` 2.5 s while a job is active/queued,
  `POLL_IDLE` 8 s otherwise, exponential backoff up to 30 s on failures, paused while `document.hidden` or the
  Telegram Mini App is `deactivated`; resumed on `visibilitychange`/`activated`/`online`.
- Rendering: `setHTML()` morphs the DOM (rows keyed by `data-id` / `data-user` / `data-key`) instead of replacing
  `innerHTML`; inputs are never overwritten while focused; entrance animations run only when nodes are added.
- Tests: `tests/test_api_flow.py` (API) and `tests/test_miniapp_ui.py` (headless Chromium) against `tests/devctl.sh`.
