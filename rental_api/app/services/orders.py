import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.db import Database
from app.domain import pricing
from app.domain.models import (
    Order,
    OrderStatus,
    PaymentKind,
    PaymentOutcome,
    PaymentState,
)
from app.domain.statuses import IN_PROGRESS
from app.errors import (
    NoPowerbanks,
    OfferExpired,
    OfferNotFound,
    OrderNotFound,
    PaymentDeclined,
    StationsUnavailable,
)
from app.metrics import ORDERS, ORDERS_IN_PROGRESS_RESPONSES, ORDERS_LEASE_TAKEOVER
from app.ports import ConfigProvider, DependencyUnavailable, OrdersCache, PaymentsClient, StationsClient
from app.repo import offers as offers_repo
from app.repo import orders as orders_repo
from app.repo import payment_tasks as tasks_repo

log = logging.getLogger(__name__)

_FAILURES = {
    PaymentDeclined.code: PaymentDeclined,
    NoPowerbanks.code: NoPowerbanks,
}


@dataclass(frozen=True)
class StartOrderResult:
    order: Order
    in_progress: bool


def raise_if_failed(order: Order) -> None:
    if order.status is OrderStatus.FAILED:
        raise _FAILURES.get(order.failure_code or "", NoPowerbanks)()


class OrderProcessor:
    """Доводит заказ, на который у вызывающего есть lease, от текущего шага до ACTIVE или FAILED.

    Каждый переход — условный UPDATE по ожидаемому статусу, внешние вызовы идут с ключом order_id,
    поэтому параллельный обработчик с истёкшим lease не задвоит ни холд, ни выдачу.
    """

    def __init__(
        self,
        db: Database,
        payments: PaymentsClient,
        stations: StationsClient,
        cache: OrdersCache,
    ):
        self._db = db
        self._payments = payments
        self._stations = stations
        self._cache = cache

    async def advance(self, order: Order) -> Order:
        if order.status is OrderStatus.CREATED:
            order = await self._hold(order)
        if order.status is OrderStatus.EJECTING:
            order = await self._eject(order)
        return order

    async def _hold(self, order: Order) -> Order:
        outcome: PaymentOutcome | None = PaymentOutcome.SUCCESS
        if order.deposit > 0:
            try:
                outcome = await self._payments.hold(order.user_id, order.id, order.deposit)
            except DependencyUnavailable:
                outcome = None

        async with self._db.main.acquire() as conn, conn.transaction():
            if outcome is PaymentOutcome.DECLINED:
                updated = await orders_repo.update(
                    conn,
                    order.id,
                    expected_status=OrderStatus.CREATED,
                    status=OrderStatus.FAILED,
                    failure_code=PaymentDeclined.code,
                    processing_until=None,
                )
            elif outcome is None:
                updated = await orders_repo.update(
                    conn,
                    order.id,
                    expected_status=OrderStatus.CREATED,
                    status=OrderStatus.EJECTING,
                    payment_state=PaymentState.DEBT,
                )
                if updated:
                    await tasks_repo.enqueue(conn, order.id, order.user_id, PaymentKind.HOLD, order.deposit)
            else:
                updated = await orders_repo.update(
                    conn,
                    order.id,
                    expected_status=OrderStatus.CREATED,
                    status=OrderStatus.EJECTING,
                    payment_state=PaymentState.HELD if order.deposit > 0 else PaymentState.NONE,
                )
            if updated is None:
                return await orders_repo.get(conn, order.id)

        await self._cache.put(updated)
        if updated.status is OrderStatus.FAILED:
            ORDERS.labels(updated.status.value).inc()
        return updated

    async def _eject(self, order: Order) -> Order:
        try:
            result = await self._stations.eject(order.start_station_id, order.id)
        except DependencyUnavailable as exc:
            # Отпускаем lease, чтобы повтор клиента сразу забрал заказ, а не получал 202 до истечения lease.
            async with self._db.main.acquire() as conn:
                await orders_repo.release_lease(conn, order.id)
            raise StationsUnavailable() from exc

        async with self._db.main.acquire() as conn, conn.transaction():
            if result.success and result.powerbank_id:
                updated = await orders_repo.update(
                    conn,
                    order.id,
                    expected_status=OrderStatus.EJECTING,
                    status=OrderStatus.ACTIVE,
                    powerbank_id=result.powerbank_id,
                    start_time=datetime.now(UTC),
                    processing_until=None,
                )
            else:
                updated = await orders_repo.update(
                    conn,
                    order.id,
                    expected_status=OrderStatus.EJECTING,
                    status=OrderStatus.FAILED,
                    failure_code=NoPowerbanks.code,
                    processing_until=None,
                )
                if updated and updated.payment_state in (PaymentState.HELD, PaymentState.DEBT):
                    await tasks_repo.enqueue(conn, order.id, order.user_id, PaymentKind.CLEAR, 0)
            if updated is None:
                return await orders_repo.get(conn, order.id)

        await self._cache.put(updated)
        ORDERS.labels(updated.status.value).inc()
        return updated


