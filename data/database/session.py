from datetime import datetime
from typing import NotRequired, TypedDict

from bson import ObjectId


class SessionDocument(TypedDict):
    _id: ObjectId
    # SHA-256 of the cookie value, so a database leak doesn't hand out logins.
    token_hash: str
    user_id: ObjectId
    created_at: datetime
    expires_at: datetime
    # Where it was started, for the account page's list of devices. Missing on
    # sessions from before that list existed.
    user_agent: NotRequired[str | None]
    ip: NotRequired[str | None]
    # Updated at most every few minutes as the session is used.
    last_used_at: NotRequired[datetime]
