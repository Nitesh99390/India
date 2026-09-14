#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║        📚 NovelTranslator PRO  —  Telegram Document Translation Bot       ║
║          v3.0  ·  Stateless edition for Render.com free tier              ║
╠══════════════════════════════════════════════════════════════════════════╣
║  • No database / no disk persistence  → everything lives in memory       ║
║  • Bot session kept in memory         → no .session files                ║
║  • Tiny HTTP health server on $PORT   → required by Render web services  ║
║  • Optional self keep-alive ping      → prevents free-tier spin-down     ║
║  • Graceful SIGTERM handling          → users are told when a deploy     ║
║    interrupts their job                                                  ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import html
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import aiohttp
import docx
import ebooklib
from aiohttp import web
from bs4 import BeautifulSoup
from ebooklib import epub

# Pyrogram still calls asyncio.get_event_loop() at import/Client-init time.
# On Python ≥ 3.12 that is deprecated (and fails on 3.14) when no loop exists,
# so we create one explicitly *before* importing/instantiating the client.
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, enums, filters, idle  # noqa: E402
from pyrogram.errors import (FloodWait, MessageIdInvalid, MessageNotModified,  # noqa: E402
                             RPCError)
from pyrogram.raw import functions  # noqa: E402
from pyrogram.types import (CallbackQuery, ChatMemberUpdated, InlineKeyboardButton,  # noqa: E402
                            InlineKeyboardMarkup, Message)

try:
    from pypdf import PdfReader
    HAS_PDF = True
except ImportError:  # pragma: no cover
    HAS_PDF = False

try:  # optional: .env for local development only
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

try:  # optional fallback translator (thread based)
    from deep_translator import GoogleTranslator as _FallbackTranslator
    HAS_FALLBACK = True
except ImportError:  # pragma: no cover
    HAS_FALLBACK = False


# ═══════════════════════════════════════════════════════════════════════════
# 🔑 CONFIGURATION — everything comes from environment variables
# ═══════════════════════════════════════════════════════════════════════════
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        print(f"[config] {name}={raw!r} is not a number → using {default}", file=sys.stderr)
        return default

def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "y")

def _env_int_list(name: str) -> List[int]:
    out: List[int] = []
    for tok in re.split(r"[,\s;]+", _env(name)):
        tok = tok.strip()
        if tok.lstrip("-").isdigit():
            out.append(int(tok))
    return out

API_ID = _env_int("API_ID", 0)
API_HASH = _env("API_HASH")
BOT_TOKEN = _env("BOT_TOKEN")
SECURITY_CODE = _env("SECURITY_CODE")
OWNER_ID = _env_int("OWNER_ID", 0)
AUTHORIZED_USERS = _env_int_list("AUTHORIZED_USERS")       # pre-authorised on boot
PUBLIC_MODE = _env_bool("PUBLIC_MODE", False)              # True → no code needed
BACKUP_GROUP_ID = _env_int("BACKUP_GROUP_ID", 0)           # 0 → backups disabled

BOT_NAME = _env("BOT_NAME", "NovelTranslator PRO")
CONCURRENCY_LIMIT = max(1, min(_env_int("CONCURRENCY", 8), 20))
CHUNK_SIZE = max(500, min(_env_int("CHUNK_SIZE", 3500), 4800))
MAX_RETRIES = 5
MAX_INPUT_MB = max(1, _env_int("MAX_INPUT_MB", 50))
MAX_JOBS_PER_USER = max(1, _env_int("MAX_JOBS_PER_USER", 2))
PENDING_TTL = 30 * 60
EDIT_INTERVAL = 4.0
REQUEST_TIMEOUT = 40
DEFAULT_LANG = _env("DEFAULT_LANG", "hi")
DEFAULT_FORMAT = _env("DEFAULT_FORMAT", "txt")
DEFAULT_SPLIT_KB = _env_int("DEFAULT_SPLIT_KB", 500)
MIN_SPLIT_KB, MAX_SPLIT_KB = 50, 15 * 1024

# Render specific
PORT = _env_int("PORT", 10000)
RENDER_EXTERNAL_URL = _env("RENDER_EXTERNAL_URL").rstrip("/")
KEEP_ALIVE = _env_bool("KEEP_ALIVE", True)
KEEP_ALIVE_INTERVAL = max(60, _env_int("KEEP_ALIVE_INTERVAL", 600))

# Temp workspace — ephemeral by design (Render disks are wiped on every deploy)
BASE_DIR = _env("WORK_DIR") or os.path.join(tempfile.gettempdir(), "noveltranslator")
STORAGE_DIR = os.path.join(BASE_DIR, "out")
INBOX_DIR = os.path.join(BASE_DIR, "inbox")
for _d in (BASE_DIR, STORAGE_DIR, INBOX_DIR):
    os.makedirs(_d, exist_ok=True)

INPUT_EXTS = {".epub", ".txt", ".docx"} | ({".pdf"} if HAS_PDF else set())
OUTPUT_FORMATS = {"txt": "📄 TXT", "docx": "📝 DOCX", "epub": "📚 EPUB"}
SPLIT_PRESETS = [(0, "🚫 No split"), (100, "100 KB"), (300, "300 KB"), (500, "500 KB"),
                 (1024, "1 MB"), (2048, "2 MB"), (5120, "5 MB")]

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
# Rough UTF-8 growth of translated text vs. Latin source (used only for estimates)
EXPANSION = {**{k: 2.6 for k in ("hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "ne")},
             "ur": 1.9, "ar": 1.8, "ru": 1.8, "th": 2.6, "zh-CN": 0.9, "ja": 1.2, "ko": 1.2}

if DEFAULT_LANG not in LANGUAGES:
    DEFAULT_LANG = "hi"
if DEFAULT_FORMAT not in OUTPUT_FORMATS:
    DEFAULT_FORMAT = "txt"
if DEFAULT_SPLIT_KB and not (MIN_SPLIT_KB <= DEFAULT_SPLIT_KB <= MAX_SPLIT_KB):
    DEFAULT_SPLIT_KB = 500

# ═══════════════════════════════════════════════════════════════════════════
# 🔧 LOGGING  (stdout only — Render captures it; no log files on disk)
# ═══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
log = logging.getLogger("bot")

def validate_config() -> None:
    problems = []
    if not API_ID: problems.append("API_ID")
    if not API_HASH: problems.append("API_HASH")
    if not BOT_TOKEN or ":" not in BOT_TOKEN: problems.append("BOT_TOKEN")
    if not PUBLIC_MODE and not SECURITY_CODE and not OWNER_ID and not AUTHORIZED_USERS:
        problems.append("SECURITY_CODE (or OWNER_ID / AUTHORIZED_USERS / PUBLIC_MODE=1)")
    if problems:
        log.critical("Missing / invalid environment variables: %s", ", ".join(problems))
        log.critical("Set them in the Render dashboard → Environment, then redeploy.")
        sys.exit(1)
    if SECURITY_CODE and len(SECURITY_CODE) < 6:
        log.warning("SECURITY_CODE is very short — use at least 6 characters.")

validate_config()


# ═══════════════════════════════════════════════════════════════════════════
# 🧠 IN-MEMORY STORE  (intentionally non-persistent — resets on every deploy)
# ═══════════════════════════════════════════════════════════════════════════
class MemoryStore:
    """All state lives here. Nothing is written to disk or to a database.

    Bootstrapping on every start:
      • OWNER_ID           → owner (if set)
      • AUTHORIZED_USERS   → pre-authorised users
      • SECURITY_CODE      → anyone who sends it gets access (first one may
                             become owner if OWNER_ID is not set)
    """

    def __init__(self):
        self.owner_id: int = OWNER_ID
        self.security_code: str = SECURITY_CODE
        self.users: Dict[int, dict] = {}
        self.chats: Dict[int, dict] = {}
        self.stats = {"jobs": 0, "parts": 0, "chars": 0, "failed": 0, "cancelled": 0}
        self.booted = time.time()
        if OWNER_ID:
            self.authorize(OWNER_ID, "Owner")
        for uid in AUTHORIZED_USERS:
            self.authorize(uid, "Pre-authorized")

    @staticmethod
    def _default_user(name: str) -> dict:
        return {"name": name, "joined": int(time.time()), "role": "user",
                "lang": DEFAULT_LANG, "fmt": DEFAULT_FORMAT, "split": DEFAULT_SPLIT_KB,
                "stats": {"jobs": 0, "parts": 0, "chars": 0}}

    # ── users ──────────────────────────────────────────────────────────
    def user(self, uid: int) -> Optional[dict]:
        return self.users.get(uid)

    def ensure_user(self, uid: int, name: str) -> dict:
        """Used in PUBLIC_MODE: silently create a profile on first contact."""
        u = self.users.get(uid)
        if u is None:
            u = self._default_user(name or "User")
            self.users[uid] = u
        elif name:
            u["name"] = name
        if uid == self.owner_id:
            u["role"] = "owner"
        return u

    def is_authorized(self, uid: int) -> bool:
        return PUBLIC_MODE or uid in self.users

    def authorize(self, uid: int, name: str) -> dict:
        u = self.ensure_user(uid, name)
        if not self.owner_id:
            self.owner_id = uid
        if uid == self.owner_id:
            u["role"] = "owner"
        return u

    def revoke(self, uid: int) -> bool:
        if uid in self.users and uid != self.owner_id:
            del self.users[uid]
            return True
        return False

    def is_owner(self, uid: int) -> bool:
        return bool(self.owner_id) and uid == self.owner_id

    # ── prefs & stats ──────────────────────────────────────────────────
    def pref(self, uid: int, key: str, default=None):
        u = self.users.get(uid)
        return u.get(key, default) if u else default

    def set_pref(self, uid: int, key: str, value):
        u = self.users.get(uid)
        if u is None and PUBLIC_MODE:
            u = self.ensure_user(uid, "User")
        if u is not None:
            u[key] = value

    def bump(self, uid: int, parts: int, chars: int, failed=False, cancelled=False):
        u = self.users.get(uid)
        if u and not failed and not cancelled:
            u["stats"]["jobs"] += 1
            u["stats"]["parts"] += parts
            u["stats"]["chars"] += chars
        if failed:
            self.stats["failed"] += 1
        elif cancelled:
            self.stats["cancelled"] += 1
        else:
            self.stats["jobs"] += 1
            self.stats["parts"] += parts
            self.stats["chars"] += chars

    # ── chats ──────────────────────────────────────────────────────────
    def track_chat(self, chat_id: int, title: str, ctype: str):
        self.chats[chat_id] = {"title": title, "type": ctype, "added": int(time.time())}

    def untrack_chat(self, chat_id: int):
        self.chats.pop(chat_id, None)


store = MemoryStore()


# ═══════════════════════════════════════════════════════════════════════════
# 🎨 UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════
DIV = "━━━━━━━━━━━━━━━━━━━━"

def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)

def b(s) -> str:
    return f"<b>{esc(s)}</b>"

def code(s) -> str:
    return f"<code>{esc(s)}</code>"

def header(title: str, icon: str = "📚") -> str:
    return f"{icon} <b>{esc(title)}</b>\n{DIV}\n"

def progress_bar(ratio: float, width: int = 12) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    return "▰" * filled + "▱" * (width - filled)

def fmt_time(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def fmt_size(num: float) -> str:
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"

def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)

def lang_label(code_: str) -> str:
    name, flag = LANGUAGES.get(code_, (code_.upper(), "🌐"))
    return f"{flag} {name}"

def split_label(kb: int) -> str:
    if not kb:
        return "No split"
    return f"{kb / 1024:g} MB" if kb >= 1024 else f"{kb} KB"

def kb_rows(buttons: List[InlineKeyboardButton], per_row: int = 2) -> List[List[InlineKeyboardButton]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]

def home_keyboard(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⚙️ Settings", callback_data="nav:settings"),
         InlineKeyboardButton("📊 My Stats", callback_data="nav:mystats")],
        [InlineKeyboardButton("📋 Queue", callback_data="nav:queue"),
         InlineKeyboardButton("ℹ️ Help", callback_data="nav:help")],
    ]
    if store.is_owner(uid):
        rows.append([InlineKeyboardButton("👑 Owner Panel", callback_data="nav:owner")])
    return InlineKeyboardMarkup(rows)