class StartOrder:
    def __init__(self, db: Database, config: ConfigProvider, processor: OrderProcessor, cache: OrdersCache):
        self._db = db
        self._config = config
        self._processor = processor
        self._cache = cache

    async def __call__(self, user_id: str, offer_id: UUID) -> StartOrderResult:
        cfg = self._config.current()
        created = False
        async with self._db.main.acquire() as conn:
            async with conn.transaction():
                offer = await offers_repo.get_for_update(conn, offer_id)
                if offer is None or offer.user_id != user_id:
                    raise OfferNotFound()
                if offer.order_id is not None:
                    order = await orders_repo.get(conn, offer.order_id)
                else:
                    # Свежесть проверяем только для нового заказа: повтор после TTL должен получить свой заказ.
                    if offer.expires_at <= datetime.now(UTC):
                        raise OfferExpired()
                    order = await orders_repo.insert(
                        conn,
                        Order(
                            id=uuid.uuid4(),
                            offer_id=offer.id,
                            user_id=user_id,
                            start_station_id=offer.station_id,
                            price_per_hour=offer.price_per_hour,
                            free_period_min=offer.free_period_min,
                            deposit=offer.deposit,
                            status=OrderStatus.CREATED,
                            payment_state=PaymentState.NONE,
                            created_at=datetime.now(UTC),
                        ),
                        lease_sec=cfg.order_lease_sec,
                    )
                    await offers_repo.set_order_id(conn, offer.id, order.id)
                    created = True

            if not created and order.status in IN_PROGRESS:
                taken = await orders_repo.take_lease(conn, order.id, cfg.order_lease_sec)
                if taken is None:
                    ORDERS_IN_PROGRESS_RESPONSES.inc()
                    return StartOrderResult(order=order, in_progress=True)
                ORDERS_LEASE_TAKEOVER.labels("client").inc()
                order = taken

        if created:
            await self._cache.put(order)
        if order.status in IN_PROGRESS:
            order = await self._processor.advance(order)
            if order.status in IN_PROGRESS:
                return StartOrderResult(order=order, in_progress=True)
        raise_if_failed(order)
        return StartOrderResult(order=order, in_progress=False)


class AdvanceOrder:
    def __init__(self, db: Database, config: ConfigProvider, processor: OrderProcessor):
        self._db = db
        self._config = config
        self._processor = processor

    async def __call__(self, order_id: UUID) -> Order:
        async with self._db.main.acquire() as conn:
            order = await orders_repo.get(conn, order_id)
            if order is None:
                raise OrderNotFound()
            if order.status not in IN_PROGRESS:
                return order
            taken = await orders_repo.take_lease(conn, order_id, self._config.current().order_lease_sec)
        if taken is None:
            return order
        ORDERS_LEASE_TAKEOVER.labels("worker").inc()
        try:
            return await self._processor.advance(taken)
        except StationsUnavailable:
            async with self._db.main.acquire() as conn:
                return await orders_repo.get(conn, order_id)


class ReturnPowerbank:
    RETURN_REPEAT_WINDOW_SEC = 3600

    def __init__(self, db: Database, config: ConfigProvider, cache: OrdersCache):
        self._db = db
        self._config = config
        self._cache = cache

    async def __call__(self, powerbank_id: str, station_id: str) -> Order:
        cfg = self._config.current()
        async with self._db.main.acquire() as conn, conn.transaction():
            order = await orders_repo.find_active_by_powerbank_for_update(conn, powerbank_id)
            if order is None:
                repeated = await orders_repo.find_recently_finished(
                    conn, powerbank_id, station_id, self.RETURN_REPEAT_WINDOW_SEC
                )
                if repeated:
                    return repeated
                log.warning(
                    "return of unknown powerbank %s at station %s", powerbank_id, station_id
                )
                raise OrderNotFound()

            now = datetime.now(UTC)
            amount = pricing.rental_cost(
                order.start_time, now, order.free_period_min, order.price_per_hour, cfg.billing_time_scale
            )
            finished = await orders_repo.update(
                conn,
                order.id,
                expected_status=OrderStatus.ACTIVE,
                status=OrderStatus.FINISHED,
                finish_time=now,
                finish_station_id=station_id,
                total_amount=amount,
            )
            await tasks_repo.enqueue(conn, order.id, order.user_id, PaymentKind.CLEAR, amount)

        await self._cache.put(finished)
        ORDERS.labels(OrderStatus.FINISHED.value).inc()
        return finished


class ApplyPaymentResult:
    _TRANSITIONS: dict[tuple[PaymentKind, PaymentOutcome], tuple[PaymentState, frozenset[PaymentState]]] = {
        (PaymentKind.HOLD, PaymentOutcome.SUCCESS): (
            PaymentState.HELD,
            frozenset({PaymentState.NONE, PaymentState.DEBT}),
        ),
        (PaymentKind.CLEAR, PaymentOutcome.SUCCESS): (
            PaymentState.CLEARED,
            frozenset({PaymentState.NONE, PaymentState.HELD, PaymentState.DEBT}),
        ),
        (PaymentKind.CLEAR, PaymentOutcome.DECLINED): (
            PaymentState.DEBT,
            frozenset({PaymentState.NONE, PaymentState.HELD}),
        ),
    }

    def __init__(self, db: Database, cache: OrdersCache):
        self._db = db
        self._cache = cache

    async def __call__(self, order_id: UUID, kind: PaymentKind, outcome: PaymentOutcome) -> None:
        async with self._db.main.acquire() as conn:
            order = await orders_repo.get(conn, order_id)
            if order is None:
                raise OrderNotFound()
            transition = self._TRANSITIONS.get((kind, outcome))
            if transition is None:
                return
            target, allowed_from = transition
            updated = await orders_repo.update(
                conn, order_id, expected_payment_states=allowed_from, payment_state=target
            )
        if updated:
            await self._cache.put(updated)
