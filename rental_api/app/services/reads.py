import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from cachetools import TTLCache

from app.db import Database
from app.domain import pricing
from app.domain.models import Order, OrderStatus
from app.errors import Overloaded, OrderNotFound
from app.metrics import DB_READ_LIMITER_REJECTED, ORDER_CACHE, SINGLEFLIGHT_SHARED
from app.ports import ConfigProvider, OrdersCache
from app.repo import orders as orders_repo
from app.services.cursor import CursorCodec, CursorPosition

T = TypeVar("T")


@dataclass(frozen=True)
class OrderView:
    order: Order
    amount: int
    amount_is_final: bool
    calculated_at: datetime


def view(order: Order, now: datetime, time_scale: float) -> OrderView:
    if order.status is OrderStatus.FINISHED:
        return OrderView(order, order.total_amount or 0, True, now)
    if order.status is OrderStatus.ACTIVE and order.start_time:
        amount = pricing.rental_cost(
            order.start_time, now, order.free_period_min, order.price_per_hour, time_scale
        )
        return OrderView(order, amount, False, now)
    return OrderView(order, 0, order.status is OrderStatus.FAILED, now)


class SingleFlight:
    """Одновременные загрузки по одному ключу выполняются один раз, остальные ждут результат."""

    def __init__(self):
        self._inflight: dict[object, asyncio.Future] = {}

    async def do(self, key: object, fn: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None:
            SINGLEFLIGHT_SHARED.inc()
            return await asyncio.shield(existing)
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            result = await fn()
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()
            raise
        else:
            future.set_result(result)
            return result
        finally:
            self._inflight.pop(key, None)


class OrderReads:
    """Чтение заказов: Redis, при промахе БД через singleflight и ограничитель соединений.

    L1 в памяти процесса работает только при недоступном Redis, иначе он нарушил бы read-your-writes
    между инстансами.
    """

    def __init__(
        self,
        db: Database,
        cache: OrdersCache,
        config: ConfigProvider,
        cursors: CursorCodec,
        read_acquire_timeout_sec: float,
        protection_enabled: bool = True,
        l1_ttl_sec: float = 2.0,
    ):
        self._db = db
        self._cache = cache
        self._config = config
        self._cursors = cursors
        self._acquire_timeout = read_acquire_timeout_sec
        self._protection = protection_enabled
        self._singleflight = SingleFlight()
        self._l1: TTLCache[UUID, Order] = TTLCache(maxsize=100_000, ttl=l1_ttl_sec)

    @asynccontextmanager
    async def _read_connection(self):
        if not self._protection:
            async with self._db.main.acquire() as conn:
                yield conn
            return
        try:
            conn = await self._db.read.acquire(timeout=self._acquire_timeout)
        except asyncio.TimeoutError as exc:
            DB_READ_LIMITER_REJECTED.inc()
            raise Overloaded() from exc
        try:
            yield conn
        finally:
            await self._db.read.release(conn)

    async def _load(self, order_id: UUID) -> Order | None:
        async with self._read_connection() as conn:
            order = await orders_repo.get(conn, order_id)
        if order is not None:
            if self._cache.available:
                await self._cache.put(order)
            elif self._protection:
                self._l1[order_id] = order
        return order

    async def _get(self, order_id: UUID) -> Order | None:
        if self._cache.available:
            order = await self._cache.get(order_id)
            if order is not None:
                return order
        if self._protection:
            if not self._cache.available:
                cached = self._l1.get(order_id)
                if cached is not None:
                    ORDER_CACHE.labels("l1_hit").inc()
                    return cached
            return await self._singleflight.do(order_id, lambda: self._load(order_id))
        return await self._load(order_id)

    async def get(self, user_id: str, order_id: UUID) -> OrderView:
        order = await self._get(order_id)
        if order is None or order.user_id != user_id:
            raise OrderNotFound()
        return view(order, datetime.now(UTC), self._config.current().billing_time_scale)

    async def list(
        self, user_id: str, limit: int, status: OrderStatus | None, cursor: str | None
    ) -> tuple[list[OrderView], str | None]:
        status_value = status.value if status else None
        after = None
        if cursor:
            position = self._cursors.decode(cursor, user_id, status_value)
            after = (position.created_at, position.order_id)
        async with self._read_connection() as conn:
            orders = await orders_repo.list_by_user(conn, user_id, limit + 1, status, after)

        next_cursor = None
        if len(orders) > limit:
            orders = orders[:limit]
            last = orders[-1]
            next_cursor = self._cursors.encode(CursorPosition(last.created_at, last.id), user_id, status_value)
        now = datetime.now(UTC)
        scale = self._config.current().billing_time_scale
        return [view(order, now, scale) for order in orders], next_cursor
