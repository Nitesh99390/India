#!/usr/bin/env python3
"""Local Mini App dev harness — runs ONLY the HTTP/API layer of bot.py with
fake jobs, no Telegram connection needed.

    MINIAPP_DEV_USER=777 python tests/dev_server.py   →  http://localhost:8080/app

Scenarios (env DEV_ROLE):  owner (default) · admin · user · locked · pending · expired · rejected · banned
    DEV_ROLE=pending python tests/dev_server.py     →  locked view with a pending request
"""
import asyncio
import os
import sys
import time
import uuid

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "dev")
os.environ.setdefault("BOT_TOKEN", "123:dev")
os.environ.setdefault("MONGO_URI", "off")          # RAM only — no network needed
os.environ.setdefault("PORT", "8080")
os.environ.setdefault("MINIAPP_DEV_USER", "777")
_ROLE = os.environ.get("DEV_ROLE", "owner").lower()
os.environ.setdefault("OWNER_ID", "777" if _ROLE == "owner" else "1")   # non-owner scenarios: someone else owns the bot
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

    # ── v5 access fixtures ──────────────────────────────────────────────
    st = bot.store
    me = int(os.environ.get("MINIAPP_DEV_USER", "777"))
    role = os.environ.get("DEV_ROLE", "owner").lower()
    if role != "owner":
        st.ensure_user(1, "Big Boss", "boss")
        st.ensure_user(me, "Dev User", "dev")
        # reset env-granted lifetime access so scenarios start from a clean slate
        st.users[me]["access"] = st._default_access(); st.users[me]["role"] = "user"
        if role == "admin":
            st.approve(me, "forever", 1); st.set_admin(me, True, 1)
        elif role == "user":
            st.approve(me, "1m", 1)
        elif role == "pending":
            st.request_access(me, "Dev User", "dev", "Please let me in, I translate a lot!")
        elif role == "expired":
            st.approve(me, "1w", 1); st.access(me).update({"status": bot.ACCESS_EXPIRED, "expires": int(time.time()) - 86400})
        elif role == "rejected":
            st.request_access(me, "Dev User", "dev"); st.reject(me, 1, "Not accepting new users this week")
        elif role == "banned":
            st.ban(me, 1, "Spam")
    # a few other users so the admin panel has something to show
    st.ensure_user(1001, "Aarav Sharma", "aarav"); st.request_access(1001, "Aarav Sharma", "aarav", "Translating Hindi web novels")
    st.ensure_user(1002, "Priya", "priya"); st.request_access(1002, "Priya", "priya")
    st.ensure_user(1003, "Ravi Kumar", "ravi"); st.approve(1003, "1m", st.owner_id)
    st.ensure_user(1004, "Meena", ""); st.approve(1004, "forever", st.owner_id)
    st.ensure_user(1005, "Old Timer", "old"); st.approve(1005, "1w", st.owner_id)
    st.access(1005).update({"status": bot.ACCESS_EXPIRED, "expires": int(time.time()) - 3 * 86400})
    st.ensure_user(1006, "Sad Panda", "sad"); st.request_access(1006, "Sad Panda", "sad"); st.reject(1006, st.owner_id, "No")
    st.ensure_user(1007, "Spammer", "spam"); st.ban(1007, st.owner_id, "Spam")
    st.ensure_user(1008, "Helper", "helper"); st.approve(1008, "forever", st.owner_id); st.set_admin(1008, True, st.owner_id)
    st.ensure_user(1009, "Expiring Soon", "soon"); st.approve(1009, "2d", st.owner_id)
    print(f"dev role={role} owner={st.owner_id} me={me} status={st.status(me)}", flush=True)

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
