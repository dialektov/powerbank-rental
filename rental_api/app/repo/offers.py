from uuid import UUID

import asyncpg

from app.domain.models import Offer

_COLUMNS = (
    "id, user_id, station_id, tariff_id, price_per_hour, free_period_min, deposit, "
    "user_fallback, created_at, expires_at, order_id"
)


def _to_offer(row: asyncpg.Record) -> Offer:
    return Offer(**dict(row))


async def insert(conn: asyncpg.Connection, offer: Offer) -> None:
    await conn.execute(
        f"INSERT INTO offers ({_COLUMNS}) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
        offer.id,
        offer.user_id,
        offer.station_id,
        offer.tariff_id,
        offer.price_per_hour,
        offer.free_period_min,
        offer.deposit,
        offer.user_fallback,
        offer.created_at,
        offer.expires_at,
        offer.order_id,
    )


async def get_for_update(conn: asyncpg.Connection, offer_id: UUID) -> Offer | None:
    row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM offers WHERE id = $1 FOR UPDATE", offer_id)
    return _to_offer(row) if row else None


async def set_order_id(conn: asyncpg.Connection, offer_id: UUID, order_id: UUID) -> None:
    await conn.execute("UPDATE offers SET order_id = $2 WHERE id = $1", offer_id, order_id)
