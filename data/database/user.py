from datetime import datetime
from typing import Literal, NotRequired, TypedDict

from bson import ObjectId


class UserDocument(TypedDict):
    _id: ObjectId
    # Stored lowercased; the unique index relies on it.
    email: str
    name: str
    password_hash: str
    created_at: datetime
    # When the user followed the link emailed to them; missing or None until
    # then (accounts from before verification existed have no field at all).
    email_verified_at: NotRequired[datetime | None]


EmailTokenPurpose = Literal["verify_email", "reset_password"]


class EmailTokenDocument(TypedDict):
    """A single-use link sent by email."""

    _id: ObjectId
    # SHA-256 of the token in the link; the token itself isn't stored.
    token_hash: str
    purpose: EmailTokenPurpose
    user_id: ObjectId
    # The address it was sent to; the link only counts while it's still the
    # account's address.
    email: str
    created_at: datetime
    expires_at: datetime
