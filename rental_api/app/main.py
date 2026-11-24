import logging
import uuid
from contextlib import asynccontextmanager
from time import perf_counter

import redis.asyncio as redis
from fastapi import FastAPI, Request
from prometheus_client import make_asgi_app

from app import db as database
from app.api import health, internal, offers, orders
from app.errors import register_error_handlers
from app.logging_setup import request_id_var, setup_logging
from app.metrics import HTTP_DURATION, HTTP_REQUESTS
from app.settings import Settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    app.state.db = await database.connect(settings)
    app.state.redis = redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_timeout_sec,
        socket_connect_timeout=settings.redis_timeout_sec,
    )
    log.info("rental-api started", extra={"instance": settings.instance_id})
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await database.close(app.state.db)


def create_app(settings: Settings | None = None) -> FastAPI:
    setup_logging()
    app = FastAPI(title="rental-api", lifespan=lifespan)
    app.state.settings = settings or Settings()

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(offers.router)
    app.include_router(orders.router)
    app.include_router(internal.router)
    app.mount("/metrics", make_asgi_app())

    @app.middleware("http")
    async def observe(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            route = request.scope.get("route")
            handle = getattr(route, "path", "unmatched")
            if not handle.startswith(("/metrics", "/health")):
                HTTP_REQUESTS.labels(handle, str(status)).inc()
                HTTP_DURATION.labels(handle).observe(perf_counter() - started)
            request_id_var.reset(token)

    return app


app = create_app()
