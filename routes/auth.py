from datetime import timedelta

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Response, status

from data.general.user import UserResponse
from data.request.auth import LoginRequest, RegisterRequest
from function.auth import (
    EmailTakenError,
    authenticate,
    create_session,
    delete_session,
    register_user,
)
from middleware.auth import SESSION_COOKIE, CurrentUser, SessionToken
from utils.config import AppSettings, Settings
from utils.db import Database

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_options(settings: Settings) -> dict:
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.cookie_secure,
        "path": "/",
    }


async def _start_session(
    response: Response, db: Database, settings: Settings, user_id: ObjectId
) -> None:
    lifetime = timedelta(days=settings.session_days)
    token = await create_session(db, user_id, lifetime)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(lifetime.total_seconds()),
        **_cookie_options(settings),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, response: Response, db: Database, settings: AppSettings
) -> UserResponse:
    try:
        user = await register_user(db, body.email, body.name, body.password)
    except EmailTakenError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists"
        )
    await _start_session(response, db, settings, user["_id"])
    return UserResponse.from_document(user)


@router.post("/login")
async def login(
    body: LoginRequest, response: Response, db: Database, settings: AppSettings
) -> UserResponse:
    user = await authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid email or password"
        )
    await _start_session(response, db, settings, user["_id"])
    return UserResponse.from_document(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response, db: Database, settings: AppSettings, token: SessionToken = None
) -> None:
    if token:
        await delete_session(db, token)
    response.delete_cookie(SESSION_COOKIE, **_cookie_options(settings))


@router.get("/me")
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.from_document(user)
