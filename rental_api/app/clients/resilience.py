import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TypeVar

import httpx

from app.metrics import DEPENDENCY_DURATION, DEPENDENCY_REQUESTS
from app.ports import DependencyUnavailable

T = TypeVar("T")


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Открывается при доле ошибок >= failure_ratio за window_sec, через open_sec пропускает пробные вызовы."""

    def __init__(
        self,
        failure_ratio: float = 0.5,
        window_sec: float = 10.0,
        min_calls: int = 10,
        open_sec: float = 5.0,
        half_open_probes: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._failure_ratio = failure_ratio
        self._window_sec = window_sec
        self._min_calls = min_calls
        self._open_sec = open_sec
        self._half_open_probes = half_open_probes
        self._clock = clock
        self._events: deque[tuple[float, bool]] = deque()
        self._state = BreakerState.CLOSED
        self._opened_at = 0.0
        self._probes_started = 0
        self._probes_succeeded = 0

    @property
    def state(self) -> BreakerState:
        if self._state is BreakerState.OPEN and self._clock() - self._opened_at >= self._open_sec:
            self._state = BreakerState.HALF_OPEN
            self._probes_started = 0
            self._probes_succeeded = 0
        return self._state

    def allow(self) -> bool:
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.HALF_OPEN and self._probes_started < self._half_open_probes:
            self._probes_started += 1
            return True
        return False

    def record(self, success: bool) -> None:
        state = self.state
        if state is BreakerState.HALF_OPEN:
            if not success:
                self._open()
                return
            self._probes_succeeded += 1
            if self._probes_succeeded >= self._half_open_probes:
                self._state = BreakerState.CLOSED
                self._events.clear()
            return
        if state is BreakerState.OPEN:
            return

        now = self._clock()
        self._events.append((now, success))
        while self._events and now - self._events[0][0] > self._window_sec:
            self._events.popleft()
        failures = sum(1 for _, ok in self._events if not ok)
        if len(self._events) >= self._min_calls and failures / len(self._events) >= self._failure_ratio:
            self._open()

    def _open(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._events.clear()


class RetryBudget:
    """Ретраев не больше ratio от потока запросов за окно, но не меньше min_retries."""

    def __init__(
        self,
        ratio: float = 0.1,
        window_sec: float = 10.0,
        min_retries: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._ratio = ratio
        self._window_sec = window_sec
        self._min_retries = min_retries
        self._clock = clock
        self._requests: deque[float] = deque()
        self._retries: deque[float] = deque()

    def _prune(self, now: float) -> None:
        for events in (self._requests, self._retries):
            while events and now - events[0] > self._window_sec:
                events.popleft()

    def record_request(self) -> None:
        self._requests.append(self._clock())

    def try_spend(self) -> bool:
        now = self._clock()
        self._prune(now)
        if len(self._retries) >= max(self._min_retries, self._ratio * len(self._requests)):
            return False
        self._retries.append(now)
        return True


def is_unavailability(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


class Policy:
    """Таймаут, ретраи с экспоненциальной задержкой и jitter, retry budget и circuit breaker для одной зависимости."""

    def __init__(
        self,
        dependency: str,
        breaker: CircuitBreaker | None = None,
        budget: RetryBudget | None = None,
    ):
        self.dependency = dependency
        self.breaker = breaker or CircuitBreaker()
        self.budget = budget or RetryBudget()

    async def call(self, fn: Callable[[], Awaitable[T]], *, retries: int = 0, base_delay_sec: float = 0.05) -> T:
        if not self.breaker.allow():
            DEPENDENCY_REQUESTS.labels(self.dependency, "circuit_open").inc()
            raise DependencyUnavailable(self.dependency, "circuit open")
        self.budget.record_request()

        attempt = 0
        while True:
            started = time.perf_counter()
            try:
                result = await fn()
            except Exception as exc:
                DEPENDENCY_DURATION.labels(self.dependency).observe(time.perf_counter() - started)
                if not is_unavailability(exc):
                    self.breaker.record(True)
                    DEPENDENCY_REQUESTS.labels(self.dependency, "error").inc()
                    raise
                self.breaker.record(False)
                result_label = "timeout" if isinstance(exc, httpx.TimeoutException) else "error"
                DEPENDENCY_REQUESTS.labels(self.dependency, result_label).inc()
                if attempt < retries and self.budget.try_spend() and self.breaker.allow():
                    attempt += 1
                    await asyncio.sleep(base_delay_sec * 2 ** (attempt - 1) * random.uniform(0.5, 1.5))
                    continue
                raise DependencyUnavailable(self.dependency, repr(exc)) from exc
            DEPENDENCY_DURATION.labels(self.dependency).observe(time.perf_counter() - started)
            self.breaker.record(True)
            DEPENDENCY_REQUESTS.labels(self.dependency, "ok").inc()
            return result
