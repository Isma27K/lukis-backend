from pydantic import BaseModel, EmailStr


class UpdateSiteSettingsRequest(BaseModel):
    # Only the settings sent are changed.
    registration_open: bool | None = None
    public_links: bool | None = None


class CreateSignupInviteRequest(BaseModel):
    email: EmailStr
