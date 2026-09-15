"""Shared configuration for the NovelTranslator PRO master and workers."""

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
except ImportError:  # pragma: no cover - dotenv is optional in tiny test harnesses
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


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name).lower()
    return default if not raw else raw in {"1", "true", "yes", "on", "y"}


def env_int_list(name: str) -> List[int]:
    return [int(value) for value in re.split(r"[,\s;]+", env(name)) if value.lstrip("-").isdigit()]


API_ID = env_int("API_ID", 0)
API_HASH = env("API_HASH")
BOT_TOKEN = env("BOT_TOKEN")
OWNER_ID = env_int("OWNER_ID", 0)
AUTHORIZED_USERS = env_int_list("AUTHORIZED_USERS")
ADMIN_USERS = env_int_list("ADMIN_USERS")
PUBLIC_MODE = env_bool("PUBLIC_MODE")
DEFAULT_APPROVAL = env("DEFAULT_APPROVAL", "1m")
EXPIRY_REMINDER_DAYS = max(0, min(env_int("EXPIRY_REMINDER_DAYS", 3), 30))
REJECT_COOLDOWN_H = max(0, env_int("REJECT_COOLDOWN_H", 24))
LEGACY_USERS = env("LEGACY_USERS", "keep").lower()

BOT_NAME = env("BOT_NAME", "NovelTranslator PRO")
VERSION = "6.0-master-worker"
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

PORT = env_int("PORT", 10000)
PUBLIC_URL = (env("PUBLIC_URL") or env("RENDER_EXTERNAL_URL")).rstrip("/")
RENDER_EXTERNAL_URL = env("RENDER_EXTERNAL_URL").rstrip("/")
KEEP_ALIVE = env_bool("KEEP_ALIVE", True)
KEEP_ALIVE_INTERVAL = max(60, env_int("KEEP_ALIVE_INTERVAL", 600))
ROLE = env("SERVICE_ROLE", "master").lower()
WORKER_NODE_ID = env("WORKER_NODE_ID", "worker-local")
WORKER_HEARTBEAT_INTERVAL = max(10, env_int("WORKER_HEARTBEAT_INTERVAL", 30))
WORKER_POLL_INTERVAL = max(1, env_int("WORKER_POLL_INTERVAL", 3))

MONGO_URI = env("MONGO_URI") or env("MONGODB_URI") or env("DATABASE_URL")
MONGO_DB = env("MONGO_DB", "noveltranslator")
if MONGO_URI.lower() in {"0", "off", "none", "memory", "disabled"}:
    MONGO_URI = ""

HISTORY_LIMIT = max(5, min(env_int("HISTORY_LIMIT", 30), 100))
DB_BUDGET_MB = max(50, min(env_int("DB_BUDGET_MB", 400), 512))
DB_MAX_JOB_DOCS = max(200, env_int("DB_MAX_JOB_DOCS", 3000))
DB_JOB_TTL_DAYS = max(7, min(env_int("DB_JOB_TTL_DAYS", 60), 365))
MINIAPP_DEV_USER = env_int("MINIAPP_DEV_USER", 0)
INIT_DATA_MAX_AGE = 24 * 3600
MINI_APP_DIR = env("MINI_APP_DIR") or str(Path(__file__).with_name("miniapp"))
MINI_APP_URL = env("MINI_APP_URL") or (f"{PUBLIC_URL}/app" if PUBLIC_URL else "")
BACKUP_GROUP_ID = env_int("BACKUP_GROUP_ID", 0)

BASE_DIR = env("WORK_DIR") or str(Path(tempfile.gettempdir()) / "noveltranslator")
STORAGE_DIR = str(Path(BASE_DIR) / "out")
INBOX_DIR = str(Path(BASE_DIR) / "inbox")
Path(STORAGE_DIR).mkdir(parents=True, exist_ok=True)
Path(INBOX_DIR).mkdir(parents=True, exist_ok=True)

LANGUAGES: Dict[str, Tuple[str, str]] = {
    "hi": ("Hindi", "🇮🇳"), "en": ("English", "🇬🇧"), "bn": ("Bengali", "🇧🇩"),
    "ta": ("Tamil", "🇮🇳"), "te": ("Telugu", "🇮🇳"), "mr": ("Marathi", "🇮🇳"),
    "gu": ("Gujarati", "🇮🇳"), "kn": ("Kannada", "🇮🇳"), "ml": ("Malayalam", "🇮🇳"),
    "pa": ("Punjabi", "🇮🇳"), "ur": ("Urdu", "🇵🇰"), "ne": ("Nepali", "🇳🇵"),
    "es": ("Spanish", "🇪🇸"), "fr": ("French", "🇫🇷"), "de": ("German", "🇩🇪"),
    "pt": ("Portuguese", "🇧🇷"), "ru": ("Russian", "🇷🇺"), "ar": ("Arabic", "🇸🇦"),
    "id": ("Indonesian", "🇮🇩"), "tr": ("Turkish", "🇹🇷"), "vi": ("Vietnamese", "🇻🇳"),
    "th": ("Thai", "🇹🇭"), "zh-CN": ("Chinese", "🇨🇳"), "ja": ("Japanese", "🇯🇵"),
    "ko": ("Korean", "🇰🇷"),
}
OUTPUT_FORMATS = {"txt": "📄 TXT", "docx": "📝 DOCX", "epub": "📚 EPUB"}
INPUT_EXTS = {".epub", ".txt", ".docx", ".pdf"}
EXPANSION = {**{key: 2.6 for key in ("hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "ne")},
             "ur": 1.9, "ar": 1.8, "ru": 1.8, "th": 2.6, "zh-CN": 0.9, "ja": 1.2, "ko": 1.2}
SPLIT_PRESETS = [(0, "🚫 No split"), (100, "100 KB"), (300, "300 KB"), (500, "500 KB"),
                 (1024, "1 MB"), (2048, "2 MB"), (5120, "5 MB")]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                    stream=sys.stdout, force=True)
log = logging.getLogger("noveltranslator")


def validate_config(require_telegram: bool = True) -> None:
    problems = []
    if require_telegram and not API_ID:
        problems.append("API_ID")
    if require_telegram and not API_HASH:
        problems.append("API_HASH")
    if require_telegram and (not BOT_TOKEN or ":" not in BOT_TOKEN):
        problems.append("BOT_TOKEN")
    if ROLE == "master" and not OWNER_ID and not PUBLIC_MODE:
        problems.append("OWNER_ID")
    if problems:
        raise RuntimeError("Missing or invalid environment variables: " + ", ".join(problems))


BOOT_TS = time.time()