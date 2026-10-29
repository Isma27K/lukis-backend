from fastapi import APIRouter
from pydantic import BaseModel

from data.database.site import RegistrationMode
from function.site import get_site, registration_mode
from utils.db import Database
from utils.mail import AppMailer

router = APIRouter(tags=["features"])


class FeaturesResponse(BaseModel):
    # Whether SMTP is set up. Without it there's no email verification,
    # password reset or invite emails; invites are shared as links.
    email: bool
    # Who may sign up: "setup" (the first account, with the setup code from
    # the logs), "open" (anyone) or "invite_only" (only with an invite link).
    registration: RegistrationMode


# Public: the login and sign-up pages need it, e.g. to decide whether to offer
# password reset.
@router.get("/features")
async def features(db: Database, mailer: AppMailer) -> FeaturesResponse:
    return FeaturesResponse(
        email=mailer is not None,
        registration=registration_mode(await get_site(db)),
    )
