from datetime import datetime
from typing import Literal, TypedDict

from bson import ObjectId

# What anyone with a drawing's public link can do with it.
ShareAccess = Literal["view", "edit"]


class ShareLinkDocument(TypedDict):
    """A drawing's public link. Each drawing has at most one."""

    _id: ObjectId
    drawing_id: ObjectId
    # The secret part of the link, /s/<token>. Kept as is rather than hashed:
    # the people who share a drawing need to copy its link again later.
    token: str
    # Turning a link off and on again keeps its URL; resetting changes it.
    enabled: bool
    access: ShareAccess
    # Argon2, like account passwords. None when the link needs no password.
    password_hash: str | None
    # Bumped when the password changes or the link is reset, which signs out
    # everyone who unlocked it before.
    epoch: int
    # None for links that never expire.
    expires_at: datetime | None
    created_by: ObjectId
    created_at: datetime
    updated_by: ObjectId
    updated_at: datetime


class ShareGrantDocument(TypedDict):
    """A browser that has entered a link's password."""

    _id: ObjectId
    # SHA-256 of the token in the browser's cookie.
    token_hash: str
    link_id: ObjectId
    # The link's epoch when unlocked; the grant stops working once it moves on.
    epoch: int
    created_at: datetime
    # Mongo deletes grants once this has passed.
    expires_at: datetime
