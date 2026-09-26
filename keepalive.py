"""Cross-service keep-alive for the Render free tier.

Render puts a free web service to sleep after ~15 minutes without inbound
traffic.  Every process (master *and* every standalone worker) runs
:func:`keep_alive_loop`, which periodically sends a ``GET /health`` to:

* its **own** public URL (``RENDER_EXTERNAL_URL`` / ``PUBLIC_URL``),
* every URL listed in ``KEEP_ALIVE_URLS`` (comma-separated, optional),
* every node that recently wrote a heartbeat with a ``public_url`` to the
  shared ``workers_status`` collection (workers publish theirs, the master's
  embedded worker publishes the master's).

So the master wakes the workers, the workers wake the master and each other —
nobody ever sleeps, and no extra cron/uptime service is required.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Iterable, Optional

import aiohttp

from config import KEEP_ALIVE, KEEP_ALIVE_INTERVAL, KEEP_ALIVE_URLS, PUBLIC_URL, RENDER_EXTERNAL_URL

log = logging.getLogger("keepalive")

SELF_URL = (RENDER_EXTERNAL_URL or PUBLIC_URL).rstrip("/")
PING_TIMEOUT = aiohttp.ClientTimeout(total=25)
FIRST_PING_DELAY = 45  # seconds after boot before the first round


def _norm(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def ping_targets(self_url: str, extra: Iterable[str] = (), workers: Iterable[dict] = ()) -> list[str]:
    """Return the de-duplicated list of ``/health`` URLs to hit this round."""
    seen: list[str] = []
    for raw in [self_url, *extra, *(w.get("public_url", "") for w in workers)]:
        u = _norm(raw)
        if u and u not in seen:
            seen.append(u)
    return [f"{u}/health" for u in seen]


async def ping_once(session: aiohttp.ClientSession, url: str) -> Optional[int]:
    try:
        async with session.get(url, timeout=PING_TIMEOUT) as r:
            return r.status
    except Exception as e:  # network hiccup / target asleep and booting
        log.debug("keep-alive %s failed: %s", url, e)
        return None


async def keep_alive_loop(*, db=None, self_url: str = SELF_URL, stop: Callable[[], bool] = lambda: False,
                          interval: int = KEEP_ALIVE_INTERVAL, node: str = "node") -> None:
    """Ping self + all known services every ``interval`` seconds until ``stop()`` is True.

    ``db`` is an optional :class:`database.MongoDatabase`; when connected its
    ``keepalive_nodes()`` rows (``public_url`` field) are added to the target list.
    """
    if not KEEP_ALIVE:
        log.info("[%s] keep-alive disabled (KEEP_ALIVE=0)", node)
        return
    if not self_url and not KEEP_ALIVE_URLS and db is None:
        log.info("[%s] keep-alive: nothing to ping (no RENDER_EXTERNAL_URL / KEEP_ALIVE_URLS)", node)
        return
    log.info("[%s] keep-alive every %ds → self=%s extra=%s (+ every heartbeat node in MongoDB)",
             node, interval, self_url or "-", KEEP_ALIVE_URLS or "-")
    session: Optional[aiohttp.ClientSession] = None
    delay = FIRST_PING_DELAY
    try:
        while not stop():
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            delay = interval
            if stop():
                break
            workers: list[dict] = []
            if db is not None and getattr(db, "connected", False):
                try:
                    workers = await db.keepalive_nodes()
                except Exception as e:
                    log.debug("keep-alive: keepalive_nodes() failed: %s", e)
            targets = ping_targets(self_url, KEEP_ALIVE_URLS, workers)
            if not targets:
                continue
            if session is None or session.closed:
                session = aiohttp.ClientSession(headers={"User-Agent": "NovelTranslator-KeepAlive/1.0"})
            t0 = time.time()
            results = await asyncio.gather(*(ping_once(session, u) for u in targets))
            ok = sum(1 for s in results if s is not None and s < 500)
            log.info("[%s] keep-alive: %d/%d awake in %.1fs → %s", node, ok, len(targets), time.time() - t0,
                     ", ".join(f"{u.replace('https://', '').replace('/health', '')}={s or 'x'}"
                               for u, s in zip(targets, results)))
    finally:
        if session is not None and not session.closed:
            await session.close()
