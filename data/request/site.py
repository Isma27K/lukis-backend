from pydantic import BaseModel, EmailStr


class UpdateSiteSettingsRequest(BaseModel):
    registration_open: bool


class CreateSignupInviteRequest(BaseModel):
    email: EmailStr
