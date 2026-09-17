"""Stateless translation worker.

Two ways to run it:

* ``python worker.py`` — a standalone Render/Docker service.  It owns its own
  Pyrogram client (send-only, never registers update handlers) and MongoDB
  connection, exposes ``/health`` and claims jobs from ``jobs_queue``.
* embedded in the master (``EMBEDDED_WORKER=1``, the default) — ``master.py``
  creates ``TranslationWorker(client=app, db=repo)`` and runs ``worker.serve()``
  as a background task, so a *single* free Render service is a complete
  deployment.  Extra standalone workers simply add parallel capacity.

Every job is claimed atomically, so any number of workers may share one queue.
"""

from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from aiohttp import web
from pyrogram import Client, enums
from pyrogram.errors import FloodWait

from config import (API_HASH, API_ID, BACKUP_GROUP_ID, BOT_TOKEN, CONCURRENCY_LIMIT, EDIT_INTERVAL, EXPANSION,
                    LANGUAGES, PORT, VERSION, WORKER_HEARTBEAT_INTERVAL, WORKER_NODE_ID, WORKER_POLL_INTERVAL,
                    validate_config)
from database import MongoDatabase
from translator import (EXTRACTORS, WRITERS, TranslationEngine, build_chunks, close_http, normalise_text,
                        split_text_by_size)

log = logging.getLogger("worker")
DIV = "━━━━━━━━━━━━━━━━━━━━"


class JobCancelled(Exception):
    """Raised inside a job when the queue document was cancelled by the user/owner."""


def _bar(ratio: float, width: int = 12) -> str:
    filled = int(round(max(0.0, min(1.0, ratio)) * width))
    return "█" * filled + "░" * (width - filled)


def _fmt_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def _lang_label(code: str) -> str:
    name, flag = LANGUAGES.get(code, (code, ""))
    return f"{flag} {name}".strip()


def _fmt_size(num: float) -> str:
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


_UNSAFE_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    """Strip path separators / control characters so the delivered file keeps the novel's title."""
    name = _UNSAFE_FN.sub("", str(name or "")).strip(" .")
    return name[:90] or "Document"


def split_factor(lang: str) -> float:
    """Expected output/input byte ratio for ``lang`` (Hindi ≈ 2.6×, Chinese ≈ 0.9×).
    Used so the *delivered* parts respect the user's split size, not the source."""
    return float(EXPANSION.get(lang, 1.15))


