from datetime import UTC, datetime, timedelta

import pytest

from app.domain import pricing
from app.domain.models import Slot

START = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def test_count_available_counts_non_empty_slots():
    slots = [Slot(1, True, 0), Slot(2, False, 100), Slot(3, False, 40)]
    assert pricing.count_available(slots) == 2


@pytest.mark.parametrize(
    ("available", "expected"),
    [(1, 75), (2, 75), (3, 50)],
)
def test_price_increases_on_last_banks(available, expected):
    assert pricing.price_per_hour(50, available, threshold=2, increase=1.5) == expected


def test_subscriber_gets_at_least_subscriber_free_period():
    assert pricing.free_period_min(5, has_subscription=True, subscriber_min=30) == 30
    assert pricing.free_period_min(45, has_subscription=True, subscriber_min=30) == 45
    assert pricing.free_period_min(5, has_subscription=False, subscriber_min=30) == 5


def test_trusted_user_has_no_deposit():
    assert pricing.deposit(300, trusted=True) == 0
    assert pricing.deposit(300, trusted=False) == 300


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (timedelta(0), 0),
        (timedelta(minutes=5), 0),
        (timedelta(minutes=5, seconds=1), 50),
        (timedelta(hours=1), 50),
        (timedelta(hours=1, seconds=1), 100),
        (timedelta(hours=2, minutes=30), 150),
    ],
)
def test_rental_cost_charges_each_started_hour(duration, expected):
    assert pricing.rental_cost(START, START + duration, free_period_min=5, price_per_hour=50) == expected


def test_rental_cost_never_negative_on_clock_skew():
    assert pricing.rental_cost(START, START - timedelta(seconds=3), 5, 50) == 0


def test_time_scale_compresses_hour_into_minute():
    assert pricing.rental_cost(START, START + timedelta(seconds=4), 5, 50, time_scale=60) == 0
    assert pricing.rental_cost(START, START + timedelta(seconds=60), 5, 50, time_scale=60) == 50
    assert pricing.rental_cost(START, START + timedelta(seconds=61), 5, 50, time_scale=60) == 100