def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="nav:home")]])

def user_prefs(uid: int) -> Tuple[str, str, int]:
    lang = store.pref(uid, "lang", DEFAULT_LANG)
    fmt = store.pref(uid, "fmt", DEFAULT_FORMAT)
    split = store.pref(uid, "split", DEFAULT_SPLIT_KB)
    if lang not in LANGUAGES: lang = DEFAULT_LANG
    if fmt not in OUTPUT_FORMATS: fmt = DEFAULT_FORMAT
    try: split = int(split)
    except (TypeError, ValueError): split = DEFAULT_SPLIT_KB
    return lang, fmt, split

def text_home(uid: int, name: str) -> str:
    lang, fmt, split = user_prefs(uid)
    return (
        header(BOT_NAME) +
        f"👋 Hello, {b(name)}!\n\n"
        f"Send me a document and I will\n"
        f"translate it into your language.\n\n"
        f"📥 {b('Input')}: {', '.join(sorted(e.lstrip('.').upper() for e in INPUT_EXTS))} (≤ {MAX_INPUT_MB} MB)\n"
        f"📤 {b('Output')}: TXT · DOCX · EPUB\n\n"
        f"{DIV}\n"
        f"🌐 Language: {b(lang_label(lang))}\n"
        f"📄 Format: {b(fmt.upper())}\n"
        f"✂️ Split: {b(split_label(split))}\n"
        f"{DIV}\n"
        f"<i>Tip: change defaults in ⚙️ Settings\nand use ⚡ Quick Start next time.</i>"
    )

def text_help() -> str:
    return (
        header("How it works", "ℹ️") +
        f"1️⃣ Send a {' / '.join(sorted(INPUT_EXTS))} file\n"
        "2️⃣ Choose language, format & split\n"
        "3️⃣ Watch the live dashboard\n"
        "4️⃣ Receive translated parts\n\n"
        f"{b('Commands')}\n"
        "/start – Home screen\n"
        "/settings – Default language, format, split\n"
        "/queue – Current queue status\n"
        "/cancel – Cancel your active job\n"
        "/mystats – Your usage statistics\n"
        "/help – This message\n\n"
        f"{b('Split size')}\n"
        "Large books are delivered in parts.\n"
        f"Custom size: {MIN_SPLIT_KB} KB – {MAX_SPLIT_KB // 1024} MB\n"
        "e.g. <code>750</code>, <code>2 MB</code>, <code>900kb</code>\n\n"
        f"{b('Note')}\n"
        "<i>This bot keeps no database. Settings and\n"
        "statistics reset whenever the server restarts.</i>"
    )

def text_settings(uid: int) -> str:
    lang, fmt, split = user_prefs(uid)
    return (
        header("Settings", "⚙️") +
        f"🌐 Language: {b(lang_label(lang))}\n"
        f"📄 Format: {b(fmt.upper())}\n"
        f"✂️ Split size: {b(split_label(split))}\n"
        f"{DIV}\n"
        "<i>These are used by ⚡ Quick Start\nand pre-selected in the wizard.\n"
        "Settings are kept in memory only.</i>"
    )

def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Language", callback_data="st:lang"),
         InlineKeyboardButton("📄 Format", callback_data="st:fmt")],
        [InlineKeyboardButton("✂️ Split size", callback_data="st:spl")],
        [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
    ])

def language_keyboard(prefix: str, selected: str, back_cb: str) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(f"{'✅ ' if c == selected else ''}{flag} {name}", callback_data=f"{prefix}{c}")
            for c, (name, flag) in LANGUAGES.items()]
    rows = kb_rows(btns, 3)
    rows.append([InlineKeyboardButton("◀️ Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)

def format_keyboard(prefix: str, selected: str, back_cb: str) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(f"{'✅ ' if f == selected else ''}{label}", callback_data=f"{prefix}{f}")
            for f, label in OUTPUT_FORMATS.items()]
    return InlineKeyboardMarkup(kb_rows(btns, 3) + [[InlineKeyboardButton("◀️ Back", callback_data=back_cb)]])

def split_keyboard(prefix: str, selected: int, back_cb: str, custom_cb: Optional[str]) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(f"{'✅ ' if kb == selected else ''}{label}", callback_data=f"{prefix}{kb}")
            for kb, label in SPLIT_PRESETS]
    if selected and selected not in dict(SPLIT_PRESETS):
        btns.append(InlineKeyboardButton(f"✅ {split_label(selected)}", callback_data=f"{prefix}{selected}"))
    rows = kb_rows(btns, 2)
    if custom_cb:
        rows.append([InlineKeyboardButton("✏️ Custom size", callback_data=custom_cb)])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════════════════════════
# 📬 FLOOD-SAFE MESSAGE UTILITIES
# ═══════════════════════════════════════════════════════════════════════════
class LiveMessage:
    """Rate-limited, flood-safe editor for a single Telegram message."""

    def __init__(self, message: Optional[Message], min_interval: float = EDIT_INTERVAL):
        self.msg = message
        self.min_interval = min_interval
        self._last_edit = 0.0
        self._last_text = ""
        self._lock = asyncio.Lock()
        self.alive = message is not None

    async def update(self, text: str, keyboard: Optional[InlineKeyboardMarkup] = None, force: bool = False):
        if not self.alive:
            return
        now = time.time()
        if not force and (now - self._last_edit) < self.min_interval:
            return
        if text == self._last_text and not force:
            return
        async with self._lock:
            try:
                await self.msg.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
                self._last_edit = time.time()
                self._last_text = text
            except MessageNotModified:
                self._last_edit = time.time()
                self._last_text = text
            except FloodWait as e:
                # Back off: do not attempt another edit until the wait expires
                self._last_edit = time.time() + float(e.value)
            except MessageIdInvalid:
                self.alive = False
            except RPCError as e:
                if "MESSAGE_ID_INVALID" in str(e) or "not modified" in str(e).lower():
                    self.alive = False
                else:
                    log.warning("edit failed: %s", e)
            except Exception as e:
                log.warning("edit failed: %s", e)

async def safe_send(chat_id: int, text: str, **kwargs) -> Optional[Message]:
    if not chat_id:
        return None
    for _ in range(3):
        try:
            return await app.send_message(chat_id, text, disable_web_page_preview=True, **kwargs)
        except FloodWait as e:
            await asyncio.sleep(min(float(e.value) + 1, 120))
        except Exception as e:
            log.debug("safe_send(%s) failed: %s", chat_id, e)
            return None
    return None

async def safe_send_document(chat_id: int, path: str, caption: str, **kwargs) -> Optional[Message]:
    if not chat_id:
        return None
    for attempt in range(4):
        try:
            return await app.send_document(chat_id, path, caption=caption[:1000], **kwargs)
        except FloodWait as e:
            await asyncio.sleep(min(float(e.value) + 2, 120))
        except RPCError as e:
            # reply_to_message_id pointing at a deleted topic etc. → retry without it
            if kwargs and attempt == 0:
                kwargs = {}
                continue
            log.warning("send_document(%s) failed: %s", chat_id, e)
            await asyncio.sleep(2)
        except Exception as e:
            log.warning("send_document(%s) failed: %s", chat_id, e)
            await asyncio.sleep(2)
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 📖 TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════
_BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "div", "td", "pre"]

