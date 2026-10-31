from datetime import timedelta
from typing import Annotated

from bson import ObjectId
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)

from data.database.user import UserDocument
from data.general.site import SignupInvitePreviewResponse
from data.general.user import UserResponse
from data.request.auth import (
    ConfirmPasswordResetRequest,
    LoginRequest,
    PasswordResetRequest,
    RegisterRequest,
    ResendVerificationRequest,
    VerifyEmailRequest,
)
from function.auth import (
    EmailTakenError,
    authenticate,
    create_email_token,
    create_session,
    delete_session,
    find_user_by_email,
    normalize_email,
    reset_password,
    verify_email,
)
from function.emails import password_reset_email, verification_email
from function.errors import EmailDisabledError, NotFoundError, TooManyRequestsError
from function.invites import INVALID_INVITE
from function.rate_limit import (
    LOGIN_FAILURES_PER_EMAIL,
    LOGIN_PER_IP,
    REGISTER_PER_IP,
    RESET_PER_EMAIL,
    RESET_PER_IP,
    TOKEN_USE_PER_IP,
    VERIFY_RESEND_PER_USER,
    RateLimiter,
    client_ip,
    get_rate_limiter,
)
from function.site import find_signup_invite, is_site_owner, register_account
from middleware.auth import SESSION_COOKIE, CurrentUser, SessionToken
from utils.config import AppSettings, Settings
from utils.db import Database
from utils.mail import AppMailer, Mailer, send_safely

router = APIRouter(prefix="/auth", tags=["auth"])

Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


def cookie_options(settings: Settings) -> dict:
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
        **cookie_options(settings),
    )


async def _send_verification(
    background: BackgroundTasks,
    db: Database,
    mailer: Mailer,
    settings: Settings,
    user: UserDocument,
    next_path: str | None,
) -> None:
    token = await create_email_token(db, user, "verify_email")
    email = verification_email(
        settings.public_url, user["email"], user["name"], token, next_path
    )
    # Sent after the response, so a slow mail server doesn't hold it up.
    background.add_task(send_safely, mailer, email)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: Database,
    mailer: AppMailer,
    limiter: Limiter,
    settings: AppSettings,
) -> UserResponse:
    limiter.check(REGISTER_PER_IP, client_ip(request))
    try:
        user, site_owner = await register_account(
            db,
            body.email,
            body.name,
            body.password,
            invite_token=body.invite_token,
            setup_code=body.setup_code,
        )
    except EmailTakenError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists"
        )
    await _start_session(response, db, settings, user["_id"])
    if mailer:
        await _send_verification(background, db, mailer, settings, user, body.next)
    return UserResponse.from_document(user, site_owner)


# Public, for the sign-up page to fill in the invited address. No rate limit,
# like GET /invites/{token}: tokens are 256 bits, and Next.js calls this from
# its server, so a limit per IP would lump every visitor together.
@router.get("/signup-invites/{token}")
async def signup_invite(token: str, db: Database) -> SignupInvitePreviewResponse:
    email = await find_signup_invite(db, token)
    if email is None:
        raise NotFoundError(INVALID_INVITE)
    return SignupInvitePreviewResponse(email=email)


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Database,
    limiter: Limiter,
    settings: AppSettings,
) -> UserResponse:
    limiter.check(LOGIN_PER_IP, client_ip(request))
    email = normalize_email(body.email)
    # Only failures count against the account, so its owner logging in
    # normally never runs into the limit.
    if retry_after := limiter.retry_after(LOGIN_FAILURES_PER_EMAIL, email):
        raise TooManyRequestsError(retry_after)

    user = await authenticate(db, email, body.password)
    if user is None:
        limiter.count(LOGIN_FAILURES_PER_EMAIL, email)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid email or password"
        )
    await _start_session(response, db, settings, user["_id"])
    return UserResponse.from_document(user, await is_site_owner(db, user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response, db: Database, settings: AppSettings, token: SessionToken = None
) -> None:
    if token:
        await delete_session(db, token)
    response.delete_cookie(SESSION_COOKIE, **cookie_options(settings))


@router.get("/me")
async def me(db: Database, user: CurrentUser) -> UserResponse:
    return UserResponse.from_document(user, await is_site_owner(db, user))


@router.post("/verify-email/resend", status_code=status.HTTP_204_NO_CONTENT)
async def resend_verification(
    body: ResendVerificationRequest,
    background: BackgroundTasks,
    db: Database,
    mailer: AppMailer,
    limiter: Limiter,
    settings: AppSettings,
    user: CurrentUser,
) -> None:
    if mailer is None:
        raise EmailDisabledError
    if user.get("email_verified_at") is not None:
        return
    limiter.check(VERIFY_RESEND_PER_USER, str(user["_id"]))
    await _send_verification(background, db, mailer, settings, user, body.next)


# Works without a session: the link may be opened in another browser.
@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_email(
    body: VerifyEmailRequest, request: Request, db: Database, limiter: Limiter
) -> None:
    limiter.check(TOKEN_USE_PER_IP, client_ip(request))
    await verify_email(db, body.token)


@router.post("/password-reset", status_code=status.HTTP_204_NO_CONTENT)
async def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    background: BackgroundTasks,
    db: Database,
    mailer: AppMailer,
    limiter: Limiter,
    settings: AppSettings,
) -> None:
    """Always answers the same way, so it can't be used to find out which
    addresses have accounts."""
    if mailer is None:
        raise EmailDisabledError
    limiter.check(RESET_PER_IP, client_ip(request))
    email = normalize_email(body.email)
    if limiter.retry_after(RESET_PER_EMAIL, email):
        # Quietly, for the same reason.
        return
    limiter.count(RESET_PER_EMAIL, email)
    user = await find_user_by_email(db, email)
    if user is None:
        return
    token = await create_email_token(db, user, "reset_password")
    message = password_reset_email(
        settings.public_url, user["email"], user["name"], token
    )
    background.add_task(send_safely, mailer, message)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(
    body: ConfirmPasswordResetRequest,
    request: Request,
    response: Response,
    db: Database,
    limiter: Limiter,
    settings: AppSettings,
) -> None:
    limiter.check(TOKEN_USE_PER_IP, client_ip(request))
    await reset_password(db, body.token, body.password)
    # Every session of the account was ended, this browser's included.
    response.delete_cookie(SESSION_COOKIE, **cookie_options(settings))
