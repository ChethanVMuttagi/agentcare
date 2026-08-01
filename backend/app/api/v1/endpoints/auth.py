"""Authentication endpoints: token issuance and current-user introspection.

Deliberately minimal — see docs/RBAC.md. No registration, password
reset, email verification, refresh tokens, or MFA in this story.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.jwt import create_access_token
from app.auth.service import authenticate_user
from app.core.config import Settings, get_settings
from app.core.exceptions import AppException
from app.core.rate_limit import auth_token_rate_limit, limiter
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.auth import CurrentUserResponse, TokenRequest, TokenResponse

router = APIRouter()


class InvalidCredentialsError(AppException):
    """401: deliberately generic — does not reveal whether the email
    exists, whether the password was wrong, or whether the account is
    inactive. See app.auth.service.authenticate_user."""

    status_code = 401
    error_code = "invalid_credentials"


@router.post("/auth/token", response_model=TokenResponse)
@limiter.limit(auth_token_rate_limit)
async def login_for_access_token(
    request: Request,
    credentials: TokenRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    """Rate limited (Sprint 2: `RATE_LIMIT_AUTH_TOKEN`, see
    `app.core.rate_limit`) — this is the one unauthenticated route in the
    whole API, and therefore the one most exposed to credential-stuffing
    attempts; `app.auth.service.authenticate_user`'s timing-safe dummy
    hash comparison slows a single guess, this limits how many can be
    attempted at all."""
    user = await authenticate_user(session, credentials.email, credentials.password)
    if user is None:
        raise InvalidCredentialsError("Incorrect email or password.")

    access_token = create_access_token(user.id, settings)
    return TokenResponse(access_token=access_token)


@router.get("/auth/me", response_model=CurrentUserResponse)
async def get_my_profile(
    current_user: Annotated[User, Depends(get_current_user)],
) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=current_user.id,
        email=current_user.email,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
    )
