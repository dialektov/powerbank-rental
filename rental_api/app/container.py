from dataclasses import dataclass

import httpx
import redis.asyncio as redis

from app import db as database
from app.cache.orders_cache import RedisOrdersCache
from app.clients.config import RemoteConfigProvider, load_defaults
from app.clients.http import HttpPaymentsClient, HttpStationsClient, HttpTariffsClient, HttpUsersClient
from app.db import Database
from app.services.cursor import CursorCodec
from app.services.offers import CreateOffer
from app.services.orders import AdvanceOrder, ApplyPaymentResult, OrderProcessor, ReturnPowerbank, StartOrder
from app.services.reads import OrderReads
from app.settings import Settings


@dataclass
class Container:
    db: Database
    http: httpx.AsyncClient
    redis: redis.Redis
    config: RemoteConfigProvider
    cache: RedisOrdersCache
    create_offer: CreateOffer
    start_order: StartOrder
    advance_order: AdvanceOrder
    return_powerbank: ReturnPowerbank
    apply_payment_result: ApplyPaymentResult
    reads: OrderReads


async def build(settings: Settings) -> Container:
    db = await database.connect(settings)
    http = httpx.AsyncClient(
        base_url=settings.stubs_url,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=100),
    )
    redis_client = redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_timeout_sec,
        socket_connect_timeout=settings.redis_timeout_sec,
    )

    config = RemoteConfigProvider(http, load_defaults(settings.config_defaults_path), settings.config_refresh_sec)
    await config.start()
    cache = RedisOrdersCache(redis_client, ttl_sec=settings.order_cache_ttl_sec)
    cache.start()

    stations = HttpStationsClient(http)
    payments = HttpPaymentsClient(http)
    processor = OrderProcessor(db, payments, stations, cache)

    return Container(
        db=db,
        http=http,
        redis=redis_client,
        config=config,
        cache=cache,
        create_offer=CreateOffer(db, config, stations, HttpTariffsClient(http, config), HttpUsersClient(http)),
        start_order=StartOrder(db, config, processor, cache),
        advance_order=AdvanceOrder(db, config, processor),
        return_powerbank=ReturnPowerbank(db, config, cache),
        apply_payment_result=ApplyPaymentResult(db, cache),
        reads=OrderReads(
            db,
            cache,
            config,
            CursorCodec(settings.cursor_secret, settings.cursor_ttl_sec),
            settings.db_read_acquire_timeout_sec,
            protection_enabled=settings.read_protection_enabled,
        ),
    )


async def close(container: Container) -> None:
    container.cache.stop()
    await container.config.stop()
    await container.http.aclose()
    await container.redis.aclose()
    await database.close(container.db)
