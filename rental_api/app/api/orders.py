from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.schemas import (
    ErrorResponse,
    OrderInfoResponse,
    OrderInProgressResponse,
    OrdersPageResponse,
    OrderStartedResponse,
    StartOrderRequest,
)
from app.dependencies import CurrentUser
from app.domain.models import OrderStatus
from app.errors import NotImplementedYet

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


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
async def start_order(body: StartOrderRequest, user_id: CurrentUser) -> OrderStartedResponse:
    raise NotImplementedYet()


@router.get(
    "/{order_id}",
    response_model=OrderInfoResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_order(order_id: UUID, user_id: CurrentUser) -> OrderInfoResponse:
    raise NotImplementedYet()


@router.get(
    "",
    response_model=OrdersPageResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def list_orders(
    user_id: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: OrderStatus | None = None,
    cursor: str | None = None,
) -> OrdersPageResponse:
    raise NotImplementedYet()
