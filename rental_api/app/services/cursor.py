import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.errors import InvalidCursor


@dataclass(frozen=True)
class CursorPosition:
    created_at: datetime
    order_id: UUID


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class CursorCodec:
    """Непрозрачный курсор: позиция, пользователь, фильтр и время выдачи под HMAC-подписью."""

    def __init__(self, secret: str, ttl_sec: int, clock=time.time):
        self._secret = secret.encode()
        self._ttl_sec = ttl_sec
        self._clock = clock

    def _sign(self, payload: bytes) -> bytes:
        return hmac.new(self._secret, payload, hashlib.sha256).digest()

    def encode(self, position: CursorPosition, user_id: str, status: str | None) -> str:
        payload = json.dumps(
            {
                "c": position.created_at.isoformat(),
                "i": str(position.order_id),
                "u": user_id,
                "s": status,
                "t": int(self._clock()),
            },
            separators=(",", ":"),
        ).encode()
        return f"{_b64encode(payload)}.{_b64encode(self._sign(payload))}"

    def decode(self, cursor: str, user_id: str, status: str | None) -> CursorPosition:
        try:
            payload_part, signature_part = cursor.split(".", 1)
            payload = _b64decode(payload_part)
            signature = _b64decode(signature_part)
        except ValueError as exc:
            raise InvalidCursor() from exc
        if not hmac.compare_digest(signature, self._sign(payload)):
            raise InvalidCursor()
        try:
            data = json.loads(payload)
            position = CursorPosition(datetime.fromisoformat(data["c"]), UUID(data["i"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidCursor() from exc
        if data["u"] != user_id or data["s"] != status:
            raise InvalidCursor("Cursor belongs to another query")
        if self._clock() - data["t"] > self._ttl_sec:
            raise InvalidCursor("Cursor expired")
        return position
