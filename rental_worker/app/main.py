import asyncio
import logging
from collections.abc import Awaitable, Callable

import asyncpg
import httpx
from prometheus_client import start_http_server

from . import jobs
from .settings import Settings

log = logging.getLogger("rental-worker")

Job = Callable[[jobs.Context], Awaitable[None]]


async def run_periodically(name: str, interval_sec: float, job: Job, ctx: jobs.Context) -> None:
    while True:
        try:
            await job(ctx)
        except Exception:
            log.exception("job %s failed", name)
        await asyncio.sleep(interval_sec)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    start_http_server(settings.metrics_port)

    db = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    async with (
        httpx.AsyncClient(base_url=settings.rental_internal_url, timeout=5.0) as rental_api,
        httpx.AsyncClient(base_url=settings.stubs_url, timeout=settings.payments_timeout_sec) as stubs,
    ):
        ctx = jobs.Context(settings=settings, db=db, rental_api=rental_api, stubs=stubs)
        log.info("rental-worker started")
        try:
            await asyncio.gather(
                run_periodically("outbox", settings.outbox_interval_sec, jobs.process_outbox, ctx),
                run_periodically("stuck_orders", settings.stuck_interval_sec, jobs.advance_stuck_orders, ctx),
                run_periodically("cleanup_offers", settings.cleanup_interval_sec, jobs.cleanup_offers, ctx),
                run_periodically("metrics", settings.metrics_interval_sec, jobs.update_metrics, ctx),
            )
        finally:
            await db.close()


if __name__ == "__main__":
    asyncio.run(main())