def extract_text_epub(path: str) -> str:
    try:
        try:
            book = epub.read_epub(path, options={"ignore_ncx": True})
        except TypeError:
            book = epub.read_epub(path)
    except Exception as e:
        log.warning("epub read failed: %s", e)
        return ""

    items = []
    for entry in getattr(book, "spine", []) or []:
        idref = entry[0] if isinstance(entry, (tuple, list)) else entry
        item = book.get_item_with_id(idref)
        if item is not None and item.get_type() == ebooklib.ITEM_DOCUMENT:
            items.append(item)
    if not items:
        items = [i for i in book.get_items() if i.get_type() == ebooklib.ITEM_DOCUMENT]

    sections, seen = [], set()
    for item in items:
        try:
            soup = BeautifulSoup(item.get_body_content(), "lxml")
        except Exception:
            try:
                soup = BeautifulSoup(item.get_body_content(), "html.parser")
            except Exception:
                continue
        for junk in soup(["script", "style", "nav", "svg"]):
            junk.decompose()
        blocks = []
        for el in soup.find_all(_BLOCK_TAGS):
            if el.find(_BLOCK_TAGS):
                continue
            txt = el.get_text(" ", strip=True)
            if txt:
                if el.name.startswith("h"):
                    blocks.append("")
                blocks.append(txt)
        text = "\n".join(blocks).strip() if blocks else soup.get_text("\n", strip=True)
        key = hash(text[:400])
        if len(text) > 30 and key not in seen:
            seen.add(key)
            sections.append(text)
    return "\n\n".join(sections)

