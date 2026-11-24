from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.domain.models import Order, OrderStatus, PaymentState

_COLUMNS = (
    "id, offer_id, user_id, start_station_id, finish_station_id, powerbank_id, price_per_hour, "
    "free_period_min, deposit, status, payment_state, failure_code, total_amount, processing_until, "
    "version, created_at, start_time, finish_time"
)

_UPDATABLE = frozenset(
    {
        "status",
        "payment_state",
        "failure_code",
        "powerbank_id",
        "total_amount",
        "processing_until",
        "start_time",
        "finish_time",
        "finish_station_id",
    }
)


def _to_order(row: asyncpg.Record) -> Order:
    data = dict(row)
    data["status"] = OrderStatus(data["status"])
    data["payment_state"] = PaymentState(data["payment_state"])
    return Order(**data)


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, (OrderStatus, PaymentState)) else value


async def insert(conn: asyncpg.Connection, order: Order, lease_sec: float) -> Order:
    row = await conn.fetchrow(
        f"""
        INSERT INTO orders (id, offer_id, user_id, start_station_id, price_per_hour, free_period_min,
                            deposit, status, payment_state, processing_until, version, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now() + make_interval(secs => $10), 1, $11)
        RETURNING {_COLUMNS}
        """,
        order.id,
        order.offer_id,
        order.user_id,
        order.start_station_id,
        order.price_per_hour,
        order.free_period_min,
        order.deposit,
        order.status.value,
        order.payment_state.value,
        float(lease_sec),
        order.created_at,
    )
    return _to_order(row)


async def get(conn: asyncpg.Connection, order_id: UUID) -> Order | None:
    row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM orders WHERE id = $1", order_id)
    return _to_order(row) if row else None


async def update(
    conn: asyncpg.Connection,
    order_id: UUID,
    *,
    expected_status: OrderStatus | None = None,
    expected_payment_states: Iterable[PaymentState] | None = None,
    **changes: Any,
) -> Order | None:
    """Условный UPDATE с version + 1. None, если заказ не в ожидаемом состоянии."""
    unknown = set(changes) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")

    params: list[Any] = [order_id]
    assignments = ["version = version + 1"]
    for column, value in changes.items():
        params.append(_plain(value))
        assignments.append(f"{column} = ${len(params)}")

    conditions = ["id = $1"]
    if expected_status is not None:
        params.append(expected_status.value)
        conditions.append(f"status = ${len(params)}")
    if expected_payment_states is not None:
        params.append([state.value for state in expected_payment_states])
        conditions.append(f"payment_state = ANY(${len(params)}::text[])")

    row = await conn.fetchrow(
        f"UPDATE orders SET {', '.join(assignments)} WHERE {' AND '.join(conditions)} RETURNING {_COLUMNS}",
        *params,
    )
    return _to_order(row) if row else None


async def take_lease(conn: asyncpg.Connection, order_id: UUID, lease_sec: float) -> Order | None:
    row = await conn.fetchrow(
        f"""
        UPDATE orders SET processing_until = now() + make_interval(secs => $2)
        WHERE id = $1
          AND status IN ('CREATED', 'EJECTING')
          AND (processing_until IS NULL OR processing_until < now())
        RETURNING {_COLUMNS}
        """,
        order_id,
        float(lease_sec),
    )
    return _to_order(row) if row else None


async def release_lease(conn: asyncpg.Connection, order_id: UUID) -> None:
    await conn.execute(
        "UPDATE orders SET processing_until = now() WHERE id = $1 AND status IN ('CREATED', 'EJECTING')",
        order_id,
    )


async def find_active_by_powerbank_for_update(conn: asyncpg.Connection, powerbank_id: str) -> Order | None:
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM orders WHERE powerbank_id = $1 AND status = 'ACTIVE' FOR UPDATE",
        powerbank_id,
    )
    return _to_order(row) if row else None


async def find_recently_finished(
    conn: asyncpg.Connection, powerbank_id: str, station_id: str, within_sec: float
) -> Order | None:
    row = await conn.fetchrow(
        f"""
        SELECT {_COLUMNS} FROM orders
        WHERE powerbank_id = $1 AND status = 'FINISHED' AND finish_station_id = $2
          AND finish_time > now() - make_interval(secs => $3)
        ORDER BY finish_time DESC
        LIMIT 1
        """,
        powerbank_id,
        station_id,
        float(within_sec),
    )
    return _to_order(row) if row else None


async def list_by_user(
    conn: asyncpg.Connection,
    user_id: str,
    limit: int,
    status: OrderStatus | None,
    after: tuple[datetime, UUID] | None,
) -> list[Order]:
    params: list[Any] = [user_id]
    conditions = ["user_id = $1"]
    if status is not None:
        params.append(status.value)
        conditions.append(f"status = ${len(params)}")
    if after is not None:
        params.extend(after)
        conditions.append(f"(created_at, id) < (${len(params) - 1}, ${len(params)})")
    params.append(limit)
    rows = await conn.fetch(
        f"""
        SELECT {_COLUMNS} FROM orders
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC, id DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    return [_to_order(row) for row in rows]
