from fastapi import APIRouter
from pydantic import BaseModel

from utils.mail import AppMailer

router = APIRouter(tags=["features"])


class FeaturesResponse(BaseModel):
    # Whether SMTP is set up. Without it there's no email verification,
    # password reset or invite emails; invites are shared as links.
    email: bool


# Public: the login page needs it to decide whether to offer password reset.
@router.get("/features")
async def features(mailer: AppMailer) -> FeaturesResponse:
    return FeaturesResponse(email=mailer is not None)
