"""Evidence for the live presentation, read from the same stores the services use."""
import asyncio
import json
import math
import os
import sys
import time
from collections import Counter
from contextlib import asynccontextmanager

from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
from redis.asyncio import Redis


@asynccontextmanager
async def payment_outage(client, stubs):
    url = stubs + "/admin/faults"
    try:
        (await client.post(url, json={"service": "payments", "mode": "error"})).raise_for_status()
        print("\nПлатежи отключены на тестовом стенде (HTTP 503). "
              "Четыре процесса Locust запускают 8 аренд через nginx, по 100 просмотров на аренду.\n")
        yield
    finally:
        (await client.post(url, json={"service": "payments", "mode": "ok"})).raise_for_status()
        print("\nПлатежи восстановлены. Ждём, пока rental-worker разберёт очередь…")


async def snapshot(db, client, stubs, prefix, traced_user, *, recovered):
    order = await db.fetchrow("""
        SELECT o.id, o.offer_id, o.powerbank_id, o.status, o.payment_state, o.total_amount,
               o.version, f.order_id AS linked_order
        FROM orders o JOIN offers f ON f.id = o.offer_id WHERE o.user_id = $1
    """, traced_user)
    if order is None:
        raise RuntimeError("Не найден заказ демонстрационного пользователя в PostgreSQL")
    tasks = await db.fetch("""
        SELECT kind, amount, status, attempts FROM payment_tasks WHERE order_id = $1 ORDER BY id
    """, order["id"])
    counts = await db.fetchrow("""
        SELECT (SELECT count(*) FROM offers WHERE starts_with(user_id, $1)) AS offers,
               count(*) AS orders, count(*) FILTER (WHERE payment_state = 'DEBT') AS debts,
               (SELECT count(*) FROM payment_tasks WHERE starts_with(user_id, $1) AND status = 'PENDING') AS pending
        FROM orders WHERE starts_with(user_id, $1)
    """, prefix)
    key = f"order:{order['id']}"
    async with Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True) as cache:
        raw, ttl = await cache.pipeline().hget(key, "d").ttl(key).execute()
    summary = await client.get(stubs + "/admin/payments/summary")
    summary.raise_for_status()
    issued = await client.get(stubs + "/admin/ejections")
    issued.raise_for_status()
    banks = [r["powerbank_id"] for r in issued.json() if r["order_id"] == str(order["id"])]
    payments = summary.json()["clear_count"]
    rubles = summary.json()["clear_amount"]

    print("\nПосле работы воркера" if recovered else "\nПосле возвратов, пока платежи недоступны")
    print(f"  PostgreSQL · offers={counts['offers']}, orders={counts['orders']}, "
          f"долгов={counts['debts']}, задач PENDING={counts['pending']}")
    print(f"  orders · {order['id']}  {order['status']} / {order['payment_state']}  "
          f"{order['total_amount']} ₽  версия {order['version']}")
    print(f"  offers · {order['offer_id']} → order_id={order['linked_order']}")
    for t in tasks:
        print(f"  payment_tasks · {t['kind']:5} {t['amount']:3} ₽  {t['status']}  неудачных попыток={t['attempts']}")
    if raw is None:
        print(f"  Redis · {key} отсутствует")
    else:
        cached = json.loads(raw)
        print(f"  Redis · {key}  {cached['status']} / {cached['payment_state']}  "
              f"версия {cached['version']}, TTL {ttl} с")
    print(f"  SQLite станции · выдачи по этому заказу={len(banks)}, банки={', '.join(banks)}")
    print(f"  SQLite платежей · успешных списаний за прогон={payments}, сумма={rubles} ₽")

    states = {t["kind"]: t["status"] for t in tasks}
    if order["linked_order"] != order["id"] or order["status"] != "FINISHED" or banks != [order["powerbank_id"]]:
        raise RuntimeError("Связь оффера, завершённого заказа и выдачи не подтверждена")
    if recovered:
        if order["payment_state"] != "CLEARED" or states != {"HOLD": "FAILED", "CLEAR": "DONE"}:
            raise RuntimeError("Отложенный холд и итоговое списание не завершились ожидаемым образом")
    elif order["payment_state"] != "DEBT" or states.get("CLEAR") != "PENDING" or payments or rubles:
        raise RuntimeError("Не подтверждён возврат в долг во время отказа платежей")

    if not recovered:
        return
    rows = await db.fetch("""
        SELECT id, offer_id, powerbank_id, status, payment_state, total_amount
        FROM orders WHERE starts_with(user_id, $1)
    """, prefix)
    expected = Counter((str(row["id"]), row["powerbank_id"]) for row in rows)
    actual = Counter((entry["order_id"], entry["powerbank_id"]) for entry in issued.json())
    if len(rows) != 8 or len({row["offer_id"] for row in rows}) != 8 or expected != actual:
        raise RuntimeError("Ожидалось восемь разных заказов, у каждого один оффер и одна выдача")
    if counts["pending"] or any((r["status"], r["payment_state"]) != ("FINISHED", "CLEARED") for r in rows):
        raise RuntimeError("Остались незавершённые аренды или платежи")
    if payments != len(rows) or rubles != sum(row["total_amount"] for row in rows):
        raise RuntimeError("Списания в журнале платежей расходятся с заказами PostgreSQL")
    return rubles


