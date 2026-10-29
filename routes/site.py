from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, status

from data.general.site import (
    CreatedSignupInviteResponse,
    SignupInviteResponse,
    SiteSettingsResponse,
)
from data.request.site import CreateSignupInviteRequest, UpdateSiteSettingsRequest
from function.emails import signup_invite_email
from function.rate_limit import INVITES_PER_USER, RateLimiter, get_rate_limiter
from function.site import (
    create_signup_invite,
    get_site,
    list_signup_invites,
    revoke_signup_invite,
    set_registration_open,
)
from middleware.auth import SiteOwner
from utils.config import AppSettings
from utils.db import Database
from utils.mail import AppMailer, send_safely

# Settings for the whole site. Only its owner (the first account) gets in.
router = APIRouter(prefix="/site", tags=["site"])


@router.get("/settings")
async def site_settings(db: Database, owner: SiteOwner) -> SiteSettingsResponse:
    return SiteSettingsResponse.from_document(await get_site(db))


@router.patch("/settings")
async def update_site_settings(
        body: UpdateSiteSettingsRequest,
        db: Database,
        owner: SiteOwner
) -> SiteSettingsResponse:
    site = await set_registration_open(db, owner, body.registration_open)
    return SiteSettingsResponse.from_document(site)


@router.get("/invites")
async def signup_invites(db: Database, owner: SiteOwner) -> list[SignupInviteResponse]:
    return [
        SignupInviteResponse.from_document(invite)
        for invite in await list_signup_invites(db)
    ]


@router.post("/invites", status_code=status.HTTP_201_CREATED)
async def new_signup_invite(
        body: CreateSignupInviteRequest,
        background: BackgroundTasks,
        db: Database,
        mailer: AppMailer,
        limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
        settings: AppSettings,
        owner: SiteOwner,
) -> CreatedSignupInviteResponse:
    if mailer:
        # Each invite sends an email, so this can't become a spam cannon.
        limiter.check(INVITES_PER_USER, str(owner["_id"]))
    invite, token = await create_signup_invite(db, owner, body.email)
    if mailer:
        message = signup_invite_email(
            settings.public_url, invite["email"], owner["name"], token
        )
        background.add_task(send_safely, mailer, message)
    # The link comes back too: the only way to pass it on without email.
    return CreatedSignupInviteResponse(
        **SignupInviteResponse.from_document(invite).model_dump(), token=token
    )


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_signup_invite(invite_id: str, db: Database, owner: SiteOwner) -> None:
    await revoke_signup_invite(db, invite_id)
