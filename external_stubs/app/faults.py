import asyncio
import random
from typing import Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

Service = Literal["stations", "payments", "tariffs", "users", "configs"]


class Fault(BaseModel):
    service: Service
    mode: Literal["ok", "error", "timeout", "slow"]
    latency_ms: int = Field(default=500, ge=0)
    error_rate: float = Field(default=1, ge=0, le=1)


def simulate_fault(service: Service):
    async def apply(request: Request) -> None:
        fault = request.app.state.faults.get(service)
        if fault is None or random.random() >= fault.error_rate:
            return
        if fault.mode == "error":
            raise HTTPException(503, detail=f"{service} unavailable")
        delay = fault.latency_ms / 1000
        if fault.mode == "timeout":
            delay = max(10, delay)
        await asyncio.sleep(delay)

    return Depends(apply)
