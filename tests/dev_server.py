#!/usr/bin/env python3
"""Local Mini App dev harness — runs ONLY the HTTP/API layer of bot.py with
fake jobs, no Telegram connection needed.

    MINIAPP_DEV_USER=777 python tests/dev_server.py   →  http://localhost:8080/app
"""
import asyncio
import os
import sys
import time
import uuid

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "dev")
os.environ.setdefault("BOT_TOKEN", "123:dev")
os.environ.setdefault("SECURITY_CODE", "secret123")
os.environ.setdefault("PORT", "8080")
os.environ.setdefault("MINIAPP_DEV_USER", "777")
os.environ.setdefault("OWNER_ID", "777")
os.environ.setdefault("KEEP_ALIVE", "0")
os.environ.pop("RENDER_EXTERNAL_URL", None)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import bot  # noqa: E402


def mk(name, size, ext, lang, fmt, split, uid=777):
    return bot.Job(job_id=uuid.uuid4().hex[:10], chat_id=uid, user_id=uid, user_name="Dev User",
                   file_path="/tmp/" + name, ext=ext, novel_name=name, file_size=size,
                   lang=lang, out_format=fmt, split_kb=split)


async def main():
    bot.QUEUE_WAKE = asyncio.Event()
    await bot.start_health_server()
    await bot.store.connect()

    if os.environ.get("FAKE_JOBS", "1") == "1":
        j1 = mk("My Novel.epub", 2_400_000, ".epub", "hi", "txt", 500)
        j1.status = "running"
        eng = bot.TranslationEngine("hi")
        eng.total, eng.done, eng.started = 120, 45, time.time() - 90
        j1.progress.update({"parts": 4, "part": 2, "phase": "translating", "engine": eng})
        bot.ACTIVE = j1

        j2 = mk("Second Book.txt", 800_000, ".txt", "ta", "docx", 0)
        j2.status = "queued"
        bot.QUEUE.append(j2)

        j3 = mk("Pending Upload.docx", 300_000, ".docx", "hi", "txt", 500)
        bot.PENDING[j3.job_id] = j3

        bot.store.add_history(777, {"job_id": "h1", "name": "Old Book.epub", "lang": "hi", "fmt": "txt",
                                    "size": 1_500_000, "parts": 3, "chars": 950_000, "secs": 420,
                                    "status": "done", "user": "Dev User"})
        bot.store.add_history(777, {"job_id": "h2", "name": "Broken.pdf", "lang": "en", "fmt": "epub",
                                    "size": 50_000, "status": "failed", "error": "NoText", "user": "Dev User"})

        async def tick():
            while True:
                await asyncio.sleep(2)
                if eng.done < eng.total:
                    eng.done += 3
        asyncio.create_task(tick())
        print("fake jobs loaded; pending job id:", j3.job_id, flush=True)

    print(f"READY → http://localhost:{bot.PORT}/app", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
