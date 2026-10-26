import secrets
from datetime import UTC, datetime, timedelta

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from data.database.user import UserDocument
from data.database.workspace import (
    InviteDocument,
    MembershipDocument,
    Role,
    WorkspaceDocument,
)
from function.access import parse_id
from function.auth import hash_token, normalize_email
from function.errors import ConflictError, ForbiddenError, NotFoundError

INVITE_TTL = timedelta(days=7)
INVALID_INVITE = "This invite link is invalid or has expired"


async def create_invite(
        db: AsyncDatabase,
        workspace: WorkspaceDocument,
        actor: MembershipDocument,
        email: str,
        role: Role,
) -> tuple[InviteDocument, str]:
    """Returns the invite and the token for its link."""
    if workspace["personal"]:
        raise ForbiddenError(
            "Personal workspaces can't be shared. Create a workspace to invite people."
        )
    if role == "owner" and actor["role"] != "owner":
        raise ForbiddenError("Only owners can invite owners")

    email = normalize_email(email)
    user = await db.users.find_one({"email": email}, {"_id": 1})
    if user and await db.memberships.find_one(
            {"workspace_id": workspace["_id"], "user_id": user["_id"]}, {"_id": 1}
    ):
        raise ConflictError(f"{email} is already a member")

    # A new invite replaces any earlier one for the same person.
    await db.invites.delete_many({"workspace_id": workspace["_id"], "email": email})
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    invite: InviteDocument = {
        "_id": ObjectId(),
        "workspace_id": workspace["_id"],
        "email": email,
        "role": role,
        "token_hash": hash_token(token),
        "invited_by": actor["user_id"],
        "created_at": now,
        "expires_at": now + INVITE_TTL,
    }
    await db.invites.insert_one(invite)
    return invite, token


async def list_invites(
        db: AsyncDatabase, workspace_id: ObjectId
) -> list[InviteDocument]:
    return (
        await db.invites.find(
            {"workspace_id": workspace_id, "expires_at": {"$gt": datetime.now(UTC)}}
        )
        .sort("created_at", -1)
        .to_list()
    )


async def revoke_invite(
        db: AsyncDatabase, workspace_id: ObjectId, invite_id: str
) -> None:
    result = await db.invites.delete_one(
        {"_id": parse_id(invite_id), "workspace_id": workspace_id}
    )
    if not result.deleted_count:
        raise NotFoundError


async def find_invite(
        db: AsyncDatabase,
        token: str
) -> tuple[InviteDocument, WorkspaceDocument]:
    # The TTL index only sweeps about once a minute, so check expiry here too.
    invite = await db.invites.find_one(
        {
            "token_hash": hash_token(token),
            "expires_at": {
                "$gt": datetime.now(UTC)
            }
        }
    )

    if invite is None:
        raise NotFoundError(INVALID_INVITE)

    workspace = await db.workspaces.find_one(
        {
            "_id": invite["workspace_id"],
            "deleted_at": None
        }
    )

    if workspace is None:
        raise NotFoundError(INVALID_INVITE)

    return invite, workspace


async def accept_invite(
        db: AsyncDatabase,
        token: str,
        user: UserDocument
) -> tuple[WorkspaceDocument, MembershipDocument]:
    invite, workspace = await find_invite(db, token)

    if invite["email"] != user["email"]:
        raise ForbiddenError(f"This invite is for {invite['email']}")

    # Someone who is already a member keeps the role they have.
    await db.memberships.update_one(
        {
            "workspace_id": workspace["_id"],
            "user_id": user["_id"]
        },
        {
            "$setOnInsert": {
                "_id": ObjectId(),
                "role": invite["role"],
                "created_at": datetime.now(UTC),
            }
        },
        upsert=True,
    )
    await db.invites.delete_one({"_id": invite["_id"]})
    membership = await db.memberships.find_one(
        {
            "workspace_id": workspace["_id"],
            "user_id": user["_id"]
        }
    )
    return workspace, membership
