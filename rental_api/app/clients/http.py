import time
from uuid import UUID

import httpx
from cachetools import LRUCache

from app.clients.resilience import Policy
from app.domain.models import EjectResult, PaymentOutcome, Slot, StationData, Tariff, UserProfile
from app.metrics import TARIFF_CACHE, USERS_FALLBACK
from app.ports import ConfigProvider, DependencyUnavailable


async def _get_json(http: httpx.AsyncClient, path: str, timeout: float, **params) -> dict:
    response = await http.get(path, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


async def _post_json(http: httpx.AsyncClient, path: str, timeout: float, body: dict) -> dict:
    response = await http.post(path, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()


class HttpStationsClient:
    def __init__(self, http: httpx.AsyncClient, policy: Policy | None = None):
        self._http = http
        self._policy = policy or Policy("stations")

    async def get_station(self, station_id: str) -> StationData:
        raw = await self._policy.call(
            lambda: _get_json(self._http, "/station-data", 0.3, id=station_id),
            retries=2,
            base_delay_sec=0.05,
        )
        return StationData(
            id=station_id,
            tariff_id=raw["tariff_id"],
            location=raw["location"],
            slots=[Slot(index=s["index"], empty=s["empty"], charge=s["charge"]) for s in raw["slots"]],
        )

    async def eject(self, station_id: str, order_id: UUID) -> EjectResult:
        raw = await self._policy.call(
            lambda: _post_json(
                self._http, "/eject-powerbank", 3.0, {"station_id": station_id, "order_id": str(order_id)}
            ),
            retries=1,
            base_delay_sec=0.2,
        )
        return EjectResult(success=bool(raw["success"]), powerbank_id=raw.get("powerbank_id") or None)


class HttpTariffsClient:
    """Look-aside LRU: запись младше tariff_cache_ttl_sec берётся из кэша, иначе из сервиса; при отказе ошибка."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        config: ConfigProvider,
        policy: Policy | None = None,
        maxsize: int = 10_000,
        clock=time.monotonic,
    ):
        self._http = http
        self._config = config
        self._policy = policy or Policy("tariffs")
        self._cache: LRUCache[str, tuple[Tariff, float]] = LRUCache(maxsize=maxsize)
        self._clock = clock

    async def get_tariff(self, tariff_id: str) -> Tariff:
        ttl = self._config.current().tariff_cache_ttl_sec
        entry = self._cache.get(tariff_id)
        if entry is not None and self._clock() - entry[1] < ttl:
            TARIFF_CACHE.labels("hit").inc()
            return entry[0]
        try:
            raw = await self._policy.call(
                lambda: _get_json(self._http, "/tariff", 0.2, id=tariff_id), retries=1, base_delay_sec=0.05
            )
        except DependencyUnavailable:
            TARIFF_CACHE.labels("stale_error").inc()
            raise
        tariff = Tariff(
            id=tariff_id,
            price_per_hour=int(raw["price_per_hour"]),
            free_period_min=int(raw["free_period_min"]),
            default_deposit=int(raw["default_deposit"]),
        )
        self._cache[tariff_id] = (tariff, self._clock())
        TARIFF_CACHE.labels("miss").inc()
        return tariff


class HttpUsersClient:
    """При недоступности users отдаёт жадный профиль: без подписки и без доверия."""

    def __init__(self, http: httpx.AsyncClient, policy: Policy | None = None):
        self._http = http
        self._policy = policy or Policy("users")

    async def get_profile(self, user_id: str) -> UserProfile:
        try:
            raw = await self._policy.call(lambda: _get_json(self._http, "/user-profile", 0.2, id=user_id))
        except DependencyUnavailable:
            USERS_FALLBACK.inc()
            return UserProfile(id=user_id, has_subscription=False, trusted=False, is_fallback=True)
        return UserProfile(
            id=user_id, has_subscription=bool(raw["has_subscription"]), trusted=bool(raw["trusted"])
        )


class HttpPaymentsClient:
    def __init__(self, http: httpx.AsyncClient, policy: Policy | None = None):
        self._http = http
        self._policy = policy or Policy("payments")

    async def _operation(self, path: str, user_id: str, order_id: UUID, amount: int) -> PaymentOutcome:
        body = {"user_id": user_id, "order_id": str(order_id), "amount": amount}
        raw = await self._policy.call(lambda: _post_json(self._http, path, 0.5, body), retries=1)
        return PaymentOutcome(raw["status"])

    async def hold(self, user_id: str, order_id: UUID, amount: int) -> PaymentOutcome:
        return await self._operation("/hold-money-for-order", user_id, order_id, amount)

    async def clear(self, user_id: str, order_id: UUID, amount: int) -> PaymentOutcome:
        return await self._operation("/clear-money-for-order", user_id, order_id, amount)
