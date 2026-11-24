from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse

from app.api.schemas import (
    ErrorResponse,
    OrderInfoResponse,
    OrderInProgressResponse,
    OrdersPageResponse,
    OrderStartedResponse,
    StartOrderRequest,
)
from app.dependencies import CurrentUser, Services
from app.domain.models import OrderStatus
from app.services.reads import OrderView

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

_NO_STORE = {"Cache-Control": "no-store"}


def to_info(item: OrderView) -> OrderInfoResponse:
    order = item.order
    return OrderInfoResponse(
        order_id=order.id,
        status=order.status,
        payment_state=order.payment_state,
        powerbank_id=order.powerbank_id,
        start_station_id=order.start_station_id,
        finish_station_id=order.finish_station_id,
        price_per_hour=order.price_per_hour,
        free_period_min=order.free_period_min,
        deposit=order.deposit,
        start_time=order.start_time,
        finish_time=order.finish_time,
        amount=item.amount,
        amount_is_final=item.amount_is_final,
        calculated_at=item.calculated_at,
    )


@router.post(
    "",
    status_code=201,
    response_model=OrderStartedResponse,
    responses={
        202: {"model": OrderInProgressResponse, "description": "Order is being processed by another request"},
        402: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def start_order(body: StartOrderRequest, user_id: CurrentUser, services: Services) -> Response:
    result = await services.start_order(user_id, body.offer_id)
    order = result.order
    if result.in_progress:
        return JSONResponse(
            status_code=202,
            content=OrderInProgressResponse(order_id=order.id, status=order.status).model_dump(mode="json"),
            headers={"Location": f"/api/v1/orders/{order.id}", "Retry-After": "1"},
        )
    return JSONResponse(
        status_code=201,
        content=OrderStartedResponse(
            order_id=order.id,
            status=order.status,
            powerbank_id=order.powerbank_id,
            start_time=order.start_time,
            price_per_hour=order.price_per_hour,
            free_period_min=order.free_period_min,
            deposit=order.deposit,
        ).model_dump(mode="json"),
    )


@router.get(
    "/{order_id}",
    response_model=OrderInfoResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_order(order_id: UUID, user_id: CurrentUser, services: Services, response: Response) -> OrderInfoResponse:
    response.headers.update(_NO_STORE)
    return to_info(await services.reads.get(user_id, order_id))


@router.get(
    "",
    response_model=OrdersPageResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def list_orders(
    user_id: CurrentUser,
    services: Services,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: OrderStatus | None = None,
    cursor: str | None = None,
) -> OrdersPageResponse:
    response.headers.update(_NO_STORE)
    items, next_cursor = await services.reads.list(user_id, limit, status, cursor)
    return OrdersPageResponse(items=[to_info(item) for item in items], next_cursor=next_cursor)
