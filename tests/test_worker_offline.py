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
    async def prune_orphan_files(self): self.orphan_sweeps = getattr(self, "orphan_sweeps", 0) + 1; return {"files": 0, "bytes": 0}
    async def claim_job(self, node): return None


# ── minimal in-memory stand-ins for Motor so database.MongoDatabase's GridFS
#    hygiene can be exercised without a MongoDB server ────────────────────────
class _Cursor:
    def __init__(self, rows): self._rows = list(rows)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._rows: raise StopAsyncIteration
        return self._rows.pop(0)
    async def to_list(self, n): return self._rows[:n]

def _match(doc, q):
    for k, cond in q.items():
        v = doc.get(k)
        if isinstance(cond, dict):
            for op, arg in cond.items():
                if op == "$in" and v not in arg: return False
                if op == "$lt" and not (v is not None and v < arg): return False
        elif v != cond: return False
    return True

class _Coll:
    def __init__(self, rows): self.rows = rows
    def find(self, q=None, proj=None): return _Cursor([r for r in self.rows if _match(r, q or {})])
    async def delete_many(self, q):
        keep = [r for r in self.rows if not _match(r, q)]; n = len(self.rows) - len(keep); self.rows[:] = keep
        class R: deleted_count = n
        return R()
    def aggregate(self, pipe):
        return _Cursor([{"n": len(self.rows), "bytes": sum(r.get("length", 0) for r in self.rows)}] if self.rows else [])

class _Bucket:
    def __init__(self, files): self.files = files; self.deleted = []
    async def delete(self, fid):
        self.deleted.append(fid); self.files.rows[:] = [f for f in self.files.rows if f["_id"] != fid]

class _DB(dict):
    def __getattr__(self, name): return self[name]

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

    # 6) GridFS hygiene: orphan sweep + prune_finished delete their sources
    import database
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc); old = now - timedelta(hours=5); fresh = now - timedelta(minutes=10)
    files = _Coll([
        {"_id": "live-old",   "length": 10, "uploadDate": old},    # referenced by a running job → keep
        {"_id": "orphan-old", "length": 20, "uploadDate": old},    # nobody references it → delete
        {"_id": "orphan-new", "length": 30, "uploadDate": fresh},  # too young (wizard may be open) → keep
        {"_id": "done-old",   "length": 40, "uploadDate": old},    # attached to a stale finished job → delete
    ])
    q = _Coll([
        {"_id": "a", "status": "running", "file_id": "live-old", "updated_at": time.time()},
        {"_id": "b", "status": "done",    "file_id": "done-old", "updated_at": time.time() - 48 * 3600},
        {"_id": "c", "status": "done",    "file_id": "",         "updated_at": time.time()},
    ])
    repo = database.MongoDatabase(uri="", db_name="fake")
    repo.db = _DB({"jobs_queue": q, "documents.files": files}); repo.files = _Bucket(files)
    st = await repo.prune_orphan_files(max_age_h=2)
    assert st == {"files": 2, "bytes": 60}, st
    assert sorted(repo.files.deleted) == ["done-old", "orphan-old"], repo.files.deleted
    assert st == {"files": 2, "bytes": 60} and (await repo.prune_orphan_files(max_age_h=2)) == {"files": 0, "bytes": 0}
    print("✅ orphan sweep ok:", repo.files.deleted)
    # prune_finished with a still-attached source: re-add the file and let it go through prune
    files.rows.append({"_id": "done-old", "length": 40, "uploadDate": old})
    dels = []
    async def _capture(fid): dels.append(fid)
    repo.delete_file = _capture           # real delete_file needs ObjectId strings
    n = await repo.prune_finished()
    assert n == 1 and dels == ["done-old"] and [r["_id"] for r in q.rows] == ["a", "c"], (n, dels, q.rows)
    stats = await repo.storage_stats(); assert stats == {"files": 3, "mb": 0.0}, stats   # live-old, orphan-new, re-added done-old
    print("✅ prune_finished deletes sources ok")
    # worker recover loop calls the sweep
    c, d = FakeClient(), FakeDB(src); w = worker.TranslationWorker(client=c, db=d, embedded=True)
    t = asyncio.create_task(w.serve()); await asyncio.sleep(0.3); w.stop(); await asyncio.wait_for(t, 5)
    assert getattr(d, "orphan_sweeps", 0) >= 1
    print("✅ recover loop runs orphan sweep")
    print("ALL WORKER TESTS PASSED")

asyncio.run(main())
