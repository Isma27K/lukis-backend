from datetime import datetime
from typing import Literal, TypedDict

from bson import ObjectId

Role = Literal["owner", "admin", "editor", "viewer"]


class WorkspaceDocument(TypedDict):
    _id: ObjectId
    name: str
    owner_id: ObjectId
    # Created automatically at signup; every drawing needs a workspace.
    personal: bool
    created_at: datetime


class MembershipDocument(TypedDict):
    _id: ObjectId
    workspace_id: ObjectId
    user_id: ObjectId
    role: Role
    created_at: datetime
