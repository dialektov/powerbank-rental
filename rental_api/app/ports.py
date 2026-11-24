from typing import Protocol
from uuid import UUID

from app.domain.models import (
    EjectResult,
    Order,
    PaymentOutcome,
    RentalConfig,
    StationData,
    Tariff,
    UserProfile,
)


class DependencyUnavailable(Exception):
    """Зависимость не ответила: таймаут, 5xx или открытый circuit breaker."""

    def __init__(self, dependency: str, reason: str = ""):
        super().__init__(f"{dependency} unavailable: {reason}" if reason else f"{dependency} unavailable")
        self.dependency = dependency


class StationsClient(Protocol):
    async def get_station(self, station_id: str) -> StationData: ...

    async def eject(self, station_id: str, order_id: UUID) -> EjectResult: ...


class TariffsClient(Protocol):
    async def get_tariff(self, tariff_id: str) -> Tariff: ...


class UsersClient(Protocol):
    async def get_profile(self, user_id: str) -> UserProfile: ...


class PaymentsClient(Protocol):
    async def hold(self, user_id: str, order_id: UUID, amount: int) -> PaymentOutcome: ...

    async def clear(self, user_id: str, order_id: UUID, amount: int) -> PaymentOutcome: ...


class ConfigProvider(Protocol):
    def current(self) -> RentalConfig: ...


class OrdersCache(Protocol):
    @property
    def available(self) -> bool: ...

    async def get(self, order_id: UUID) -> Order | None: ...

    async def put(self, order: Order) -> None: ...
