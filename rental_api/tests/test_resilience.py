import httpx
import pytest

from app.clients.resilience import BreakerState, CircuitBreaker, Policy, RetryBudget
from app.ports import DependencyUnavailable


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(failure_ratio=0.5, window_sec=10, min_calls=4, open_sec=5, half_open_probes=2, clock=clock)


def test_breaker_opens_on_failure_ratio_and_recovers():
    clock = FakeClock()
    breaker = make_breaker(clock)
    for ok in (True, False, True, False):
        breaker.record(ok)
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allow()

    clock.now = 5
    assert breaker.allow() and breaker.allow()
    assert not breaker.allow()
    breaker.record(True)
    breaker.record(True)
    assert breaker.state is BreakerState.CLOSED


def test_breaker_reopens_on_failed_probe():
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(4):
        breaker.record(False)
    clock.now = 5
    assert breaker.allow()
    breaker.record(False)
    assert breaker.state is BreakerState.OPEN


def test_breaker_ignores_old_failures():
    clock = FakeClock()
    breaker = make_breaker(clock)
    breaker.record(False)
    breaker.record(False)
    clock.now = 11
    breaker.record(True)
    breaker.record(True)
    assert breaker.state is BreakerState.CLOSED


def test_retry_budget_limits_retries_to_ratio():
    clock = FakeClock()
    budget = RetryBudget(ratio=0.1, window_sec=10, min_retries=1, clock=clock)
    for _ in range(30):
        budget.record_request()
    assert [budget.try_spend() for _ in range(4)] == [True, True, True, False]


def failing(status: int | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if status is None:
            raise httpx.ConnectTimeout("timeout", request=request)
        return httpx.Response(status)

    return handler


async def call(policy: Policy, handler, retries: int) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://stub") as http:

        async def request():
            response = await http.get("/x")
            response.raise_for_status()
            return response

        return await policy.call(request, retries=retries, base_delay_sec=0)


async def test_policy_retries_unavailability_then_raises():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(503)

    with pytest.raises(DependencyUnavailable):
        await call(Policy("dep"), handler, retries=2)
    assert len(attempts) == 3


async def test_policy_does_not_retry_client_errors():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(400)

    with pytest.raises(httpx.HTTPStatusError):
        await call(Policy("dep"), handler, retries=2)
    assert len(attempts) == 1


async def test_open_breaker_fails_fast():
    breaker = CircuitBreaker(min_calls=1, open_sec=60)
    policy = Policy("dep", breaker=breaker)
    with pytest.raises(DependencyUnavailable):
        await call(policy, failing(), retries=0)
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(200)

    with pytest.raises(DependencyUnavailable, match="circuit open"):
        await call(policy, handler, retries=0)
    assert attempts == []
