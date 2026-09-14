#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║        📚 NovelTranslator PRO  —  Telegram Document Translation Bot       ║
║               (Professional single-file rewrite, v2.0)                    ║
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
import time
import uuid
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional, Tuple

import docx
import ebooklib
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from ebooklib import epub
from pyrogram import Client, enums, filters, idle
from pyrogram.errors import FloodWait, MessageIdInvalid, MessageNotModified, RPCError
from pyrogram.raw import functions
from pyrogram.types import (CallbackQuery, ChatMemberUpdated, InlineKeyboardButton,
                            InlineKeyboardMarkup, Message)

try:
    from pypdf import PdfReader
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:  # optional: load .env file if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════════════════
# 🔑 CREDENTIALS  (kept as requested — env vars override if present)
# ═══════════════════════════════════════════════════════════════════════════
API_ID = int(os.getenv("API_ID", "30417468"))
API_HASH = os.getenv("API_HASH", "3905c3cb0effc91feef4d4cdcae89354")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8869725226:AAHLAbI2rIygyi7sI8RD65hGqwKhKDbZzLA")
SECURITY_CODE = os.getenv("SECURITY_CODE", "Ranjeet@#$3210")
BACKUP_GROUP_ID = int(os.getenv("BACKUP_GROUP_ID", "-1003873153201"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))          # 0 → first unlocked user = owner

# ═══════════════════════════════════════════════════════════════════════════
# ⚙️ CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════
BOT_NAME = "NovelTranslator PRO"
CONCURRENCY_LIMIT = 10          
CHUNK_SIZE = 4000               
MAX_RETRIES = 5
MAX_INPUT_MB = 100              
PENDING_TTL = 30 * 60           
EDIT_INTERVAL = 4.0             
DEFAULT_LANG = "hi"
DEFAULT_FORMAT = "txt"
DEFAULT_SPLIT_KB = 500

BASE_DIR = os.path.abspath("workspace_textbot")
STORAGE_DIR = os.path.join(BASE_DIR, "translated_files")
INBOX_DIR = os.path.join(BASE_DIR, "inbox")
DB_FILE = os.path.join(BASE_DIR, "database.json")
LEGACY_AUTH_FILE = os.path.join(BASE_DIR, "authorized_users.txt")
LOG_FILE = os.path.join(BASE_DIR, "bot.log")
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
    "es": ("Spanish", "🇪🇸"), "fr": ("French", "🇫🇷"), "ar": ("Arabic", "🇸🇦"),
    "id": ("Indonesian", "🇮🇩"),
}

# ═══════════════════════════════════════════════════════════════════════════
# 🔧 LOGGING
# ═══════════════════════════════════════════════════════════════════════════
_fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
_root = logging.getLogger()
_root.setLevel(logging.INFO)
_console = logging.StreamHandler(sys.stdout); _console.setFormatter(_fmt)
_file = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_file.setFormatter(_fmt)
_root.handlers = [_console, _file]
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ═══════════════════════════════════════════════════════════════════════════
# 🗄️ PERSISTENT STORE (JSON)
# ═══════════════════════════════════════════════════════════════════════════
class Store:
    def __init__(self, path: str):
        self.path = path
        self.data = {
            "owner_id": 0,
            "security_code": None,
            "users": {},      
            "chats": {},      
            "stats": {"jobs": 0, "parts": 0, "chars": 0, "failed": 0},
        }
        self._load()
        self._migrate_legacy()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                for k, v in saved.items():
                    self.data[k] = v
            except Exception as e:
                log.error("DB load failed: %s", e)

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _migrate_legacy(self):
        if os.path.exists(LEGACY_AUTH_FILE):
            added = 0
            with open(LEGACY_AUTH_FILE, "r") as f:
                for line in f:
                    if line.strip().isdigit() and line.strip() not in self.data["users"]:
                        self.authorize(int(line.strip()), "Legacy User", save=False)
                        added += 1
            os.rename(LEGACY_AUTH_FILE, LEGACY_AUTH_FILE + ".migrated")
            self.save()
            log.info("Migrated %d users from legacy auth file", added)

    @staticmethod
    def _default_user(name: str) -> dict:
        return {"name": name, "joined": int(time.time()), "role": "user",
                "lang": DEFAULT_LANG, "fmt": DEFAULT_FORMAT, "split": DEFAULT_SPLIT_KB,
                "stats": {"jobs": 0, "parts": 0, "chars": 0}}

    def user(self, uid: int) -> Optional[dict]:
        return self.data["users"].get(str(uid))

    def is_authorized(self, uid: int) -> bool:
        return str(uid) in self.data["users"]

    def authorize(self, uid: int, name: str, save=True):
        if str(uid) not in self.data["users"]:
            self.data["users"][str(uid)] = self._default_user(name)
        else:
            self.data["users"][str(uid)]["name"] = name
        if not self.owner_id():
            self.data["owner_id"] = uid
            self.data["users"][str(uid)]["role"] = "owner"
        if save:
            self.save()

    def revoke(self, uid: int) -> bool:
        if str(uid) in self.data["users"] and uid != self.owner_id():
            del self.data["users"][str(uid)]
            self.save()
            return True
        return False

    def owner_id(self) -> int:
        return OWNER_ID or int(self.data.get("owner_id") or 0)

    def is_owner(self, uid: int) -> bool:
        return uid == self.owner_id()

    def pref(self, uid: int, key: str, default=None):
        u = self.user(uid)
        return u.get(key, default) if u else default

    def set_pref(self, uid: int, key: str, value):
        u = self.user(uid)
        if u:
            u[key] = value
            self.save()

    def bump(self, uid: int, parts: int, chars: int, failed=False):
        u = self.user(uid)
        if u:
            u["stats"]["jobs"] += 1
            u["stats"]["parts"] += parts
            u["stats"]["chars"] += chars
        self.data["stats"]["jobs"] += 1
        self.data["stats"]["parts"] += parts
        self.data["stats"]["chars"] += chars
        if failed:
            self.data["stats"]["failed"] += 1
        self.save()

    def security_code(self) -> str:
        return self.data.get("security_code") or SECURITY_CODE

    def set_security_code(self, code: str):
        self.data["security_code"] = code
        self.save()

    def track_chat(self, chat_id: int, title: str, ctype: str):
        self.data["chats"][str(chat_id)] = {"title": title, "type": ctype, "added": int(time.time())}
        self.save()

    def untrack_chat(self, chat_id: int):
        if str(chat_id) in self.data["chats"]:
            del self.data["chats"][str(chat_id)]
            self.save()


