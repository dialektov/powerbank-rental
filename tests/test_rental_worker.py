import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from prometheus_client import REGISTRY

from rental_worker.app import jobs
from rental_worker.app.settings import Settings

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def worker(db, stubs, handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api") as api:
        yield jobs.Context(Settings(outbox_concurrency=1), db, api, stubs)


async def enqueue(db, order, kind, user="u1"):
    return await db.fetchval("""
        INSERT INTO payment_tasks (order_id, user_id, kind, amount, status, next_attempt_at, created_at)
        VALUES ($1, $2, $3, 50, 'PENDING', now(), now()) RETURNING id
    """, order, user, kind)


async def task_status(db, task):
    return await db.fetchval("SELECT status FROM payment_tasks WHERE id = $1", task)


async def test_payment_retry_after_lost_confirmation(db, stubs):
    order = uuid4()
    task = await enqueue(db, order, "CLEAR")
    callbacks = []

    def confirm(req):
        callbacks.append((req.url.path, json.loads(req.content)))
        if len(callbacks) == 1:
            raise httpx.ReadTimeout("lost acknowledgement", request=req)
        return httpx.Response(204)

    async with worker(db, stubs, confirm) as ctx:
        await jobs.process_outbox(ctx)
        row = await db.fetchrow("""
            SELECT status, attempts, next_attempt_at > now() AS delayed FROM payment_tasks WHERE id = $1
        """, task)
        assert tuple(row) == ("PENDING", 1, True)
        await db.execute("UPDATE payment_tasks SET next_attempt_at = now() WHERE id = $1", task)
        await asyncio.gather(jobs.process_outbox(ctx), jobs.process_outbox(ctx))
    assert callbacks == [(f"/internal/v1/orders/{order}/payment-result", {"kind": "CLEAR", "result": "success"})] * 2
    assert await task_status(db, task) == "DONE"
    assert (await stubs.get("/admin/payments/summary")).json() == {"clear_amount": 50, "clear_count": 1}


async def test_unavailable_payments_then_direct_clear(db, stubs):
    order = uuid4()
    hold = await enqueue(db, order, "HOLD")
    await stubs.post("/admin/faults", json={"service": "payments", "mode": "error"})
    callbacks = []

    def confirm(req):
        callbacks.append(json.loads(req.content))
        return httpx.Response(204)

    async with worker(db, stubs, confirm) as ctx:
        await jobs.process_outbox(ctx)
        assert await task_status(db, hold) == "PENDING"
        assert callbacks == []
        clear = await enqueue(db, order, "CLEAR")
        await stubs.post("/admin/faults", json={"service": "payments", "mode": "ok"})
        await jobs.process_outbox(ctx)
    assert callbacks == [{"kind": "CLEAR", "result": "success"}]
    assert await task_status(db, hold) == "FAILED"
    assert await task_status(db, clear) == "DONE"


async def test_clear_waits_for_inflight_hold_while_other_orders_progress(db, stubs):
    order = uuid4()
    hold = await enqueue(db, order, "HOLD")
    in_callback = asyncio.Event()
    release = asyncio.Event()
    callbacks = []

    async def confirm(req):
        body = json.loads(req.content)
        callbacks.append(body)
        if body == {"kind": "HOLD", "result": "success"}:
            in_callback.set()
            await release.wait()
        return httpx.Response(204)

    async with worker(db, stubs, confirm) as ctx:
        ctx.settings.outbox_concurrency = 2
        async with asyncio.TaskGroup() as group, asyncio.timeout(5):
            group.create_task(jobs.process_outbox(ctx))
            await in_callback.wait()
            clear = await enqueue(db, order, "CLEAR")
            other = await enqueue(db, uuid4(), "HOLD", user="poor-1")
            group.create_task(jobs.process_outbox(ctx))
            while await task_status(db, other) != "DONE":
                await asyncio.sleep(0.01)
            assert (await stubs.get("/admin/payments/summary")).json()["clear_count"] == 0
            release.set()
    assert [await task_status(db, task) for task in (hold, clear)] == ["DONE", "DONE"]
    assert callbacks == [
        {"kind": "HOLD", "result": "success"},
        {"kind": "HOLD", "result": "declined"},
        {"kind": "CLEAR", "result": "success"},
    ]


@pytest.mark.parametrize("failure", ["invalid_json", "unknown_result", "unexpected_ack"])
async def test_invalid_responses_leave_pending(db, stubs, failure):
    task = await enqueue(db, uuid4(), "CLEAR")

    def payment(req):
        if failure == "invalid_json":
            return httpx.Response(200, text="missing json")
        status = "success" if failure == "unexpected_ack" else "unknown"
        return httpx.Response(200, json={"status": status})

    async with httpx.AsyncClient(transport=httpx.MockTransport(payment), base_url="http://payments") as client:
        async with worker(db, client, lambda req: httpx.Response(200)) as ctx:
            await jobs.process_outbox(ctx)
    assert await task_status(db, task) == "PENDING"


async def test_batch_limit_and_scheduled_retries(db, stubs):
    deferred = await enqueue(db, uuid4(), "CLEAR")
    await db.execute("""
        UPDATE payment_tasks SET next_attempt_at = now() + interval '10 minutes' WHERE id = $1
    """, deferred)
    due = [await enqueue(db, uuid4(), "CLEAR") for _ in range(6)]
    async with worker(db, stubs, lambda req: httpx.Response(204)) as ctx:
        ctx.settings.outbox_batch_size = 4
        ctx.settings.outbox_concurrency = 3
        await jobs.process_outbox(ctx)
    assert [await task_status(db, task) for task in due] == ["DONE"] * 4 + ["PENDING"] * 2
    assert await task_status(db, deferred) == "PENDING"


async def test_maintenance_and_metrics(db, stubs):
    async def add_order(status, age, debt=False):
        order = uuid4()
        await db.execute("""
            INSERT INTO orders (id, offer_id, user_id, start_station_id, price_per_hour,
                free_period_min, deposit, status, payment_state, processing_until, created_at)
            VALUES ($1,$2,'u','s',50,5,300,$3,$4,now() - $5 * interval '1 second',now())
        """, order, uuid4(), status, "DEBT" if debt else "NONE", age)
        return order

    old_created = await add_order("CREATED", 70)
    old_ejecting = await add_order("EJECTING", 61, debt=True)
    await add_order("EJECTING", 59)
    await add_order("ACTIVE", 70)

    async def offer(age, order=None):
        return await db.fetchval("""
            INSERT INTO offers (id, user_id, station_id, tariff_id, price_per_hour, free_period_min,
                deposit, user_fallback, created_at, expires_at, order_id)
            VALUES ($1,'u','s','t',50,5,300,false,now() - $2 * interval '1 second',now(),$3)
            RETURNING id
        """, uuid4(), age, order)

    await offer(86401)
    fresh = await offer(86399)
    linked = await offer(90000, old_created)
    advanced = []

    def advance(req):
        advanced.append(req.url.path)
        return httpx.Response(503 if len(advanced) == 1 else 200)

    async with worker(db, stubs, advance) as ctx:
        await jobs.advance_stuck_orders(ctx)
        await jobs.cleanup_offers(ctx)
        await jobs.update_metrics(ctx)
        assert REGISTRY.get_sample_value("outbox_pending") == 0
        assert REGISTRY.get_sample_value("outbox_oldest_age_seconds") == 0
        assert REGISTRY.get_sample_value("payment_debt_open") == 1
        task = await enqueue(db, old_created, "HOLD")
        await db.execute("UPDATE payment_tasks SET created_at = now() - interval '1 minute' WHERE id = $1", task)
        await jobs.update_metrics(ctx)
        assert REGISTRY.get_sample_value("outbox_pending") == 1
        assert REGISTRY.get_sample_value("outbox_oldest_age_seconds") >= 60

    assert advanced == [f"/internal/v1/orders/{order}/advance" for order in (old_created, old_ejecting)]
    assert {r["id"] for r in await db.fetch("SELECT id FROM offers")} == {fresh, linked}