def extract_text_txt(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
    text = None
    for enc in encodings:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")

def extract_text_docx(path: str) -> str:
    try:
        d = docx.Document(path)
        parts = [p.text.rstrip() for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    except Exception as e:
        log.warning("docx read failed: %s", e)
        return ""

def extract_text_pdf(path: str) -> str:
    if not HAS_PDF:
        return ""
    try:
        reader = PdfReader(path)
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                return ""
        pages = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                pages.append(t.strip())
        return "\n\n".join(pages)
    except Exception as e:
        log.warning("pdf read failed: %s", e)
        return ""

EXTRACTORS = {".epub": extract_text_epub, ".txt": extract_text_txt,
              ".docx": extract_text_docx, ".pdf": extract_text_pdf}

def normalise_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\u00ad", "")
    text = re.sub(r"[ \t\u00a0]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════
# ✂️ SPLITTING & SMART CHUNKING
# ═══════════════════════════════════════════════════════════════════════════
def split_text_by_size(text: str, max_kb: int, factor: float = 1.0) -> List[str]:
    """Split on line boundaries so that each part's *estimated output size*
    (len_utf8 × factor) stays below max_kb. factor accounts for translated
    scripts (Devanagari etc.) taking ~2-3× more bytes than Latin source."""
    if max_kb <= 0:
        return [text]
    max_bytes = max(1024, int(max_kb * 1024 / max(factor, 0.1)))
    parts, current, size = [], [], 0
    for line in text.split("\n"):
        lb = len(line.encode("utf-8")) + 1
        if size + lb > max_bytes and current:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += lb
    if current:
        parts.append("\n".join(current))
    return parts or [text]

_SENTENCE_RE = re.compile(r"(?<=[.!?।。！？])\s+")

def _split_long_line(line: str, limit: int) -> List[str]:
    pieces, buf = [], ""
    for sent in _SENTENCE_RE.split(line):
        if not sent:
            continue
        if len(sent) > limit:
            if buf:
                pieces.append(buf); buf = ""
            pieces.extend(sent[i:i + limit] for i in range(0, len(sent), limit))
            continue
        if len(buf) + len(sent) + 1 > limit and buf:
            pieces.append(buf); buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf:
        pieces.append(buf)
    return pieces

def build_chunks(text: str, limit: int = CHUNK_SIZE) -> List[List[str]]:
    """Group lines into chunks of ≤ limit chars. Each chunk is a *list of
    lines*; they are sent to the API as separate segments so paragraph
    structure survives translation exactly."""
    chunks: List[List[str]] = []
    buf: List[str] = []
    size = 0

    def flush():
        nonlocal buf, size
        if buf:
            chunks.append(buf)
            buf, size = [], 0

    for line in text.split("\n"):
        line = line.rstrip()
        if len(line) > limit:
            flush()
            for piece in _split_long_line(line, limit):
                chunks.append([piece])
            continue
        if size + len(line) + 1 > limit:
            flush()
        buf.append(line)
        size += len(line) + 1
    flush()
    return chunks


# ═══════════════════════════════════════════════════════════════════════════
# 📝 OUTPUT WRITERS
# ═══════════════════════════════════════════════════════════════════════════
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

def _xml_safe(s: str) -> str:
    return _XML_ILLEGAL.sub("", s)

def write_txt(text: str, path: str, title: str, lang: str):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def write_docx(text: str, path: str, title: str, lang: str):
    d = docx.Document()
    d.add_heading(_xml_safe(title), level=1)
    for line in text.split("\n"):
        line = line.strip()
        if line:
            d.add_paragraph(_xml_safe(line))
    d.save(path)

def write_epub(text: str, path: str, title: str, lang: str):
    book = epub.EpubBook()
    book.set_identifier(uuid.uuid4().hex)
    book.set_title(title)
    book.set_language(lang.split("-")[0])
    book.add_author(BOT_NAME)
    lines = [l for l in text.split("\n")]
    per_section = 400
    chapters = []
    groups = [lines[i:i + per_section] for i in range(0, len(lines), per_section)] or [[]]
    for n, group in enumerate(groups, 1):
        body = "".join(f"<p>{html.escape(_xml_safe(l.strip()))}</p>" for l in group if l.strip())
        ch = epub.EpubHtml(title=f"Section {n}", file_name=f"section_{n:03d}.xhtml", lang=lang.split("-")[0])
        ch.content = (f"<html xmlns=\"http://www.w3.org/1999/xhtml\"><head><title>{html.escape(title)}</title></head>"
                      f"<body><h2>{html.escape(title)}{' — Section ' + str(n) if len(groups) > 1 else ''}</h2>{body}</body></html>")
        book.add_item(ch)
        chapters.append(ch)
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters
    epub.write_epub(path, book)

WRITERS = {"txt": write_txt, "docx": write_docx, "epub": write_epub}


# ═══════════════════════════════════════════════════════════════════════════
# 🧩 JOB MODEL & QUEUE
# ═══════════════════════════════════════════════════════════════════════════
class JobCancelled(Exception):
    pass

@dataclass
class Job:
    job_id: str
    chat_id: int
    user_id: int
    user_name: str
    file_path: str
    ext: str
    novel_name: str
    file_size: int
    lang: str = DEFAULT_LANG
    out_format: str = DEFAULT_FORMAT
    split_kb: int = DEFAULT_SPLIT_KB
    status: str = "pending"
    created: float = field(default_factory=time.time)
    msg: Optional[Message] = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def lang_name(self) -> str:
        return LANGUAGES.get(self.lang, (self.lang, ""))[0]

    @property
    def out_dir(self) -> str:
        return os.path.join(STORAGE_DIR, f"{self.chat_id}_{self.job_id}")

    def cleanup(self):
        try:
            if self.file_path and os.path.exists(self.file_path):
                os.remove(self.file_path)
        except Exception:
            pass
        shutil.rmtree(self.out_dir, ignore_errors=True)


PENDING: Dict[str, Job] = {}          # wizard not finished yet
QUEUE: List[Job] = []                 # waiting for the worker
ACTIVE: Optional[Job] = None          # currently translating
QUEUE_WAKE: Optional[asyncio.Event] = None
USER_STATE: Dict[int, dict] = {}      # chat_id → {"state": ..., "job_id": ...}
SHUTTING_DOWN = False

def queue_position(job: Job) -> int:
    try:
        return QUEUE.index(job) + 1
    except ValueError:
        return 0

def user_job_count(uid: int) -> int:
    n = sum(1 for j in QUEUE if j.user_id == uid)
    if ACTIVE and ACTIVE.user_id == uid:
        n += 1
    return n

def summary_block(job: Job) -> str:
    return (
        f"📘 File: {b(job.novel_name)}\n"
        f"💾 Size: {b(fmt_size(job.file_size))}\n"
        f"🌐 Language: {b(lang_label(job.lang))}\n"
        f"📄 Format: {b(job.out_format.upper())}\n"
        f"✂️ Split: {b(split_label(job.split_kb))}\n"
    )

def queued_text(job: Job, pos: int) -> str:
    return (
        header("Queued", "⏳") + summary_block(job) + f"{DIV}\n"
        f"📍 Position: {b('#' + str(pos))}\n"
        f"{'▶️ Next up!' if pos == 1 else '🕐 Please wait…'}"
    )

def cancel_kb(job: Job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel job", callback_data=f"qx:{job.job_id}")]])

async def enqueue(job: Job, message: Message):
    job.msg = message
    job.status = "queued"
    QUEUE.append(job)
    await LiveMessage(message).update(queued_text(job, queue_position(job)), cancel_kb(job), force=True)
    if QUEUE_WAKE:
        QUEUE_WAKE.set()

async def notify_positions():
    for i, j in enumerate(list(QUEUE), 1):
        await LiveMessage(j.msg).update(queued_text(j, i), cancel_kb(j), force=True)

async def queue_worker():
    global ACTIVE
    log.info("Queue worker started")
    while not SHUTTING_DOWN:
        if not QUEUE:
            QUEUE_WAKE.clear()
            await QUEUE_WAKE.wait()
            continue
        job = QUEUE.pop(0)
        ACTIVE = job
        job.status = "running"
        asyncio.create_task(notify_positions())
        try:
            await process_job(job)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Worker error: %s", e)
        finally:
            ACTIVE = None
            job.cleanup()

async def janitor():
    while not SHUTTING_DOWN:
        await asyncio.sleep(300)
        now = time.time()
        for jid, job in list(PENDING.items()):
            if now - job.created > PENDING_TTL:
                job.cleanup()
                PENDING.pop(jid, None)
                USER_STATE.pop(job.chat_id, None)
                await LiveMessage(job.msg).update(
                    header("Session expired", "⌛") + f"📘 {b(job.novel_name)}\nPlease send the file again.",
                    back_home_kb(), force=True)
        # orphaned temp files (e.g. after a crash mid-download)
        active_paths = {j.file_path for j in list(PENDING.values()) + QUEUE} | ({ACTIVE.file_path} if ACTIVE else set())
        for root in (INBOX_DIR, STORAGE_DIR):
            try:
                for name in os.listdir(root):
                    p = os.path.join(root, name)
                    if p in active_paths:
                        continue
                    try:
                        if now - os.path.getmtime(p) > 2 * 3600:
                            if os.path.isdir(p):
                                shutil.rmtree(p, ignore_errors=True)
                            else:
                                os.remove(p)
                    except Exception:
                        pass
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# 🌐 TRANSLATION ENGINE  (async aiohttp → Google web endpoint, thread fallback)
# ═══════════════════════════════════════════════════════════════════════════
HTTP: Optional[aiohttp.ClientSession] = None
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_GT_URL = "https://translate.googleapis.com/translate_a/t"

async def get_http() -> aiohttp.ClientSession:
    global HTTP
    if HTTP is None or HTTP.closed:
        HTTP = aiohttp.ClientSession(
            headers={"User-Agent": _UA},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            connector=aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT + 4, ttl_dns_cache=300),
        )
    return HTTP

class TranslateError(Exception):
    pass

async def gt_translate_lines(lines: List[str], target: str) -> List[str]:
    """Translate a list of segments in a single request. Empty segments are
    passed through untouched. Returns list of the same length."""
    idx = [i for i, l in enumerate(lines) if l.strip()]
    if not idx:
        return list(lines)
    sess = await get_http()
    data = [("q", lines[i]) for i in idx]
    async with sess.post(_GT_URL, params={"client": "gtx", "sl": "auto", "tl": target}, data=data) as resp:
        if resp.status == 429 or resp.status >= 500:
            raise TranslateError(f"HTTP {resp.status}")
        if resp.status != 200:
            raise TranslateError(f"HTTP {resp.status}")
        try:
            payload = json.loads(await resp.text())
        except Exception as e:
            raise TranslateError(f"bad json: {e}")
    if not isinstance(payload, list) or len(payload) != len(idx):
        raise TranslateError("unexpected payload shape")
    out = list(lines)
    for pos, item in zip(idx, payload):
        # sl=auto → [text, detected_lang]; fixed sl → "text"
        txt = item[0] if isinstance(item, list) else item
        if not isinstance(txt, str):
            raise TranslateError("non-string result")
        out[pos] = html.unescape(txt)
    return out

def _fallback_translate(text: str, target: str) -> str:
    if not HAS_FALLBACK:
        raise TranslateError("no fallback")
    res = _FallbackTranslator(source="auto", target=target).translate(text)
    if not res or "Error 500" in res or "Server Error" in res:
        raise TranslateError("fallback bad result")
    return res

class TranslationEngine:
    def __init__(self, target: str, concurrency: int = CONCURRENCY_LIMIT):
        self.target = target
        self.sem = asyncio.Semaphore(concurrency)
        self.total = 0
        self.done = 0
        self.failed = 0
        self.started = time.time()

    async def _translate_one(self, idx: int, lines: List[str], cancel: asyncio.Event) -> Tuple[int, List[str]]:
        if not any(l.strip() for l in lines):
            self.done += 1
            return idx, lines
        async with self.sem:
            if cancel.is_set():
                raise JobCancelled()
            delay = 1.5
            for attempt in range(MAX_RETRIES):
                if cancel.is_set():
                    raise JobCancelled()
                try:
                    res = await gt_translate_lines(lines, self.target)
                    self.done += 1
                    return idx, res
                except (TranslateError, aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
                    log.debug("chunk %d attempt %d failed: %s", idx, attempt + 1, e)
                    if attempt == MAX_RETRIES - 2:            # penultimate: try the thread fallback
                        try:
                            joined = "\n".join(lines)
                            res_text = await asyncio.wait_for(
                                asyncio.to_thread(_fallback_translate, joined, self.target), REQUEST_TIMEOUT + 20)
                            self.done += 1
                            return idx, [res_text]
                        except Exception as e2:
                            log.debug("fallback failed: %s", e2)
                    await asyncio.sleep(delay + random.uniform(0, 1.0))
                    delay = min(delay * 2, 20)
            self.done += 1
            self.failed += 1
            return idx, lines                                 # keep original text

    async def run(self, chunks: List[List[str]], cancel: asyncio.Event, on_progress) -> str:
        self.total = len(chunks)
        self.started = time.time()
        results: List[Optional[List[str]]] = [None] * self.total
        tasks = [asyncio.create_task(self._translate_one(i, c, cancel)) for i, c in enumerate(chunks)]
        try:
            for fut in asyncio.as_completed(tasks):
                idx, lines = await fut
                results[idx] = lines
                await on_progress(self)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return "\n".join("\n".join(results[i] if results[i] is not None else chunks[i]) for i in range(self.total))

    @property
    def speed(self) -> float:
        return self.done / max(time.time() - self.started, 0.001)

    @property
    def eta(self) -> float:
        return (self.total - self.done) / self.speed if self.speed > 0 and self.done else 0.0

def dashboard_text(job: Job, part: int, total_parts: int, eng: TranslationEngine, phase: str) -> str:
    ratio = eng.done / eng.total if eng.total else 0
    return (
        header("Live Dashboard", "🖥") +
        f"📘 {b(job.novel_name)}\n"
        f"🌐 {lang_label(job.lang)}  ·  📄 {job.out_format.upper()}\n"
        f"📦 Part {b(f'{part} / {total_parts}')}\n"
        f"{DIV}\n"
        f"{progress_bar(ratio)} {b(f'{ratio * 100:.1f}%')}\n"
        f"🧩 Chunks: {eng.done} / {eng.total}\n"
        f"⚡ Speed: {eng.speed:.2f} chunk/s\n"
        f"⏱ Elapsed: {fmt_time(time.time() - eng.started)}  ·  ETA: {fmt_time(eng.eta)}\n"
        + (f"⚠️ Retries exhausted: {eng.failed}\n" if eng.failed else "") +
        f"{DIV}\n"
        f"<i>{esc(phase)}</i>"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 🚀 MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════
async def create_backup_topic(title: str) -> Optional[int]:
    if not BACKUP_GROUP_ID:
        return None
    try:
        peer = await app.resolve_peer(BACKUP_GROUP_ID)
        result = await app.invoke(functions.channels.CreateForumTopic(
            channel=peer, title=title[:128], random_id=random.randint(1, 2 ** 62)))
        for upd in result.updates:
            msg = getattr(upd, "message", None)
            if msg is not None and hasattr(msg, "id"):
                return msg.id
    except Exception as e:
        log.debug("create_backup_topic failed (forum topics disabled?): %s", e)
    return None

def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return name[:90] or "Document"

async def process_job(job: Job):
    live = LiveMessage(job.msg)
    out_dir = job.out_dir
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    total_chars = 0
    total_parts = 0
    failed_chunks = 0

    try:
        await live.update(header("Processing", "🔄") + summary_block(job) + f"{DIV}\n📖 Extracting text…",
                          cancel_kb(job), force=True)
        if not os.path.exists(job.file_path):
            raise FileNotFoundError("source file vanished (server restarted?)")
        extractor = EXTRACTORS.get(job.ext, extract_text_txt)
        text = normalise_text(await asyncio.to_thread(extractor, job.file_path))
        if len(text) < 20:
            job.status = "failed"
            store.bump(job.user_id, 0, 0, failed=True)
            await live.update(header("Failed", "❌") + summary_block(job) + f"{DIV}\n"
                              "No readable text found.\n<i>Scanned PDFs / image-only files are not supported.</i>",
                              back_home_kb(), force=True)
            return
        if job.cancel.is_set():
            raise JobCancelled()

        parts = split_text_by_size(text, job.split_kb, EXPANSION.get(job.lang, 1.15))
        total_parts = len(parts)
        total_chars = len(text)

        topic_id = await create_backup_topic(job.novel_name)
        backup_kwargs = {"reply_to_message_id": topic_id} if topic_id else {}
        if BACKUP_GROUP_ID:
            await safe_send(BACKUP_GROUP_ID,
                            header("New Job", "📥") + summary_block(job) +
                            f"👤 User: {b(job.user_name)} ({code(job.user_id)})\n"
                            f"🧩 Parts: {b(total_parts)}  ·  🔤 Chars: {b(fmt_int(total_chars))}",
                            **backup_kwargs)

        for i, part_text in enumerate(parts, 1):
            if job.cancel.is_set():
                raise JobCancelled()
            chunks = build_chunks(part_text)
            eng = TranslationEngine(job.lang)

            async def on_progress(e: TranslationEngine, _i=i):
                if job.cancel.is_set():
                    raise JobCancelled()
                await live.update(dashboard_text(job, _i, total_parts, e, "Translating…"), cancel_kb(job))

            translated = await eng.run(chunks, job.cancel, on_progress)
            failed_chunks += eng.failed
            await live.update(dashboard_text(job, i, total_parts, eng, f"Building .{job.out_format} file…"),
                              cancel_kb(job), force=True)

            part_title = f"{job.novel_name} — Part {i} of {total_parts}" if total_parts > 1 else job.novel_name
            base = f"Part {i} of {total_parts} | {job.novel_name} [{job.lang_name}]" if total_parts > 1 \
                else f"{job.novel_name} [{job.lang_name}]"
            out_path = os.path.join(out_dir, f"{safe_filename(base)}.{job.out_format}")
            await asyncio.to_thread(WRITERS[job.out_format], translated, out_path, part_title, job.lang)

            caption = (f"📘 {b(job.novel_name)}\n"
                       f"📦 Part {i} / {total_parts}  ·  🌐 {lang_label(job.lang)}\n"
                       f"💾 {fmt_size(os.path.getsize(out_path))}")
            sent = await safe_send_document(job.chat_id, out_path, caption)
            if sent is None:
                log.error("Could not deliver part %d of job %s", i, job.job_id)
                await safe_send(job.chat_id, f"⚠️ Part {i} could not be delivered. Please retry later.")
            if BACKUP_GROUP_ID:
                await safe_send_document(BACKUP_GROUP_ID, out_path,
                                         caption + f"\n👤 {b(job.user_name)} ({code(job.user_id)})", **backup_kwargs)
            try:
                os.remove(out_path)          # free disk as we go
            except Exception:
                pass

        job.status = "done"
        store.bump(job.user_id, total_parts, total_chars)
        elapsed = time.time() - started
        await live.update(
            header("Completed", "✅") + summary_block(job) + f"{DIV}\n"
            f"🧩 Parts: {b(total_parts)}\n"
            f"🔤 Characters: {b(fmt_int(total_chars))}\n"
            f"⏱ Time: {b(fmt_time(elapsed))}\n"
            + (f"⚠️ {failed_chunks} chunk(s) kept original text\n" if failed_chunks else "") +
            f"{DIV}\n🚀 Thank you for using {esc(BOT_NAME)}!",
            back_home_kb(), force=True)

    except JobCancelled:
        job.status = "cancelled"
        store.bump(job.user_id, 0, 0, cancelled=True)
        reason = "Server is restarting (new deploy). Please resend the file in a minute." if SHUTTING_DOWN \
            else "Job stopped by user."
        await live.update(header("Cancelled", "🛑") + summary_block(job) + f"{DIV}\n{reason}", back_home_kb(), force=True)
        if BACKUP_GROUP_ID and not SHUTTING_DOWN:
            await safe_send(BACKUP_GROUP_ID, f"🛑 Job cancelled: {b(job.novel_name)} by {code(job.user_id)}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        job.status = "failed"
        log.exception("Job %s failed: %s", job.job_id, e)
        store.bump(job.user_id, 0, 0, failed=True)
        await live.update(header("Error", "⚠️") + summary_block(job) + f"{DIV}\n"
                          f"Something went wrong: {code(type(e).__name__)}\nPlease try again.",
                          back_home_kb(), force=True)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# 🤖 CLIENT & FILTERS
# ═══════════════════════════════════════════════════════════════════════════
app = Client(
    "novel_translator_pro",
    api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
    in_memory=True,                    # ← no .session file on disk
    workdir=BASE_DIR,
    parse_mode=enums.ParseMode.HTML,
    sleep_threshold=30,                # auto-sleep small FloodWaits
    max_concurrent_transmissions=2,
)

async def _auth_msg(_, __, m: Message):
    return bool(m.from_user and store.is_authorized(m.from_user.id))

async def _auth_cb(_, __, q: CallbackQuery):
    return bool(q.from_user and store.is_authorized(q.from_user.id))

async def _owner_msg(_, __, m: Message):
    return bool(m.from_user and store.is_owner(m.from_user.id))

authorized = filters.create(_auth_msg)
authorized_cb = filters.create(_auth_cb)
owner_only = filters.create(_owner_msg)
PRIVATE = filters.private & filters.incoming
OUTPUT_NAME_RE = re.compile(r"^Part\s*\d+\s*(of\s*\d+)?\s*\|", re.IGNORECASE)

def touch_user(m: Message) -> None:
    """In PUBLIC_MODE make sure a profile exists so prefs/stats work."""
    if PUBLIC_MODE and m.from_user:
        store.ensure_user(m.from_user.id, m.from_user.first_name or "User")


# ═══════════════════════════════════════════════════════════════════════════
# 🔒 UNAUTHORIZED
# ═══════════════════════════════════════════════════════════════════════════
@app.on_message(PRIVATE & ~authorized)
async def unauthorized_message(_, m: Message):
    if not m.from_user:
        return
    if m.text and store.security_code and m.text.strip() == store.security_code:
        store.authorize(m.from_user.id, m.from_user.first_name or "User")
        role = "👑 Owner" if store.is_owner(m.from_user.id) else "👤 User"
        try:
            await m.delete()               # don't leave the code in chat history
        except Exception:
            pass
        await m.reply(header("Access Granted", "✅") +
                      f"Welcome, {b(m.from_user.first_name)}!\nRole: {b(role)}\n\nSend /start to begin.")
        return
    await m.reply(header("Security Locked", "🔒") +
                  "This bot is private.\nSend the <b>security code</b> to unlock.\n\n"
                  f"<i>Your ID: {code(m.from_user.id)}</i>")

@app.on_callback_query(~authorized_cb)
async def unauthorized_callback(_, q: CallbackQuery):
    await q.answer("🔒 Not authorized. Send the security code first.", show_alert=True)


# ═══════════════════════════════════════════════════════════════════════════
# 💬 COMMANDS
# ═══════════════════════════════════════════════════════════════════════════
@app.on_message(filters.command("start") & PRIVATE & authorized)
async def cmd_start(_, m: Message):
    touch_user(m)
    USER_STATE.pop(m.chat.id, None)
    await m.reply(text_home(m.from_user.id, m.from_user.first_name or "there"),
                  reply_markup=home_keyboard(m.from_user.id))

@app.on_message(filters.command("help") & PRIVATE & authorized)
async def cmd_help(_, m: Message):
    await m.reply(text_help(), reply_markup=back_home_kb())

@app.on_message(filters.command("settings") & PRIVATE & authorized)
async def cmd_settings(_, m: Message):
    touch_user(m)
    await m.reply(text_settings(m.from_user.id), reply_markup=settings_keyboard())

def text_queue(uid: int) -> str:
    lines = [header("Queue Status", "📋")]
    if ACTIVE:
        who = "you" if ACTIVE.user_id == uid else esc(ACTIVE.user_name)
        lines.append(f"▶️ Running: {b(ACTIVE.novel_name)}\n   <i>by {who}</i>\n")
    else:
        lines.append("▶️ Running: <i>nothing</i>\n")
    if QUEUE:
        lines.append(f"⏳ Waiting: {b(len(QUEUE))}")
        for i, j in enumerate(QUEUE[:10], 1):
            mark = "🟢" if j.user_id == uid else "⚪️"
            lines.append(f"{mark} #{i} {esc(j.novel_name[:32])}")
        if len(QUEUE) > 10:
            lines.append(f"… and {len(QUEUE) - 10} more")
    else:
        lines.append("⏳ Waiting: <i>empty</i>")
    return "\n".join(lines)

@app.on_message(filters.command("queue") & PRIVATE & authorized)
async def cmd_queue(_, m: Message):
    await m.reply(text_queue(m.from_user.id), reply_markup=back_home_kb())

@app.on_message(filters.command("cancel") & PRIVATE & authorized)
async def cmd_cancel(_, m: Message):
    uid = m.from_user.id
    n = 0
    if ACTIVE and ACTIVE.user_id == uid:
        ACTIVE.cancel.set(); n += 1
    for j in [j for j in QUEUE if j.user_id == uid]:
        QUEUE.remove(j); j.status = "cancelled"; j.cleanup(); n += 1
        await LiveMessage(j.msg).update(header("Cancelled", "🛑") + summary_block(j), back_home_kb(), force=True)
    for jid, j in [(k, v) for k, v in PENDING.items() if v.user_id == uid]:
        PENDING.pop(jid, None); j.cleanup(); n += 1
    USER_STATE.pop(m.chat.id, None)
    if n:
        await notify_positions()
        await m.reply(f"🛑 Cancelled {b(n)} job(s).")
    else:
        await m.reply("ℹ️ You have no active or queued jobs.")

def text_mystats(uid: int) -> str:
    u = store.user(uid) or {}
    s = u.get("stats", {})
    since = time.strftime("%d %b %Y", time.localtime(u.get("joined", time.time())))
    return (
        header("My Statistics", "📊") +
        f"👤 {b(u.get('name', 'User'))}  ·  {code(uid)}\n"
        f"🎖 Role: {b(u.get('role', 'user').title())}\n"
        f"📅 Since restart: {b(since)}\n"
        f"{DIV}\n"
        f"📚 Files translated: {b(s.get('jobs', 0))}\n"
        f"🧩 Parts delivered: {b(s.get('parts', 0))}\n"
        f"🔤 Characters: {b(fmt_int(s.get('chars', 0)))}\n"
        f"{DIV}\n<i>Stats are in-memory and reset on redeploy.</i>"
    )

@app.on_message(filters.command("mystats") & PRIVATE & authorized)
async def cmd_mystats(_, m: Message):
    touch_user(m)
    await m.reply(text_mystats(m.from_user.id), reply_markup=back_home_kb())

@app.on_message(filters.command("id") & PRIVATE)
async def cmd_id(_, m: Message):
    await m.reply(f"🆔 Your Telegram ID: {code(m.from_user.id)}")

# ── Owner commands ──────────────────────────────────────────────────────────
def text_owner() -> str:
    s = store.stats
    up = fmt_time(time.time() - store.booted)
    return (
        header("Owner Panel", "👑") +
        f"⏱ Uptime: {b(up)}\n"
        f"👥 Users: {b(len(store.users))}  ·  🔓 Public: {b('yes' if PUBLIC_MODE else 'no')}\n"
        f"💬 Tracked chats: {b(len(store.chats))}\n"
        f"📚 Jobs done: {b(s['jobs'])}  ·  ❌ Failed: {b(s['failed'])}  ·  🛑 Cancelled: {b(s['cancelled'])}\n"
        f"🧩 Parts: {b(s['parts'])}  ·  🔤 Chars: {b(fmt_int(s['chars']))}\n"
        f"▶️ Active: {b(ACTIVE.novel_name if ACTIVE else '—')}\n"
        f"⏳ Queue: {b(len(QUEUE))}  ·  📝 Pending wizards: {b(len(PENDING))}\n"
        f"🗄 Backup group: {b(BACKUP_GROUP_ID or 'disabled')}\n"
        f"{DIV}\n"
        f"{b('Commands')}\n"
        "/users – list users\n"
        "/adduser &lt;id&gt; · /deluser &lt;id&gt;\n"
        "/broadcast &lt;text&gt; (or reply)\n"
        "/setcode &lt;new code&gt;\n"
        "/links – admin invite links\n"
        "/stats – this panel\n"
        f"{DIV}\n<i>⚠️ No database: users added at runtime are lost on\n"
        "restart. Put permanent IDs in AUTHORIZED_USERS env.</i>"
    )

@app.on_message(filters.command("stats") & PRIVATE & owner_only)
async def cmd_stats(_, m: Message):
    await m.reply(text_owner(), reply_markup=back_home_kb())

@app.on_message(filters.command("users") & PRIVATE & owner_only)
async def cmd_users(_, m: Message):
    users = store.users
    lines = [header(f"Users ({len(users)})", "👥")]
    for uid, u in list(users.items())[:60]:
        crown = "👑 " if u.get("role") == "owner" else ""
        lines.append(f"{crown}{esc(u.get('name', 'User'))} — {code(uid)} · {u['stats'].get('jobs', 0)} jobs")
    if len(users) > 60:
        lines.append(f"… and {len(users) - 60} more")
    if not users:
        lines.append("<i>No users yet.</i>")
    await m.reply("\n".join(lines))

@app.on_message(filters.command("adduser") & PRIVATE & owner_only)
async def cmd_adduser(_, m: Message):
    if len(m.command) < 2 or not m.command[1].lstrip("-").isdigit():
        return await m.reply("Usage: <code>/adduser 123456789</code>")
    uid = int(m.command[1])
    store.authorize(uid, "Added by owner")
    await m.reply(f"✅ User {code(uid)} authorized.\n"
                  f"<i>Add to AUTHORIZED_USERS env to keep after restarts.</i>")
    await safe_send(uid, header("Access Granted", "✅") + "You have been authorized.\nSend /start to begin.")

@app.on_message(filters.command("deluser") & PRIVATE & owner_only)
async def cmd_deluser(_, m: Message):
    if len(m.command) < 2 or not m.command[1].lstrip("-").isdigit():
        return await m.reply("Usage: <code>/deluser 123456789</code>")
    uid = int(m.command[1])
    ok = store.revoke(uid)
    await m.reply(f"🗑 User {code(uid)} removed." if ok else "⚠️ Not found (or is the owner).")

@app.on_message(filters.command("setcode") & PRIVATE & owner_only)
async def cmd_setcode(_, m: Message):
    if len(m.command) < 2:
        return await m.reply("Usage: <code>/setcode NewSecret123</code>")
    new_code = m.text.split(None, 1)[1].strip()
    if len(new_code) < 6:
        return await m.reply("⚠️ Code must be at least 6 characters.")
    store.security_code = new_code
    try:
        await m.delete()
    except Exception:
        pass
    await m.reply("🔐 Security code updated (until next restart).\n"
                  "<i>Set SECURITY_CODE env to make it permanent.</i>")

@app.on_message(filters.command("broadcast") & PRIVATE & owner_only)
async def cmd_broadcast(_, m: Message):
    src = m.reply_to_message
    text = m.text.split(None, 1)[1] if len(m.command) > 1 else None
    if not src and not text:
        return await m.reply("Usage: <code>/broadcast Hello everyone</code>")
    sent = failed = 0
    status = await m.reply("📣 Broadcasting…")
    for uid in list(store.users.keys()):
        for _attempt in range(2):
            try:
                if src:
                    await src.copy(uid)
                else:
                    await app.send_message(uid, header("Announcement", "📣") + esc(text))
                sent += 1
                break
            except FloodWait as e:
                await asyncio.sleep(min(float(e.value) + 1, 60))
            except Exception:
                failed += 1
                break
        await asyncio.sleep(0.1)
    await status.edit_text(f"📣 Broadcast done.\n✅ Sent: {b(sent)}  ·  ❌ Failed: {b(failed)}")

@app.on_message(filters.command("links") & PRIVATE & owner_only)
async def cmd_links(client: Client, m: Message):
    status = await m.reply("🔄 Collecting invite links…")
    chats = dict(store.chats)
    if BACKUP_GROUP_ID and BACKUP_GROUP_ID not in chats:
        chats[BACKUP_GROUP_ID] = {"title": "Backup Group", "type": "supergroup"}
    lines = []
    for cid in chats:
        try:
            chat = await client.get_chat(cid)
            me = await client.get_chat_member(cid, "me")
            if me.status not in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER):
                continue
            link = chat.invite_link or await client.export_chat_invite_link(cid)
            lines.append(f"📌 {b(chat.title)}\n🔗 {esc(link)}")
        except Exception as e:
            log.debug("links(%s): %s", cid, e)
    if lines:
        await status.edit_text(header("Admin Invite Links", "🔗") + "\n\n".join(lines), disable_web_page_preview=True)
    else:
        await status.edit_text("❌ No chats found where the bot is admin.")

@app.on_chat_member_updated()
async def track_membership(_, upd: ChatMemberUpdated):
    try:
        new = upd.new_chat_member
        if not new or not new.user or not new.user.is_self:
            return
        if new.status in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
            store.untrack_chat(upd.chat.id)
        else:
            store.track_chat(upd.chat.id, upd.chat.title or "Chat", str(upd.chat.type).split(".")[-1].lower())
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 📥 DOCUMENT INTAKE  →  WIZARD
# ═══════════════════════════════════════════════════════════════════════════
def wizard_lang_text(job: Job) -> str:
    return header("Step 1 · Language", "🌐") + summary_block(job) + f"{DIV}\nChoose the target language:"

def wizard_lang_kb(job: Job) -> InlineKeyboardMarkup:
    kb = language_keyboard(f"jl:{job.job_id}:", job.lang, f"jx:{job.job_id}")
    rows = [[InlineKeyboardButton("⚡ Quick Start (use my defaults)", callback_data=f"jq:{job.job_id}")]]
    rows += kb.inline_keyboard[:-1]
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"jx:{job.job_id}")])
    return InlineKeyboardMarkup(rows)

def wizard_fmt_text(job: Job) -> str:
    return header("Step 2 · Format", "📄") + summary_block(job) + f"{DIV}\nChoose the output format:"

def wizard_fmt_kb(job: Job) -> InlineKeyboardMarkup:
    return format_keyboard(f"jf:{job.job_id}:", job.out_format, f"jb:{job.job_id}:lang")

def wizard_split_text(job: Job) -> str:
    return header("Step 3 · Split size", "✂️") + summary_block(job) + f"{DIV}\nMax size per delivered part:"

def wizard_split_kb(job: Job) -> InlineKeyboardMarkup:
    return split_keyboard(f"js:{job.job_id}:", job.split_kb, f"jb:{job.job_id}:fmt", f"jc:{job.job_id}")

def wizard_confirm_text(job: Job) -> str:
    if job.split_kb:
        est_parts = max(1, -(-job.file_size // (job.split_kb * 1024)))
    else:
        est_parts = 1
    ahead = len(QUEUE) + (1 if ACTIVE else 0)
    return (header("Step 4 · Confirm", "✅") + summary_block(job) + f"{DIV}\n"
            f"🧩 Estimated parts: {b('~' + str(est_parts))}\n"
            f"👥 Jobs ahead of you: {b(ahead)}\n\n"
            "Everything looks good?")

def wizard_confirm_kb(job: Job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start translation", callback_data=f"jgo:{job.job_id}")],
        [InlineKeyboardButton("◀️ Back", callback_data=f"jb:{job.job_id}:spl"),
         InlineKeyboardButton("❌ Cancel", callback_data=f"jx:{job.job_id}")],
    ])

@app.on_message(filters.document & PRIVATE & authorized)
async def handle_document(_, m: Message):
    touch_user(m)
    doc = m.document
    file_name = doc.file_name or "Document"
    if OUTPUT_NAME_RE.search(file_name):
        return  # user forwarded one of our own outputs back
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in INPUT_EXTS:
        return await m.reply(header("Unsupported file", "❌") +
                             f"Allowed: {b(', '.join(sorted(INPUT_EXTS)))}")
    if doc.file_size and doc.file_size > MAX_INPUT_MB * 1024 * 1024:
        return await m.reply(f"❌ File too large. Limit is {b(f'{MAX_INPUT_MB} MB')}.")
    if SHUTTING_DOWN:
        return await m.reply("🔄 Server is restarting, please try again in a minute.")

    uid = m.from_user.id
    if user_job_count(uid) >= MAX_JOBS_PER_USER:
        return await m.reply(f"⏳ You already have {b(MAX_JOBS_PER_USER)} job(s) running/queued.\n"
                             "Please wait for them to finish or use /cancel.")

    # discard any unfinished wizard for this chat
    for jid, j in [(k, v) for k, v in PENDING.items() if v.chat_id == m.chat.id]:
        PENDING.pop(jid, None); j.cleanup()
    USER_STATE.pop(m.chat.id, None)

    novel_name = safe_filename(os.path.splitext(file_name)[0])
    lang, fmt, split = user_prefs(uid)
    job = Job(job_id=uuid.uuid4().hex[:10], chat_id=m.chat.id, user_id=uid,
              user_name=m.from_user.first_name or "User", file_path="", ext=ext,
              novel_name=novel_name, file_size=doc.file_size or 0,
              lang=lang, out_format=fmt, split_kb=split)
    job.file_path = os.path.join(INBOX_DIR, f"src_{job.job_id}{ext}")

    status = await m.reply(header("Downloading", "📥") + f"📘 {b(novel_name)}\n{progress_bar(0)} 0%")
    live = LiveMessage(status, min_interval=3.0)

    async def dl_progress(current: int, total: int):
        r = current / total if total else 0
        await live.update(header("Downloading", "📥") + f"📘 {b(novel_name)}\n{progress_bar(r)} {r * 100:.0f}%\n"
                          f"{fmt_size(current)} / {fmt_size(total)}")

    try:
        path = await m.download(file_name=job.file_path, progress=dl_progress)
        if not path or not os.path.exists(job.file_path):
            raise RuntimeError("download returned no file")
    except Exception as e:
        log.warning("download failed: %s", e)
        job.cleanup()
        return await live.update(header("Download failed", "❌") + "Please send the file again.", back_home_kb(), force=True)

    job.msg = status
    PENDING[job.job_id] = job
    await live.update(wizard_lang_text(job), wizard_lang_kb(job), force=True)


# ═══════════════════════════════════════════════════════════════════════════
# 🔘 CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════
async def _edit(q: CallbackQuery, text: str, kb: Optional[InlineKeyboardMarkup] = None):
    try:
        await q.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(min(float(e.value) + 1, 30))
        try:
            await q.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except Exception:
            pass
    except Exception as e:
        log.debug("callback edit failed: %s", e)

async def _answer(q: CallbackQuery, text: Optional[str] = None, alert: bool = False):
    try:
        await q.answer(text, show_alert=alert)
    except Exception:
        pass

def _pending(q: CallbackQuery, job_id: str) -> Optional[Job]:
    job = PENDING.get(job_id)
    if not job or job.chat_id != q.message.chat.id or job.user_id != q.from_user.id:
        return None
    return job

@app.on_callback_query(authorized_cb)
async def callbacks(_, q: CallbackQuery):
    if not q.message:
        return await _answer(q)
    data = q.data or ""
    uid = q.from_user.id
    chat_id = q.message.chat.id
    parts = data.split(":")
    kind = parts[0]
    arg1 = parts[1] if len(parts) > 1 else ""
    arg2 = parts[2] if len(parts) > 2 else ""
    if PUBLIC_MODE:
        store.ensure_user(uid, q.from_user.first_name or "User")

    if kind == "nav":
        USER_STATE.pop(chat_id, None)
        if arg1 == "home":
            await _edit(q, text_home(uid, q.from_user.first_name or "there"), home_keyboard(uid))
        elif arg1 == "help":
            await _edit(q, text_help(), back_home_kb())
        elif arg1 == "settings":
            await _edit(q, text_settings(uid), settings_keyboard())
        elif arg1 == "queue":
            await _edit(q, text_queue(uid), InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="nav:queue"),
                InlineKeyboardButton("🏠 Home", callback_data="nav:home")]]))
        elif arg1 == "mystats":
            await _edit(q, text_mystats(uid), back_home_kb())
        elif arg1 == "owner" and store.is_owner(uid):
            await _edit(q, text_owner(), InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="nav:owner"),
                InlineKeyboardButton("🏠 Home", callback_data="nav:home")]]))
        return await _answer(q)

    if kind == "st":
        lang, fmt, split = user_prefs(uid)
        if arg1 == "lang":
            await _edit(q, header("Default language", "🌐") + "Pick your default target language:",
                        language_keyboard("sl:", lang, "nav:settings"))
        elif arg1 == "fmt":
            await _edit(q, header("Default format", "📄") + "Pick your default output format:",
                        format_keyboard("sf:", fmt, "nav:settings"))
        elif arg1 == "spl":
            await _edit(q, header("Default split size", "✂️") + "Pick your default part size:",
                        split_keyboard("ss:", split, "nav:settings", None))
        return await _answer(q)

    if kind in ("sl", "sf", "ss"):
        if kind == "sl" and arg1 in LANGUAGES:
            store.set_pref(uid, "lang", arg1)
        elif kind == "sf" and arg1 in OUTPUT_FORMATS:
            store.set_pref(uid, "fmt", arg1)
        elif kind == "ss" and arg1.isdigit():
            store.set_pref(uid, "split", int(arg1))
        await _edit(q, text_settings(uid), settings_keyboard())
        return await _answer(q, "✅ Saved")

    if kind == "qx":
        if ACTIVE and ACTIVE.job_id == arg1 and ACTIVE.user_id == uid:
            ACTIVE.cancel.set()
            return await _answer(q, "🛑 Stopping…")
        for j in list(QUEUE):
            if j.job_id == arg1 and j.user_id == uid:
                QUEUE.remove(j); j.status = "cancelled"; j.cleanup()
                await _edit(q, header("Cancelled", "🛑") + summary_block(j), back_home_kb())
                await notify_positions()
                return await _answer(q, "Removed from queue")
        return await _answer(q, "Job already finished.")

    if kind in ("jl", "jf", "js", "jc", "jx", "jq", "jgo", "jb"):
        job = _pending(q, arg1)
        if not job:
            await _edit(q, header("Session expired", "⌛") + "Please send the file again.", back_home_kb())
            return await _answer(q, "Session expired", alert=True)

        if kind == "jx":
            PENDING.pop(arg1, None); job.cleanup(); USER_STATE.pop(chat_id, None)
            await _edit(q, header("Cancelled", "❌") + f"📘 {b(job.novel_name)} was discarded.", back_home_kb())
            return await _answer(q)

        if kind == "jl":
            if arg2 in LANGUAGES:
                job.lang = arg2
            await _edit(q, wizard_fmt_text(job), wizard_fmt_kb(job))
        elif kind == "jf":
            if arg2 in OUTPUT_FORMATS:
                job.out_format = arg2
            await _edit(q, wizard_split_text(job), wizard_split_kb(job))
        elif kind == "js":
            if arg2.isdigit():
                val = int(arg2)
                if val == 0 or MIN_SPLIT_KB <= val <= MAX_SPLIT_KB:
                    job.split_kb = val
            USER_STATE.pop(chat_id, None)
            await _edit(q, wizard_confirm_text(job), wizard_confirm_kb(job))
        elif kind == "jc":
            USER_STATE[chat_id] = {"state": "await_size", "job_id": arg1}
            await _edit(q, header("Custom split size", "✏️") + summary_block(job) + f"{DIV}\n"
                        "Type the size per part, e.g.\n"
                        "<code>750</code> (KB) · <code>2 MB</code> · <code>900kb</code>\n"
                        f"<i>Allowed: {MIN_SPLIT_KB} KB – {MAX_SPLIT_KB // 1024} MB</i>",
                        InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data=f"jb:{arg1}:spl")]]))
        elif kind == "jb":
            USER_STATE.pop(chat_id, None)
            if arg2 == "lang":
                await _edit(q, wizard_lang_text(job), wizard_lang_kb(job))
            elif arg2 == "fmt":
                await _edit(q, wizard_fmt_text(job), wizard_fmt_kb(job))
            else:
                await _edit(q, wizard_split_text(job), wizard_split_kb(job))
        elif kind in ("jq", "jgo"):
            if user_job_count(uid) >= MAX_JOBS_PER_USER:
                return await _answer(q, f"⏳ Max {MAX_JOBS_PER_USER} jobs at once. Wait or /cancel.", alert=True)
            if SHUTTING_DOWN:
                return await _answer(q, "🔄 Server restarting, try again shortly.", alert=True)
            PENDING.pop(arg1, None)
            USER_STATE.pop(chat_id, None)
            if kind == "jq":
                job.lang, job.out_format, job.split_kb = user_prefs(uid)
            await enqueue(job, q.message)
        return await _answer(q)

    await _answer(q)