async def run_clients(prefix, traced_user):
    children = []
    try:
        for shard in range(4):
            indices = [str(i) for i in range(shard, 8, 4)]
            child = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).with_name("traffic.py")), prefix, traced_user, *indices,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            )
            children.append(child)
        async with asyncio.timeout(90):
            ready = await asyncio.gather(*(p.stdout.readline() for p in children))
            if any(line.strip() != b"ready" for line in ready):
                raise RuntimeError("Процесс Locust не запустился")
            start_at = time.time() + 0.5
            output = await asyncio.gather(*(p.communicate(f"{start_at}\n".encode()) for p in children))
        if any(p.returncode for p in children):
            raise RuntimeError("Клиент не завершил сценарий аренды")
        samples = [sample for stdout, _ in output for sample in json.loads(stdout)]
        if sum(name == "info" for _, name, _ in samples) != 800:
            raise RuntimeError("Ожидалось 800 просмотров")
        return start_at, samples
    finally:
        for child in children:
            if child.returncode is None:
                child.terminate()
                await child.wait()


async def run():
    prefix = f"demo-{uuid4().hex}-"
    traced_user = prefix + "1"
    stubs = os.environ["STUBS_URL"]
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        for url in (os.environ["RENTAL_URL"] + "/health/ready", stubs + "/configs",
                    "http://rental-api-1:8000/health/ready", "http://rental-api-2:8000/health/ready",
                    "http://rental-worker:9100/metrics"):
            async with asyncio.timeout(60):
                while True:
                    try:
                        (await client.get(url)).raise_for_status()
                        break
                    except httpx.HTTPError:
                        await asyncio.sleep(1)
        if (await client.get(stubs + "/configs")).raise_for_status().json()["billing_time_scale"] != 60:
            raise RuntimeError("Для показа требуется масштаб времени 60")

        db = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
        try:
            async with payment_outage(client, stubs):
                start_at, samples = await run_clients(prefix, traced_user)
                await snapshot(db, client, stubs, prefix, traced_user, recovered=False)
            async with asyncio.timeout(120):
                while await db.fetchval("""
                    SELECT EXISTS (SELECT 1 FROM payment_tasks WHERE starts_with(user_id, $1) AND status = 'PENDING')
                """, prefix):
                    await asyncio.sleep(1)
            rubles = await snapshot(db, client, stubs, prefix, traced_user, recovered=True)
        finally:
            await db.close()

    lines = ["# Прогон demo", "", f"PASS — 8 завершённых аренд, 800 просмотров, 8 выдач. "
             f"Очередь пуста, 8 списаний на {rubles} ₽ совпали с PostgreSQL.", "",
             "Клиентские измерения за первые 8 секунд:", "",
             "| Операция | RPS | p95, мс |", "|---|---:|---:|"]
    for operation in ("offer", "start", "info", "list", "return"):
        times = sorted(ms for ts, name, ms in samples if name == operation and start_at <= ts < start_at + 8)
        p95 = times[math.ceil(len(times) * .95) - 1] if times else "—"
        lines.append(f"| {operation} | {len(times) / 8:.2f} | {p95} |")
    return "\n".join(lines)


if __name__ == "__main__":
    report = Path("/results/report.md")
    try:
        result = asyncio.run(run())
    except Exception as exc:
        report.write_text(f"# Прогон demo\n\nFAIL — {exc!r}\n")
        raise
    report.write_text(result + "\n")
    print(result)