store = Store(DB_FILE)

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

def lang_label(code_: str) -> str:
    name, flag = LANGUAGES.get(code_, (code_.upper(), "🌐"))
    return f"{flag} {name}"

def split_label(kb: int) -> str:
    if kb == 0:
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

def text_home(uid: int, name: str) -> str:
    u = store.user(uid) or {}
    return (
        header(BOT_NAME) +
        f"👋 Hello, {b(name)}!\n\n"
        f"Send me a document and I will\n"
        f"translate it into your language.\n\n"
        f"📥 {b('Input')}: {', '.join(sorted(e.lstrip('.').upper() for e in INPUT_EXTS))}\n"
        f"📤 {b('Output')}: TXT · DOCX · EPUB\n\n"
        f"{DIV}\n"
        f"🌐 Language: {b(lang_label(u.get('lang', DEFAULT_LANG)))}\n"
        f"📄 Format: {b(u.get('fmt', DEFAULT_FORMAT).upper())}\n"
        f"✂️ Split: {b(split_label(u.get('split', DEFAULT_SPLIT_KB)))}\n"
        f"{DIV}\n"
        f"<i>Tip: change defaults in ⚙️ Settings\nand use ⚡ Quick Start next time.</i>"
    )

def text_help() -> str:
    return (
        header("How it works", "ℹ️") +
        "1️⃣ Send a .epub / .txt / .docx file\n"
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
        "Custom size: 50 KB – 15 MB\n"
        "e.g. <code>750</code>, <code>2 MB</code>, <code>900kb</code>"
    )

