import httpx
import pytest

from app.clients.config import RemoteConfigProvider, load_defaults
from app.clients.http import HttpTariffsClient, HttpUsersClient
from app.ports import DependencyUnavailable

DEFAULTS = load_defaults("config.defaults.json")


class StaticConfig:
    def current(self):
        return DEFAULTS


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://stub")


TARIFF = {"price_per_hour": 50, "free_period_min": 5, "default_deposit": 300}


async def test_tariff_served_from_cache_while_fresh_then_error_when_stale():
    state = {"up": True, "calls": 0}

    def handler(request):
        state["calls"] += 1
        return httpx.Response(200, json=TARIFF) if state["up"] else httpx.Response(503)

    clock = Clock()
    async with client(handler) as http:
        tariffs = HttpTariffsClient(http, StaticConfig(), clock=clock)
        await tariffs.get_tariff("t1")
        state["up"] = False

        clock.now = DEFAULTS.tariff_cache_ttl_sec - 1
        assert (await tariffs.get_tariff("t1")).price_per_hour == 50
        assert state["calls"] == 1

        clock.now = DEFAULTS.tariff_cache_ttl_sec + 1
        with pytest.raises(DependencyUnavailable):
            await tariffs.get_tariff("t1")


async def test_stale_tariff_refreshed_when_service_is_up():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=TARIFF)

    clock = Clock()
    async with client(handler) as http:
        tariffs = HttpTariffsClient(http, StaticConfig(), clock=clock)
        await tariffs.get_tariff("t1")
        clock.now = DEFAULTS.tariff_cache_ttl_sec + 1
        await tariffs.get_tariff("t1")
    assert len(calls) == 2


async def test_users_fallback_is_greedy():
    async with client(lambda request: httpx.Response(500)) as http:
        profile = await HttpUsersClient(http).get_profile("u1")
    assert profile.is_fallback
    assert not profile.has_subscription
    assert not profile.trusted


async def test_config_keeps_defaults_when_service_down_and_merges_partial_config():
    state = {"up": False}

    def handler(request):
        if not state["up"]:
            return httpx.Response(503)
        return httpx.Response(200, json={"offer_ttl_sec": 60})

    async with client(handler) as http:
        provider = RemoteConfigProvider(http, DEFAULTS, refresh_sec=60)
        assert not await provider.refresh()
        assert provider.current() == DEFAULTS

        state["up"] = True
        assert await provider.refresh()
        assert provider.current().offer_ttl_sec == 60
        assert provider.current().last_banks_threshold == DEFAULTS.last_banks_threshold
