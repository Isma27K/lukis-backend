from datetime import datetime

from pydantic import BaseModel

from data.database.share import ShareAccess, ShareLinkDocument


class ShareLinkResponse(BaseModel):
    """A drawing's public link, as the people who manage it see it."""

    enabled: bool
    # The link is /s/<token>.
    token: str
    access: ShareAccess
    has_password: bool
    expires_at: datetime | None

    @classmethod
    def from_document(cls, link: ShareLinkDocument) -> "ShareLinkResponse":
        return cls(
            enabled=link["enabled"],
            token=link["token"],
            access=link["access"],
            has_password=link["password_hash"] is not None,
            expires_at=link["expires_at"],
        )


class ShareInfoResponse(BaseModel):
    """What the shared page learns about a link before opening the drawing."""

    access: ShareAccess
    password_required: bool
    # Whether this browser may open the drawing: no password, or entered.
    unlocked: bool
    # Hidden until unlocked.
    title: str | None
    has_thumbnail: bool
    # Set when the visitor is a member, who gets the full editor instead.
    member_drawing_id: str | None
    # The logged-in visitor's name, which others see instead of asking.
    viewer_name: str | None
