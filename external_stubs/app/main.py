import os
import uuid
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

app = FastAPI(title="external-stubs")

RENTAL_INTERNAL_URL = os.environ.get("RENTAL_INTERNAL_URL", "http://localhost:8081")

CONFIG = {
    "last_banks_threshold": 2,
    "last_banks_increase": 1.5,
    "offer_ttl_sec": 300,
    "tariff_cache_ttl_sec": 600,
    "order_lease_sec": 10,
    "billing_time_scale": 1,
    "subscriber_min_free_period_min": 30,
}

# Ключи идемпотентности: повтор с тем же ключом получает тот же ответ.
ejects: dict[str, dict] = {}
payments: dict[tuple[str, str], dict] = {}


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


@app.get("/station-data")
async def station_data(id: str = Query(min_length=1)) -> dict:
    return {
        "id": id,
        "tariff_id": "tariff18",
        "location": "Королев, универмаг Детский мир",
        "slots": _slots(id),
    }


@app.get("/tariff")
async def tariff(id: str = Query(min_length=1)) -> dict:
    return {"id": id, "price_per_hour": 50, "free_period_min": 5, "default_deposit": 300}


@app.get("/user-profile")
async def user_profile(id: str = Query(min_length=1)) -> dict:
    return {
        "id": id,
        "has_subscription": id.startswith("sub-"),
        "trusted": id.startswith("trusted-"),
    }


@app.get("/configs")
async def configs() -> dict:
    return CONFIG


@app.post("/eject-powerbank")
async def eject_powerbank(body: EjectRequest) -> dict:
    if body.order_id not in ejects:
        if not _slots(body.station_id):
            ejects[body.order_id] = {"success": False, "powerbank_id": None}
        else:
            ejects[body.order_id] = {"success": True, "powerbank_id": f"pb-{uuid.uuid4().hex[:12]}"}
    return ejects[body.order_id]


def _money(kind: Literal["HOLD", "CLEAR"], body: MoneyRequest) -> dict:
    key = (body.order_id, kind)
    if key not in payments:
        declined = kind == "HOLD" and body.user_id.startswith("poor-")
        payments[key] = {
            "status": "declined" if declined else "success",
            "user_id": body.user_id,
            "amount": body.amount,
        }
    return {"status": payments[key]["status"]}


@app.post("/hold-money-for-order")
async def hold_money(body: MoneyRequest) -> dict:
    return _money("HOLD", body)


@app.post("/clear-money-for-order")
async def clear_money(body: MoneyRequest) -> dict:
    return _money("CLEAR", body)


@app.post("/stations/return")
async def station_return(body: StationReturnRequest) -> Response:
    async with httpx.AsyncClient(base_url=RENTAL_INTERNAL_URL, timeout=5.0) as client:
        upstream = await client.post("/internal/v1/orders/return", json=body.model_dump())
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


@app.get("/admin/payments/summary")
async def payments_summary() -> dict:
    cleared = [p for (_, kind), p in payments.items() if kind == "CLEAR" and p["status"] == "success"]
    return {"clear_count": len(cleared), "clear_amount": sum(p["amount"] for p in cleared)}


@app.post("/admin/faults")
async def faults() -> None:
    raise HTTPException(status_code=501, detail="not implemented yet")
