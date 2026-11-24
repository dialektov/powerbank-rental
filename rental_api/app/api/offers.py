from fastapi import APIRouter

from app.api.schemas import CreateOfferRequest, ErrorResponse, OfferResponse
from app.dependencies import CurrentUser
from app.errors import NotImplementedYet

router = APIRouter(prefix="/api/v1/offers", tags=["offers"])


@router.post(
    "",
    status_code=201,
    response_model=OfferResponse,
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def create_offer(body: CreateOfferRequest, user_id: CurrentUser) -> OfferResponse:
    raise NotImplementedYet()
