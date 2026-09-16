"""MongoDB persistence, GridFS storage and the atomic shared worker queue."""

from __future__ import annotations

import io
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, ReturnDocument

try:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
except ImportError:  # pragma: no cover
    AsyncIOMotorClient = None
    AsyncIOMotorGridFSBucket = None

from config import (DB_JOB_TTL_DAYS, JOB_MAX_REQUEUES, MONGO_DB, MONGO_URI, ORPHAN_FILE_AGE_H,
                    QUEUE_DONE_KEEP_H, WORKER_HEARTBEAT_INTERVAL, WORKER_STALE_AFTER)

FINISHED = ("done", "failed", "cancelled")
log = logging.getLogger("database")


class MongoDatabase:
    """One async Mongo client shared by a master or worker process."""

    def __init__(self, uri: str = MONGO_URI, db_name: str = MONGO_DB):
        self.uri, self.db_name = uri, db_name
        self.client = None
        self.db = None
        self.files = None

    @property
    def connected(self) -> bool:
        return self.db is not None

    async def connect(self) -> bool:
        if not self.uri or AsyncIOMotorClient is None:
            return False
        try:
            self.client = AsyncIOMotorClient(self.uri, serverSelectionTimeoutMS=8000,
                                             appname="noveltranslator-master-worker")
            await self.client.admin.command("ping")
            self.db = self.client[self.db_name]
            self.files = AsyncIOMotorGridFSBucket(self.db, bucket_name="documents")
        except Exception as e:
            log.error("MongoDB connection failed: %s: %s", type(e).__name__, e)
            self.client = self.db = self.files = None
            return False
        # Index maintenance is best-effort: a name/option conflict with an index the
        # master created earlier must never take the worker down.
        await self.ensure_indexes()
        return True

    async def _index(self, coll, keys, **opts) -> None:
        try:
            await coll.create_index(keys, **opts)
        except Exception as e:  # IndexOptionsConflict / IndexKeySpecsConflict etc.
            log.warning("index %s.%s: %s", coll.name, opts.get("name") or keys, e)

    async def ensure_indexes(self) -> None:
        if self.db is None:
            return
        # Default (auto-generated) names — identical to what earlier versions created.
        await self._index(self.db.jobs_queue, [("status", ASCENDING), ("created_at", ASCENDING)])
        await self._index(self.db.jobs_queue, "user_id")
        await self._index(self.db.jobs_queue, "updated_at")
        await self._index(self.db.workers_status, "last_seen")
        # Same names the master (bot.py) uses, so both processes agree on the `jobs` indexes.
        await self._index(self.db.jobs, [("uid", ASCENDING), ("ts", DESCENDING)], name="uid_ts")
        want = DB_JOB_TTL_DAYS * 86400
        try:
            existing = await self.db.jobs.index_information()
        except Exception:
            existing = {}
        for name, info in existing.items():
            keys = info.get("key") or []
            if keys and len(keys) == 1 and keys[0][0] == "ts" and "expireAfterSeconds" in info:
                if int(info["expireAfterSeconds"]) == want:
                    return  # TTL index already correct — leave it alone
                try:  # MongoDB cannot change expireAfterSeconds in place
                    await self.db.jobs.drop_index(name)
                except Exception as e:
                    log.debug("drop_index %s: %s", name, e)
        await self._index(self.db.jobs, "ts", name="ts_ttl", expireAfterSeconds=want)

    async def upload_bytes(self, filename: str, content: bytes, metadata: Dict[str, Any]) -> Optional[str]:
        if self.files is None:
            return None
        file_id = await self.files.upload_from_stream(filename, io.BytesIO(content), metadata=metadata)
        return str(file_id)

    async def download_to(self, file_id: str, path: str) -> None:
        if self.files is None:
            raise RuntimeError("MongoDB GridFS is not connected")
        target = io.BytesIO()
        await self.files.download_to_stream(ObjectId(file_id), target)
        Path(path).write_bytes(target.getvalue())

    async def delete_file(self, file_id: str) -> None:
        if self.files is not None and file_id:
            try:
                await self.files.delete(ObjectId(file_id))
            except Exception:
                pass

    async def enqueue_job(self, job: Dict[str, Any]) -> bool:
        if self.db is None:
            return False
        job = {**job, "_id": job["job_id"], "status": "queued", "requeues": 0,
               "created_at": time.time(), "updated_at": time.time()}
        await self.db.jobs_queue.replace_one({"_id": job["job_id"]}, job, upsert=True)
        return True

    async def claim_job(self, node_id: str) -> Optional[Dict[str, Any]]:
        if self.db is None:
            return None
        return await self.db.jobs_queue.find_one_and_update(
            {"status": "queued"},
            {"$set": {"status": "running", "worker_id": node_id, "started_at": time.time(),
                      "heartbeat": time.time(), "updated_at": time.time()}},
            sort=[("created_at", ASCENDING)], return_document=ReturnDocument.AFTER)

    async def update_job(self, job_id: str, **fields: Any) -> None:
        if self.db is not None:
            fields["updated_at"] = time.time()
            await self.db.jobs_queue.update_one({"_id": job_id}, {"$set": fields})

    async def touch_job(self, job_id: str, progress: Optional[Dict[str, Any]] = None) -> bool:
        """Refresh the job heartbeat (+ optional progress). Returns False when the
        job was cancelled meanwhile so the worker can stop early."""
        if self.db is None:
            return True
        fields: Dict[str, Any] = {"heartbeat": time.time(), "updated_at": time.time()}
        if progress is not None:
            fields["progress"] = progress
        doc = await self.db.jobs_queue.find_one_and_update(
            {"_id": job_id}, {"$set": fields}, projection={"status": 1}, return_document=ReturnDocument.AFTER)
        return bool(doc) and doc.get("status") == "running"

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.db is None:
            return None
        return await self.db.jobs_queue.find_one({"_id": job_id})

    async def queue_position(self, job_id: str) -> int:
        """1-based position of a queued job (0 = not queued)."""
        if self.db is None:
            return 0
        doc = await self.db.jobs_queue.find_one({"_id": job_id}, {"created_at": 1, "status": 1})
        if not doc or doc.get("status") != "queued":
            return 0
        ahead = await self.db.jobs_queue.count_documents(
            {"status": "queued", "created_at": {"$lt": doc.get("created_at", 0)}})
        return ahead + 1

    async def queued_count(self, user_id: Optional[int] = None) -> int:
        if self.db is None:
            return 0
        query: Dict[str, Any] = {"status": {"$in": ["queued", "running"]}}
        if user_id is not None:
            query["user_id"] = user_id
        return await self.db.jobs_queue.count_documents(query)

    async def requeue_stale(self) -> list[dict]:
        """Hand jobs back to the queue when their worker stopped sending heartbeats
        (crash / redeploy of a free instance). Jobs over JOB_MAX_REQUEUES fail."""
        if self.db is None:
            return []
        cutoff = time.time() - WORKER_STALE_AFTER
        stale = await self.db.jobs_queue.find(
            {"status": "running", "heartbeat": {"$lt": cutoff}}).to_list(100)
        touched = []
        for job in stale:
            requeues = int(job.get("requeues", 0) or 0)
            if requeues >= JOB_MAX_REQUEUES:
                update = {"$set": {"status": "failed", "error": "WorkerLost", "finished_at": time.time(),
                                   "updated_at": time.time()}}
            else:
                update = {"$set": {"status": "queued", "worker_id": None, "progress": {"phase": "requeued", "ratio": 0},
                                   "updated_at": time.time()}, "$inc": {"requeues": 1}}
            result = await self.db.jobs_queue.update_one(
                {"_id": job["_id"], "status": "running", "heartbeat": {"$lt": cutoff}}, update)
            if result.modified_count:
                job["requeued"] = requeues < JOB_MAX_REQUEUES
                touched.append(job)
        return touched

    async def prune_finished(self) -> int:
        """Drop finished queue documents older than QUEUE_DONE_KEEP_H (history lives in `jobs`).
        Any GridFS source still attached to such a document is removed first."""
        if self.db is None:
            return 0
        cutoff = time.time() - QUEUE_DONE_KEEP_H * 3600
        query = {"status": {"$in": list(FINISHED)}, "updated_at": {"$lt": cutoff}}
        async for doc in self.db.jobs_queue.find(query, {"file_id": 1}):
            await self.delete_file(str(doc.get("file_id") or ""))
        result = await self.db.jobs_queue.delete_many(query)
        return int(result.deleted_count)

    async def prune_orphan_files(self, max_age_h: float = ORPHAN_FILE_AGE_H) -> Dict[str, Any]:
        """Delete GridFS sources that no *live* (queued/running) job references.

        Sources become orphans when the options wizard is abandoned (the upload
        happens before the job is enqueued), when a master restarts mid-wizard, or
        when a worker dies between finishing a job and deleting its file. Only files
        older than ``max_age_h`` are touched so an upload whose wizard is still open
        (PENDING_TTL = 30 min) is never removed under the user's feet.
        Returns ``{"files": n, "bytes": b}``.
        """
        stats = {"files": 0, "bytes": 0}
        if self.db is None or self.files is None:
            return stats
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.0, max_age_h))
        live: set[str] = set()
        async for doc in self.db.jobs_queue.find({"status": {"$in": ["queued", "running"]}}, {"file_id": 1}):
            if doc.get("file_id"):
                live.add(str(doc["file_id"]))
        async for f in self.db["documents.files"].find({"uploadDate": {"$lt": cutoff}}, {"length": 1}):
            if str(f["_id"]) in live:
                continue
            try:
                await self.files.delete(f["_id"])
            except Exception as e:
                log.debug("orphan delete %s: %s", f["_id"], e)
                continue
            stats["files"] += 1
            stats["bytes"] += int(f.get("length", 0) or 0)
        if stats["files"]:
            log.info("pruned %d orphaned GridFS file(s), %.1f MB", stats["files"], stats["bytes"] / 1e6)
        return stats

    async def storage_stats(self) -> Dict[str, Any]:
        """GridFS usage summary for /health and the admin panel."""
        if self.db is None:
            return {"files": 0, "mb": 0.0}
        try:
            rows = await self.db["documents.files"].aggregate(
                [{"$group": {"_id": None, "n": {"$sum": 1}, "bytes": {"$sum": "$length"}}}]).to_list(1)
        except Exception:
            return {"files": 0, "mb": 0.0}
        if not rows:
            return {"files": 0, "mb": 0.0}
        return {"files": int(rows[0].get("n", 0)), "mb": round(int(rows[0].get("bytes", 0)) / 1e6, 2)}

    async def cancel_job(self, job_id: str, user_id: int, owner: bool = False) -> bool:
        if self.db is None:
            return False
        query = {"_id": job_id, "status": {"$in": ["queued", "running"]}}
        if not owner:
            query["user_id"] = user_id
        result = await self.db.jobs_queue.update_one(
            query, {"$set": {"status": "cancelled", "finished_at": time.time(), "updated_at": time.time()}})
        return result.modified_count == 1

    async def list_user_jobs(self, user_id: int, owner: bool = False, limit: int = 30) -> list[dict]:
        if self.db is None:
            return []
        query = {} if owner else {"user_id": user_id}
        rows = await self.db.jobs_queue.find(query).sort("created_at", DESCENDING).to_list(limit)
        return rows

    async def heartbeat(self, node_id: str, status: str = "idle", current_job: Optional[str] = None,
                        **extra: Any) -> None:
        if self.db is not None:
            await self.db.workers_status.update_one(
                {"_id": node_id},
                {"$set": {"node_id": node_id, "status": status, "current_job": current_job,
                          "last_seen": time.time(), "heartbeat_interval": WORKER_HEARTBEAT_INTERVAL, **extra}},
                upsert=True)

    async def workers(self) -> list[dict]:
        if self.db is None:
            return []
        cutoff = time.time() - max(WORKER_HEARTBEAT_INTERVAL * 3, 90)
        rows = await self.db.workers_status.find({"last_seen": {"$gte": cutoff}}, {"_id": 0}).sort("last_seen", DESCENDING).to_list(100)
        now = time.time()
        for row in rows:
            row["age"] = int(now - float(row.get("last_seen", now)))
        return rows

    async def remove_worker(self, node_id: str) -> None:
        if self.db is not None:
            await self.db.workers_status.delete_one({"_id": node_id})

    async def record_history(self, job: Dict[str, Any]) -> None:
        if self.db is not None:
            await self.db.jobs.insert_one({key: value for key, value in job.items() if key != "_id"})

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()


def job_repo_from_store(store: Any) -> Optional[MongoDatabase]:
    """Expose the queue repository from the legacy Store without duplicating clients."""
    if getattr(store, "db", None) is None:
        return None
    repo = MongoDatabase(uri="", db_name=MONGO_DB)
    repo.db = store.db
    repo.files = AsyncIOMotorGridFSBucket(store.db, bucket_name="documents") if AsyncIOMotorGridFSBucket else None
    return repo