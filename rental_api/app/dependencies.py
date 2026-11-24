from typing import Annotated

from fastapi import Depends, Header, Request

from app.container import Container
from app.errors import ValidationFailed


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_user_id(x_user_id: Annotated[str | None, Header()] = None) -> str:
    if not x_user_id:
        raise ValidationFailed("X-User-Id header is required")
    return x_user_id


CurrentUser = Annotated[str, Depends(get_user_id)]
Services = Annotated[Container, Depends(get_container)]
