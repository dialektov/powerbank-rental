from uuid import UUID

import asyncpg

from app.domain.models import PaymentKind


async def enqueue(conn: asyncpg.Connection, order_id: UUID, user_id: str, kind: PaymentKind, amount: int) -> None:
    await conn.execute(
        """
        INSERT INTO payment_tasks (order_id, user_id, kind, amount, status, next_attempt_at, created_at)
        VALUES ($1, $2, $3, $4, 'PENDING', now(), now())
        ON CONFLICT (order_id, kind) DO NOTHING
        """,
        order_id,
        user_id,
        kind.value,
        amount,
    )
