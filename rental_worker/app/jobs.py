from dataclasses import dataclass

import asyncpg
import httpx

from app.settings import Settings


@dataclass
class Context:
    settings: Settings
    db: asyncpg.Pool
    rental_api: httpx.AsyncClient
    stubs: httpx.AsyncClient


async def process_outbox(ctx: Context) -> None:
    """Пачка PENDING-задач из payment_tasks через FOR UPDATE SKIP LOCKED.

    Для каждой: вызвать payments (ключ order_id), затем
    POST /internal/v1/orders/{order_id}/payment-result, затем пометить задачу DONE.
    При ошибке: attempts += 1, next_attempt_at по экспоненте до payments_max_backoff_sec.
    """
    raise NotImplementedError


async def advance_stuck_orders(ctx: Context) -> None:
    """Заказы в CREATED или EJECTING, у которых processing_until старше stuck_after_sec:
    для каждого POST /internal/v1/orders/{order_id}/advance.
    """
    raise NotImplementedError


async def cleanup_offers(ctx: Context) -> None:
    """Удалить офферы без заказа старше offers_retention_sec."""
    raise NotImplementedError
