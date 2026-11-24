from dataclasses import dataclass

import asyncpg

from app.settings import Settings


@dataclass
class Database:
    main: asyncpg.Pool
    # Отдельный пул для GET /orders/{id} и списка: ограничитель чтений при отказе Redis.
    read: asyncpg.Pool


async def connect(settings: Settings) -> Database:
    main = await asyncpg.create_pool(
        settings.database_url, min_size=2, max_size=settings.db_pool_size
    )
    read = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=settings.db_read_pool_size
    )
    return Database(main=main, read=read)


async def close(db: Database) -> None:
    await db.read.close()
    await db.main.close()
