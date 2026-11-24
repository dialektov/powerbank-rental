import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.db import Database
from app.domain import pricing
from app.domain.models import Offer
from app.errors import NoPowerbanks, StationsUnavailable, TariffUnavailable
from app.ports import ConfigProvider, DependencyUnavailable, StationsClient, TariffsClient, UsersClient
from app.repo import offers as offers_repo


class CreateOffer:
    def __init__(
        self,
        db: Database,
        config: ConfigProvider,
        stations: StationsClient,
        tariffs: TariffsClient,
        users: UsersClient,
    ):
        self._db = db
        self._config = config
        self._stations = stations
        self._tariffs = tariffs
        self._users = users

    async def __call__(self, user_id: str, station_id: str) -> Offer:
        cfg = self._config.current()
        profile_task = asyncio.create_task(self._users.get_profile(user_id))
        try:
            try:
                station = await self._stations.get_station(station_id)
            except DependencyUnavailable as exc:
                raise StationsUnavailable() from exc

            available = pricing.count_available(station.slots)
            if available == 0:
                raise NoPowerbanks()

            try:
                tariff = await self._tariffs.get_tariff(station.tariff_id)
            except DependencyUnavailable as exc:
                raise TariffUnavailable() from exc

            profile = await profile_task
        finally:
            if not profile_task.done():
                profile_task.cancel()

        now = datetime.now(UTC)
        offer = Offer(
            id=uuid.uuid4(),
            user_id=user_id,
            station_id=station_id,
            tariff_id=tariff.id,
            price_per_hour=pricing.price_per_hour(
                tariff.price_per_hour, available, cfg.last_banks_threshold, cfg.last_banks_increase
            ),
            free_period_min=pricing.free_period_min(
                tariff.free_period_min, profile.has_subscription, cfg.subscriber_min_free_period_min
            ),
            deposit=pricing.deposit(tariff.default_deposit, profile.trusted),
            user_fallback=profile.is_fallback,
            created_at=now,
            expires_at=now + timedelta(seconds=cfg.offer_ttl_sec),
        )
        async with self._db.main.acquire() as conn:
            await offers_repo.insert(conn, offer)
        return offer
