import math
from datetime import datetime

from app.domain.models import Slot


def count_available(slots: list[Slot]) -> int:
    return sum(1 for slot in slots if not slot.empty)


def price_per_hour(base_price: int, available: int, threshold: int, increase: float) -> int:
    if available <= threshold:
        return int(base_price * increase)
    return base_price


def free_period_min(tariff_free_min: int, has_subscription: bool, subscriber_min: int) -> int:
    if has_subscription:
        return max(tariff_free_min, subscriber_min)
    return tariff_free_min


def deposit(default_deposit: int, trusted: bool) -> int:
    return 0 if trusted else default_deposit


def rental_cost(
    start: datetime,
    now: datetime,
    free_period_min: int,
    price_per_hour: int,
    time_scale: float = 1.0,
) -> int:
    """Аренда не дольше бесплатного периода бесплатна, иначе оплачивается каждый начатый час."""
    billable_sec = max((now - start).total_seconds(), 0.0) * time_scale
    if billable_sec <= free_period_min * 60:
        return 0
    return math.ceil(billable_sec / 3600) * price_per_hour
