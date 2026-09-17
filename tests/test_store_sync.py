"""Offline test for the master-side stats/history sync (bot.Store) — no network.

    python tests/test_store_sync.py

Reproduces the "parts / jobs / chars stay 0" bug: a *worker* finishes a job and
writes the history row + $inc counters straight into MongoDB, while the master
only knew the counters that lived in its own RAM. The master must now:
  * pull those rows/counters into its cache (Store.sync_from_db),
  * mirror the embedded worker's result instantly (Store.ingest_finished),
  * never overwrite `users.stats` with a stale RAM copy (Store._save_user),
  * credit legacy in-process jobs with atomic $inc ops (Store.bump).
"""
import asyncio, os, sys, time
os.environ.update(API_ID="1", API_HASH="x", BOT_TOKEN="1234567890:TESTtesttesttesttesttesttesttestte",
                  MONGO_URI="off", OWNER_ID="1", KEEP_ALIVE="0")
os.environ.pop("RENDER_EXTERNAL_URL", None)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import bot  # noqa: E402


# ── tiny motor look-alike (only what Store touches) ──────────────────────────
class _Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, key, direction): self.rows.sort(key=lambda d: d.get(key, 0), reverse=direction == -1); return self
    def limit(self, n): self.rows = self.rows[:n]; return self
    def __aiter__(self):
        async def gen():
            for r in self.rows:
                yield dict(r)
        return gen()

def _match(doc, q):
    for k, cond in q.items():
        v = doc.get(k)
        if isinstance(cond, dict):
            if "$gt" in cond and not (v is not None and v > cond["$gt"]): return False
        elif v != cond: return False
    return True

class _Coll:
    def __init__(self): self.docs = {}; self.ops = []
    def find(self, q=None, proj=None): return _Cursor([d for d in self.docs.values() if _match(d, q or {})])
    async def find_one(self, q, proj=None):
        for d in self.docs.values():
            if _match(d, q): return dict(d)
        return None
    async def update_one(self, q, upd, upsert=False):
        self.ops.append(("update_one", q, upd, upsert))
        doc = next((d for d in self.docs.values() if _match(d, q)), None)
        if doc is None:
            if not upsert: return
            doc = dict(q); self.docs[q["_id"]] = doc
            for k, v in (upd.get("$setOnInsert") or {}).items(): doc[k] = v
        for k, v in (upd.get("$set") or {}).items(): doc[k] = v
        for k, v in (upd.get("$inc") or {}).items():
            if "." in k:
                a, b_ = k.split(".", 1); sub = doc.setdefault(a, {}); sub[b_] = int(sub.get(b_, 0) or 0) + v
            else:
                doc[k] = int(doc.get(k, 0) or 0) + v
    async def replace_one(self, q, doc, upsert=False):
        self.ops.append(("replace_one", q, doc, upsert)); self.docs[q["_id"]] = {**doc, "_id": q["_id"]}
    async def insert_one(self, doc):
        self.ops.append(("insert_one", doc)); self.docs[id(doc)] = dict(doc)
    async def delete_one(self, q): self.ops.append(("delete_one", q))

class FakeMotor:
    def __init__(self): self.users = _Coll(); self.stats = _Coll(); self.jobs = _Coll(); self.meta = _Coll(); self.audit = _Coll(); self.chats = _Coll()


async def drain(store):
    """Run every queued fire-and-forget write synchronously."""
    while store._writes is not None and not store._writes.empty():
        await store._writes.get_nowait()()


def fresh_store(db):
    s = bot.Store()
    s.db, s.connected, s._writes = db, True, asyncio.Queue()
    return s


_TS = [int(time.time()) - 100]

async def worker_finishes(db, uid, job_id, parts, chars, status="done", ts=None):
    """What worker.TranslationWorker._finish() + MongoDatabase.bump_stats() do on another node."""
    _TS[0] += 1                                       # distinct whole-second timestamps → deterministic order
    row = {"uid": uid, "job_id": job_id, "name": "Novel.txt", "lang": "hi", "fmt": "txt", "split": 0,
           "size": 1000, "parts": parts, "chars": chars, "secs": 3, "status": status, "user": "U",
           "worker": "worker-x", "ts": ts or _TS[0]}
    await db.jobs.insert_one(dict(row))
    now = int(time.time())
    if status == "done":
        await db.users.update_one({"_id": uid}, {"$inc": {"stats.jobs": 1, "stats.parts": parts, "stats.chars": chars},
                                                "$set": {"stats_updated": now}})
        await db.stats.update_one({"_id": "global"}, {"$inc": {"jobs": 1, "parts": parts, "chars": chars},
                                                      "$set": {"updated": now}}, upsert=True)
    else:
        await db.stats.update_one({"_id": "global"}, {"$inc": {status: 1}, "$set": {"updated": now}}, upsert=True)
    return row


