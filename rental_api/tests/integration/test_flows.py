"""Сценарии из раздела 11 ADR на поднятом docker compose.

Запуск: docker compose up -d && docker compose run --rm --build integration-tests
"""

import asyncio
import os
import uuid

import asyncpg
import httpx
import pytest

RENTAL_URL = os.environ.get("RENTAL_URL", "http://localhost:8080")
STUBS_URL = os.environ.get("STUBS_URL", "http://localhost:3629")
DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://rental:rental@localhost:5432/rental")


def _stack_is_up() -> bool:
    try:
        return httpx.get(f"{RENTAL_URL}/health/ready", timeout=2, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _stack_is_up(), reason="docker compose stack is not running")


@pytest.fixture
async def api():
    async with httpx.AsyncClient(base_url=RENTAL_URL, timeout=10, trust_env=False) as client:
        yield client


@pytest.fixture
async def stubs():
    async with httpx.AsyncClient(base_url=STUBS_URL, timeout=10, trust_env=False) as client:
        yield client


@pytest.fixture
async def db():
    conn = await asyncpg.connect(DATABASE_URL)
    yield conn
    await conn.close()


def new_user(prefix: str = "") -> dict[str, str]:
    return {"X-User-Id": f"{prefix}it-{uuid.uuid4().hex[:8]}"}


async def create_offer(api: httpx.AsyncClient, user: dict, station: str = "st-1") -> dict:
    response = await api.post("/api/v1/offers", json={"station_id": station}, headers=user)
    assert response.status_code == 201, response.text
    return response.json()


async def start_order(api: httpx.AsyncClient, user: dict, offer_id: str) -> httpx.Response:
    return await api.post("/api/v1/orders", json={"offer_id": offer_id}, headers=user)


