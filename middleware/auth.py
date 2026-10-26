from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status

from data.database.user import UserDocument
from function.auth import get_session_user
from utils.db import Database

SESSION_COOKIE = "lukis_session"

SessionToken = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]


async def current_user(db: Database, token: SessionToken = None) -> UserDocument:
    user = await get_session_user(db, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


CurrentUser = Annotated[UserDocument, Depends(current_user)]