# ═══════════════════════════════════════════════════════════════════════════
# ⌨️ FREE TEXT  (custom size input + hint)
# ═══════════════════════════════════════════════════════════════════════════
def parse_size_kb(text: str) -> Optional[int]:
    t = text.strip().lower().replace(" ", "").replace(",", ".")
    mt = re.fullmatch(r"(\d+(?:\.\d+)?)(mb|m|kb|k)?", t)
    if not mt:
        return None
    val = float(mt.group(1))
    unit = mt.group(2) or "kb"
    return int(val * 1024) if unit.startswith("m") else int(val)

@app.on_message(filters.text & PRIVATE & authorized & ~filters.via_bot)
async def handle_text(client: Client, m: Message):
    if not m.text or m.text.startswith("/"):
        return  # unknown command → ignore silently
    chat_id = m.chat.id
    text_lower = m.text.strip().lower()

    if text_lower == "give me link":
        if store.is_owner(m.from_user.id):
            return await cmd_links(client, m)
        return

    state = USER_STATE.get(chat_id)
    if not state or state.get("state") != "await_size":
        return await m.reply("📎 Send me a document to translate,\nor use /help for instructions.")

    size_kb = parse_size_kb(m.text)
    if size_kb is None:
        return await m.reply("⚠️ Invalid format. Try <code>500</code> or <code>2 MB</code>.")
    if not MIN_SPLIT_KB <= size_kb <= MAX_SPLIT_KB:
        return await m.reply(f"⚠️ Size must be between {MIN_SPLIT_KB} KB and {MAX_SPLIT_KB // 1024} MB.")

    job = PENDING.get(state["job_id"])
    USER_STATE.pop(chat_id, None)
    if not job or job.chat_id != chat_id:
        return await m.reply("⌛ Session expired. Please send the file again.")
    job.split_kb = size_kb
    await m.reply(wizard_confirm_text(job), reply_markup=wizard_confirm_kb(job))


