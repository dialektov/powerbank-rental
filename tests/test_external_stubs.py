import asyncio

import pytest

from external_stubs.app.main import app

pytestmark = pytest.mark.anyio


async def test_ledger_survives_duplicates_and_restart(stubs):
    eject = {"station_id": "st-1", "order_id": "o1"}
    money = {"user_id": "u1", "order_id": "o1", "amount": 50}
    responses = await asyncio.gather(*(stubs.post("/eject-powerbank", json=eject) for _ in range(10)))
    bank = responses[0].json()["powerbank_id"]
    assert bank and all(r.json() == {"success": True, "powerbank_id": bank} for r in responses)
    payments = await asyncio.gather(*(stubs.post("/clear-money-for-order", json=money) for _ in range(10)))
    assert all(r.json() == {"status": "success"} for r in payments)
    assert (await stubs.post("/hold-money-for-order", json=money)).json() == {"status": "declined"}

    empty = {"station_id": "empty-station-id", "order_id": "o2"}
    assert (await stubs.post("/eject-powerbank", json=empty)).json()["success"] is False
    poor = {**money, "order_id": "o3", "user_id": "poor-1"}
    assert (await stubs.post("/hold-money-for-order", json=poor)).json() == {"status": "declined"}

    # Reopen the ledger with a fresh lifespan, as when uvicorn restarts.
    async with app.router.lifespan_context(app):
        assert (await stubs.post("/eject-powerbank", json=eject)).json()["powerbank_id"] == bank
        assert (await stubs.post("/eject-powerbank", json={**empty, "station_id": "st-1"})).json()["success"] is False
        assert (await stubs.post("/hold-money-for-order", json={**poor, "user_id": "u1"})).json()["status"] == "declined"
        assert (await stubs.post("/clear-money-for-order", json={**money, "amount": 90})).json()["status"] == "success"
        assert (await stubs.get("/admin/ejections")).json() == [{**eject, "powerbank_id": bank}]
        assert (await stubs.get("/admin/payments/summary")).json() == {"clear_amount": 50, "clear_count": 1}


@pytest.mark.parametrize(("service", "path"), [
    ("stations", "/station-data?id=st1"), ("tariffs", "/tariff?id=t1"),
    ("users", "/user-profile?id=u1"), ("configs", "/configs"), ("payments", "/clear-money-for-order"),
])
async def test_fault_is_scoped_and_resettable(stubs, service, path):
    async def call():
        if service == "payments":
            return await stubs.post(path, json={"order_id": "o1", "user_id": "u1", "amount": 50})
        return await stubs.get(path)

    for mode, rate, expected in [("error", 1, 503), ("error", 0, 200), ("ok", 1, 200)]:
        r = await stubs.post("/admin/faults", json={"service": service, "mode": mode, "error_rate": rate})
        assert r.status_code == 204
        assert (await call()).status_code == expected
        unaffected = "/user-profile?id=u1" if service == "configs" else "/configs"
        assert (await stubs.get(unaffected)).status_code == 200
        assert (await stubs.get("/admin/ejections")).status_code == 200


async def test_slow_and_timeout(stubs):
    await stubs.post("/admin/faults", json={"service": "configs", "mode": "slow", "latency_ms": 40})
    start = asyncio.get_running_loop().time()
    assert (await stubs.get("/configs")).status_code == 200
    assert asyncio.get_running_loop().time() - start >= 0.04

    await stubs.post("/admin/faults", json={"service": "configs", "mode": "timeout", "latency_ms": 0})
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(stubs.get("/configs"), timeout=0.05)
    assert (await stubs.post("/admin/faults", json={"service": "users", "mode": "slow", "error_rate": 2})).status_code == 422
