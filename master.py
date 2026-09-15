"""Master process entrypoint.

The mature Telegram handlers and Mini App implementation remain compatible through
the ``bot`` compatibility module.  This entrypoint is intentionally the only
process that starts Telegram polling; translation execution belongs to worker.py.
"""

from bot import *  # noqa: F401,F403 - backwards-compatible handler surface
from bot import app, main as _legacy_master_main


async def main():
    """Run the Telegram polling master and its HTTP/Mini App server."""
    return await _legacy_master_main()


if __name__ == "__main__":
    app.run(main())