# ═══════════════════════════════════════════════════════════════════════════
# 🌍 HEALTH SERVER  (Render web services must bind $PORT)
# ═══════════════════════════════════════════════════════════════════════════
BOT_USERNAME = ""

async def health(_request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok" if not SHUTTING_DOWN else "shutting_down",
        "bot": BOT_USERNAME,
        "uptime_sec": int(time.time() - store.booted),
        "users": len(store.users),
        "active": ACTIVE.novel_name if ACTIVE else None,
        "queue": len(QUEUE),
        "pending": len(PENDING),
        "stats": store.stats,
    })

async def index(_request: web.Request) -> web.Response:
    body = (f"<!doctype html><meta charset='utf-8'><title>{html.escape(BOT_NAME)}</title>"
            f"<body style='font-family:system-ui;padding:2rem;background:#0f172a;color:#e2e8f0'>"
            f"<h1>📚 {html.escape(BOT_NAME)}</h1>"
            f"<p>Telegram bot is <b style='color:#4ade80'>online</b>"
            + (f" as <a style='color:#93c5fd' href='https://t.me/{html.escape(BOT_USERNAME)}'>@{html.escape(BOT_USERNAME)}</a>" if BOT_USERNAME else "")
            + ".</p><p><a style='color:#93c5fd' href='/health'>/health</a></p></body>")
    return web.Response(text=body, content_type="text/html")

