import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.cache.orders_cache import deserialize, serialize
from app.domain.models import Order, OrderStatus, PaymentState
from app.errors import InvalidCursor
from app.services.cursor import CursorCodec, CursorPosition
from app.services.reads import SingleFlight, view

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def make_order(**changes) -> Order:
    base = dict(
        id=uuid.uuid4(),
        offer_id=uuid.uuid4(),
        user_id="u1",
        start_station_id="st-1",
        price_per_hour=50,
        free_period_min=5,
        deposit=300,
        status=OrderStatus.ACTIVE,
        payment_state=PaymentState.HELD,
        created_at=NOW,
        powerbank_id="pb-1",
        start_time=NOW,
    )
    base.update(changes)
    return Order(**base)


def test_cache_roundtrip_keeps_all_fields():
    order = make_order(version=3, finish_time=NOW + timedelta(hours=1), total_amount=50)
    assert deserialize(serialize(order)) == order


def test_active_order_amount_is_accrued_and_not_final():
    item = view(make_order(), NOW + timedelta(minutes=61), time_scale=1)
    assert (item.amount, item.amount_is_final) == (100, False)


def test_finished_order_amount_is_total():
    order = make_order(status=OrderStatus.FINISHED, total_amount=150)
    item = view(order, NOW + timedelta(days=1), time_scale=1)
    assert (item.amount, item.amount_is_final) == (150, True)


class TestCursor:
    codec = CursorCodec("secret", ttl_sec=100, clock=lambda: 1000)
    position = CursorPosition(NOW, uuid.uuid4())

    def test_roundtrip(self):
        cursor = self.codec.encode(self.position, "u1", "ACTIVE")
        assert self.codec.decode(cursor, "u1", "ACTIVE") == self.position

    @pytest.mark.parametrize(("user", "status"), [("u2", "ACTIVE"), ("u1", None)])
    def test_bound_to_user_and_filter(self, user, status):
        cursor = self.codec.encode(self.position, "u1", "ACTIVE")
        with pytest.raises(InvalidCursor):
            self.codec.decode(cursor, user, status)

    def test_tampered_cursor_rejected(self):
        cursor = self.codec.encode(self.position, "u1", None)
        payload, signature = cursor.split(".")
        with pytest.raises(InvalidCursor):
            self.codec.decode(payload[:-2] + "AA." + signature, "u1", None)

    def test_garbage_rejected(self):
        with pytest.raises(InvalidCursor):
            self.codec.decode("not-a-cursor", "u1", None)

    def test_expired_cursor_rejected(self):
        cursor = self.codec.encode(self.position, "u1", None)
        later = CursorCodec("secret", ttl_sec=100, clock=lambda: 1200)
        with pytest.raises(InvalidCursor):
            later.decode(cursor, "u1", None)


async def test_singleflight_runs_loader_once_for_concurrent_callers():
    calls = []
    release = asyncio.Event()

    async def loader():
        calls.append(1)
        await release.wait()
        return "value"

    flight = SingleFlight()
    tasks = [asyncio.create_task(flight.do("k", loader)) for _ in range(10)]
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(*tasks) == ["value"] * 10
    assert calls == [1]


async def test_singleflight_propagates_errors_to_all_waiters():
    release = asyncio.Event()

    async def loader():
        await release.wait()
        raise RuntimeError("db down")

    flight = SingleFlight()
    tasks = [asyncio.create_task(flight.do("k", loader)) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(r, RuntimeError) for r in results)
