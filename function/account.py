"""What users change about their own account: name, picture, password, the
devices they're logged in on, and deleting it all."""

from datetime import UTC, datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from data.database.session import SessionDocument
from data.database.user import UserDocument
from function.access import parse_id
from function.auth import (
    has_password,
    hash_password,
    hash_token,
    normalize_email,
    verify_password,
)
from function.drawings import purge_drawing
from function.errors import (
    ConflictError,
    ForbiddenError,
    InvalidFileError,
    NotFoundError,
    TooManyRequestsError,
)
from function.files import decode_data_url
from function.rate_limit import PASSWORD_FAILURES_PER_USER, RateLimiter
from function.site import is_site_owner
from utils.storage import Storage, avatar_key, user_prefix

# The account page crops and shrinks pictures to 256×256 before uploading, so
# this is plenty. No SVG: pictures are served to anyone, and can't carry
# scripts this way.
AVATAR_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_AVATAR_BYTES = 512 * 1024

WRONG_PASSWORD = "That's not your current password"
WRONG_EMAIL = "Type your email address exactly as it's shown"
SITE_OWNER_CANT_LEAVE = (
    "The site owner's account can't be deleted. Make another account the "
    "site owner first (python -m scripts.site set-owner)."
)


def _matches_type(mime_type: str, data: bytes) -> bool:
    """Whether the bytes start the way the claimed image type does."""
    if mime_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


async def update_name(
    db: AsyncDatabase, user: UserDocument, name: str
) -> UserDocument:
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"name": name}})
    return {**user, "name": name}


async def save_avatar(
    db: AsyncDatabase, storage: Storage, user: UserDocument, data_url: str
) -> UserDocument:
    mime_type, data = decode_data_url(data_url, AVATAR_TYPES, MAX_AVATAR_BYTES)
    if not _matches_type(mime_type, data):
        raise InvalidFileError
    await storage.put(avatar_key(user["_id"]), data, mime_type)
    version = int(datetime.now(UTC).timestamp() * 1000)
    await db.users.update_one(
        {"_id": user["_id"]}, {"$set": {"avatar_version": version}}
    )
    return {**user, "avatar_version": version}


async def remove_avatar(
    db: AsyncDatabase, storage: Storage, user: UserDocument
) -> UserDocument:
    # The picture's URL is public, so the object goes, not just the reference.
    await storage.delete_prefix(user_prefix(user["_id"]))
    await db.users.update_one(
        {"_id": user["_id"]}, {"$set": {"avatar_version": None}}
    )
    return {**user, "avatar_version": None}


async def check_password(
    limiter: RateLimiter, user: UserDocument, password: str | None
) -> None:
    """For changes that need the password even with a session. Only wrong
    guesses count against the limit. Only for accounts that have one."""
    if not password:
        raise ForbiddenError(WRONG_PASSWORD)
    key = str(user["_id"])
    if retry_after := limiter.retry_after(PASSWORD_FAILURES_PER_USER, key):
        raise TooManyRequestsError(retry_after)
    if not await verify_password(password, user["password_hash"] or ""):
        limiter.count(PASSWORD_FAILURES_PER_USER, key)
        raise ForbiddenError(WRONG_PASSWORD)


async def confirm_deletion(
    limiter: RateLimiter,
    user: UserDocument,
    password: str | None,
    email: str | None,
) -> None:
    """The password, or for accounts without one, their email address typed
    out, so a stray click can't delete everything."""
    if has_password(user):
        await check_password(limiter, user, password)
    elif normalize_email(email or "") != user["email"]:
        raise ForbiddenError(WRONG_EMAIL)


async def change_password(
    db: AsyncDatabase, user: UserDocument, password: str
) -> None:
    """Logs out every session, this one too; the caller starts a new one."""
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": await hash_password(password)}},
    )
    await db.sessions.delete_many({"user_id": user["_id"]})
    # A reset link asked for earlier shouldn't undo the change.
    await db.email_tokens.delete_many(
        {"user_id": user["_id"], "purpose": "reset_password"}
    )


async def list_sessions(
    db: AsyncDatabase, user: UserDocument
) -> list[SessionDocument]:
    # The TTL index only sweeps about once a minute, so check expiry here too.
    return await db.sessions.find(
        {"user_id": user["_id"], "expires_at": {"$gt": datetime.now(UTC)}}
    ).to_list()


async def end_session(db: AsyncDatabase, user: UserDocument, session_id: str) -> None:
    result = await db.sessions.delete_one(
        {"_id": parse_id(session_id), "user_id": user["_id"]}
    )
    if result.deleted_count == 0:
        raise NotFoundError


async def end_other_sessions(
    db: AsyncDatabase, user: UserDocument, token: str
) -> None:
    await db.sessions.delete_many(
        {"user_id": user["_id"], "token_hash": {"$ne": hash_token(token)}}
    )


async def _purge_workspace(
    db: AsyncDatabase, storage: Storage, workspace_id: ObjectId
) -> None:
    """Deletes a workspace for good, with every drawing in it."""
    drawings = await db.drawings.find({"workspace_id": workspace_id}).to_list()
    for drawing in drawings:
        await purge_drawing(db, storage, drawing)
    await db.invites.delete_many({"workspace_id": workspace_id})
    await db.memberships.delete_many({"workspace_id": workspace_id})
    await db.workspaces.delete_one({"_id": workspace_id})


async def delete_account(
    db: AsyncDatabase, storage: Storage, user: UserDocument
) -> None:
    """Deletes the account with the workspaces nobody else is in. Drawings in
    shared workspaces stay; they belong to the workspace."""
    if await is_site_owner(db, user):
        raise ConflictError(SITE_OWNER_CANT_LEAVE)

    memberships = await db.memberships.find({"user_id": user["_id"]}).to_list()
    workspace_ids = [membership["workspace_id"] for membership in memberships]
    others = await db.memberships.find(
        {"workspace_id": {"$in": workspace_ids}, "user_id": {"$ne": user["_id"]}},
        {"workspace_id": 1, "role": 1},
    ).to_list()
    shared = {other["workspace_id"] for other in others}
    other_owners = {o["workspace_id"] for o in others if o["role"] == "owner"}

    # A workspace with members left needs an owner left too.
    orphaned = [
        m["workspace_id"]
        for m in memberships
        if m["role"] == "owner"
        and m["workspace_id"] in shared
        and m["workspace_id"] not in other_owners
    ]
    if orphaned:
        names = [
            workspace["name"]
            for workspace in await db.workspaces.find(
                {"_id": {"$in": orphaned}}, {"name": 1}
            ).to_list()
        ]
        raise ConflictError(
            f"You're the only owner of {', '.join(sorted(names))}. "
            "Make someone else an owner first."
        )

    # Storage goes first, as in purge_drawing: if it fails, the account is
    # still there to try again.
    for workspace_id in workspace_ids:
        if workspace_id not in shared:
            await _purge_workspace(db, storage, workspace_id)
    await storage.delete_prefix(user_prefix(user["_id"]))

    await db.memberships.delete_many({"user_id": user["_id"]})
    await db.sessions.delete_many({"user_id": user["_id"]})
    await db.email_tokens.delete_many({"user_id": user["_id"]})
    await db.identities.delete_many({"user_id": user["_id"]})
    await db.oauth_states.delete_many({"user_id": user["_id"]})
    await db.users.delete_one({"_id": user["_id"]})
