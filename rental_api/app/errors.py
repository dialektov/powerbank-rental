from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "Internal error"
    retryable: bool = False

    def __init__(self, message: str | None = None, *, headers: dict[str, str] | None = None):
        super().__init__(message or self.message)
        if message:
            self.message = message
        self.headers = headers


class ValidationFailed(AppError):
    status_code, code, message = 400, "VALIDATION_ERROR", "Request validation failed"


class InvalidCursor(AppError):
    status_code, code, message = 400, "INVALID_CURSOR", "Cursor is invalid or expired"


class PaymentDeclined(AppError):
    status_code, code, message = 402, "PAYMENT_DECLINED", "Payment declined"


class OfferNotFound(AppError):
    status_code, code, message = 404, "OFFER_NOT_FOUND", "Offer not found"


class OrderNotFound(AppError):
    status_code, code, message = 404, "ORDER_NOT_FOUND", "Order not found"


class OfferExpired(AppError):
    status_code, code, message = 409, "OFFER_EXPIRED", "Offer expired, request a new one"


class NoPowerbanks(AppError):
    status_code, code, message = 409, "NO_POWERBANKS", "No powerbanks available at the station"


class NotImplementedYet(AppError):
    status_code, code, message = 501, "NOT_IMPLEMENTED", "Not implemented yet"


class StationsUnavailable(AppError):
    status_code, code, message, retryable = 503, "STATIONS_UNAVAILABLE", "Station is unavailable", True


class TariffUnavailable(AppError):
    status_code, code, message, retryable = 503, "TARIFF_UNAVAILABLE", "Tariff is unavailable", True


class Overloaded(AppError):
    status_code, code, message, retryable = 503, "OVERLOADED", "Service is overloaded", True

    def __init__(self, retry_after_sec: int = 1):
        super().__init__(headers={"Retry-After": str(retry_after_sec)})


class DbUnavailable(AppError):
    status_code, code, message, retryable = 503, "DB_UNAVAILABLE", "Database is unavailable", True


def error_body(code: str, message: str, retryable: bool) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.retryable),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return JSONResponse(
            status_code=ValidationFailed.status_code,
            content=error_body(ValidationFailed.code, details, False),
        )
