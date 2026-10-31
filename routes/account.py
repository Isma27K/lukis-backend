from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from data.general.user import SessionResponse, UserResponse
from data.request.account import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    UpdateProfileRequest,
    UploadAvatarRequest,
)
from function.account import (
    change_password,
    check_password,
    delete_account,
    end_other_sessions,
    end_session,
    list_sessions,
    remove_avatar,
    save_avatar,
    update_name,
)
from function.auth import hash_token
from function.emails import account_deleted_email, password_changed_email
from function.site import is_site_owner
from middleware.auth import SESSION_COOKIE, CurrentUser, SessionToken
from routes.auth import Limiter, cookie_options, start_session
from utils.config import AppSettings
from utils.db import Database
from utils.mail import AppMailer, send_safely
from utils.storage import ObjectStorage

router = APIRouter(prefix="/account", tags=["account"])


@router.patch("")
async def update_profile(
    body: UpdateProfileRequest, db: Database, user: CurrentUser
) -> UserResponse:
    user = await update_name(db, user, body.name)
    return UserResponse.from_document(user, await is_site_owner(db, user))


@router.put("/avatar")
async def upload_avatar(
    body: UploadAvatarRequest, db: Database, storage: ObjectStorage, user: CurrentUser
) -> UserResponse:
    user = await save_avatar(db, storage, user, body.data_url)
    return UserResponse.from_document(user, await is_site_owner(db, user))


@router.delete("/avatar")
async def delete_avatar(
    db: Database, storage: ObjectStorage, user: CurrentUser
) -> UserResponse:
    user = await remove_avatar(db, storage, user)
    return UserResponse.from_document(user, await is_site_owner(db, user))


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def update_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: Database,
    mailer: AppMailer,
    limiter: Limiter,
    settings: AppSettings,
    user: CurrentUser,
) -> None:
    await check_password(limiter, user, body.current_password)
    await change_password(db, user, body.new_password)
    # Every session ended, so whoever had the old password is out; this
    # browser carries on with a new one.
    await start_session(request, response, db, settings, user["_id"])
    if mailer:
        message = password_changed_email(
            settings.public_url, user["email"], user["name"]
        )
        background.add_task(send_safely, mailer, message)


@router.get("/sessions")
async def sessions(
    db: Database, user: CurrentUser, token: SessionToken = None
) -> list[SessionResponse]:
    current = hash_token(token) if token else None
    listed = [
        SessionResponse.from_document(session, session["token_hash"] == current)
        for session in await list_sessions(db, user)
    ]
    # This device first, then the most recently used.
    listed.sort(key=lambda s: (s.current, s.last_used_at), reverse=True)
    return listed


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def log_out_session(session_id: str, db: Database, user: CurrentUser) -> None:
    await end_session(db, user, session_id)


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def log_out_other_sessions(
    db: Database, user: CurrentUser, token: SessionToken = None
) -> None:
    # CurrentUser already needed the cookie, so token is set.
    await end_other_sessions(db, user, token or "")


# POST rather than DELETE, so the password can go in a body.
@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
async def remove_account(
    body: DeleteAccountRequest,
    response: Response,
    background: BackgroundTasks,
    db: Database,
    storage: ObjectStorage,
    mailer: AppMailer,
    limiter: Limiter,
    settings: AppSettings,
    user: CurrentUser,
) -> None:
    await check_password(limiter, user, body.password)
    await delete_account(db, storage, user)
    response.delete_cookie(SESSION_COOKIE, **cookie_options(settings))
    if mailer:
        message = account_deleted_email(user["email"], user["name"])
        background.add_task(send_safely, mailer, message)
