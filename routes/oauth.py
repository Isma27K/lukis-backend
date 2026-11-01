"""Logging in with Google, GitHub and Microsoft (see function/oauth.py).

The login page links to GET /auth/oauth/{provider}/start, which sends the
browser on to the provider, which sends it back to /callback. Both are page
loads through the frontend's /api rewrite, so they answer with redirects, and
problems go back to the page that started it as ?oauth_error=<code>.
"""

import hmac
import logging
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Cookie, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from data.database.identity import OAuthPage
from data.general.user import OAuthStartResponse
from data.request.auth import NextPath, Token
from function.emails import account_connected_email
from function.errors import NotFoundError
from function.oauth import (
    STATE_TTL,
    OAuthErrorCode,
    OAuthFailure,
    begin,
    link,
    sign_in,
    take_state,
)
from function.rate_limit import OAUTH_PER_IP, client_ip
from middleware.auth import CurrentUser, OptionalUser
from routes.auth import Limiter, cookie_options, send_verification, start_session
from utils.config import AppSettings, Settings
from utils.db import Database
from utils.mail import AppMailer, send_safely
from utils.oauth import PROVIDER_LABELS, OAuthError, OAuthProviders, ProviderName

logger = logging.getLogger("lukis.oauth")

router = APIRouter(prefix="/auth/oauth", tags=["auth"])

# Holds the state of the login under way, so it only finishes in the browser
# that started it.
STATE_COOKIE = "lukis_oauth"


def callback_url(settings: Settings, provider: ProviderName) -> str:
    """The redirect URI to register with the provider. The frontend sends
    /api/* on to this API."""
    return f"{settings.public_url}/api/auth/oauth/{provider}/callback"


def _problem_url(
    page: OAuthPage,
    provider: ProviderName,
    code: OAuthErrorCode,
    next_path: str | None = None,
    invite: str | None = None,
) -> str:
    query = {"oauth_error": code, "provider": provider}
    if page == "account":
        return f"/account?{urlencode(query)}#connected"
    # Kept, so trying again still leads where they were going.
    if invite:
        query = {"invite": invite, **query}
    if next_path and next_path != "/":
        query = {"next": next_path, **query}
    return f"/{page}?{urlencode(query)}"


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _set_state_cookie(response: Response, settings: Settings, state: str) -> None:
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=int(STATE_TTL.total_seconds()),
        **cookie_options(settings),
    )


@router.get("/{provider}/start")
async def start(
    provider: ProviderName,
    request: Request,
    db: Database,
    providers: OAuthProviders,
    limiter: Limiter,
    settings: AppSettings,
    next_path: Annotated[NextPath | None, Query(alias="next")] = None,
    invite: Token | None = None,
    page: Literal["login", "register"] = "login",
) -> RedirectResponse:
    """Logs in, or signs up. A link, not a form: it only sends the browser to
    the provider, and nothing happens until it comes back."""
    oauth = providers.get(provider)
    if oauth is None:
        return _redirect(_problem_url(page, provider, "unavailable", next_path, invite))
    ip = client_ip(request)
    if limiter.retry_after(OAUTH_PER_IP, ip):
        return _redirect(
            _problem_url(page, provider, "rate_limited", next_path, invite)
        )
    limiter.count(OAUTH_PER_IP, ip)

    state, url = await begin(
        db,
        oauth,
        callback_url(settings, provider),
        "login",
        page,
        next_path=next_path,
        invite_token=invite,
    )
    response = _redirect(url)
    _set_state_cookie(response, settings, state)
    return response


# A POST, unlike /start: it's for the logged-in user, so it goes through the
# JSON-only CSRF guard. The page then sends the browser to `url`.
@router.post("/{provider}/link")
async def start_link(
    provider: ProviderName,
    request: Request,
    response: Response,
    db: Database,
    providers: OAuthProviders,
    limiter: Limiter,
    settings: AppSettings,
    user: CurrentUser,
) -> OAuthStartResponse:
    oauth = providers.get(provider)
    if oauth is None:
        raise NotFoundError(
            f"Logging in with {PROVIDER_LABELS[provider]} isn't set up on this server"
        )
    limiter.check(OAUTH_PER_IP, client_ip(request))
    state, url = await begin(
        db, oauth, callback_url(settings, provider), "link", "account", user["_id"]
    )
    _set_state_cookie(response, settings, state)
    return OAuthStartResponse(url=url)


@router.get("/{provider}/callback")
async def callback(
    provider: ProviderName,
    request: Request,
    background: BackgroundTasks,
    db: Database,
    providers: OAuthProviders,
    mailer: AppMailer,
    limiter: Limiter,
    settings: AppSettings,
    user: OptionalUser,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    state_cookie: Annotated[str | None, Cookie(alias=STATE_COOKIE)] = None,
) -> RedirectResponse:
    def done(url: str) -> RedirectResponse:
        response = _redirect(url)
        # The login under way is used up, whatever happened.
        response.delete_cookie(STATE_COOKIE, **cookie_options(settings))
        return response

    # Logged-in people only ever connect accounts, from the account page; the
    # login page would send them away.
    fallback: OAuthPage = "account" if user else "login"
    ip = client_ip(request)
    if limiter.retry_after(OAUTH_PER_IP, ip):
        return done(_problem_url(fallback, provider, "rate_limited"))
    limiter.count(OAUTH_PER_IP, ip)

    # The state has to come back to the browser that set off with it.
    # Otherwise someone could send a victim a link that finishes the
    # attacker's own login there, logging them in to the attacker's account.
    flow = None
    if (
        state
        and state_cookie
        and hmac.compare_digest(state.encode(), state_cookie.encode())
    ):
        flow = await take_state(db, provider, state)
    if flow is None:
        return done(_problem_url(fallback, provider, "expired"))

    def problem(code: OAuthErrorCode) -> RedirectResponse:
        return done(
            _problem_url(
                flow["page"], provider, code, flow["next"], flow["invite_token"]
            )
        )

    oauth = providers.get(provider)
    if oauth is None:
        return problem("unavailable")
    if error or not code:
        # access_denied is what every provider sends for Cancel.
        return problem("denied" if error == "access_denied" else "failed")
    try:
        profile = await oauth.fetch_profile(
            code, callback_url(settings, provider), flow["code_verifier"], flow["nonce"]
        )
    except OAuthError as failure:
        logger.warning(f"Logging in with {PROVIDER_LABELS[provider]} failed: {failure}")
        return problem("failed")

    try:
        if flow["intent"] == "link":
            # Only into the account that asked, still logged in here.
            if user is None or user["_id"] != flow["user_id"]:
                raise OAuthFailure("expired")
            if await link(db, user, provider, profile) and mailer:
                message = account_connected_email(
                    settings.public_url,
                    user["email"],
                    user["name"],
                    PROVIDER_LABELS[provider],
                )
                background.add_task(send_safely, mailer, message)
            return done(f"/account?{urlencode({'connected': provider})}#connected")

        account, created = await sign_in(db, provider, profile, flow["invite_token"])
    except OAuthFailure as failure:
        return problem(failure.code)

    response = done(flow["next"] or "/")
    await start_session(request, response, db, settings, account["_id"])
    if created and mailer and account.get("email_verified_at") is None:
        await send_verification(background, db, mailer, settings, account, flow["next"])
    return response
