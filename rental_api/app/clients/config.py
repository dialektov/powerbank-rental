import asyncio
import dataclasses
import json
import logging
import time
from pathlib import Path

import httpx

from app.domain.models import RentalConfig
from app.metrics import CONFIG_AGE

log = logging.getLogger(__name__)


def _parse(raw: dict, fallback: RentalConfig) -> RentalConfig:
    values = {}
    for field in dataclasses.fields(RentalConfig):
        value = raw.get(field.name, getattr(fallback, field.name))
        values[field.name] = field.type(value)
    return RentalConfig(**values)


def load_defaults(path: str) -> RentalConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return RentalConfig(**{f.name: f.type(raw[f.name]) for f in dataclasses.fields(RentalConfig)})


class RemoteConfigProvider:
    """Последний успешно полученный конфиг, обновляется в фоне. До первого успеха работают дефолты."""

    def __init__(self, http: httpx.AsyncClient, defaults: RentalConfig, refresh_sec: float, timeout_sec: float = 1.0):
        self._http = http
        self._config = defaults
        self._refresh_sec = refresh_sec
        self._timeout_sec = timeout_sec
        self._loaded_at = time.monotonic()
        self._task: asyncio.Task | None = None

    def current(self) -> RentalConfig:
        return self._config

    async def refresh(self) -> bool:
        try:
            response = await self._http.get("/configs", timeout=self._timeout_sec)
            response.raise_for_status()
            self._config = _parse(response.json(), self._config)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning("config refresh failed, keeping previous values: %s", exc)
            return False
        self._loaded_at = time.monotonic()
        CONFIG_AGE.set(0)
        return True

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_sec)
            await self.refresh()
            CONFIG_AGE.set(time.monotonic() - self._loaded_at)

    async def start(self) -> None:
        if not await self.refresh():
            log.warning("configs unavailable on start, using defaults")
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
