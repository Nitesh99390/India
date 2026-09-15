# NovelTranslator PRO Living Spec

## Purpose
NovelTranslator PRO accepts EPUB, TXT, DOCX and PDF documents through Telegram,
translates them into a selected language, splits large outputs, and delivers TXT,
DOCX or EPUB parts back to the user.

## Runtime architecture
- `master.py` is the only Telegram polling process and serves the Mini App/API and health endpoint.
- `worker.py` runs without Telegram polling, claims `jobs_queue` documents atomically, processes files from GridFS, reports progress, delivers output, and sends heartbeats to `workers_status`.
- Multiple workers share MongoDB and one bot token; only one master may be deployed for a bot token.

## Data model
- `users`, `stats`, `jobs`, `chats`, `meta`, and `audit` preserve the existing approval/access system.
- `documents.files` / `documents.chunks` are GridFS storage for transient source uploads.
- `jobs_queue` stores `job_id`, `file_id`, `chat_id`, `user_id`, preferences, status, worker id, progress and timestamps.
- `workers_status` stores node id, idle/running state, current job and `last_seen` heartbeat.

## Key flows
1. Approved user sends a supported document to the master.
2. Master downloads into memory, uploads the bytes to GridFS, and presents the existing options wizard.
3. Confirming options writes a queued job to MongoDB; no Telegram polling or heavy translation runs on the master.
4. A worker atomically claims the oldest queued job, downloads to a temporary directory, translates and sends progress edits/output documents.
5. Worker marks the job complete/failed, writes history, removes the GridFS source, and cleans temporary files.
6. Admin Mini App reads `/api/admin/overview` and displays active worker heartbeats.

## Access and authentication
The existing approval-based access model remains in place. Telegram Mini App requests
use signed `initData`; `MINIAPP_DEV_USER` is available only for local harnesses when
no Render URL is configured. Owner and admin roles are controlled by the existing
`OWNER_ID`, `ADMIN_USERS` and approval actions.