async def start_health_server() -> web.AppRunner:
    web_app = web.Application()
    web_app.add_routes([web.get("/", index), web.get("/health", health), web.head("/", index),
                        web.head("/health", health)])
    runner = web.AppRunner(web_app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Health server listening on 0.0.0.0:%d", PORT)
    return runner

async def keep_alive():
    """Render free tier sleeps after ~15 min without inbound traffic.
    Pinging our own public URL keeps the instance awake."""
    if not KEEP_ALIVE or not RENDER_EXTERNAL_URL:
        log.info("Keep-alive disabled (KEEP_ALIVE=%s, RENDER_EXTERNAL_URL=%r)", KEEP_ALIVE, RENDER_EXTERNAL_URL)
        return
    url = f"{RENDER_EXTERNAL_URL}/health"
    log.info("Keep-alive pinging %s every %ds", url, KEEP_ALIVE_INTERVAL)
    while not SHUTTING_DOWN:
        await asyncio.sleep(KEEP_ALIVE_INTERVAL)
        try:
            sess = await get_http()
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                log.debug("keep-alive → %s", r.status)
        except Exception as e:
            log.debug("keep-alive failed: %s", e)


# ═══════════════════════════════════════════════════════════════════════════
# ▶️ ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════
async def shutdown_jobs():
    """Tell every affected user that a restart interrupted them."""
    global SHUTTING_DOWN
    SHUTTING_DOWN = True
    if ACTIVE:
        ACTIVE.cancel.set()
    for j in list(QUEUE):
        QUEUE.remove(j)
        j.cleanup()
        await LiveMessage(j.msg).update(
            header("Restarting", "🔄") + summary_block(j) + f"{DIV}\n"
            "The server is being redeployed. Please resend the file in a minute.",
            back_home_kb(), force=True)
    for jid, j in list(PENDING.items()):
        PENDING.pop(jid, None)
        j.cleanup()
    if QUEUE_WAKE:
        QUEUE_WAKE.set()

async def main():
    global QUEUE_WAKE, BOT_USERNAME
    QUEUE_WAKE = asyncio.Event()
    runner = await start_health_server()          # bind the port FIRST → Render sees us healthy

    for attempt in range(1, 6):
        try:
            await app.start()
            break
        except FloodWait as e:
            log.warning("FloodWait on start: sleeping %ss", e.value)
            await asyncio.sleep(float(e.value) + 1)
        except Exception as e:
            fatal = any(k in str(e) for k in ("API_ID_INVALID", "ACCESS_TOKEN_INVALID", "ACCESS_TOKEN_EXPIRED",
                                               "API_ID_PUBLISHED_FLOOD", "USER_DEACTIVATED"))
            log.error("Telegram start failed (attempt %d/5): %s", attempt, e)
            if fatal:
                log.critical("Credentials rejected by Telegram — check API_ID / API_HASH / BOT_TOKEN.")
            if attempt == 5 or fatal:
                await runner.cleanup()
                sys.exit(1)
            await asyncio.sleep(5 * attempt)

    me = await app.get_me()
    BOT_USERNAME = me.username or ""
    tasks = [asyncio.create_task(queue_worker(), name="queue_worker"),
             asyncio.create_task(janitor(), name="janitor"),
             asyncio.create_task(keep_alive(), name="keep_alive")]
    log.info("%s online as @%s | owner=%s | preauth=%d | public=%s | pdf=%s | port=%d",
             BOT_NAME, me.username, store.owner_id or "none", len(AUTHORIZED_USERS), PUBLIC_MODE, HAS_PDF, PORT)
    if store.owner_id:
        await safe_send(store.owner_id, header("Bot Online", "🟢") +
                        f"@{me.username} is running on Render.\n"
                        f"👥 Pre-authorized: {b(len(store.users))}\n"
                        f"📄 PDF support: {b('yes' if HAS_PDF else 'no')}\n"
                        f"🗄 Storage: {b('in-memory (no database)')}")
    else:
        log.warning("No owner yet — first user to send the security code becomes owner.")

    await idle()                                  # blocks until SIGINT/SIGTERM

    log.info("Shutting down…")
    await shutdown_jobs()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if HTTP and not HTTP.closed:
        await HTTP.close()
    try:
        await app.stop()
    except Exception:
        pass
    await runner.cleanup()
    shutil.rmtree(BASE_DIR, ignore_errors=True)
    log.info("Bot stopped cleanly.")

if __name__ == "__main__":
    try:
        app.run(main())
    except KeyboardInterrupt:
        pass
