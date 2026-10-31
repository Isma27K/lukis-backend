from datetime import datetime

from pydantic import BaseModel

from data.database.session import SessionDocument
from data.database.user import UserDocument


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    email_verified: bool
    # Whether they run the whole site (the first account), as opposed to
    # owning a workspace.
    site_owner: bool
    # The picture is at /users/{id}/avatar?v={avatar_version}; None without one.
    avatar_version: int | None

    @classmethod
    def from_document(cls, user: UserDocument, site_owner: bool) -> "UserResponse":
        return cls(
            id=str(user["_id"]),
            email=user["email"],
            name=user["name"],
            email_verified=user.get("email_verified_at") is not None,
            site_owner=site_owner,
            avatar_version=user.get("avatar_version"),
        )


class SessionResponse(BaseModel):
    """A device the user is logged in on."""

    id: str
    created_at: datetime
    # When it was last used, to within a few minutes.
    last_used_at: datetime
    # None for sessions from before these were recorded.
    user_agent: str | None
    ip: str | None
    # The session making this request.
    current: bool

    @classmethod
    def from_document(
        cls, session: SessionDocument, current: bool
    ) -> "SessionResponse":
        return cls(
            id=str(session["_id"]),
            created_at=session["created_at"],
            last_used_at=session.get("last_used_at", session["created_at"]),
            user_agent=session.get("user_agent"),
            ip=session.get("ip"),
            current=current,
        )
