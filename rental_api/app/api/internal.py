from uuid import UUID

from fastapi import APIRouter, Response

from app.api.schemas import (
    AdvanceResponse,
    ErrorResponse,
    PaymentResultRequest,
    ReturnRequest,
    ReturnResponse,
)
from app.dependencies import Services

router = APIRouter(prefix="/internal/v1/orders", tags=["internal"])


@router.post("/return", response_model=ReturnResponse, responses={404: {"model": ErrorResponse}})
async def return_powerbank(body: ReturnRequest, services: Services) -> ReturnResponse:
    """Вызывает станция, когда банку вставили в слот. Повтор в течение часа получает тот же ответ."""
    order = await services.return_powerbank(body.powerbank_id, body.station_id)
    return ReturnResponse(order_id=order.id, total_amount=order.total_amount, finish_time=order.finish_time)


@router.post(
    "/{order_id}/advance", response_model=AdvanceResponse, responses={404: {"model": ErrorResponse}}
)
async def advance_order(order_id: UUID, services: Services) -> AdvanceResponse:
    """Вызывает rental-worker для заказов с истёкшим lease: забрать lease и довести заказ."""
    order = await services.advance_order(order_id)
    return AdvanceResponse(order_id=order.id, status=order.status)


@router.post("/{order_id}/payment-result", status_code=204, responses={404: {"model": ErrorResponse}})
async def payment_result(order_id: UUID, body: PaymentResultRequest, services: Services) -> Response:
    """Вызывает rental-worker после проведения задачи outbox. Повтор безопасен."""
    await services.apply_payment_result(order_id, body.kind, body.result)
    return Response(status_code=204)
