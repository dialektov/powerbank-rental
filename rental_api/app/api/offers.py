from fastapi import APIRouter

from app.api.schemas import CreateOfferRequest, ErrorResponse, OfferResponse
from app.dependencies import CurrentUser, Services

router = APIRouter(prefix="/api/v1/offers", tags=["offers"])


@router.post(
    "",
    status_code=201,
    response_model=OfferResponse,
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def create_offer(body: CreateOfferRequest, user_id: CurrentUser, services: Services) -> OfferResponse:
    offer = await services.create_offer(user_id, body.station_id)
    return OfferResponse(
        offer_id=offer.id,
        station_id=offer.station_id,
        price_per_hour=offer.price_per_hour,
        free_period_min=offer.free_period_min,
        deposit=offer.deposit,
        user_fallback=offer.user_fallback,
        expires_at=offer.expires_at,
    )
