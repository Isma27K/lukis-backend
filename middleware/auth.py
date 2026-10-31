from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status

from data.database.user import UserDocument
from function.auth import get_session_user
from function.errors import ForbiddenError
from function.site import is_site_owner
from utils.db import Database

SESSION_COOKIE = "lukis_session"

SessionToken = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]


# Not named `token`: FastAPI would treat it as the {token} path parameter on
# routes that have one, like the invite routes.
async def current_user(
        db: Database,
        session_token: SessionToken = None
) -> UserDocument:
    user = await get_session_user(db, session_token) if session_token else None

    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    return user


CurrentUser = Annotated[UserDocument, Depends(current_user)]


# For routes anyone can use, which still credit a logged-in user.
async def optional_user(
    db: Database, session_token: SessionToken = None
) -> UserDocument | None:
    return await get_session_user(db, session_token) if session_token else None


OptionalUser = Annotated[UserDocument | None, Depends(optional_user)]


# Checked against the database on every request; nothing the browser sends
# can make someone the site owner.
async def site_owner(db: Database, user: CurrentUser) -> UserDocument:
    if not await is_site_owner(db, user):
        raise ForbiddenError("Only the site owner can do that")
    return user


SiteOwner = Annotated[UserDocument, Depends(site_owner)]
