from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.models import OrderStatus, PaymentKind, PaymentOutcome, PaymentState


class ErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorInfo


class CreateOfferRequest(BaseModel):
    station_id: str = Field(min_length=1)


class OfferResponse(BaseModel):
    offer_id: UUID
    station_id: str
    price_per_hour: int
    free_period_min: int
    deposit: int
    user_fallback: bool
    expires_at: datetime


class StartOrderRequest(BaseModel):
    offer_id: UUID


class OrderStartedResponse(BaseModel):
    order_id: UUID
    status: Literal[OrderStatus.ACTIVE, OrderStatus.FINISHED]
    powerbank_id: str
    start_time: datetime
    price_per_hour: int
    free_period_min: int
    deposit: int


class OrderInProgressResponse(BaseModel):
    order_id: UUID
    status: Literal[OrderStatus.CREATED, OrderStatus.EJECTING]


class OrderInfoResponse(BaseModel):
    order_id: UUID
    status: OrderStatus
    payment_state: PaymentState
    powerbank_id: str | None
    start_station_id: str
    finish_station_id: str | None
    price_per_hour: int
    free_period_min: int
    deposit: int
    start_time: datetime | None
    finish_time: datetime | None
    amount: int
    amount_is_final: bool
    calculated_at: datetime


class OrdersPageResponse(BaseModel):
    items: list[OrderInfoResponse]
    next_cursor: str | None = None


class ReturnRequest(BaseModel):
    powerbank_id: str = Field(min_length=1)
    station_id: str = Field(min_length=1)


class ReturnResponse(BaseModel):
    order_id: UUID
    total_amount: int
    finish_time: datetime


class AdvanceResponse(BaseModel):
    order_id: UUID
    status: OrderStatus


class PaymentResultRequest(BaseModel):
    kind: PaymentKind
    result: PaymentOutcome
