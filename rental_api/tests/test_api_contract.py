from fastapi.testclient import TestClient

from app.main import create_app

# Без контекстного менеджера TestClient не запускает lifespan, поэтому БД и Redis не нужны.
# До обработчика эти запросы не доходят, поэтому контейнер-заглушка не используется.
app = create_app()
app.state.container = object()
client = TestClient(app)


def test_missing_user_header_is_validation_error():
    response = client.post("/api/v1/offers", json={"station_id": "st-1"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_body_is_validation_error():
    response = client.post("/api/v1/orders", json={"offer_id": "not-a-uuid"}, headers={"X-User-Id": "u1"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["retryable"] is False
    assert "offer_id" in error["message"]


def test_request_id_is_echoed():
    response = client.get("/health/live", headers={"X-Request-Id": "abc"})
    assert response.headers["X-Request-Id"] == "abc"


def test_limit_above_100_is_rejected():
    response = client.get("/api/v1/orders", params={"limit": 101}, headers={"X-User-Id": "u1"})
    assert response.status_code == 400