async def test_full_rental_flow(api, stubs):
    user = new_user()
    offer = await create_offer(api, user)
    assert offer["price_per_hour"] == 50
    assert offer["deposit"] == 300

    started = await start_order(api, user, offer["offer_id"])
    assert started.status_code == 201, started.text
    order = started.json()
    assert order["status"] == "ACTIVE"

    info = await api.get(f"/api/v1/orders/{order['order_id']}", headers=user)
    assert info.status_code == 200
    assert info.json()["payment_state"] == "HELD"
    assert info.json()["amount_is_final"] is False

    returned = await stubs.post(
        "/stations/return", json={"powerbank_id": order["powerbank_id"], "station_id": "st-2"}
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["total_amount"] == 0

    for _ in range(4):
        info = await api.get(f"/api/v1/orders/{order['order_id']}", headers=user)
        assert info.json()["status"] == "FINISHED"
        assert info.json()["amount_is_final"] is True


async def test_parallel_duplicates_create_one_order(api):
    user = new_user()
    offer = await create_offer(api, user)
    responses = await asyncio.gather(*(start_order(api, user, offer["offer_id"]) for _ in range(10)))

    codes = sorted(r.status_code for r in responses)
    assert set(codes) <= {201, 202}, codes
    assert len({r.json()["order_id"] for r in responses}) == 1
    powerbanks = {r.json()["powerbank_id"] for r in responses if r.status_code == 201}
    assert len(powerbanks) == 1


async def test_repeated_order_after_success_returns_same_order(api):
    user = new_user()
    offer = await create_offer(api, user)
    first = await start_order(api, user, offer["offer_id"])
    second = await start_order(api, user, offer["offer_id"])
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()


async def test_foreign_offer_and_order_are_not_found(api):
    owner, stranger = new_user(), new_user()
    offer = await create_offer(api, owner)
    foreign = await start_order(api, stranger, offer["offer_id"])
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "OFFER_NOT_FOUND"

    order = (await start_order(api, owner, offer["offer_id"])).json()
    info = await api.get(f"/api/v1/orders/{order['order_id']}", headers=stranger)
    assert info.status_code == 404


async def test_expired_offer_rejected_but_existing_order_returned(api, db):
    user = new_user()
    fresh = await create_offer(api, user)
    order = (await start_order(api, user, fresh["offer_id"])).json()

    stale = await create_offer(api, user)
    await db.execute(
        "UPDATE offers SET expires_at = now() - interval '1 second' WHERE id = ANY($1::uuid[])",
        [fresh["offer_id"], stale["offer_id"]],
    )

    rejected = await start_order(api, user, stale["offer_id"])
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "OFFER_EXPIRED"

    repeated = await start_order(api, user, fresh["offer_id"])
    assert repeated.status_code == 201
    assert repeated.json()["order_id"] == order["order_id"]


async def test_no_powerbanks(api):
    user = new_user()
    response = await api.post("/api/v1/offers", json={"station_id": "empty-station-id"}, headers=user)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_POWERBANKS"


async def test_last_banks_increase_price_and_subscriber_free_period(api):
    offer = await create_offer(api, new_user("sub-"), station="last-banks-station-id")
    assert offer["price_per_hour"] == 75
    assert offer["free_period_min"] == 30


async def test_payment_declined_is_final(api):
    user = new_user("poor-")
    offer = await create_offer(api, user)
    for _ in range(2):
        response = await start_order(api, user, offer["offer_id"])
        assert response.status_code == 402
        assert response.json()["error"]["code"] == "PAYMENT_DECLINED"


async def test_abandoned_order_is_taken_over_after_lease(api, db):
    user = new_user()
    offer = await create_offer(api, user)
    order_id = uuid.uuid4()
    async with db.transaction():
        await db.execute(
            """
            INSERT INTO orders (id, offer_id, user_id, start_station_id, price_per_hour, free_period_min,
                                deposit, status, payment_state, processing_until, created_at)
            VALUES ($1, $2, $3, 'st-1', 50, 5, 300, 'CREATED', 'NONE', now() + interval '1 hour', now())
            """,
            order_id,
            uuid.UUID(offer["offer_id"]),
            user["X-User-Id"],
        )
        await db.execute("UPDATE offers SET order_id = $1 WHERE id = $2", order_id, uuid.UUID(offer["offer_id"]))

    busy = await start_order(api, user, offer["offer_id"])
    assert busy.status_code == 202
    assert busy.headers["Retry-After"] == "1"
    assert busy.json() == {"order_id": str(order_id), "status": "CREATED"}

    await db.execute("UPDATE orders SET processing_until = now() - interval '1 second' WHERE id = $1", order_id)
    taken = await start_order(api, user, offer["offer_id"])
    assert taken.status_code == 201, taken.text
    assert taken.json()["order_id"] == str(order_id)
    assert taken.json()["status"] == "ACTIVE"


async def test_repeated_return_is_idempotent(api, stubs, db):
    user = new_user()
    offer = await create_offer(api, user)
    order = (await start_order(api, user, offer["offer_id"])).json()
    await db.execute(
        "UPDATE orders SET start_time = start_time - interval '90 minutes' WHERE id = $1",
        uuid.UUID(order["order_id"]),
    )

    body = {"powerbank_id": order["powerbank_id"], "station_id": "st-2"}
    first = await stubs.post("/stations/return", json=body)
    second = await stubs.post("/stations/return", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["total_amount"] == 100

    tasks = await db.fetch(
        "SELECT kind, amount FROM payment_tasks WHERE order_id = $1", uuid.UUID(order["order_id"])
    )
    assert [(t["kind"], t["amount"]) for t in tasks] == [("CLEAR", 100)]


async def test_unknown_powerbank_return_is_404(stubs):
    response = await stubs.post("/stations/return", json={"powerbank_id": "pb-unknown", "station_id": "st-1"})
    assert response.status_code == 404


async def test_list_pagination_has_no_gaps_or_duplicates(api):
    user = new_user()
    created = []
    for _ in range(5):
        offer = await create_offer(api, user)
        created.append((await start_order(api, user, offer["offer_id"])).json()["order_id"])

    seen, cursors, cursor = [], [], None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = await api.get("/api/v1/orders", params=params, headers=user)
        assert page.status_code == 200, page.text
        seen.extend(item["order_id"] for item in page.json()["items"])
        cursor = page.json().get("next_cursor")
        if not cursor:
            break
        cursors.append(cursor)
    assert seen == list(reversed(created))

    stranger = await api.get("/api/v1/orders", params={"cursor": cursors[0]}, headers=new_user())
    assert stranger.status_code == 400
    assert stranger.json()["error"]["code"] == "INVALID_CURSOR"
