"""Stateless translation worker; it never registers Telegram update handlers."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from aiohttp import web
from pyrogram import Client, enums

from config import API_HASH, API_ID, BOT_TOKEN, PORT, WORKER_HEARTBEAT_INTERVAL, WORKER_NODE_ID, WORKER_POLL_INTERVAL, validate_config
from database import MongoDatabase
from translator import EXTRACTORS, WRITERS, TranslationEngine, build_chunks, normalise_text, split_text_by_size


class TranslationWorker:
    def __init__(self):
        self.node_id = WORKER_NODE_ID or f"worker-{uuid.uuid4().hex[:8]}"
        self.db = MongoDatabase()
        self.client = Client(self.node_id, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
                             in_memory=True, parse_mode=enums.ParseMode.HTML)
        self.stop_event = asyncio.Event()
        self.current_job = None
        self.runner = None

    async def health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "role": "worker", "node_id": self.node_id,
                                  "current_job": self.current_job, "db": self.db.connected})

    async def start_health(self) -> None:
        app = web.Application(); app.router.add_get("/health", self.health)
        self.runner = web.AppRunner(app, access_log=None); await self.runner.setup()
        await web.TCPSite(self.runner, "0.0.0.0", PORT).start()

    async def heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            await self.db.heartbeat(self.node_id, "running" if self.current_job else "idle", self.current_job)
            try: await asyncio.wait_for(self.stop_event.wait(), WORKER_HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError: pass

    async def process(self, job: dict) -> None:
        job_id = job["job_id"]; self.current_job = job_id
        temp_dir = Path(tempfile.mkdtemp(prefix=f"noveltranslator-{job_id}-")); source = temp_dir / f"source{job.get('ext', '.txt')}"
        progress_message = None
        try:
            try:
                progress_message = await self.client.send_message(job["chat_id"], f"🔄 Processing {job['name']}…\nExtracting text")
            except Exception:
                pass
            await self.db.download_to(str(job["file_id"]), str(source))
            await self.db.update_job(job_id, progress={"phase": "extracting", "ratio": 0})
            text = normalise_text(await asyncio.to_thread(EXTRACTORS.get(job.get("ext"), EXTRACTORS[".txt"]), str(source)))
            if len(text) < 20: raise ValueError("No readable text found")
            parts = split_text_by_size(text, int(job.get("split_kb") or 0), 1.15)
            translated_parts = []
            for part_index, part in enumerate(parts, 1):
                engine = TranslationEngine(job["lang"])
                async def progress(current_engine):
                    ratio = ((part_index - 1) + (current_engine.done / current_engine.total if current_engine.total else 0)) / len(parts)
                    await self.db.update_job(job_id, progress={"phase": "translating", "part": part_index,
                        "parts": len(parts), "chunks_done": current_engine.done, "chunks_total": current_engine.total,
                        "ratio": ratio})
                    if progress_message:
                        try:
                            await self.client.edit_message_text(job["chat_id"], progress_message.id,
                                f"🔄 Translating {job['name']}\nPart {part_index}/{len(parts)} · {ratio * 100:.0f}%")
                        except Exception:
                            pass
                translated_parts.append(await engine.run(build_chunks(part), asyncio.Event(), progress))
                output = temp_dir / f"part-{part_index}.{job['fmt']}"
                await asyncio.to_thread(WRITERS[job["fmt"]], translated_parts[-1], str(output), job["name"], job["lang"])
                await self.client.send_document(job["chat_id"], str(output), caption=f"📘 {job['name']} · Part {part_index}/{len(parts)}")
            await self.db.update_job(job_id, status="done", finished_at=time.time(), progress={"phase": "complete", "ratio": 1, "parts": len(parts), "chars": len(text)})
            if progress_message:
                try: await self.client.edit_message_text(job["chat_id"], progress_message.id, f"✅ Completed {job['name']}")
                except Exception: pass
            await self.db.record_history({"uid": job["user_id"], "job_id": job_id, "name": job["name"], "lang": job["lang"], "fmt": job["fmt"], "split": job.get("split_kb", 0), "size": job.get("size", 0), "parts": len(parts), "chars": len(text), "status": "done", "ts": time.time()})
        except Exception as error:
            await self.db.update_job(job_id, status="failed", error=type(error).__name__, finished_at=time.time())
            try: await self.client.send_message(job["chat_id"], "⚠️ Translation failed. Please try again.")
            except Exception: pass
        finally:
            await self.db.delete_file(str(job.get("file_id", ""))); shutil.rmtree(temp_dir, ignore_errors=True); self.current_job = None

    async def queue_loop(self) -> None:
        while not self.stop_event.is_set():
            job = await self.db.claim_job(self.node_id)
            if job:
                await self.process(job)
            else:
                await asyncio.sleep(WORKER_POLL_INTERVAL)

    async def run(self) -> None:
        validate_config(require_telegram=True)
        await self.db.connect(); await self.start_health(); await self.client.start()
        tasks = [asyncio.create_task(self.heartbeat_loop()), asyncio.create_task(self.queue_loop())]
        try: await asyncio.Event().wait()
        finally:
            self.stop_event.set(); await asyncio.gather(*tasks, return_exceptions=True)
            await self.db.heartbeat(self.node_id, "offline", None); await self.client.stop()
            if self.runner: await self.runner.cleanup()
            await self.db.close()


if __name__ == "__main__":
    asyncio.run(TranslationWorker().run())