def text_settings(uid: int) -> str:
    u = store.user(uid) or {}
    return (
        header("Settings", "⚙️") +
        f"🌐 Language: {b(lang_label(u.get('lang', DEFAULT_LANG)))}\n"
        f"📄 Format: {b(u.get('fmt', DEFAULT_FORMAT).upper())}\n"
        f"✂️ Split size: {b(split_label(u.get('split', DEFAULT_SPLIT_KB)))}\n"
        f"{DIV}\n"
        "<i>These are used by ⚡ Quick Start\nand pre-selected in the wizard.</i>"
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
    rows = kb_rows(btns, 2)
    rows.append([InlineKeyboardButton("◀️ Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)

def format_keyboard(prefix: str, selected: str, back_cb: str) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(f"{'✅ ' if f == selected else ''}{label}", callback_data=f"{prefix}{f}")
            for f, label in OUTPUT_FORMATS.items()]
    return InlineKeyboardMarkup(kb_rows(btns, 3) + [[InlineKeyboardButton("◀️ Back", callback_data=back_cb)]])

def split_keyboard(prefix: str, selected: int, back_cb: str, custom_cb: Optional[str]) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(f"{'✅ ' if kb == selected else ''}{label}", callback_data=f"{prefix}{kb}")
            for kb, label in SPLIT_PRESETS]
    rows = kb_rows(btns, 2)
    if custom_cb:
        rows.append([InlineKeyboardButton("✏️ Custom size", callback_data=custom_cb)])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════════════════════════
# 📬 FLOOD-SAFE MESSAGE UTILITIES
# ═══════════════════════════════════════════════════════════════════════════
class LiveMessage:
    def __init__(self, message: Optional[Message], min_interval: float = EDIT_INTERVAL):
        self.msg = message
        self.min_interval = min_interval
        self._last_edit = 0.0
        self._last_text = ""
        self.alive = message is not None

    async def update(self, text: str, keyboard: Optional[InlineKeyboardMarkup] = None, force: bool = False):
        if not self.alive: return
        now = time.time()
        if not force and (now - self._last_edit) < self.min_interval: return
        if text == self._last_text and not force: return
        try:
            await self.msg.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
            self._last_edit = time.time()
            self._last_text = text
        except MessageNotModified:
            self._last_edit = time.time()
        except FloodWait as e:
            self._last_edit = time.time() + float(e.value)
        except MessageIdInvalid:
            self.alive = False          
        except Exception as e:      
            log.warning("edit failed: %s", e)

async def safe_send(chat_id: int, text: str, **kwargs) -> Optional[Message]:
    for _ in range(3):
        try:
            return await app.send_message(chat_id, text, disable_web_page_preview=True, **kwargs)
        except FloodWait as e:
            await asyncio.sleep(float(e.value) + 1)
        except Exception: return None
    return None

async def safe_send_document(chat_id: int, path: str, caption: str, **kwargs) -> Optional[Message]:
    for attempt in range(4):
        try:
            return await app.send_document(chat_id, path, caption=caption, **kwargs)
        except FloodWait as e:
            await asyncio.sleep(float(e.value) + 2)
        except Exception: await asyncio.sleep(2)
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 📖 TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════
def extract_text_epub(path: str) -> str:
    try:
        try: book = epub.read_epub(path, options={"ignore_ncx": True})
        except TypeError: book = epub.read_epub(path)
    except Exception: return ""

    items = []
    for entry in getattr(book, "spine", []):
        idref = entry[0] if isinstance(entry, (tuple, list)) else entry
        item = book.get_item_with_id(idref)
        if item is not None and item.get_type() == ebooklib.ITEM_DOCUMENT:
            items.append(item)
    if not items:
        items = [i for i in book.get_items() if i.get_type() == ebooklib.ITEM_DOCUMENT]

    sections, seen = [], set()
    block_tags = ["h1", "h2", "h3", "h4", "h5", "p", "li", "blockquote", "div", "td"]
    for item in items:
        try: soup = BeautifulSoup(item.get_body_content(), "html.parser")
        except Exception: continue
        for junk in soup(["script", "style", "nav"]): junk.decompose()
        blocks = []
        for el in soup.find_all(block_tags):
            if el.find(block_tags): continue
            txt = el.get_text(" ", strip=True)
            if txt:
                if el.name.startswith("h"): blocks.append("")        
                blocks.append(txt)
        text = "\n".join(blocks).strip() if blocks else soup.get_text("\n", strip=True)
        key = hash(text[:300])
        if len(text) > 30 and key not in seen:
            seen.add(key)
            sections.append(text)
    return "\n\n".join(sections)

def extract_text_txt(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError: continue
    else: text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")

def extract_text_docx(path: str) -> str:
    try:
        d = docx.Document(path)
        return "\n".join(p.text.rstrip() for p in d.paragraphs)
    except Exception: return ""

def extract_text_pdf(path: str) -> str:
    if not HAS_PDF: return ""
    try:
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip(): pages.append(t.strip())
        return "\n\n".join(pages)
    except Exception: return ""

EXTRACTORS = {".epub": extract_text_epub, ".txt": extract_text_txt,
              ".docx": extract_text_docx, ".pdf": extract_text_pdf}

def normalise_text(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════
# ✂️ SPLITTING & SMART CHUNKING
# ═══════════════════════════════════════════════════════════════════════════
def split_text_by_size(text: str, max_kb: int) -> List[str]:
    if max_kb <= 0: return [text]
    max_bytes = max_kb * 1024
    parts, current, size = [], [], 0
    for line in text.split("\n"):
        lb = len((line + "\n").encode("utf-8"))
        if size + lb > max_bytes and size > 0:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += lb
    if current: parts.append("\n".join(current))
    return parts

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")

def _split_long_line(line: str, limit: int) -> List[str]:
    pieces, buf = [], ""
    for sent in _SENTENCE_RE.split(line):
        if len(sent) > limit:                       
            if buf: pieces.append(buf); buf = ""
            pieces.extend(sent[i:i + limit] for i in range(0, len(sent), limit))
            continue
        if len(buf) + len(sent) + 1 > limit and buf:
            pieces.append(buf); buf = sent
        else: buf = f"{buf} {sent}".strip()
    if buf: pieces.append(buf)
    return pieces

def build_chunks(text: str, limit: int = CHUNK_SIZE) -> List[Tuple[str, str]]:
    chunks: List[Tuple[str, str]] = []
    buf: List[str] = []
    size = 0
    def flush():
        nonlocal buf, size
        if buf:
            chunks.append(("\n".join(buf), "\n"))
            buf, size = [], 0
    for line in text.split("\n"):
        line = line.rstrip()
        if len(line) > limit:
            flush()
            pieces = _split_long_line(line, limit)
            for p in pieces[:-1]: chunks.append((p, " "))
            chunks.append((pieces[-1], "\n"))
            continue
        if size + len(line) + 1 > limit: flush()
        buf.append(line)
        size += len(line) + 1
    flush()
    return chunks


# ═══════════════════════════════════════════════════════════════════════════
# 📝 OUTPUT WRITERS
# ═══════════════════════════════════════════════════════════════════════════
def write_txt(text: str, path: str, title: str, lang: str):
    with open(path, "w", encoding="utf-8") as f: f.write(text)

def write_docx(text: str, path: str, title: str, lang: str):
    d = docx.Document()
    d.add_heading(title, level=1)
    for line in text.split("\n"):
        if line.strip(): d.add_paragraph(line.strip())
    d.save(path)

def write_epub(text: str, path: str, title: str, lang: str):
    book = epub.EpubBook()
    book.set_identifier(uuid.uuid4().hex)
    book.set_title(title)
    book.set_language(lang)
    book.add_author(BOT_NAME)
    lines = text.split("\n")
    per_section = 400
    chapters = []
    for n, start in enumerate(range(0, len(lines), per_section), 1):
        group = lines[start:start + per_section]
        body = "".join(f"<p>{html.escape(l.strip())}</p>" for l in group if l.strip())
        ch = epub.EpubHtml(title=f"Section {n}", file_name=f"section_{n:03d}.xhtml", lang=lang)
        ch.content = (f"<html><head><title>{html.escape(title)}</title></head>"
                      f"<body><h2>{html.escape(title)} — Section {n}</h2>{body}</body></html>")
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
class JobCancelled(Exception): pass

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

    def cleanup(self):
        try:
            if os.path.exists(self.file_path): os.remove(self.file_path)
        except Exception: pass
        out_dir = os.path.join(STORAGE_DIR, f"{self.chat_id}_{self.job_id}")
        shutil.rmtree(out_dir, ignore_errors=True)


PENDING: Dict[str, Job] = {}          
QUEUE: List[Job] = []                 
ACTIVE: Optional[Job] = None
QUEUE_WAKE = asyncio.Event()
USER_STATE: Dict[int, dict] = {}      

def queue_position(job: Job) -> int:
    try: return QUEUE.index(job) + 1
    except ValueError: return 0

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
    pos = queue_position(job)
    live = LiveMessage(message)
    await live.update(queued_text(job, pos), cancel_kb(job), force=True)
    QUEUE_WAKE.set()

async def notify_positions():
    for i, j in enumerate(QUEUE, 1):
        await LiveMessage(j.msg).update(queued_text(j, i), cancel_kb(j), force=True)

async def queue_worker():
    global ACTIVE
    log.info("Queue worker started")
    while True:
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
        except Exception as e:                      
            log.exception("Worker error: %s", e)
        finally:
            ACTIVE = None
            job.cleanup()

async def janitor():
    while True:
        await asyncio.sleep(600)
        now = time.time()
        for jid, job in list(PENDING.items()):
            if now - job.created > PENDING_TTL:
                job.cleanup()
                PENDING.pop(jid, None)
                USER_STATE.pop(job.chat_id, None)
        for name in os.listdir(INBOX_DIR):
            p = os.path.join(INBOX_DIR, name)
            try:
                if now - os.path.getmtime(p) > 4 * 3600: os.remove(p)
            except Exception: pass


# ═══════════════════════════════════════════════════════════════════════════
# 🌐 TRANSLATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════
class TranslationEngine:
    def __init__(self, target: str, concurrency: int = CONCURRENCY_LIMIT):
        self.target = target
        self.sem = asyncio.Semaphore(concurrency)
        self.total = 0
        self.done = 0
        self.failed = 0
        self.started = time.time()

    async def _translate_one(self, idx: int, chunk: str, cancel: asyncio.Event) -> Tuple[int, str]:
        if not chunk.strip():
            self.done += 1
            return idx, chunk
        async with self.sem:
            if cancel.is_set(): raise JobCancelled()
            delay = 2.0
            for attempt in range(MAX_RETRIES):
                try:
                    translator = GoogleTranslator(source="auto", target=self.target)
                    res = await asyncio.to_thread(translator.translate, chunk)
                    if not res or "Error 500" in res or "Server Error" in res: raise RuntimeError("Bad API")
                    self.done += 1
                    return idx, res
                except JobCancelled: raise
                except Exception:
                    if cancel.is_set(): raise JobCancelled()
                    await asyncio.sleep(delay + random.uniform(0, 1.5))
                    delay = min(delay * 2, 30)
            self.done += 1
            self.failed += 1
            return idx, chunk                              

    async def run(self, chunks: List[Tuple[str, str]], cancel: asyncio.Event, on_progress) -> str:
        self.total = len(chunks)
        self.started = time.time()
        results: List[Optional[str]] = [None] * self.total
        tasks = [asyncio.create_task(self._translate_one(i, c[0], cancel)) for i, c in enumerate(chunks)]
        try:
            for fut in asyncio.as_completed(tasks):
                idx, text = await fut
                results[idx] = text
                await on_progress(self)
        except JobCancelled:
            for t in tasks: t.cancel()
            raise
        except Exception:
            for t in tasks: t.cancel()
            raise
        return "".join((results[i] or chunks[i][0]) + chunks[i][1] for i in range(self.total))

    @property
    def speed(self) -> float:
        elapsed = max(time.time() - self.started, 0.001)
        return self.done / elapsed

    @property
    def eta(self) -> float:
        return (self.total - self.done) / self.speed if self.speed > 0 else 0.0

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
    try:
        peer = await app.resolve_peer(BACKUP_GROUP_ID)
        result = await app.invoke(functions.channels.CreateForumTopic(
            channel=peer, title=title[:128], random_id=random.randint(1, 2**62)))
        for upd in result.updates:
            msg = getattr(upd, "message", None)
            if msg is not None and hasattr(msg, "id"):
                return msg.id
    except Exception as e:
        log.debug("create_backup_topic failed: %s", e)
    return None

async def process_job(job: Job):
    live = LiveMessage(job.msg)
    out_dir = os.path.join(STORAGE_DIR, f"{job.chat_id}_{job.job_id}")
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    total_chars = 0
    total_parts = 0
    failed_chunks = 0

    try:
        await live.update(header("Processing", "🔄") + summary_block(job) + f"{DIV}\n📖 Extracting text…",
                          cancel_kb(job), force=True)
        extractor = EXTRACTORS.get(job.ext, extract_text_txt)
        text = normalise_text(await asyncio.to_thread(extractor, job.file_path))
        if len(text) < 20:
            job.status = "failed"
            await live.update(header("Failed", "❌") + summary_block(job) + f"{DIV}\nNo readable text found.", back_home_kb(), force=True)
            return
        if job.cancel.is_set(): raise JobCancelled()

        parts = split_text_by_size(text, job.split_kb)
        total_parts = len(parts)
        total_chars = len(text)

        topic_id = await create_backup_topic(job.novel_name)
        backup_kwargs = {"reply_to_message_id": topic_id} if topic_id else {}
        chars_str = f"{total_chars:,}"
        await safe_send(BACKUP_GROUP_ID,
                        header("New Job", "📥") + summary_block(job) +
                        f"👤 User: {b(job.user_name)} ({code(job.user_id)})\n"
                        f"🧩 Parts: {b(total_parts)}  ·  🔤 Chars: {b(chars_str)}",
                        **backup_kwargs)

        for i, part_text in enumerate(parts, 1):
            if job.cancel.is_set(): raise JobCancelled()
            chunks = build_chunks(part_text)
            eng = TranslationEngine(job.lang)

            async def on_progress(e: TranslationEngine, _i=i):
                if job.cancel.is_set(): raise JobCancelled()
                await live.update(dashboard_text(job, _i, total_parts, e, "Translating…"), cancel_kb(job))

            translated = await eng.run(chunks, job.cancel, on_progress)
            failed_chunks += eng.failed

            await live.update(dashboard_text(job, i, total_parts, eng, f"Building .{job.out_format} file…"), cancel_kb(job), force=True)

            part_title = f"{job.novel_name} — Part {i} of {total_parts}" if total_parts > 1 else job.novel_name
            out_name = f"Part {i} of {total_parts} | {job.novel_name} [{job.lang_name}].{job.out_format}"
            out_path = os.path.join(out_dir, out_name)
            await asyncio.to_thread(WRITERS[job.out_format], translated, out_path, part_title, job.lang)

            caption = (f"📘 {b(job.novel_name)}\n"
                       f"📦 Part {i} / {total_parts}  ·  🌐 {lang_label(job.lang)}\n"
                       f"💾 {fmt_size(os.path.getsize(out_path))}")
            await safe_send_document(job.chat_id, out_path, caption)
            await safe_send_document(BACKUP_GROUP_ID, out_path, caption + f"\n👤 {b(job.user_name)} ({code(job.user_id)})", **backup_kwargs)

        job.status = "done"
        store.bump(job.user_id, total_parts, total_chars)
        elapsed = time.time() - started
        chars_str_final = f"{total_chars:,}"
        await live.update(
            header("Completed", "✅") + summary_block(job) + f"{DIV}\n"
            f"🧩 Parts: {b(total_parts)}\n"
            f"🔤 Characters: {b(chars_str_final)}\n"
            f"⏱ Time: {b(fmt_time(elapsed))}\n"
            + (f"⚠️ {failed_chunks} chunk(s) kept original text\n" if failed_chunks else "") +
            f"{DIV}\n🚀 Thank you for using {BOT_NAME}!",
            back_home_kb(), force=True)

    except JobCancelled:
        job.status = "cancelled"
        await live.update(header("Cancelled", "🛑") + summary_block(job) + f"{DIV}\nJob stopped by user.", back_home_kb(), force=True)
        await safe_send(BACKUP_GROUP_ID, f"🛑 Job cancelled: {b(job.novel_name)} by {code(job.user_id)}")
    except Exception as e:
        job.status = "failed"
        log.exception("Job %s failed: %s", job.job_id, e)
        store.bump(job.user_id, 0, 0, failed=True)
        await live.update(header("Error", "⚠️") + summary_block(job) + f"{DIV}\nSomething went wrong.", back_home_kb(), force=True)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# 🤖 CLIENT & FILTERS
# ═══════════════════════════════════════════════════════════════════════════
app = Client("novel_translator_pro", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
             workdir=BASE_DIR, parse_mode=enums.ParseMode.HTML)

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


# ═══════════════════════════════════════════════════════════════════════════
# 🔒 UNAUTHORIZED
# ═══════════════════════════════════════════════════════════════════════════
@app.on_message(PRIVATE & ~authorized)
async def unauthorized_message(_, m: Message):
    if m.text and m.text.strip() == store.security_code():
        store.authorize(m.from_user.id, m.from_user.first_name or "User")
        role = "👑 Owner" if store.is_owner(m.from_user.id) else "👤 User"
        await m.reply(header("Access Granted", "✅") + f"Welcome, {b(m.from_user.first_name)}!\nRole: {b(role)}\n\nSend /start to begin.")
        return
    await m.reply(header("Security Locked", "🔒") + "This bot is private.\nSend the <b>security code</b> to unlock.")

@app.on_callback_query(~authorized_cb)
async def unauthorized_callback(_, q: CallbackQuery):
    await q.answer("🔒 Not authorized.", show_alert=True)


# ═══════════════════════════════════════════════════════════════════════════
# 💬 COMMANDS
# ═══════════════════════════════════════════════════════════════════════════
@app.on_message(filters.command("start") & PRIVATE & authorized)
async def cmd_start(_, m: Message):
    USER_STATE.pop(m.chat.id, None)
    await m.reply(text_home(m.from_user.id, m.from_user.first_name), reply_markup=home_keyboard(m.from_user.id))

@app.on_message(filters.command("help") & PRIVATE & authorized)
async def cmd_help(_, m: Message):
    await m.reply(text_help(), reply_markup=back_home_kb())

@app.on_message(filters.command("settings") & PRIVATE & authorized)
async def cmd_settings(_, m: Message):
    await m.reply(text_settings(m.from_user.id), reply_markup=settings_keyboard())

def text_queue(uid: int) -> str:
    lines = [header("Queue Status", "📋")]
    if ACTIVE:
        who = "you" if ACTIVE.user_id == uid else esc(ACTIVE.user_name)
        lines.append(f"▶️ Running: {b(ACTIVE.novel_name)}\n   <i>by {who}</i>\n")
    else: lines.append("▶️ Running: <i>nothing</i>\n")
    if QUEUE:
        lines.append(f"⏳ Waiting: {b(len(QUEUE))}")
        for i, j in enumerate(QUEUE[:10], 1):
            mark = "🟢" if j.user_id == uid else "⚪️"
            lines.append(f"{mark} #{i} {esc(j.novel_name[:32])}")
        if len(QUEUE) > 10: lines.append(f"… and {len(QUEUE) - 10} more")
    else: lines.append("⏳ Waiting: <i>empty</i>")
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
    
    # 🟢 FIX: Number formatting ko f-string se bahar nikal liya
    chars_str = f"{s.get('chars', 0):,}"
    
    return (
        header("My Statistics", "📊") +
        f"👤 {b(u.get('name', 'User'))}  ·  {code(uid)}\n"
        f"🎖 Role: {b(u.get('role', 'user').title())}\n"
        f"📅 Member since: {b(since)}\n"
        f"{DIV}\n"
        f"📚 Files translated: {b(s.get('jobs', 0))}\n"
        f"🧩 Parts delivered: {b(s.get('parts', 0))}\n"
        f"🔤 Characters: {b(chars_str)}"
    )

@app.on_message(filters.command("mystats") & PRIVATE & authorized)
async def cmd_mystats(_, m: Message):
    await m.reply(text_mystats(m.from_user.id), reply_markup=back_home_kb())

# ── Owner commands ──────────────────────────────────────────────────────────
def text_owner() -> str:
    s = store.data["stats"]
    
    # 🟢 FIX: Number formatting ko f-string se bahar nikal liya
    chars_str = f"{s['chars']:,}"
    
    return (
        header("Owner Panel", "👑") +
        f"👥 Users: {b(len(store.data['users']))}\n"
        f"💬 Tracked chats: {b(len(store.data['chats']))}\n"
        f"📚 Jobs done: {b(s['jobs'])}  ·  ❌ Failed: {b(s['failed'])}\n"
        f"🧩 Parts: {b(s['parts'])}  ·  🔤 Chars: {b(chars_str)}\n"
        f"▶️ Active: {b(ACTIVE.novel_name if ACTIVE else '—')}\n"
        f"⏳ Queue: {b(len(QUEUE))}\n"
        f"{DIV}\n"
        f"{b('Commands')}\n"
        "/users – list users\n"
        "/adduser &lt;id&gt; · /deluser &lt;id&gt;\n"
        "/broadcast &lt;text&gt; (or reply)\n"
        "/setcode &lt;new code&gt;\n"
        "/links – admin invite links\n"
        "/stats – this panel"
    )

@app.on_message(filters.command("stats") & PRIVATE & owner_only)
async def cmd_stats(_, m: Message):
    await m.reply(text_owner(), reply_markup=back_home_kb())

@app.on_message(filters.command("users") & PRIVATE & owner_only)
async def cmd_users(_, m: Message):
    users = store.data["users"]
    lines = [header(f"Users ({len(users)})", "👥")]
    for uid, u in list(users.items())[:60]:
        crown = "👑 " if u.get("role") == "owner" else ""
        lines.append(f"{crown}{esc(u.get('name', 'User'))} — {code(uid)} · {u['stats'].get('jobs', 0)} jobs")
    if len(users) > 60: lines.append(f"… and {len(users) - 60} more")
    await m.reply("\n".join(lines))

@app.on_message(filters.command("adduser") & PRIVATE & owner_only)
async def cmd_adduser(_, m: Message):
    if len(m.command) < 2 or not m.command[1].lstrip("-").isdigit(): return await m.reply("Usage: <code>/adduser 123456789</code>")
    uid = int(m.command[1])
    store.authorize(uid, "Added by owner")
    await m.reply(f"✅ User {code(uid)} authorized.")
    await safe_send(uid, header("Access Granted", "✅") + "You have been authorized.\nSend /start to begin.")

@app.on_message(filters.command("deluser") & PRIVATE & owner_only)
async def cmd_deluser(_, m: Message):
    if len(m.command) < 2 or not m.command[1].lstrip("-").isdigit(): return await m.reply("Usage: <code>/deluser 123456789</code>")
    uid = int(m.command[1])
    ok = store.revoke(uid)
    await m.reply(f"🗑 User {code(uid)} removed." if ok else "⚠️ Not found (or is the owner).")

@app.on_message(filters.command("setcode") & PRIVATE & owner_only)
async def cmd_setcode(_, m: Message):
    if len(m.command) < 2: return await m.reply("Usage: <code>/setcode NewSecret123</code>")
    new_code = m.text.split(None, 1)[1].strip()
    if len(new_code) < 6: return await m.reply("⚠️ Code must be at least 6 characters.")
    store.set_security_code(new_code)
    await m.reply(f"🔐 Security code updated to {code(new_code)}")

@app.on_message(filters.command("broadcast") & PRIVATE & owner_only)
async def cmd_broadcast(_, m: Message):
    src = m.reply_to_message
    text = m.text.split(None, 1)[1] if len(m.command) > 1 else None
    if not src and not text: return await m.reply("Usage: <code>/broadcast Hello everyone</code>")
    sent = failed = 0
    status = await m.reply("📣 Broadcasting…")
    for uid in list(store.data["users"].keys()):
        try:
            if src: await src.copy(int(uid))
            else: await app.send_message(int(uid), header("Announcement", "📣") + esc(text))
            sent += 1
        except FloodWait as e: await asyncio.sleep(float(e.value) + 1)
        except Exception: failed += 1
    await status.edit_text(f"📣 Broadcast done.\n✅ Sent: {b(sent)}  ·  ❌ Failed: {b(failed)}")

@app.on_message(filters.command("links") & PRIVATE & owner_only)
async def cmd_links(client: Client, m: Message):
    status = await m.reply("🔄 Collecting invite links…")
    chats = dict(store.data["chats"])
    if str(BACKUP_GROUP_ID) not in chats:
        chats[str(BACKUP_GROUP_ID)] = {"title": "Backup Group", "type": "supergroup"}
    lines = []
    for cid, info in chats.items():
        try:
            chat = await client.get_chat(int(cid))
            me = await client.get_chat_member(int(cid), "me")
            if me.status not in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER): continue
            link = chat.invite_link or await client.export_chat_invite_link(int(cid))
            lines.append(f"📌 {b(chat.title)}\n🔗 {esc(link)}")
        except Exception as e: log.debug("links: %s", e)
    if lines: await status.edit_text(header("Admin Invite Links", "🔗") + "\n\n".join(lines), disable_web_page_preview=True)
    else: await status.edit_text("❌ No chats found where the bot is admin.")

@app.on_chat_member_updated()
async def track_membership(_, upd: ChatMemberUpdated):
    try:
        new = upd.new_chat_member
        if not new or not new.user or not new.user.is_self: return
        if new.status in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
            store.untrack_chat(upd.chat.id)
        else: store.track_chat(upd.chat.id, upd.chat.title or "Chat", str(upd.chat.type).split(".")[-1].lower())
    except Exception: pass


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
    est_parts = max(1, -(-job.file_size // (job.split_kb * 1024))) if job.split_kb else 1
    return (header("Step 4 · Confirm", "✅") + summary_block(job) + f"{DIV}\n"
            f"🧩 Estimated parts: {b('~' + str(est_parts))}\n"
            f"👥 Jobs ahead of you: {b(len(QUEUE) + (1 if ACTIVE else 0))}\n\n"
            "Everything looks good?")

def wizard_confirm_kb(job: Job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start translation", callback_data=f"jgo:{job.job_id}")],
        [InlineKeyboardButton("◀️ Back", callback_data=f"jb:{job.job_id}:spl"),
         InlineKeyboardButton("❌ Cancel", callback_data=f"jx:{job.job_id}")],
    ])

@app.on_message(filters.document & PRIVATE & authorized)
async def handle_document(_, m: Message):
    doc = m.document
    file_name = doc.file_name or "Document"
    if OUTPUT_NAME_RE.search(file_name): return
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in INPUT_EXTS: return await m.reply(header("Unsupported file", "❌") + f"Allowed: {b(', '.join(sorted(INPUT_EXTS)))}")
    if doc.file_size and doc.file_size > MAX_INPUT_MB * 1024 * 1024: return await m.reply(f"❌ File too large. Limit is {b(f'{MAX_INPUT_MB} MB')}.")

    for jid, j in [(k, v) for k, v in PENDING.items() if v.chat_id == m.chat.id]:
        PENDING.pop(jid, None); j.cleanup()
    USER_STATE.pop(m.chat.id, None)

    novel_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", os.path.splitext(file_name)[0]).strip() or "Document"
    uid = m.from_user.id
    job = Job(job_id=uuid.uuid4().hex[:8], chat_id=m.chat.id, user_id=uid,
              user_name=m.from_user.first_name or "User", file_path="", ext=ext,
              novel_name=novel_name[:90], file_size=doc.file_size or 0,
              lang=store.pref(uid, "lang", DEFAULT_LANG),
              out_format=store.pref(uid, "fmt", DEFAULT_FORMAT),
              split_kb=int(store.pref(uid, "split", DEFAULT_SPLIT_KB)))
    job.file_path = os.path.join(INBOX_DIR, f"src_{job.job_id}{ext}")

    status = await m.reply(header("Downloading", "📥") + f"📘 {b(novel_name[:90])}\n{progress_bar(0)} 0%")
    live = LiveMessage(status, min_interval=3.0)

    async def dl_progress(current: int, total: int):
        r = current / total if total else 0
        await live.update(header("Downloading", "📥") + f"📘 {b(novel_name[:90])}\n{progress_bar(r)} {r * 100:.0f}%\n{fmt_size(current)} / {fmt_size(total)}")

    try: await m.download(file_name=job.file_path, progress=dl_progress)
    except Exception: return await live.update(header("Download failed", "❌") + "Please send the file again.", force=True)

    PENDING[job.job_id] = job
    await live.update(wizard_lang_text(job), wizard_lang_kb(job), force=True)


# ═══════════════════════════════════════════════════════════════════════════
# 🔘 CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════
async def _edit(q: CallbackQuery, text: str, kb: Optional[InlineKeyboardMarkup] = None):
    try: await q.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except MessageNotModified: pass
    except FloodWait as e:
        await asyncio.sleep(float(e.value) + 1)
        try: await q.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except Exception: pass
    except Exception: pass

def _pending(q: CallbackQuery, job_id: str) -> Optional[Job]:
    job = PENDING.get(job_id)
    if not job or job.chat_id != q.message.chat.id: return None
    return job

@app.on_callback_query(authorized_cb)
async def callbacks(_, q: CallbackQuery):
    if not q.message: return await q.answer()
    data = q.data or ""
    uid = q.from_user.id
    chat_id = q.message.chat.id
    parts = data.split(":")
    kind = parts[0]

    if kind == "nav":
        page = parts[1]
        USER_STATE.pop(chat_id, None)
        if page == "home": await _edit(q, text_home(uid, q.from_user.first_name), home_keyboard(uid))
        elif page == "help": await _edit(q, text_help(), back_home_kb())
        elif page == "settings": await _edit(q, text_settings(uid), settings_keyboard())
        elif page == "queue": await _edit(q, text_queue(uid), InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="nav:queue"), InlineKeyboardButton("🏠 Home", callback_data="nav:home")]]))
        elif page == "mystats": await _edit(q, text_mystats(uid), back_home_kb())
        elif page == "owner" and store.is_owner(uid): await _edit(q, text_owner(), back_home_kb())
        return await q.answer()

    if kind == "st":
        what = parts[1]
        if what == "lang": await _edit(q, header("Default language", "🌐") + "Pick your default target language:", language_keyboard("sl:", store.pref(uid, "lang", DEFAULT_LANG), "nav:settings"))
        elif what == "fmt": await _edit(q, header("Default format", "📄") + "Pick your default output format:", format_keyboard("sf:", store.pref(uid, "fmt", DEFAULT_FORMAT), "nav:settings"))
        elif what == "spl": await _edit(q, header("Default split size", "✂️") + "Pick your default part size:", split_keyboard("ss:", int(store.pref(uid, "split", DEFAULT_SPLIT_KB)), "nav:settings", None))
        return await q.answer()

    if kind in ("sl", "sf", "ss"):
        val = parts[1]
        if kind == "sl" and val in LANGUAGES: store.set_pref(uid, "lang", val)
        elif kind == "sf" and val in OUTPUT_FORMATS: store.set_pref(uid, "fmt", val)
        elif kind == "ss" and val.isdigit(): store.set_pref(uid, "split", int(val))
        await _edit(q, text_settings(uid), settings_keyboard())
        return await q.answer("✅ Saved")

    if kind == "qx":
        job_id = parts[1]
        if ACTIVE and ACTIVE.job_id == job_id and ACTIVE.chat_id == chat_id:
            ACTIVE.cancel.set()
            return await q.answer("🛑 Stopping…")
        for j in QUEUE:
            if j.job_id == job_id and j.chat_id == chat_id:
                QUEUE.remove(j); j.status = "cancelled"; j.cleanup()
                await _edit(q, header("Cancelled", "🛑") + summary_block(j), back_home_kb())
                await notify_positions()
                return await q.answer("Removed from queue")
        return await q.answer("Job already finished.")

    if kind in ("jl", "jf", "js", "jc", "jx", "jq", "jgo", "jb"):
        job_id = parts[1]
        job = _pending(q, job_id)
        if not job:
            await _edit(q, header("Session expired", "⌛") + "Please send the file again.", back_home_kb())
            return await q.answer("Session expired", show_alert=True)

        if kind == "jx":
            PENDING.pop(job_id, None); job.cleanup(); USER_STATE.pop(chat_id, None)
            await _edit(q, header("Cancelled", "❌") + f"📘 {b(job.novel_name)} was discarded.", back_home_kb())
            return await q.answer()

        if kind == "jl":
            if parts[2] in LANGUAGES: job.lang = parts[2]
            await _edit(q, wizard_fmt_text(job), wizard_fmt_kb(job))
        elif kind == "jf":
            if parts[2] in OUTPUT_FORMATS: job.out_format = parts[2]
            await _edit(q, wizard_split_text(job), wizard_split_kb(job))
        elif kind == "js":
            job.split_kb = int(parts[2])
            await _edit(q, wizard_confirm_text(job), wizard_confirm_kb(job))
        elif kind == "jc":
            USER_STATE[chat_id] = {"state": "await_size", "job_id": job_id}
            await _edit(q, header("Custom split size", "✏️") + summary_block(job) + f"{DIV}\n"
                        "Type the size per part, e.g.\n"
                        "<code>750</code> (KB) · <code>2 MB</code> · <code>900kb</code>\n"
                        "<i>Allowed: 50 KB – 15 MB</i>",
                        InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data=f"jb:{job_id}:spl")]]))
        elif kind == "jb":
            USER_STATE.pop(chat_id, None)
            step = parts[2]
            if step == "lang": await _edit(q, wizard_lang_text(job), wizard_lang_kb(job))
            elif step == "fmt": await _edit(q, wizard_fmt_text(job), wizard_fmt_kb(job))
            else: await _edit(q, wizard_split_text(job), wizard_split_kb(job))
        elif kind in ("jq", "jgo"):
            PENDING.pop(job_id, None)
            USER_STATE.pop(chat_id, None)
            await enqueue(job, q.message)
        return await q.answer()

    await q.answer()


