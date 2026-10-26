from datetime import datetime
from typing import TypedDict

from bson import ObjectId


class SessionDocument(TypedDict):
    _id: ObjectId
    # SHA-256 of the cookie value, so a database leak doesn't hand out logins.
    token_hash: str
    user_id: ObjectId
    created_at: datetime
    expires_at: datetime
