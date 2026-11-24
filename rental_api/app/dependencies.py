from typing import Annotated

from fastapi import Depends, Header, Request

from app.db import Database
from app.errors import ValidationFailed
from app.settings import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_user_id(x_user_id: Annotated[str | None, Header()] = None) -> str:
    if not x_user_id:
        raise ValidationFailed("X-User-Id header is required")
    return x_user_id


CurrentUser = Annotated[str, Depends(get_user_id)]
