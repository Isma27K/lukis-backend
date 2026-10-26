from datetime import datetime
from typing import TypedDict

from bson import ObjectId


class UserDocument(TypedDict):
    _id: ObjectId
    # Stored lowercased; the unique index relies on it.
    email: str
    name: str
    password_hash: str
    created_at: datetime
