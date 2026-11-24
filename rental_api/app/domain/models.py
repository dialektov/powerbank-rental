from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    EJECTING = "EJECTING"
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class PaymentState(StrEnum):
    NONE = "NONE"
    HELD = "HELD"
    DEBT = "DEBT"
    CLEARED = "CLEARED"


class PaymentKind(StrEnum):
    HOLD = "HOLD"
    CLEAR = "CLEAR"


class PaymentOutcome(StrEnum):
    SUCCESS = "success"
    DECLINED = "declined"


@dataclass(frozen=True)
class Slot:
    index: int
    empty: bool
    charge: int


@dataclass(frozen=True)
class StationData:
    id: str
    tariff_id: str
    location: str
    slots: list[Slot]


@dataclass(frozen=True)
class Tariff:
    id: str
    price_per_hour: int
    free_period_min: int
    default_deposit: int


@dataclass(frozen=True)
class UserProfile:
    id: str
    has_subscription: bool
    trusted: bool
    is_fallback: bool = False


@dataclass(frozen=True)
class EjectResult:
    success: bool
    powerbank_id: str | None


@dataclass(frozen=True)
class RentalConfig:
    last_banks_threshold: int
    last_banks_increase: float
    offer_ttl_sec: int
    tariff_cache_ttl_sec: int
    order_lease_sec: int
    billing_time_scale: float
    subscriber_min_free_period_min: int


@dataclass(frozen=True)
class Offer:
    id: UUID
    user_id: str
    station_id: str
    tariff_id: str
    price_per_hour: int
    free_period_min: int
    deposit: int
    user_fallback: bool
    created_at: datetime
    expires_at: datetime
    order_id: UUID | None = None


@dataclass(frozen=True)
class Order:
    id: UUID
    offer_id: UUID
    user_id: str
    start_station_id: str
    price_per_hour: int
    free_period_min: int
    deposit: int
    status: OrderStatus
    payment_state: PaymentState
    created_at: datetime
    version: int = 1
    finish_station_id: str | None = None
    powerbank_id: str | None = None
    failure_code: str | None = None
    total_amount: int | None = None
    processing_until: datetime | None = None
    start_time: datetime | None = None
    finish_time: datetime | None = None
