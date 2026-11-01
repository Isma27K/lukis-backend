from fastapi import APIRouter
from pydantic import BaseModel

from data.database.site import RegistrationMode
from function.site import get_site, public_links_enabled, registration_mode
from utils.db import Database
from utils.mail import AppMailer
from utils.oauth import OAuthProviders, ProviderName

router = APIRouter(tags=["features"])


class FeaturesResponse(BaseModel):
    # Whether SMTP is set up. Without it there's no email verification,
    # password reset or invite emails; invites are shared as links.
    email: bool
    # Who may sign up: "setup" (the first account, with the setup code from
    # the logs), "open" (anyone) or "invite_only" (only with an invite link).
    registration: RegistrationMode
    # Whether the site owner allows sharing drawings through public links.
    public_links: bool
    # Which "Continue with …" buttons to show: the providers with a client ID
    # and secret set, in order.
    oauth_providers: list[ProviderName]


# Public: the login and sign-up pages need it, e.g. to decide whether to offer
# password reset.
@router.get("/features")
async def features(
    db: Database, mailer: AppMailer, providers: OAuthProviders
) -> FeaturesResponse:
    site = await get_site(db)
    return FeaturesResponse(
        email=mailer is not None,
        registration=registration_mode(site),
        public_links=public_links_enabled(site),
        oauth_providers=list(providers),
    )
