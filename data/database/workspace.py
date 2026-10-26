from datetime import datetime
from typing import Literal, TypedDict

from bson import ObjectId

Role = Literal["owner", "admin", "editor", "viewer"]


class WorkspaceDocument(TypedDict):
    _id: ObjectId
    name: str
    owner_id: ObjectId
    # Created automatically at signup; every drawing needs a workspace. Can't
    # be shared or deleted.
    personal: bool
    created_at: datetime
    # Set on delete, when all memberships and invites are removed too.
    deleted_at: datetime | None


class MembershipDocument(TypedDict):
    _id: ObjectId
    workspace_id: ObjectId
    user_id: ObjectId
    role: Role
    created_at: datetime


class InviteDocument(TypedDict):
    _id: ObjectId
    workspace_id: ObjectId
    # Only the account with this (lowercased) email can accept.
    email: str
    role: Role
    # SHA-256 of the token in the invite link; the token itself isn't stored.
    token_hash: str
    invited_by: ObjectId
    created_at: datetime
    expires_at: datetime
