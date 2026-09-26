"""Smoke test: the fleet keep-alive pings self + KEEP_ALIVE_URLS + every MongoDB node."""
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("MONGO_URI", "off")
os.environ["RENDER_EXTERNAL_URL"] = "http://127.0.0.1:18765"
os.environ["KEEP_ALIVE_URLS"] = "http://127.0.0.1:18766"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import web  # noqa: E402

import keepalive  # noqa: E402


class FakeDB:
    connected = True

    async def keepalive_nodes(self):
        return [{"node_id": "w1", "public_url": "http://127.0.0.1:18767"},
                {"node_id": "self", "public_url": "http://127.0.0.1:18765"}]  # duplicate of self → deduped


def test_ping_targets_dedupe():
    got = keepalive.ping_targets("https://a.onrender.com", ["b.onrender.com/"],
                                 [{"public_url": "https://a.onrender.com"}, {"public_url": ""}])
    assert got == ["https://a.onrender.com/health", "https://b.onrender.com/health"]


def test_loop_pings_every_node():
    keepalive.FIRST_PING_DELAY = 0.2
    hits = {18765: 0, 18766: 0, 18767: 0}

    async def mk(port):
        async def h(_r):
            hits[port] += 1
            return web.json_response({"ok": port})
        a = web.Application()
        a.router.add_get("/health", h)
        r = web.AppRunner(a)
        await r.setup()
        await web.TCPSite(r, "127.0.0.1", port).start()
        return r

    async def main():
        runners = [await mk(p) for p in hits]
        stopped = False
        t = asyncio.create_task(keepalive.keep_alive_loop(db=FakeDB(), stop=lambda: stopped, interval=60,
                                                          node="test", self_url=os.environ["RENDER_EXTERNAL_URL"]))
        await asyncio.sleep(1.5)
        stopped = True
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        for r in runners:
            await r.cleanup()

    asyncio.run(main())
    assert hits == {18765: 1, 18766: 1, 18767: 1}, hits


if __name__ == "__main__":
    test_ping_targets_dedupe()
    test_loop_pings_every_node()
    print("keepalive OK")
