"""Shared configuration for NovelTranslator PRO master and workers (v6.2 Master-Worker).

Everything can be overridden from environment variables (``.env`` is loaded when
python-dotenv is installed).  ``API_ID`` / ``API_HASH``, ``OWNER_ID`` and the MongoDB
cluster ship with working built-in defaults, so the **only** value a fresh
Render/Docker deploy of *either* service (master or worker) must provide is
``BOT_TOKEN`` — the bot secret is never committed to the repository.  Set any other
env var only when you want to change something.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def env_int(name: str, default: int) -> int:
    raw = env(name)
    try:
        return int(float(raw)) if raw else default
    except ValueError:
        print(f"[config] {name}={raw!r} is not numeric; using {default}", file=sys.stderr)
        return default


def env_float(name: str, default: float) -> float:
    raw = env(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        print(f"[config] {name}={raw!r} is not numeric; using {default}", file=sys.stderr)
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name).lower()
    return default if not raw else raw in {"1", "true", "yes", "on", "y"}


def env_int_list(name: str) -> List[int]:
    out: List[int] = []
    for tok in re.split(r"[,\s;]+", env(name)):
        tok = tok.strip()
        if tok.lstrip("-").isdigit():
            out.append(int(tok))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 🔑 TELEGRAM & IDENTITY
# ═══════════════════════════════════════════════════════════════════════════
# API_ID / API_HASH / OWNER_ID: built-in defaults — env vars override when present.
# BOT_TOKEN: **environment variable only** (Render dashboard → Environment, Docker
# `-e BOT_TOKEN=…`, or .env). The bot secret is deliberately NOT stored in the repo.
# https://my.telegram.org → API development tools · @BotFather → /newbot
DEFAULT_API_ID = 36681596
DEFAULT_API_HASH = "bece5a5cb8d1abc08b644410b6e85d5e"
# Numeric Telegram ID of the bot owner (approves everyone else). Send /id to the bot.
DEFAULT_OWNER_ID = 6069200310

API_ID = env_int("API_ID", DEFAULT_API_ID) or DEFAULT_API_ID
API_HASH = env("API_HASH") or DEFAULT_API_HASH
# Accept a few common aliases so a copy-pasted Render/Heroku variable name still works.
BOT_TOKEN = env("BOT_TOKEN") or env("TELEGRAM_BOT_TOKEN") or env("TG_BOT_TOKEN")
OWNER_ID = env_int("OWNER_ID", DEFAULT_OWNER_ID) or DEFAULT_OWNER_ID
AUTHORIZED_USERS = env_int_list("AUTHORIZED_USERS")
ADMIN_USERS = env_int_list("ADMIN_USERS")
PUBLIC_MODE = env_bool("PUBLIC_MODE", False)
DEFAULT_APPROVAL = env("DEFAULT_APPROVAL", "1m")
EXPIRY_REMINDER_DAYS = max(0, min(env_int("EXPIRY_REMINDER_DAYS", 3), 30))
REJECT_COOLDOWN_H = max(0, env_int("REJECT_COOLDOWN_H", 24))
LEGACY_USERS = env("LEGACY_USERS", "keep").lower()

# ═══════════════════════════════════════════════════════════════════════════
# 📋 BOT IDENTITY & VERSIONING
# ═══════════════════════════════════════════════════════════════════════════
BOT_NAME = env("BOT_NAME", "NovelTranslator PRO")
VERSION = "6.7-master-worker"
SERVICE_ROLE = env("SERVICE_ROLE", "master").lower()  # "master" or "worker"
WORKER_NODE_ID = env("WORKER_NODE_ID", "worker-local")
# The master also runs a translation worker in-process by default, so a single
# free Render service is a complete deployment. Extra worker services simply
# add more parallel capacity. Set EMBEDDED_WORKER=0 to make the master polling-only.
EMBEDDED_WORKER = env_bool("EMBEDDED_WORKER", True)
AUDIT_LIMIT = 300  # audit entries kept in RAM / DB

# ═══════════════════════════════════════════════════════════════════════════
# ⚙️ TRANSLATION SETTINGS
# ═══════════════════════════════════════════════════════════════════════════
CONCURRENCY_LIMIT = max(1, min(env_int("CONCURRENCY", 8), 20))
CHUNK_SIZE = max(500, min(env_int("CHUNK_SIZE", 3500), 4800))
MAX_RETRIES = 5
MAX_INPUT_MB = max(1, env_int("MAX_INPUT_MB", 50))
MAX_JOBS_PER_USER = max(1, env_int("MAX_JOBS_PER_USER", 2))
PENDING_TTL = 30 * 60
EDIT_INTERVAL = 4.0
REQUEST_TIMEOUT = 40
DEFAULT_LANG = env("DEFAULT_LANG", "hi")
DEFAULT_FORMAT = env("DEFAULT_FORMAT", "txt")
DEFAULT_SPLIT_KB = env_int("DEFAULT_SPLIT_KB", 500)
MIN_SPLIT_KB, MAX_SPLIT_KB = 50, 15 * 1024

# ═══════════════════════════════════════════════════════════════════════════
# 🌐 WEB SERVER & NETWORKING
# ═══════════════════════════════════════════════════════════════════════════
PORT = env_int("PORT", 10000)
PUBLIC_URL = (env("PUBLIC_URL") or env("RENDER_EXTERNAL_URL")).rstrip("/")
RENDER_EXTERNAL_URL = env("RENDER_EXTERNAL_URL").rstrip("/")
KEEP_ALIVE = env_bool("KEEP_ALIVE", True)
KEEP_ALIVE_INTERVAL = max(60, env_int("KEEP_ALIVE_INTERVAL", 600))

# ═══════════════════════════════════════════════════════════════════════════
# 🗄 MONGODB & JOB QUEUE
# ═══════════════════════════════════════════════════════════════════════════
# Atlas free tier (M0, 512 MB) — the project's own cluster. Env var wins; the baked-in
# string only makes a fresh deploy persistent without any setup. `off` → RAM only.
# ⚠️  Only *metadata* + transient GridFS uploads are stored (deleted after delivery).
DEFAULT_MONGO_URI = (
    "mongodb+srv://bhuimharniteshbhuimhar_db_user:nitesh9939"
    "@nitesh99390.qbwrf1c.mongodb.net/?appName=Nitesh99390&retryWrites=true&w=majority"
)
DEFAULT_MONGO_DB = "noveltranslator"
MONGO_URI = env("MONGO_URI") or env("MONGODB_URI") or env("DATABASE_URL") or DEFAULT_MONGO_URI
if MONGO_URI.lower() in {"0", "off", "none", "memory", "disabled"}:
    MONGO_URI = ""
MONGO_DB = env("MONGO_DB", DEFAULT_MONGO_DB)
HISTORY_LIMIT = max(5, min(env_int("HISTORY_LIMIT", 30), 100))
DB_BUDGET_MB = max(50, min(env_int("DB_BUDGET_MB", 400), 512))
DB_MAX_JOB_DOCS = max(200, env_int("DB_MAX_JOB_DOCS", 3000))
DB_JOB_TTL_DAYS = max(7, min(env_int("DB_JOB_TTL_DAYS", 60), 365))

# Worker polling & heartbeat
WORKER_HEARTBEAT_INTERVAL = max(10, env_int("WORKER_HEARTBEAT_INTERVAL", 30))
WORKER_POLL_INTERVAL = max(1, env_int("WORKER_POLL_INTERVAL", 3))
# A "running" job whose worker heartbeat is older than this is handed back to the queue
WORKER_STALE_AFTER = max(60, env_int("WORKER_STALE_AFTER", 150))
JOB_MAX_REQUEUES = max(0, env_int("JOB_MAX_REQUEUES", 2))
# Finished queue documents are kept this long for the Mini App, then pruned
QUEUE_DONE_KEEP_H = max(1, env_int("QUEUE_DONE_KEEP_H", 24))
# GridFS sources not referenced by any queued/running job (abandoned wizard, crash
# before cleanup) are deleted once older than this many hours. Must stay above
# PENDING_TTL (30 min) so an open wizard never loses its upload.
ORPHAN_FILE_AGE_H = max(1.0, env_float("ORPHAN_FILE_AGE_H", 2.0))

# ═══════════════════════════════════════════════════════════════════════════
# 📱 MINI APP & WEB INTERFACE
# ═══════════════════════════════════════════════════════════════════════════
MINI_APP_URL = env("MINI_APP_URL") or (f"{PUBLIC_URL}/app" if PUBLIC_URL else "")
MINI_APP_DIR = env("MINI_APP_DIR") or str(Path(__file__).with_name("miniapp"))
MINIAPP_DEV_USER = env_int("MINIAPP_DEV_USER", 0)
INIT_DATA_MAX_AGE = 24 * 3600
BACKUP_GROUP_ID = env_int("BACKUP_GROUP_ID", 0)

# ═══════════════════════════════════════════════════════════════════════════
# 📂 STORAGE & TEMPORARY FILES
# ═══════════════════════════════════════════════════════════════════════════
BASE_DIR = env("WORK_DIR") or str(Path(tempfile.gettempdir()) / "noveltranslator")
STORAGE_DIR = str(Path(BASE_DIR) / "out")
INBOX_DIR = str(Path(BASE_DIR) / "inbox")
Path(STORAGE_DIR).mkdir(parents=True, exist_ok=True)
Path(INBOX_DIR).mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════
# 🌍 LANGUAGE & FORMAT SUPPORT
# ═══════════════════════════════════════════════════════════════════════════
INPUT_EXTS = {".epub", ".txt", ".docx", ".pdf"}
OUTPUT_FORMATS = {"txt": "📄 TXT", "docx": "📝 DOCX", "epub": "📚 EPUB"}
SPLIT_PRESETS = [
    (0, "🚫 No split"),
    (100, "100 KB"),
    (300, "300 KB"),
    (500, "500 KB"),
    (1024, "1 MB"),
    (2048, "2 MB"),
    (5120, "5 MB"),
]

LANGUAGES: Dict[str, Tuple[str, str]] = {
    "hi": ("Hindi", "🇮🇳"),
    "en": ("English", "🇬🇧"),
    "bn": ("Bengali", "🇧🇩"),
    "ta": ("Tamil", "🇮🇳"),
    "te": ("Telugu", "🇮🇳"),
    "mr": ("Marathi", "🇮🇳"),
    "gu": ("Gujarati", "🇮🇳"),
    "kn": ("Kannada", "🇮🇳"),
    "ml": ("Malayalam", "🇮🇳"),
    "pa": ("Punjabi", "🇮🇳"),
    "ur": ("Urdu", "🇵🇰"),
    "ne": ("Nepali", "🇳🇵"),
    "es": ("Spanish", "🇪🇸"),
    "fr": ("French", "🇫🇷"),
    "de": ("German", "🇩🇪"),
    "pt": ("Portuguese", "🇧🇷"),
    "ru": ("Russian", "🇷🇺"),
    "ar": ("Arabic", "🇸🇦"),
    "id": ("Indonesian", "🇮🇩"),
    "tr": ("Turkish", "🇹🇷"),
    "vi": ("Vietnamese", "🇻🇳"),
    "th": ("Thai", "🇹🇭"),
    "zh-CN": ("Chinese", "🇨🇳"),
    "ja": ("Japanese", "🇯🇵"),
    "ko": ("Korean", "🇰🇷"),
}

EXPANSION = {
    **{k: 2.6 for k in ("hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "ne")},
    "ur": 1.9,
    "ar": 1.8,
    "ru": 1.8,
    "th": 2.6,
    "zh-CN": 0.9,
    "ja": 1.2,
    "ko": 1.2,
}

if DEFAULT_LANG not in LANGUAGES:
    DEFAULT_LANG = "hi"
if DEFAULT_FORMAT not in OUTPUT_FORMATS:
    DEFAULT_FORMAT = "txt"
if DEFAULT_SPLIT_KB and not (MIN_SPLIT_KB <= DEFAULT_SPLIT_KB <= MAX_SPLIT_KB):
    DEFAULT_SPLIT_KB = 500

# ═══════════════════════════════════════════════════════════════════════════
# 📊 LOGGING
# ═══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
log = logging.getLogger("noveltranslator")

BOOT_TS = time.time()


# Telegram bot tokens look like "<numeric bot id>:<35 url-safe chars>"; be lenient on
# the length so future formats still pass, strict on the shape so typos are caught early.
_BOT_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")


def bot_token_problem(token: str = BOT_TOKEN) -> str:
    """Return a human readable reason why ``token`` is unusable, or "" when it looks valid."""
    if not token:
        return ("BOT_TOKEN is not set. It is the only required environment variable: create the bot with "
                "@BotFather (/newbot), then add BOT_TOKEN in the Render dashboard → Environment "
                "(or in .env / `docker run -e BOT_TOKEN=…`) and redeploy.")
    if token.lower().startswith("bot") and ":" in token:
        return "BOT_TOKEN must not start with the 'bot' prefix used in HTTP API URLs — paste only the raw token."
    if ":" not in token:
        return "BOT_TOKEN is malformed (expected '<bot id>:<secret>' exactly as @BotFather sent it)."
    if any(ch.isspace() for ch in token) or token.startswith(("'", '"')) or token.endswith(("'", '"')):
        return "BOT_TOKEN contains spaces or quotes — paste only the raw token without quotes."
    if not _BOT_TOKEN_RE.match(token):
        return "BOT_TOKEN does not look like a Telegram bot token ('<numeric id>:<35 letters/digits>')."
    return ""


def validate_config(require_telegram: bool = True, role: str = SERVICE_ROLE) -> None:
    """Validate the effective configuration (environment variables / .env).

    ``BOT_TOKEN`` has no built-in default, so a missing/malformed token gets its own
    actionable message; the remaining checks only fire when a built-in default was
    overridden by an env var with a bad value.
    """
    if require_telegram:
        token_issue = bot_token_problem()
        if token_issue:
            raise RuntimeError(token_issue)
    problems = []
    if require_telegram and not API_ID:
        problems.append("API_ID")
    if require_telegram and not API_HASH:
        problems.append("API_HASH")
    if require_telegram and role == "master" and not OWNER_ID and not PUBLIC_MODE:
        problems.append("OWNER_ID")
    if problems:
        raise RuntimeError("Missing or invalid configuration: " + ", ".join(problems)
                           + " — the built-in defaults were overridden by an env var with a bad value; "
                             "fix or remove it in the Render dashboard → Environment (or .env).")
