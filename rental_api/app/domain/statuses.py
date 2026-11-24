from app.domain.models import OrderStatus

IN_PROGRESS = frozenset({OrderStatus.CREATED, OrderStatus.EJECTING})
SETTLED_FOR_START = frozenset({OrderStatus.ACTIVE, OrderStatus.FINISHED, OrderStatus.FAILED})

_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.EJECTING, OrderStatus.FAILED}),
    OrderStatus.EJECTING: frozenset({OrderStatus.ACTIVE, OrderStatus.FAILED}),
    OrderStatus.ACTIVE: frozenset({OrderStatus.FINISHED}),
    OrderStatus.FINISHED: frozenset(),
    OrderStatus.FAILED: frozenset(),
}


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in _TRANSITIONS[current]
