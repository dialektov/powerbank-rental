import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Query, Request, Response
from pydantic import BaseModel

from .faults import Fault, simulate_fault
from .ledger import Ledger


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ledger = Ledger(Path(os.environ.get("STUBS_DB_PATH", "stubs.sqlite3")))
    app.state.faults = {}
    yield


app = FastAPI(title="external-stubs", lifespan=lifespan)

RENTAL_INTERNAL_URL = os.environ.get("RENTAL_INTERNAL_URL", "http://localhost:8081")

CONFIG = {
    "last_banks_threshold": 2,
    "last_banks_increase": 1.5,
    "offer_ttl_sec": 300,
    "tariff_cache_ttl_sec": 600,
    "order_lease_sec": 10,
    "billing_time_scale": float(os.environ.get("STUB_BILLING_TIME_SCALE", "1")),
    "subscriber_min_free_period_min": 30,
}


class EjectRequest(BaseModel):
    station_id: str
    order_id: str


class MoneyRequest(BaseModel):
    user_id: str
    order_id: str
    amount: int


class StationReturnRequest(BaseModel):
    powerbank_id: str
    station_id: str


def _slots(station_id: str) -> list[dict]:
    if station_id == "empty-station-id":
        return []
    banks = 1 if station_id == "last-banks-station-id" else 3
    return [
        {"index": i, "empty": i > banks, "charge": 100 if i <= banks else 0}
        for i in range(1, 5)
    ]


@app.get("/station-data", dependencies=[simulate_fault("stations")])
async def station_data(id: str = Query(min_length=1)) -> dict:
    return {
        "id": id,
        "tariff_id": "tariff18",
        "location": "Королев, универмаг Детский мир",
        "slots": _slots(id),
    }


@app.get("/tariff", dependencies=[simulate_fault("tariffs")])
async def tariff(id: str = Query(min_length=1)) -> dict:
    return {"id": id, "price_per_hour": 50, "free_period_min": 5, "default_deposit": 300}


@app.get("/user-profile", dependencies=[simulate_fault("users")])
async def user_profile(id: str = Query(min_length=1)) -> dict:
    return {
        "id": id,
        "has_subscription": id.startswith("sub-"),
        "trusted": id.startswith("trusted-"),
    }


@app.get("/configs", dependencies=[simulate_fault("configs")])
async def configs() -> dict:
    return CONFIG


@app.post("/eject-powerbank", dependencies=[simulate_fault("stations")])
def eject_powerbank(body: EjectRequest, request: Request) -> dict:
    return request.app.state.ledger.eject(body.order_id, body.station_id, bool(_slots(body.station_id)))


@app.post("/hold-money-for-order", dependencies=[simulate_fault("payments")])
def hold_money(body: MoneyRequest, request: Request) -> dict:
    return request.app.state.ledger.pay("HOLD", body.order_id, body.user_id, body.amount)


@app.post("/clear-money-for-order", dependencies=[simulate_fault("payments")])
def clear_money(body: MoneyRequest, request: Request) -> dict:
    return request.app.state.ledger.pay("CLEAR", body.order_id, body.user_id, body.amount)


@app.post("/stations/return", dependencies=[simulate_fault("stations")])
async def station_return(body: StationReturnRequest) -> Response:
    async with httpx.AsyncClient(base_url=RENTAL_INTERNAL_URL, timeout=5.0) as client:
        upstream = await client.post("/internal/v1/orders/return", json=body.model_dump())
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


@app.get("/admin/payments/summary")
def payments_summary(request: Request) -> dict:
    return request.app.state.ledger.summary()


@app.get("/admin/ejections")
def ejections(request: Request) -> list[dict]:
    return request.app.state.ledger.ejections()


@app.post("/admin/faults", status_code=204)
async def faults(body: Fault, request: Request) -> Response:
    if body.mode == "ok":
        request.app.state.faults.pop(body.service, None)
    else:
        request.app.state.faults[body.service] = body
    return Response(status_code=204)