async def main():
    uid = 5
    db = FakeMotor()
    store = fresh_store(db)
    bot.store = store                                # text_mystats()/text_owner() read the module global
    store.users[uid] = store._default_user("Tester")
    db.users.docs[uid] = {"_id": uid, "name": "Tester", "stats": {"jobs": 0, "parts": 0, "chars": 0}}
    assert store.users[uid]["stats"] == {"jobs": 0, "parts": 0, "chars": 0}

    # 1) external worker finishes a job → master sync picks up history + counters
    await worker_finishes(db, uid, "j1", parts=3, chars=40018)
    added = await store.sync_from_db(force=True)
    assert added == 1, added
    st = store.users[uid]["stats"]
    assert st == {"jobs": 1, "parts": 3, "chars": 40018}, st
    assert store.stats["jobs"] == 1 and store.stats["parts"] == 3 and store.stats["chars"] == 40018, store.stats
    h = store.user_history(uid)
    assert len(h) == 1 and h[0]["job_id"] == "j1" and h[0]["parts"] == 3 and h[0]["chars"] == 40018, h
    print("✅ sync_from_db credits worker job:", st, "| history:", len(h))

    # 2) /mystats and owner /stats text render the credited numbers
    txt = bot.text_mystats(uid)
    assert "40,018" in txt and "Parts delivered: <b>3</b>" in txt and "translated: <b>1</b>" in txt, txt
    owner_txt = bot.text_owner()
    assert "40,018" in owner_txt, owner_txt
    print("✅ /mystats + /stats render synced counters")

    # 3) throttle: a second sync right away is a no-op; force bypasses it
    await worker_finishes(db, uid, "j2", parts=1, chars=1000)
    assert await store.sync_from_db() == 0
    assert await store.sync_from_db(force=True) == 1
    assert store.users[uid]["stats"] == {"jobs": 2, "parts": 4, "chars": 41018}
    assert [r["job_id"] for r in store.user_history(uid)] == ["j2", "j1"]   # newest first
    print("✅ throttle + ordering ok")

    # 4) dedupe: same row synced twice never duplicates history
    assert await store.sync_from_db(force=True) == 0
    assert len(store.user_history(uid)) == 2
    assert not store.ingest_finished({"uid": uid, "job_id": "j2", "ts": int(time.time())})
    print("✅ dedupe ok")

    # 5) embedded hook: mirrors the row immediately and re-reads counters
    row = await worker_finishes(db, uid, "j3", parts=2, chars=5000)
    store.ingest_finished(row)  # what _embedded_job_finished does first
    assert store.user_history(uid)[0]["job_id"] == "j3"
    await store.sync_from_db(force=True)
    assert store.users[uid]["stats"] == {"jobs": 3, "parts": 6, "chars": 46018}, store.users[uid]["stats"]
    print("✅ embedded hook path ok")

    # 6) failed / cancelled rows are listed but never credited
    await worker_finishes(db, uid, "j4", parts=0, chars=0, status="failed")
    await worker_finishes(db, uid, "j5", parts=1, chars=999, status="cancelled")
    await store.sync_from_db(force=True)
    assert store.users[uid]["stats"] == {"jobs": 3, "parts": 6, "chars": 46018}
    assert store.stats["failed"] == 1 and store.stats["cancelled"] == 1 and store.stats["jobs"] == 3, store.stats
    assert len(store.user_history(uid)) == 5
    print("✅ failed/cancelled not credited:", store.stats)

    # 7) _save_user must NOT clobber stats that workers $inc-ed (the original bug)
    store.users[uid]["stats"] = {"jobs": 0, "parts": 0, "chars": 0}        # stale RAM copy
    store.users[uid]["prefs"] = {**store.users[uid].get("prefs", {}), "lang": "es"}
    store._save_user(uid)
    await drain(store)
    op = db.users.ops[-1]
    assert op[0] == "update_one" and "stats" not in op[2]["$set"], op
    assert db.users.docs[uid]["stats"] == {"jobs": 3, "parts": 6, "chars": 46018}, db.users.docs[uid]["stats"]
    assert db.users.docs[uid]["prefs"]["lang"] == "es"
    await store.sync_from_db(force=True)
    assert store.users[uid]["stats"] == {"jobs": 3, "parts": 6, "chars": 46018}   # cache heals from DB
    print("✅ _save_user preserves DB stats")

    # 8) legacy in-process bump() → atomic $inc (not replace_one) on users + global
    store.bump(uid, 4, 7777)
    store.bump(uid, 0, 0, failed=True)
    await drain(store)
    assert store.users[uid]["stats"] == {"jobs": 4, "parts": 10, "chars": 53795}
    assert db.users.docs[uid]["stats"] == {"jobs": 4, "parts": 10, "chars": 53795}
    g = db.stats.docs["global"]
    assert g["jobs"] == 4 and g["parts"] == 10 and g["chars"] == 53795 and g["failed"] == 2, g
    assert all(o[0] != "replace_one" for o in db.stats.ops), "global stats must never be replaced wholesale"
    print("✅ bump() uses $inc:", g)

    # 9) _load() on a fresh master (restart) sees the same numbers + string/None-safe stats
    db.users.docs[uid]["stats"]["chars"] = str(db.users.docs[uid]["stats"]["chars"])   # legacy bad type
    s2 = fresh_store(db)
    await s2._load()
    assert s2.users[uid]["stats"] == {"jobs": 4, "parts": 10, "chars": 53795}, s2.users[uid]["stats"]
    assert s2.stats["jobs"] == 4 and s2.stats["parts"] == 10
    assert [r["job_id"] for r in s2.user_history(uid)][:2] == ["j5", "j4"]
    assert s2._synced_ts > 0
    print("✅ restart (_load) consistent")

    # 10) RAM-only mode (no MongoDB) — sync is a harmless no-op
    s3 = bot.Store()
    assert await s3.sync_from_db(force=True) == 0
    s3.users[uid] = s3._default_user("X"); s3.bump(uid, 1, 10)
    assert s3.users[uid]["stats"]["parts"] == 1 and s3.stats["parts"] == 1
    print("✅ RAM-only mode ok")

    print("ALL STORE SYNC TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