class TranslationWorker:
    """Claims queued jobs, translates them and delivers the output via Telegram."""

    def __init__(self, client: Optional[Client] = None, db: Optional[MongoDatabase] = None,
                 node_id: Optional[str] = None, embedded: bool = False):
        self.embedded = embedded or client is not None
        self.node_id = node_id or (WORKER_NODE_ID if WORKER_NODE_ID != "worker-local" or not self.embedded
                                   else f"master-{uuid.uuid4().hex[:6]}")
        if self.node_id == "worker-local":
            self.node_id = f"worker-{uuid.uuid4().hex[:8]}"
        self.db = db or MongoDatabase()
        self.client = client or Client(self.node_id, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
                                       in_memory=True, no_updates=True, parse_mode=enums.ParseMode.HTML)
        self.stop_event = asyncio.Event()
        self.current_job: Optional[str] = None
        self.current_name: Optional[str] = None
        self.jobs_done = 0
        self.jobs_failed = 0
        self.started = time.time()
        self.runner: Optional[web.AppRunner] = None
        # Backup group (0 = disabled). Overridable per instance for tests / masters.
        self.backup_group: int = int(BACKUP_GROUP_ID or 0)
        # Optional callback set by the master for the embedded worker: receives
        # every finished-job record so the master's RAM cache (history + stats)
        # reflects the result immediately instead of on the next DB sync.
        self.on_finished = None

    # ── telegram helpers (never raise) ────────────────────────────────────
    async def _send(self, chat_id: int, text: str, **kw):
        try:
            return await self.client.send_message(chat_id, text, disable_web_page_preview=True, **kw)
        except FloodWait as e:
            await asyncio.sleep(min(float(e.value) + 1, 60))
            try:
                return await self.client.send_message(chat_id, text, disable_web_page_preview=True, **kw)
            except Exception:
                return None
        except Exception as e:
            log.debug("send_message failed: %s", e)
            return None

    async def _edit(self, chat_id: int, message_id: Optional[int], text: str) -> None:
        if not message_id:
            return
        try:
            await self.client.edit_message_text(chat_id, message_id, text, disable_web_page_preview=True)
        except FloodWait as e:
            await asyncio.sleep(min(float(e.value) + 1, 60))
        except Exception as e:
            log.debug("edit_message_text failed: %s", e)

    async def _send_document(self, chat_id: int, path: str, caption: str) -> bool:
        for attempt in range(3):
            try:
                await self.client.send_document(chat_id, path, caption=caption)
                return True
            except FloodWait as e:
                await asyncio.sleep(min(float(e.value) + 1, 90))
            except Exception as e:
                log.warning("send_document failed (attempt %d): %s", attempt + 1, e)
                await asyncio.sleep(3 * (attempt + 1))
        return False

    # ── backup group (optional, BACKUP_GROUP_ID) ──────────────────────────
    # Every worker — embedded or standalone — mirrors what it delivers into the
    # owner's backup group: one forum topic per job (when the group has topics
    # enabled), a "New Job" card, every delivered part and a final summary.
    # All of it is best-effort: the group must never break a user's job.
    async def _backup_topic(self, title: str) -> Optional[int]:
        if not self.backup_group:
            return None
        try:
            from pyrogram.raw import functions
            peer = await self.client.resolve_peer(self.backup_group)
            result = await self.client.invoke(functions.channels.CreateForumTopic(
                channel=peer, title=str(title)[:128], random_id=random.randint(1, 2 ** 62)))
            for upd in getattr(result, "updates", []) or []:
                msg = getattr(upd, "message", None)
                if msg is not None and hasattr(msg, "id"):
                    return int(msg.id)
        except Exception as e:
            log.debug("backup topic not created (forum topics disabled?): %s", e)
        return None

    async def _backup_text(self, text: str, topic_id: Optional[int]) -> None:
        if not self.backup_group:
            return
        kw = {"reply_to_message_id": topic_id} if topic_id else {}
        await self._send(self.backup_group, text, **kw)

    async def _backup_document(self, path: str, caption: str, topic_id: Optional[int]) -> None:
        if not self.backup_group:
            return
        kw = {"reply_to_message_id": topic_id} if topic_id else {}
        try:
            await self.client.send_document(self.backup_group, path, caption=caption, **kw)
        except FloodWait as e:
            await asyncio.sleep(min(float(e.value) + 1, 60))
            try:
                await self.client.send_document(self.backup_group, path, caption=caption, **kw)
            except Exception as e2:
                log.debug("backup send_document failed: %s", e2)
        except Exception as e:
            log.debug("backup send_document failed: %s", e)

    # ── http health (standalone only) ─────────────────────────────────────
    def status(self) -> dict:
        return {"status": "ok" if not self.stop_event.is_set() else "stopping", "role": "worker",
                "node_id": self.node_id, "embedded": self.embedded, "version": VERSION,
                "current_job": self.current_job, "current_name": self.current_name,
                "jobs_done": self.jobs_done, "jobs_failed": self.jobs_failed,
                "uptime_sec": int(time.time() - self.started), "db": self.db.connected}

    async def health(self, _request: web.Request) -> web.Response:
        return web.json_response(self.status())

    async def start_health(self) -> None:
        app = web.Application()
        app.router.add_get("/", self.health)
        app.router.add_get("/health", self.health)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "0.0.0.0", PORT).start()
        log.info("worker health server on :%d", PORT)

    # ── loops ─────────────────────────────────────────────────────────────
    async def heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.db.heartbeat(self.node_id, "running" if self.current_job else "idle", self.current_job,
                                        embedded=self.embedded, jobs_done=self.jobs_done,
                                        jobs_failed=self.jobs_failed, version=VERSION)
                if self.current_job and not await self.db.touch_job(self.current_job):
                    pass  # cancellation is picked up by the progress callback
            except Exception as e:
                log.debug("heartbeat failed: %s", e)
            try:
                await asyncio.wait_for(self.stop_event.wait(), WORKER_HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def recover_loop(self) -> None:
        """Requeue jobs whose worker vanished; prune finished queue docs."""
        while not self.stop_event.is_set():
            try:
                for job in await self.db.requeue_stale():
                    if job.get("requeued"):
                        log.warning("job %s requeued (worker %s lost)", job["_id"], job.get("worker_id"))
                        await self._send(job["chat_id"], f"♻️ <b>{html.escape(job.get('name', 'Document'))}</b> "
                                                         "was handed back to the queue — its worker went offline.")
                    else:
                        log.error("job %s failed after repeated worker loss", job["_id"])
                        await self.db.delete_file(str(job.get("file_id", "")))
                        await self._send(job["chat_id"], f"⚠️ <b>{html.escape(job.get('name', 'Document'))}</b> "
                                                         "could not be completed. Please send the file again.")
                await self.db.prune_finished()
                # GridFS sources left behind by abandoned wizards / crashed nodes.
                # Cheap (one indexed query) so every worker may run it each minute.
                if hasattr(self.db, "prune_orphan_files"):
                    await self.db.prune_orphan_files()
            except Exception as e:
                log.debug("recover loop: %s", e)
            try:
                await asyncio.wait_for(self.stop_event.wait(), 60)
            except asyncio.TimeoutError:
                pass

    async def queue_loop(self) -> None:
        log.info("worker %s polling jobs_queue every %ss", self.node_id, WORKER_POLL_INTERVAL)
        while not self.stop_event.is_set():
            job = None
            try:
                job = await self.db.claim_job(self.node_id)
            except Exception as e:
                log.warning("claim_job failed: %s", e)
            if job:
                try:
                    await self.process(job)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # process() already handles job-level errors
                    log.exception("unexpected worker error: %s", e)
            else:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), WORKER_POLL_INTERVAL)
                except asyncio.TimeoutError:
                    pass

    # ── bookkeeping ───────────────────────────────────────────────────────
    async def _finish(self, job: dict, status: str, *, parts: int, chars: int, secs: int,
                      error: str = "", failed_chunks: int = 0) -> dict:
        """Persist one finished job: history row (``jobs``) + atomic counters
        (``users.stats`` / ``stats.global``). Uses the same keys as the legacy
        in-process runner so the Mini App / ``/mystats`` render both identically.
        Never raises — a bookkeeping failure must not turn a delivered job into a
        failed one."""
        uid = int(job.get("user_id", 0) or 0)
        entry = {"uid": uid, "job_id": str(job["job_id"]), "name": job.get("name", "Document"),
                 "lang": job.get("lang", "hi"), "fmt": job.get("fmt", "txt"),
                 "split": int(job.get("split_kb", 0) or 0), "size": int(job.get("size", 0) or 0),
                 "parts": int(parts), "chars": int(chars), "secs": int(secs), "status": status,
                 "user": job.get("user_name", ""), "worker": self.node_id, "ts": int(time.time())}
        if error:
            entry["error"] = error[:200]
        if failed_chunks:
            entry["failed_chunks"] = int(failed_chunks)
        try:
            await self.db.record_history(dict(entry))
        except Exception as e:
            log.warning("record_history failed for job %s: %s", entry["job_id"], e)
        bump = getattr(self.db, "bump_stats", None)
        if bump is not None:
            try:
                await bump(uid, parts=parts if status == "done" else 0,
                           chars=chars if status == "done" else 0, status=status)
            except Exception as e:
                log.warning("bump_stats failed for job %s: %s", entry["job_id"], e)
        if self.on_finished is not None:
            try:
                result = self.on_finished(dict(entry))
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                log.debug("on_finished hook failed: %s", e)
        return entry

    # ── one job ───────────────────────────────────────────────────────────
    async def process(self, job: dict) -> None:
        job_id = str(job["job_id"])
        name = job.get("name", "Document")
        chat_id = int(job["chat_id"])
        lang, fmt = job.get("lang", "hi"), job.get("fmt", "txt")
        self.current_job, self.current_name = job_id, name
        temp_dir = Path(tempfile.mkdtemp(prefix=f"noveltranslator-{job_id}-"))
        source = temp_dir / f"source{job.get('ext', '.txt')}"
        head = f"🔄 <b>Translating</b>\n{DIV}\n📘 <b>{html.escape(name)}</b>\n🌐 {_lang_label(lang)} · 📄 {fmt.upper()}\n{DIV}\n"
        msg = await self._send(chat_id, head + "📥 Fetching source…")
        msg_id = msg.id if msg else None
        cancel = asyncio.Event()
        last_edit = 0.0
        started = time.time()
        parts_sent = 0
        failed_chunks = 0
        text = ""
        keep_source = False
        topic_id: Optional[int] = None
        user_tag = (f"👤 {html.escape(str(job.get('user_name') or job.get('user_id', '')))} "
                    f"(<code>{job.get('user_id', '')}</code>)")
        try:
            await self.db.download_to(str(job["file_id"]), str(source))
            if not await self.db.touch_job(job_id, {"phase": "extracting", "ratio": 0}):
                raise JobCancelled()
            await self._edit(chat_id, msg_id, head + "📖 Extracting text…")
            extractor = EXTRACTORS.get(job.get("ext"), EXTRACTORS[".txt"])
            text = normalise_text(await asyncio.to_thread(extractor, str(source)))
            if len(text) < 20:
                raise ValueError("No readable text found in the document")
            # Output-aware split: Hindi/Bengali… outputs are ~2.6× the source bytes, so the
            # *delivered* parts — not the source slices — must respect the user's split size.
            parts = split_text_by_size(text, int(job.get("split_kb") or 0), split_factor(lang))
            n_parts = len(parts)
            stem = safe_filename(Path(safe_filename(name)).stem or name)   # sanitise BEFORE .stem: "My/Novel.epub" → "MyNovel"

            if self.backup_group:
                topic_id = await self._backup_topic(name)
                await self._backup_text(
                    f"📥 <b>New Job</b>\n{DIV}\n📘 <b>{html.escape(name)}</b>\n"
                    f"🌐 {_lang_label(lang)} · 📄 {fmt.upper()} · 💾 {_fmt_size(job.get('size', 0))}\n"
                    f"{user_tag}\n🧩 Parts: <b>{n_parts}</b> · 🔤 Chars: <b>{len(text):,}</b>"
                    f" · 🖥 <code>{html.escape(self.node_id)}</code>", topic_id)

            for part_index, part in enumerate(parts, 1):
                engine = TranslationEngine(lang, CONCURRENCY_LIMIT)

                async def progress(eng: TranslationEngine, _pi=part_index):
                    nonlocal last_edit
                    ratio = ((_pi - 1) + (eng.done / eng.total if eng.total else 0)) / n_parts
                    now = time.time()
                    if now - last_edit < EDIT_INTERVAL and eng.done < eng.total:
                        return
                    last_edit = now
                    alive = await self.db.touch_job(job_id, {
                        "phase": "translating", "part": _pi, "parts": n_parts, "chunks_done": eng.done,
                        "chunks_total": eng.total, "ratio": round(ratio, 4), "speed": round(eng.speed, 2),
                        "eta": int(eng.eta), "elapsed": int(now - started)})
                    if not alive:
                        cancel.set()
                        return
                    await self._edit(chat_id, msg_id, head +
                                     f"{_bar(ratio)} <b>{ratio * 100:.0f}%</b>\n"
                                     f"📑 Part {_pi}/{n_parts} · chunk {eng.done}/{eng.total}\n"
                                     f"⚡ {eng.speed:.1f} chunks/s · ⏳ ETA {_fmt_eta(eng.eta)}")

                try:
                    translated = await engine.run(build_chunks(part), cancel, progress)
                except asyncio.CancelledError:
                    if cancel.is_set():
                        raise JobCancelled()
                    raise
                if cancel.is_set():
                    raise JobCancelled()
                failed_chunks += engine.failed
                await self._edit(chat_id, msg_id, head + f"📝 Building part {part_index}/{n_parts} (.{fmt})…")
                output = temp_dir / (f"{stem}_{lang}_part{part_index:02d}.{fmt}" if n_parts > 1
                                     else f"{stem}_{lang}.{fmt}")
                part_title = f"{name} — Part {part_index} of {n_parts}" if n_parts > 1 else name
                await asyncio.to_thread(WRITERS[fmt], translated, str(output), part_title, lang)
                caption = (f"📘 <b>{html.escape(name)}</b>\n"
                           f"📦 Part {part_index} / {n_parts} · 🌐 {_lang_label(lang)}\n"
                           f"💾 {_fmt_size(output.stat().st_size)}")
                if not await self._send_document(chat_id, str(output), caption):
                    raise RuntimeError("Telegram upload failed")
                parts_sent += 1
                await self._backup_document(str(output), caption + "\n" + user_tag, topic_id)
                output.unlink(missing_ok=True)
                last_edit = 0.0                       # next part's first progress edit goes out immediately

            elapsed = int(time.time() - started)
            await self.db.update_job(job_id, status="done", finished_at=time.time(),
                                     progress={"phase": "complete", "ratio": 1, "parts": n_parts, "chars": len(text),
                                               "elapsed": elapsed, "failed_chunks": failed_chunks})
            warn = (f"\n⚠️ {failed_chunks} chunk{'s' if failed_chunks != 1 else ''} could not be translated "
                    "and were kept in the original language.") if failed_chunks else ""
            await self._edit(chat_id, msg_id,
                             f"✅ <b>Completed</b>\n{DIV}\n📘 <b>{html.escape(name)}</b>\n"
                             f"🌐 {_lang_label(lang)} · 📄 {fmt.upper()} · 📑 {n_parts} part{'s' if n_parts != 1 else ''}\n"
                             f"🔤 {len(text):,} characters · ⏱ {_fmt_eta(elapsed)}{warn}")
            await self._backup_text(
                f"✅ <b>Job Completed</b>\n{DIV}\n📘 <b>{html.escape(name)}</b>\n{user_tag}\n"
                f"🧩 Parts: <b>{n_parts}</b> · 🔤 Chars: <b>{len(text):,}</b> · ⏱ <b>{_fmt_eta(elapsed)}</b>"
                + (f"\n⚠️ {failed_chunks} untranslated chunk(s)" if failed_chunks else ""), topic_id)
            await self._finish(job, "done", parts=n_parts, chars=len(text), secs=elapsed, failed_chunks=failed_chunks)
            self.jobs_done += 1
            log.info("job %s done (%d parts, %d chars, %ds, %d failed chunks)", job_id, n_parts, len(text), elapsed,
                     failed_chunks)
        except JobCancelled:
            await self.db.update_job(job_id, status="cancelled", finished_at=time.time())
            await self._edit(chat_id, msg_id, f"🛑 <b>Cancelled</b>\n{DIV}\n📘 <b>{html.escape(name)}</b>"
                             + (f"\n📤 {parts_sent} part(s) were already delivered." if parts_sent else ""))
            await self._backup_text(f"🛑 Job cancelled: <b>{html.escape(name)}</b> · {user_tag}"
                                    + (f" · {parts_sent} part(s) delivered" if parts_sent else ""), topic_id)
            await self._finish(job, "cancelled", parts=parts_sent, chars=len(text),
                               secs=int(time.time() - started))
            log.info("job %s cancelled", job_id)
        except asyncio.CancelledError:
            # worker is shutting down — give the job back so another node picks it up
            keep_source = True
            await self.db.update_job(job_id, status="queued", worker_id=None,
                                     progress={"phase": "requeued", "ratio": 0})
            await self._edit(chat_id, msg_id, head + "♻️ Worker restarting — job handed back to the queue.")
            raise
        except Exception as error:
            self.jobs_failed += 1
            if isinstance(error, (ValueError, RuntimeError)):
                log.warning("job %s failed: %s: %s", job_id, type(error).__name__, error)
            else:
                log.exception("job %s failed: %s", job_id, error)
            await self.db.update_job(job_id, status="failed", error=type(error).__name__,
                                     error_text=str(error)[:200], finished_at=time.time())
            await self._edit(chat_id, msg_id,
                             f"⚠️ <b>Translation failed</b>\n{DIV}\n📘 <b>{html.escape(name)}</b>\n"
                             f"❗ {html.escape(type(error).__name__)}: {html.escape(str(error)[:120])}\n"
                             "Please try again in a few minutes.")
            await self._backup_text(f"⚠️ Job failed: <b>{html.escape(name)}</b> · {user_tag}\n"
                                    f"❗ {html.escape(type(error).__name__)}: {html.escape(str(error)[:160])}", topic_id)
            await self._finish(job, "failed", parts=parts_sent, chars=len(text),
                               secs=int(time.time() - started),
                               error=f"{type(error).__name__}: {error}")
        finally:
            if not keep_source:
                await self.db.delete_file(str(job.get("file_id", "")))
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.current_job = self.current_name = None

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def serve(self) -> None:
        """Run the worker loops until ``stop()`` is called (client/db already started)."""
        self.started = time.time()
        tasks = [asyncio.create_task(self.heartbeat_loop(), name=f"{self.node_id}-heartbeat"),
                 asyncio.create_task(self.recover_loop(), name=f"{self.node_id}-recover"),
                 asyncio.create_task(self.queue_loop(), name=f"{self.node_id}-queue")]
        try:
            await self.stop_event.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.db.heartbeat(self.node_id, "offline", None, embedded=self.embedded)
                await self.db.remove_worker(self.node_id)
            except Exception:
                pass

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        """Standalone entrypoint: own client, own DB connection, own health server."""
        validate_config(require_telegram=True, role="worker")
        if not await self.db.connect():
            raise SystemExit("worker could not connect to MongoDB — the queue lives there. "
                             "Check the connection error above (Atlas Network Access must allow 0.0.0.0/0).")
        await self.start_health()
        await self.client.start()
        me = await self.client.get_me()
        log.info("worker %s online as @%s | db=%s | v%s", self.node_id, me.username, self.db.db_name, VERSION)
        try:
            await self.serve()
        finally:
            await close_http()
            try:
                await self.client.stop()
            except Exception:
                pass
            if self.runner:
                await self.runner.cleanup()
            await self.db.close()


async def _standalone() -> None:
    worker = TranslationWorker()
    loop = asyncio.get_running_loop()
    try:
        import signal
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, worker.stop)
    except (NotImplementedError, ImportError):
        pass
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(_standalone())
    except KeyboardInterrupt:
        pass
