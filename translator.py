"""Text extraction, intelligent chunking, and async translation engine."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qsl

import aiohttp
import docx
import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from config import (
    CHUNK_SIZE,
    EXPANSION,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    log,
)

try:
    from pypdf import PdfReader
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    from deep_translator import GoogleTranslator as FallbackTranslator
    HAS_FALLBACK = True
except ImportError:
    HAS_FALLBACK = False


# ═══════════════════════════════════════════════════════════════════════════
# 📖 TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════
_BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "div", "td", "pre"]


def extract_text_epub(path: str) -> str:
    """Extract text from EPUB file."""
    try:
        book = epub.read_epub(path, options={"ignore_ncx": True})
    except TypeError:
        book = epub.read_epub(path)

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
    """Extract text from TXT file with smart encoding detection."""
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
    for enc in encodings:
        try:
            return raw.decode(enc).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def extract_text_docx(path: str) -> str:
    """Extract text from DOCX file."""
    try:
        doc = docx.Document(path)
        parts = [p.text.rstrip() for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    except Exception as e:
        log.warning("DOCX extraction failed: %s", e)
        return ""


def extract_text_pdf(path: str) -> str:
    """Extract text from PDF file."""
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
        log.warning("PDF extraction failed: %s", e)
        return ""


EXTRACTORS = {
    ".epub": extract_text_epub,
    ".txt": extract_text_txt,
    ".docx": extract_text_docx,
    ".pdf": extract_text_pdf,
}


def normalise_text(text: str) -> str:
    """Clean and normalize extracted text."""
    text = text.replace("\x00", "").replace("\u00ad", "")
    text = re.sub(r"[ \t\u00a0]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════
# ✂️ SPLITTING & SMART CHUNKING
# ═══════════════════════════════════════════════════════════════════════════
def split_text_by_size(text: str, max_kb: int, factor: float = 1.0) -> List[str]:
    """Split text on line boundaries, accounting for output expansion (scripts like Hindi 2-3× larger)."""
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
    """Break a single line longer than limit into smaller segments."""
    pieces, buf = [], ""
    for sent in _SENTENCE_RE.split(line):
        if not sent:
            continue
        if len(sent) > limit:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.extend(sent[i : i + limit] for i in range(0, len(sent), limit))
            continue
        if len(buf) + len(sent) + 1 > limit and buf:
            pieces.append(buf)
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf:
        pieces.append(buf)
    return pieces


def build_chunks(text: str, limit: int = CHUNK_SIZE) -> List[List[str]]:
    """Group lines into chunks of ≤ limit chars. Each chunk is a list of lines
    so they survive translation together, preserving paragraph structure exactly."""
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
    """Remove XML-illegal characters."""
    return _XML_ILLEGAL.sub("", s)


def write_txt(text: str, path: str, title: str, lang: str) -> None:
    """Write translated text to TXT file."""
    Path(path).write_text(text, encoding="utf-8")


def write_docx(text: str, path: str, title: str, lang: str) -> None:
    """Write translated text to DOCX file."""
    doc = docx.Document()
    doc.add_heading(_xml_safe(title), level=1)
    for line in text.split("\n"):
        line = line.strip()
        if line:
            doc.add_paragraph(_xml_safe(line))
    doc.save(path)


def write_epub(text: str, path: str, title: str, lang: str) -> None:
    """Write translated text to EPUB file."""
    import uuid

    book = epub.EpubBook()
    book.set_identifier(uuid.uuid4().hex)
    book.set_title(title)
    book.set_language(lang.split("-")[0])
    book.add_author("NovelTranslator PRO")
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    per_section = 400
    chapters = []
    groups = [lines[i : i + per_section] for i in range(0, len(lines), per_section)] or [[]]
    for n, group in enumerate(groups, 1):
        body = "".join(
            f"<p>{html.escape(_xml_safe(line))}</p>" for line in group if line.strip()
        )
        ch = epub.EpubHtml(
            title=f"Section {n}",
            file_name=f"section_{n:03d}.xhtml",
            lang=lang.split("-")[0],
        )
        ch.content = (
            f"<html xmlns=\"http://www.w3.org/1999/xhtml\"><head><title>{html.escape(title)}</title></head>"
            f"<body><h2>{html.escape(title)}{' — Section ' + str(n) if len(groups) > 1 else ''}</h2>{body}</body></html>"
        )
        book.add_item(ch)
        chapters.append(ch)
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters
    epub.write_epub(path, book)


WRITERS = {"txt": write_txt, "docx": write_docx, "epub": write_epub}


# ═══════════════════════════════════════════════════════════════════════════
# 🌐 TRANSLATION ENGINE (async aiohttp + Google Translate + fallback)
# ═══════════════════════════════════════════════════════════════════════════
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_GT_URL = "https://translate.googleapis.com/translate_a/t"
_HTTP_SESSION: Optional[aiohttp.ClientSession] = None


class TranslationError(Exception):
    """Translation attempt failed."""

    pass


async def get_http() -> aiohttp.ClientSession:
    """Get or create shared HTTP session."""
    global _HTTP_SESSION
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        _HTTP_SESSION = aiohttp.ClientSession(
            headers={"User-Agent": _UA},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            connector=aiohttp.TCPConnector(limit=16, ttl_dns_cache=300),
        )
    return _HTTP_SESSION


async def gt_translate_lines(lines: List[str], target: str) -> List[str]:
    """Translate a list of segments via Google Translate. Empty segments pass through."""
    idx = [i for i, l in enumerate(lines) if l.strip()]
    if not idx:
        return list(lines)
    sess = await get_http()
    data = [("q", lines[i]) for i in idx]
    async with sess.post(
        _GT_URL, params={"client": "gtx", "sl": "auto", "tl": target}, data=data
    ) as resp:
        if resp.status == 429 or resp.status >= 500:
            raise TranslationError(f"HTTP {resp.status}")
        if resp.status != 200:
            raise TranslationError(f"HTTP {resp.status}")
        try:
            payload = json.loads(await resp.text())
        except Exception as e:
            raise TranslationError(f"bad json: {e}")
    if not isinstance(payload, list) or len(payload) != len(idx):
        raise TranslationError("unexpected payload shape")
    out = list(lines)
    for pos, item in zip(idx, payload):
        txt = item[0] if isinstance(item, list) else item
        if not isinstance(txt, str):
            raise TranslationError("non-string result")
        out[pos] = html.unescape(txt)
    return out


def _fallback_translate(text: str, target: str) -> str:
    """Synchronous fallback using deep_translator (thread pool)."""
    if not HAS_FALLBACK:
        raise TranslationError("no fallback")
    res = FallbackTranslator(source="auto", target=target).translate(text)
    if not res or "Error 500" in res or "Server Error" in res:
        raise TranslationError("fallback bad result")
    return res


class TranslationEngine:
    """Async translation with semaphore-based concurrency control and retry logic."""

    def __init__(self, target: str, concurrency: int = 8):
        self.target = target
        self.sem = asyncio.Semaphore(concurrency)
        self.total = 0
        self.done = 0
        self.failed = 0
        self.started = time.time()

    async def _translate_one(
        self, idx: int, lines: List[str], cancel: asyncio.Event
    ) -> tuple[int, List[str]]:
        """Translate one chunk with exponential backoff retry."""
        if not any(l.strip() for l in lines):
            self.done += 1
            return idx, lines
        async with self.sem:
            if cancel.is_set():
                raise asyncio.CancelledError()
            delay = 1.5
            for attempt in range(MAX_RETRIES):
                if cancel.is_set():
                    raise asyncio.CancelledError()
                try:
                    res = await gt_translate_lines(lines, self.target)
                    self.done += 1
                    return idx, res
                except (
                    TranslationError,
                    aiohttp.ClientError,
                    asyncio.TimeoutError,
                    json.JSONDecodeError,
                ) as e:
                    log.debug("chunk %d attempt %d failed: %s", idx, attempt + 1, e)
                    if attempt == MAX_RETRIES - 2:  # penultimate: try fallback
                        try:
                            joined = "\n".join(lines)
                            res_text = await asyncio.wait_for(
                                asyncio.to_thread(_fallback_translate, joined, self.target),
                                REQUEST_TIMEOUT + 20,
                            )
                            self.done += 1
                            return idx, [res_text]
                        except Exception as e2:
                            log.debug("fallback failed: %s", e2)
                    await asyncio.sleep(delay + (time.time() % 1.0))
                    delay = min(delay * 2, 20)
            self.done += 1
            self.failed += 1
            return idx, lines  # Keep original if all retries exhausted

    async def run(
        self, chunks: List[List[str]], cancel: asyncio.Event, on_progress
    ) -> str:
        """Translate all chunks, call on_progress periodically."""
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
        return "\n".join(
            "\n".join(results[i] if results[i] is not None else chunks[i])
            for i in range(self.total)
        )

    @property
    def speed(self) -> float:
        """Chunks per second."""
        return self.done / max(time.time() - self.started, 0.001)

    @property
    def eta(self) -> float:
        """Estimated seconds remaining."""
        return (self.total - self.done) / self.speed if self.speed > 0 and self.done else 0.0


async def close_http() -> None:
    """Close shared HTTP session."""
    global _HTTP_SESSION
    if _HTTP_SESSION and not _HTTP_SESSION.closed:
        await _HTTP_SESSION.close()
