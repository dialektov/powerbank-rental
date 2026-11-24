import asyncio
import logging
import random
from dataclasses import dataclass

import asyncpg
import httpx
from prometheus_client import Gauge

from .settings import Settings

log = logging.getLogger("rental-worker")
OUTBOX_PENDING = Gauge("outbox_pending", "Pending payment tasks")
OUTBOX_OLDEST = Gauge("outbox_oldest_age_seconds", "Age of the oldest pending payment task")
PAYMENT_DEBT = Gauge("payment_debt_open", "Orders with open payment debt")


@dataclass
class Context:
    settings: Settings
    db: asyncpg.Pool
    rental_api: httpx.AsyncClient
    stubs: httpx.AsyncClient


async def _deliver_payment(ctx: Context, task: asyncpg.Record) -> None:
    order_id = str(task["order_id"])
    kind = task["kind"]
    endpoint = "/hold-money-for-order" if kind == "HOLD" else "/clear-money-for-order"
    payment = await ctx.stubs.post(
        endpoint, json={"order_id": order_id, "user_id": task["user_id"], "amount": task["amount"]}
    )
    payment.raise_for_status()
    body = payment.json()
    if not isinstance(body, dict) or body.get("status") not in ("success", "declined"):
        raise ValueError("unexpected payment result")

    response = await ctx.rental_api.post(
        f"/internal/v1/orders/{order_id}/payment-result",
        json={"kind": kind, "result": body["status"]},
    )
    response.raise_for_status()
    if response.status_code != 204:
        raise ValueError(f"payment-result returned {response.status_code}, expected 204")


async def _process_one(ctx: Context) -> bool:
    # Lock only the task being delivered. Other workers can process unrelated orders.
    async with ctx.db.acquire() as conn, conn.transaction():
        task = await conn.fetchrow("""
            SELECT id, order_id, user_id, kind, amount, attempts
            FROM payment_tasks t
            WHERE status = 'PENDING' AND next_attempt_at <= now()
              AND (kind = 'CLEAR' OR NOT EXISTS (
                  SELECT 1 FROM payment_tasks c WHERE c.order_id = t.order_id AND c.kind = 'CLEAR'
              ))
            ORDER BY next_attempt_at, id
            LIMIT 1 FOR UPDATE SKIP LOCKED
        """)
        if task is None:
            return False

        if task["kind"] == "CLEAR":
            # This UPDATE waits for an in-flight HOLD before CLEAR can reach payments.
            await conn.execute("""
                UPDATE payment_tasks SET status = 'FAILED'
                WHERE order_id = $1 AND kind = 'HOLD' AND status = 'PENDING'
            """, task["order_id"])
        try:
            await _deliver_payment(ctx, task)
        except (httpx.HTTPError, ValueError) as exc:
            # Equal jitter preserves a minimum pause even when many retries are due.
            cap = ctx.settings.payments_max_backoff_sec
            window = min(2.0 ** min(task["attempts"], 63), cap)
            delay = random.uniform(window / 2, window)
            await conn.execute("""
                UPDATE payment_tasks SET attempts = attempts + 1,
                    next_attempt_at = clock_timestamp() + $2 * interval '1 second'
                WHERE id = $1
            """, task["id"], delay)
            log.warning("retry order_id=%s kind=%s in %.2fs: %s",
                        task["order_id"], task["kind"], delay, exc)
        else:
            await conn.execute("UPDATE payment_tasks SET status = 'DONE' WHERE id = $1", task["id"])
        return True


async def process_outbox(ctx: Context) -> None:
    budget = iter(range(ctx.settings.outbox_batch_size))

    async def drain() -> None:
        for _ in budget:
            if not await _process_one(ctx):
                break

    async with asyncio.TaskGroup() as group:
        for _ in range(ctx.settings.outbox_concurrency):
            group.create_task(drain())


async def advance_stuck_orders(ctx: Context) -> None:
    orders = await ctx.db.fetch("""
        SELECT id FROM orders WHERE status IN ('CREATED', 'EJECTING')
          AND processing_until < now() - $1 * interval '1 second'
        ORDER BY processing_until
    """, ctx.settings.stuck_after_sec)
    for order in orders:
        try:
            response = await ctx.rental_api.post(f"/internal/v1/orders/{order['id']}/advance")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("advance failed for order_id=%s: %s", order["id"], exc)


async def cleanup_offers(ctx: Context) -> None:
    await ctx.db.execute("""
        DELETE FROM offers WHERE order_id IS NULL
          AND created_at < now() - $1 * interval '1 second'
    """, ctx.settings.offers_retention_sec)


async def update_metrics(ctx: Context) -> None:
    metrics = await ctx.db.fetchrow("""
        SELECT count(*) AS pending,
               coalesce(extract(epoch FROM now() - min(created_at)), 0) AS oldest,
               (SELECT count(*) FROM orders WHERE payment_state = 'DEBT') AS debts
        FROM payment_tasks WHERE status = 'PENDING'
    """)
    OUTBOX_PENDING.set(metrics["pending"])
    OUTBOX_OLDEST.set(max(0, metrics["oldest"]))
    PAYMENT_DEBT.set(metrics["debts"])
