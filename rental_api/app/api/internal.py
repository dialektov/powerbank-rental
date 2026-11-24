from uuid import UUID

from fastapi import APIRouter

from app.api.schemas import (
    AdvanceResponse,
    ErrorResponse,
    PaymentResultRequest,
    ReturnRequest,
    ReturnResponse,
)
from app.errors import NotImplementedYet

router = APIRouter(prefix="/internal/v1/orders", tags=["internal"])


@router.post("/return", response_model=ReturnResponse, responses={404: {"model": ErrorResponse}})
async def return_powerbank(body: ReturnRequest) -> ReturnResponse:
    """Вызывает станция, когда банку вставили в слот."""
    raise NotImplementedYet()


@router.post(
    "/{order_id}/advance", response_model=AdvanceResponse, responses={404: {"model": ErrorResponse}}
)
async def advance_order(order_id: UUID) -> AdvanceResponse:
    """Вызывает rental-worker для заказов с истёкшим lease: забрать lease и довести заказ."""
    raise NotImplementedYet()


@router.post("/{order_id}/payment-result", status_code=204, responses={404: {"model": ErrorResponse}})
async def payment_result(order_id: UUID, body: PaymentResultRequest) -> None:
    """Вызывает rental-worker после проведения задачи outbox. Повтор безопасен."""
    raise NotImplementedYet()
