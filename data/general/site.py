from datetime import datetime

from pydantic import BaseModel

from data.database.site import SignupInviteDocument, SiteDocument
from function.site import public_links_enabled


class SiteSettingsResponse(BaseModel):
    registration_open: bool
    public_links: bool

    @classmethod
    def from_document(cls, site: SiteDocument) -> "SiteSettingsResponse":
        return cls(
            registration_open=site["registration_open"],
            public_links=public_links_enabled(site),
        )


class SignupInviteResponse(BaseModel):
    id: str
    email: str
    created_at: datetime
    expires_at: datetime

    @classmethod
    def from_document(cls, invite: SignupInviteDocument) -> "SignupInviteResponse":
        return cls(
            id=str(invite["_id"]),
            email=invite["email"],
            created_at=invite["created_at"],
            expires_at=invite["expires_at"],
        )


class CreatedSignupInviteResponse(SignupInviteResponse):
    # Only ever returned here, once: the database keeps just its hash.
    token: str


class SignupInvitePreviewResponse(BaseModel):
    # The only address that can sign up with the link.
    email: str