# ═══════════════════════════════════════════════════════════════════════════
# ⌨️ FREE TEXT (custom size input, fallback hint, and SECRET command)
# ═══════════════════════════════════════════════════════════════════════════
def parse_size_kb(text: str) -> Optional[int]:
    t = text.strip().lower().replace(" ", "")
    mt = re.fullmatch(r"(\d+(?:\.\d+)?)(mb|m|kb|k)?", t)
    if not mt: return None
    val = float(mt.group(1))
    unit = mt.group(2) or "kb"
    return int(val * 1024) if unit.startswith("m") else int(val)

@app.on_message(filters.text & PRIVATE & authorized)
async def handle_text(client: Client, m: Message):
    chat_id = m.chat.id
    text_lower = m.text.strip().lower() if m.text else ""
    
    # 🔥 HIDDEN SECRET FEATURE: EXACTLY MATCH "give me link" (Only for Authorized Users/Owner)
    if text_lower == "give me link" and store.is_owner(m.from_user.id):
        return await cmd_links(client, m)

    state = USER_STATE.get(chat_id)
    if not state or state.get("state") != "await_size":
        if text_lower == "give me link": return  # Ignore if not owner but tried secret
        return await m.reply("📎 Send me a document to translate,\nor use /help for instructions.")

    size_kb = parse_size_kb(m.text)
    if size_kb is None: return await m.reply("⚠️ Invalid format. Try <code>500</code> or <code>2 MB</code>.")
    if not 50 <= size_kb <= 15360: return await m.reply("⚠️ Size must be between 50 KB and 15 MB.")

    job = PENDING.get(state["job_id"])
    USER_STATE.pop(chat_id, None)
    if not job or job.chat_id != chat_id: return await m.reply("⌛ Session expired. Please send the file again.")
    job.split_kb = size_kb
    await m.reply(wizard_confirm_text(job), reply_markup=wizard_confirm_kb(job))


# ═══════════════════════════════════════════════════════════════════════════
# ▶️ ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════
async def main():
    await app.start()
    me = await app.get_me()
    asyncio.create_task(queue_worker())
    asyncio.create_task(janitor())
    log.info("%s online as @%s | users=%d | pdf=%s", BOT_NAME, me.username, len(store.data["users"]), HAS_PDF)
    owner = store.owner_id()
    if owner:
        await safe_send(owner, header("Bot Online", "🟢") +
                        f"@{me.username} is running.\n"
                        f"👥 Users: {b(len(store.data['users']))}\n"
                        f"📄 PDF support: {b('yes' if HAS_PDF else 'no (pip install pypdf)')}")
    else: log.warning("No owner yet — first user to send the security code becomes owner.")
    await idle()
    if ACTIVE: ACTIVE.cancel.set()
    await app.stop()
    log.info("Bot stopped cleanly.")

if __name__ == "__main__":
    app.run(main())
