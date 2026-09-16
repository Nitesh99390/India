"""Offline smoke test for worker.TranslationWorker (fake client + fake DB, no network).

    python tests/test_worker_offline.py
"""
import asyncio, os, sys, time
os.environ.update(API_ID="1", API_HASH="x", BOT_TOKEN="1:x", MONGO_URI="off", OWNER_ID="1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import translator, worker

class Msg:  # fake telegram message
    def __init__(self, i): self.id = i

class FakeClient:
    def __init__(self): self.sent=[]; self.edits=[]; self.docs=[]
    async def send_message(self, chat_id, text, **kw): self.sent.append(text); return Msg(len(self.sent))
    async def edit_message_text(self, chat_id, mid, text, **kw): self.edits.append(text)
    async def send_document(self, chat_id, path, caption=None, **kw):
        assert os.path.exists(path); self.docs.append((os.path.basename(path), caption)); return True

class FakeDB:
    connected = True; db_name = "fake"
    def __init__(self, src):
        self.src = src; self.jobs = {}; self.hb = []; self.hist = []; self.deleted = []; self.cancel_after = None
    async def download_to(self, fid, dest):
        with open(dest, "wb") as f: f.write(self.src)
    async def touch_job(self, jid, progress=None):
        j = self.jobs.setdefault(jid, {"status": "running"})
        if progress: j["progress"] = progress
        if self.cancel_after is not None and progress and progress.get("phase") == "translating" and progress.get("chunks_done", 0) >= self.cancel_after:
            j["status"] = "cancelled"
        return j["status"] == "running"
    async def update_job(self, jid, **f): self.jobs.setdefault(jid, {}).update(f)
    async def record_history(self, h): self.hist.append(h)
    async def delete_file(self, fid): self.deleted.append(fid)
    async def heartbeat(self, node, status, cur, **extra): self.hb.append((node, status, cur))
    async def remove_worker(self, node): pass
    async def requeue_stale(self): return []
    async def prune_finished(self): return 0
    async def claim_job(self, node): return None

async def fake_translate(self, idx, lines, cancel):
    await asyncio.sleep(0.01); self.done += 1
    return idx, [f"[{self.target}] " + l for l in lines]
translator.TranslationEngine._translate_one = fake_translate

async def main():
    text = ("Chapter one. " + "Hello world, this is a sentence. " * 40 + "\n\n") * 30
    src = text.encode()
    job = {"job_id": "j1", "file_id": "f1", "chat_id": 5, "user_id": 5, "name": "Novel.txt", "ext": ".txt",
           "lang": "hi", "fmt": "txt", "split_kb": 0, "size": len(src)}

    # 1) success path
    c, d = FakeClient(), FakeDB(src)
    w = worker.TranslationWorker(client=c, db=d, embedded=True)
    assert w.embedded and w.node_id.startswith("master-"), w.node_id
    await w.process(dict(job))
    assert d.jobs["j1"]["status"] == "done", d.jobs
    assert len(c.docs) == 1 and c.docs[0][0] == "Novel_hi.txt", c.docs
    assert "✅" in c.edits[-1] and d.hist[-1]["status"] == "done" and d.deleted == ["f1"]
    assert w.jobs_done == 1 and w.current_job is None
    print("✅ success path:", c.docs, "| edits:", len(c.edits))

    # 2) cancelled mid-way
    c, d = FakeClient(), FakeDB(src); d.cancel_after = 1
    w = worker.TranslationWorker(client=c, db=d, embedded=True)
    await w.process(dict(job, job_id="j2"))
    assert d.jobs["j2"]["status"] == "cancelled", d.jobs["j2"]
    assert not c.docs and "🛑" in c.edits[-1] and d.hist[-1]["status"] == "cancelled"
    print("✅ cancel path ok")

    # 3) failure (unreadable doc)
    c, d = FakeClient(), FakeDB(b"x")
    w = worker.TranslationWorker(client=c, db=d, embedded=True)
    await w.process(dict(job, job_id="j3"))
    assert d.jobs["j3"]["status"] == "failed" and d.jobs["j3"]["error"] == "ValueError"
    assert "⚠️" in c.edits[-1] and w.jobs_failed == 1
    print("✅ failure path ok:", d.jobs["j3"]["error_text"])

    # 4) serve()/stop() lifecycle with heartbeat
    c, d = FakeClient(), FakeDB(src)
    w = worker.TranslationWorker(client=c, db=d, embedded=True)
    t = asyncio.create_task(w.serve()); await asyncio.sleep(0.3); w.stop(); await asyncio.wait_for(t, 5)
    assert d.hb and d.hb[0][1] == "idle" and d.hb[-1][1] == "offline", d.hb
    print("✅ serve/stop lifecycle ok:", d.hb)

    # 5) standalone node id + status()
    w2 = worker.TranslationWorker(db=FakeDB(src))
    assert w2.node_id.startswith("worker-") and not w2.embedded
    s = w2.status(); assert s["role"] == "worker" and s["version"]
    print("✅ standalone ctor ok:", w2.node_id)
    print("ALL WORKER TESTS PASSED")

asyncio.run(main())
