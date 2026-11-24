import asyncio
import dataclasses
import json
import logging
import time
from datetime import datetime
from uuid import UUID

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.clients.resilience import BreakerState, CircuitBreaker
from app.domain.models import Order, OrderStatus, PaymentState
from app.metrics import ORDER_CACHE, ORDER_CACHE_WRITES

log = logging.getLogger(__name__)

_DATETIME_FIELDS = ("created_at", "processing_until", "start_time", "finish_time")

# Пишем, только если версия новее лежащей: медленный читатель не затрёт свежую запись старой.
_SET_IF_NEWER = """
local current = redis.call('HGET', KEYS[1], 'v')
if current and tonumber(current) >= tonumber(ARGV[1]) then
    return 0
end
redis.call('HSET', KEYS[1], 'v', ARGV[1], 'd', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""


def serialize(order: Order) -> str:
    data = dataclasses.asdict(order)
    data["id"] = str(order.id)
    data["offer_id"] = str(order.offer_id)
    data["status"] = order.status.value
    data["payment_state"] = order.payment_state.value
    for name in _DATETIME_FIELDS:
        value = data[name]
        data[name] = value.isoformat() if value else None
    return json.dumps(data, separators=(",", ":"))


def deserialize(raw: str | bytes) -> Order:
    data = json.loads(raw)
    data["id"] = UUID(data["id"])
    data["offer_id"] = UUID(data["offer_id"])
    data["status"] = OrderStatus(data["status"])
    data["payment_state"] = PaymentState(data["payment_state"])
    for name in _DATETIME_FIELDS:
        if data[name]:
            data[name] = datetime.fromisoformat(data[name])
    return Order(**data)


def _key(order_id: UUID) -> str:
    return f"order:{order_id}"


class RedisOrdersCache:
    """Look-aside кэш заказов с версиями. Ошибки Redis не пробрасываются: кэш необязателен."""

    def __init__(
        self,
        client: redis.Redis,
        ttl_sec: int = 600,
        repair_window_sec: float = 30.0,
        breaker: CircuitBreaker | None = None,
    ):
        self._client = client
        self._ttl_sec = ttl_sec
        self._repair_window_sec = repair_window_sec
        self._breaker = breaker or CircuitBreaker(min_calls=5, open_sec=2.0)
        self._set_if_newer = client.register_script(_SET_IF_NEWER)
        self._pending: dict[UUID, tuple[Order, float]] = {}
        self._repair_task: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        return self._breaker.state is not BreakerState.OPEN

    async def get(self, order_id: UUID) -> Order | None:
        if not self._breaker.allow():
            ORDER_CACHE.labels("error").inc()
            return None
        try:
            raw = await self._client.hget(_key(order_id), "d")
        except (RedisError, OSError, asyncio.TimeoutError):
            self._breaker.record(False)
            ORDER_CACHE.labels("error").inc()
            return None
        self._breaker.record(True)
        if raw is None:
            ORDER_CACHE.labels("miss").inc()
            return None
        ORDER_CACHE.labels("hit").inc()
        return deserialize(raw)

    async def put(self, order: Order) -> None:
        if not await self._write(order):
            existing = self._pending.get(order.id)
            if existing is None or existing[0].version <= order.version:
                self._pending[order.id] = (order, time.monotonic())

    async def _write(self, order: Order) -> bool:
        if not self._breaker.allow():
            ORDER_CACHE_WRITES.labels("error").inc()
            return False
        try:
            written = await self._set_if_newer(
                keys=[_key(order.id)], args=[order.version, serialize(order), self._ttl_sec]
            )
        except (RedisError, OSError, asyncio.TimeoutError):
            self._breaker.record(False)
            ORDER_CACHE_WRITES.labels("error").inc()
            return False
        self._breaker.record(True)
        ORDER_CACHE_WRITES.labels("written" if written else "stale_skipped").inc()
        return True

    async def _repair_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            for order_id, (order, since) in list(self._pending.items()):
                if now - since > self._repair_window_sec:
                    log.warning("giving up cache write-through", extra={"order_id": order_id})
                    self._pending.pop(order_id, None)
                elif await self._write(order) and self._pending.get(order_id, (None,))[0] is order:
                    self._pending.pop(order_id, None)

    def start(self) -> None:
        self._repair_task = asyncio.create_task(self._repair_loop())

    def stop(self) -> None:
        if self._repair_task:
            self._repair_task.cancel()


class NullOrdersCache:
    available = False

    async def get(self, order_id: UUID) -> Order | None:
        return None

    async def put(self, order: Order) -> None:
        return None
