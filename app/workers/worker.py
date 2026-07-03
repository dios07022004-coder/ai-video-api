"""RQ worker bootstrap.

Run with:  python -m app.workers.worker   (or the `aivideo-worker` console script)

Ensures the database schema exists, configures logging, then blocks consuming
the generation queue. Scale by running multiple processes / pods — Redis is the
shared distributed queue, so N workers → N GPUs.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from rq import Worker

from app.config.settings import get_settings
from app.database import get_database
from app.logging import configure_logging, get_logger
from app.queue.broker import get_queue, get_redis

logger = get_logger("workers.worker")


async def _prepare() -> None:
    settings = get_settings()
    settings.ensure_directories()
    # Bootstrap schema for SQLite/dev; production uses migrations.
    db = get_database()
    await db.create_all()
    # Dispose + drop the cached engine: it is bound to this startup loop, but each
    # RQ job runs in its own loop and rebuilds a fresh engine (see pipeline.py).
    await db.dispose()
    get_database.cache_clear()


def main() -> int:
    settings = get_settings()
    configure_logging(
        level=settings.log_level, json_output=settings.log_json, log_dir=settings.log_dir, service="worker"
    )
    asyncio.run(_prepare())

    queue = get_queue()
    worker = Worker([queue], connection=get_redis())

    def _graceful(signum, _frame):  # noqa: ANN001
        logger.info("worker_shutdown_signal", signal=signum)
        worker.request_stop(signum, _frame)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _graceful)
        except (ValueError, OSError):
            pass  # not on main thread / unsupported platform

    logger.info("worker_started", queue=settings.queue_name, redis=settings.redis_url)
    worker.work(with_scheduler=True, logging_level=settings.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
