"""Offline smoke test for worker.TranslationWorker (fake client + fake DB, no network).

    python tests/test_worker_offline.py
"""
import asyncio, os, sys, time
os.environ.update(API_ID="1", API_HASH="x", BOT_TOKEN="1234567890:TESTtesttesttesttesttesttesttestte", MONGO_URI="off", OWNER_ID="1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import translator, worker

class Msg:  # fake telegram message
    def __init__(self, i): self.id = i

class FakeClient:
    """Records every Telegram call; ``group`` collects what lands in the backup group."""
    def __init__(self, backup_group=0):
        self.sent=[]; self.edits=[]; self.docs=[]; self.group=[]; self.backup_group = backup_group
        self.topics = 0; self.topic_fail = False
    async def send_message(self, chat_id, text, **kw):
        if self.backup_group and chat_id == self.backup_group:
            self.group.append(("text", text, kw.get("reply_to_message_id"))); return Msg(900 + len(self.group))
        self.sent.append(text); return Msg(len(self.sent))
    async def edit_message_text(self, chat_id, mid, text, **kw): self.edits.append(text)
    async def send_document(self, chat_id, path, caption=None, **kw):
        assert os.path.exists(path)
        if self.backup_group and chat_id == self.backup_group:
            self.group.append(("doc", os.path.basename(path), kw.get("reply_to_message_id"))); return True
        self.docs.append((os.path.basename(path), caption)); return True
    # raw API used by _backup_topic (forum topics)
    async def resolve_peer(self, chat_id): return ("peer", chat_id)
    async def invoke(self, query):
        if self.topic_fail: raise RuntimeError("TOPICS_DISABLED")
        self.topics += 1
        class U:  # one update carrying the topic's service message
            message = Msg(500 + self.topics)
        class R: updates = [U()]
        return R()

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
    async def bump_stats(self, uid, parts=0, chars=0, status="done"):
        self.bumps = getattr(self, "bumps", []); self.bumps.append((uid, parts, chars, status))
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
    # history row uses the same keys as the legacy runner + counters are $inc-ed
    h = d.hist[-1]
    n_chars = h["chars"]                       # normalise_text() trims whitespace → slightly < len(text)
    assert h["parts"] == 1 and 0.9 * len(text) < n_chars <= len(text) and h["uid"] == 5 and h["job_id"] == "j1", h
    assert {"secs", "user", "split", "size", "ts", "worker"} <= set(h), h.keys()
    assert d.bumps == [(5, 1, n_chars, "done")], d.bumps
    print("✅ success path:", c.docs, "| edits:", len(c.edits), "| bump:", d.bumps)

    # 2) cancelled mid-way
    c, d = FakeClient(), FakeDB(src); d.cancel_after = 1
    w = worker.TranslationWorker(client=c, db=d, embedded=True)
    await w.process(dict(job, job_id="j2"))
    assert d.jobs["j2"]["status"] == "cancelled", d.jobs["j2"]
    assert not c.docs and "🛑" in c.edits[-1] and d.hist[-1]["status"] == "cancelled"
    assert d.bumps == [(5, 0, 0, "cancelled")], d.bumps      # never credits parts/chars for a cancel
    print("✅ cancel path ok")

    # 3) failure (unreadable doc)
    c, d = FakeClient(), FakeDB(b"x")
    w = worker.TranslationWorker(client=c, db=d, embedded=True)
    await w.process(dict(job, job_id="j3"))
    assert d.jobs["j3"]["status"] == "failed" and d.jobs["j3"]["error"] == "ValueError"
    assert "⚠️" in c.edits[-1] and w.jobs_failed == 1
    assert d.bumps == [(5, 0, 0, "failed")] and d.hist[-1]["error"].startswith("ValueError"), (d.bumps, d.hist[-1])
    print("✅ failure path ok:", d.jobs["j3"]["error_text"])

    # 3b) on_finished hook (embedded master) receives the record; hook errors never break the job
    c, d = FakeClient(), FakeDB(src)
    w = worker.TranslationWorker(client=c, db=d, embedded=True)
    seen = []
    async def hook(entry): seen.append(entry); raise RuntimeError("boom")
    w.on_finished = hook
    await w.process(dict(job, job_id="j3b"))
    assert d.jobs["j3b"]["status"] == "done" and len(seen) == 1 and seen[0]["job_id"] == "j3b" and seen[0]["parts"] == 1
    print("✅ on_finished hook ok")

    # 3c) a DB without bump_stats (older schema) still works
    c, d = FakeClient(), FakeDB(src); del FakeDB.bump_stats
    try:
        w = worker.TranslationWorker(client=c, db=d, embedded=True)
        await w.process(dict(job, job_id="j3c"))
        assert d.jobs["j3c"]["status"] == "done" and d.hist[-1]["status"] == "done"
    finally:
        async def _bump(self, uid, parts=0, chars=0, status="done"):
            self.bumps = getattr(self, "bumps", []); self.bumps.append((uid, parts, chars, status))
        FakeDB.bump_stats = _bump
    print("✅ legacy db without bump_stats ok")

    # 3d) v6.7 — output-aware split: Hindi output is ~2.6× the source, so a 100 KB split of a
    #     ~40 KB English source must yield several parts (legacy factor 1.15 would give 1)
    assert worker.split_factor("hi") == 2.6 and worker.split_factor("zh-CN") == 0.9 and worker.split_factor("xx") == 1.15
    c, d = FakeClient(), FakeDB(src)
    w = worker.TranslationWorker(client=c, db=d, embedded=True)
    await w.process(dict(job, job_id="j3d", split_kb=100))
    n = d.jobs["j3d"]["progress"]["parts"]
    assert n == len(c.docs) >= 2, (n, c.docs)
    assert c.docs[0][0] == "Novel_hi_part01.txt" and c.docs[-1][0] == f"Novel_hi_part{n:02d}.txt", c.docs
    assert "Part 1 / %d" % n in c.docs[0][1] and "💾" in c.docs[0][1], c.docs[0][1]
    assert d.hist[-1]["parts"] == n and d.bumps == [(5, n, d.hist[-1]["chars"], "done")], (d.hist[-1], d.bumps)
    # …while a script that shrinks (Chinese) keeps the bigger, fewer parts
    c2, d2 = FakeClient(), FakeDB(src)
    await worker.TranslationWorker(client=c2, db=d2, embedded=True).process(dict(job, job_id="j3d2", split_kb=100, lang="zh-CN"))
    assert d2.jobs["j3d2"]["progress"]["parts"] < n, (d2.jobs["j3d2"]["progress"]["parts"], n)
    print(f"✅ output-aware split ok: hi → {n} parts, zh-CN → {d2.jobs['j3d2']['progress']['parts']} part(s)")

    # 3e) v6.7 — unsafe titles never leak path separators into the delivered file name
    c, d = FakeClient(), FakeDB(src)
    await worker.TranslationWorker(client=c, db=d, embedded=True).process(dict(job, job_id="j3e", name='My/Novel: "Vol<1>".epub'))
    assert c.docs[0][0] == "MyNovel Vol1_hi.txt", c.docs
    assert worker.safe_filename("../../etc/passwd") == "etcpasswd" and worker.safe_filename("  ") == "Document"
    print("✅ safe filename ok:", c.docs[0][0])

    # 3f) v6.7 — backup group: topic per job, New Job card, every part mirrored, completion summary
    c, d = FakeClient(backup_group=-100), FakeDB(src)
    w = worker.TranslationWorker(client=c, db=d, embedded=True); w.backup_group = -100
    await w.process(dict(job, job_id="j3f", split_kb=100))
    kinds = [g[0] for g in c.group]
    assert c.topics == 1 and kinds[0] == "text" and "New Job" in c.group[0][1] and kinds.count("doc") == n, (c.topics, kinds)
    assert "Job Completed" in c.group[-1][1] and all(g[2] == 501 for g in c.group), c.group   # all inside the topic
    assert len(c.docs) == n and d.jobs["j3f"]["status"] == "done"                           # user delivery unaffected
    print(f"✅ backup group ok: topic + {kinds.count('doc')} mirrored parts + summary")

    # 3g) backup group without forum topics (or any group error) must never break the job
    c, d = FakeClient(backup_group=-100), FakeDB(src); c.topic_fail = True
    w = worker.TranslationWorker(client=c, db=d, embedded=True); w.backup_group = -100
    await w.process(dict(job, job_id="j3g"))
    assert d.jobs["j3g"]["status"] == "done" and c.topics == 0 and all(g[2] is None for g in c.group) and len(c.group) == 3, c.group
    # cancelled / failed jobs post a one-line note to the group as well
    c, d = FakeClient(backup_group=-100), FakeDB(src); d.cancel_after = 1
    w = worker.TranslationWorker(client=c, db=d, embedded=True); w.backup_group = -100
    await w.process(dict(job, job_id="j3g2"))
    assert "cancelled" in c.group[-1][1].lower(), c.group[-1]
    c, d = FakeClient(backup_group=-100), FakeDB(b"x")
    w = worker.TranslationWorker(client=c, db=d, embedded=True); w.backup_group = -100
    await w.process(dict(job, job_id="j3g3"))
    assert "failed" in c.group[-1][1].lower() and "ValueError" in c.group[-1][1], c.group[-1]
    # disabled (default BACKUP_GROUP_ID=0) → nothing is sent anywhere else
    c, d = FakeClient(backup_group=-100), FakeDB(src)
    await worker.TranslationWorker(client=c, db=d, embedded=True).process(dict(job, job_id="j3g4"))
    assert not c.group and c.topics == 0
    print("✅ backup group fallbacks ok (no topics · cancel · fail · disabled)")

    # 3h) v6.7 — chunks that exhausted every retry are reported, not silently kept
    real = translator.TranslationEngine._translate_one
    async def flaky(self, idx, lines, cancel):
        self.done += 1
        if idx == 0:
            self.failed += 1; return idx, lines
        return idx, [f"[{self.target}] " + l for l in lines]
    translator.TranslationEngine._translate_one = flaky
    try:
        c, d = FakeClient(backup_group=-100), FakeDB(src)
        w = worker.TranslationWorker(client=c, db=d, embedded=True); w.backup_group = -100
        await w.process(dict(job, job_id="j3h"))
    finally:
        translator.TranslationEngine._translate_one = real
    assert d.jobs["j3h"]["status"] == "done" and d.jobs["j3h"]["progress"]["failed_chunks"] == 1
    assert d.hist[-1]["failed_chunks"] == 1 and "could not be translated" in c.edits[-1] and "untranslated" in c.group[-1][1]
    print("✅ untranslated chunks reported ok")

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
