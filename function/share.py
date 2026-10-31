"""Public links: anyone with a drawing's link can view it, or edit it too,
without an account, optionally after entering a password.

Members manage the link through /drawings/{id}/share; everyone else reaches
the drawing through /share/{token}, which mirrors the drawing routes the
editor uses (scene, files, thumbnail, live). Every one of those goes through
resolve_share.
"""

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from data.database.drawing import DrawingDocument
from data.database.share import ShareAccess, ShareGrantDocument, ShareLinkDocument
from data.database.user import UserDocument
from data.database.workspace import Role
from function.auth import hash_password, hash_token, verify_password
from function.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PasswordRequiredError,
)
from function.site import get_site, public_links_enabled

# How long a browser stays unlocked after entering a link's password.
GRANT_TTL = timedelta(days=7)
GRANT_COOKIE_PREFIX = "lukis_share_"
# What a link's access lets someone do, in the editor's terms.
ACCESS_ROLES: dict[ShareAccess, Role] = {"view": "viewer", "edit": "editor"}

# Marks a setting the request left alone, where None means "remove".
UNCHANGED: Any = object()


@dataclass(frozen=True)
class SharedAccess:
    link: ShareLinkDocument
    drawing: DrawingDocument

    @property
    def role(self) -> Role:
        return ACCESS_ROLES[self.link["access"]]


def _new_token() -> str:
    # 144 bits, 24 characters in the URL.
    return secrets.token_urlsafe(18)


def grant_cookie(link: ShareLinkDocument) -> str:
    """The cookie holding a browser's grant for this link."""
    return f"{GRANT_COOKIE_PREFIX}{link['_id']}"


async def get_link(
    db: AsyncDatabase, drawing: DrawingDocument
) -> ShareLinkDocument | None:
    return await db.share_links.find_one({"drawing_id": drawing["_id"]})


async def save_link(
    db: AsyncDatabase,
    drawing: DrawingDocument,
    user: UserDocument,
    enabled: bool,
    access: ShareAccess,
    expires_at: datetime | None,
    password: str | None = UNCHANGED,
) -> ShareLinkDocument:
    """Creates the drawing's link on first use, or changes its settings.

    `password` is a new password, None to drop it, or UNCHANGED. A new
    password signs out everyone who unlocked the old one.
    """
    now = datetime.now(UTC)
    changes: dict[str, Any] = {
        "enabled": enabled,
        "access": access,
        "expires_at": expires_at,
        "updated_by": user["_id"],
        "updated_at": now,
    }
    on_insert: dict[str, Any] = {
        "_id": ObjectId(),
        "token": _new_token(),
        "created_by": user["_id"],
        "created_at": now,
    }
    update: dict[str, Any] = {"$set": changes, "$setOnInsert": on_insert}
    if password is UNCHANGED:
        on_insert["password_hash"] = None
    else:
        changes["password_hash"] = await hash_password(password) if password else None
    if password is not UNCHANGED and password is not None:
        update["$inc"] = {"epoch": 1}
    else:
        on_insert["epoch"] = 0

    # Two first saves at once both try to insert; the loser updates instead.
    for _ in range(2):
        try:
            return await db.share_links.find_one_and_update(
                {"drawing_id": drawing["_id"]},
                update,
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            continue
    raise ConflictError("Couldn't save the link, try again")


async def reset_link(db: AsyncDatabase, link: ShareLinkDocument) -> ShareLinkDocument:
    """Gives the link a new URL. The old one stops working, and so does every
    grant for it."""
    link = await db.share_links.find_one_and_update(
        {"_id": link["_id"]},
        {"$set": {"token": _new_token()}, "$inc": {"epoch": 1}},
        return_document=ReturnDocument.AFTER,
    )
    await db.share_grants.delete_many({"link_id": link["_id"]})
    return link


async def delete_links(db: AsyncDatabase, drawing_id: ObjectId) -> None:
    """For a drawing deleted for good."""
    links = await db.share_links.find({"drawing_id": drawing_id}, {"_id": 1}).to_list()
    link_ids = [link["_id"] for link in links]
    await db.share_grants.delete_many({"link_id": {"$in": link_ids}})
    await db.share_links.delete_many({"drawing_id": drawing_id})


async def find_live_link(
    db: AsyncDatabase, token: str
) -> tuple[ShareLinkDocument, DrawingDocument]:
    """The link and its drawing, if the link works right now: public links are
    on for the site, this one is on and unexpired, and its drawing is neither
    in the trash nor in a deleted workspace. Anything else is "not found"."""
    if not public_links_enabled(await get_site(db)):
        raise NotFoundError
    link = await db.share_links.find_one({"token": token, "enabled": True})
    if link is None or (
        link["expires_at"] is not None and link["expires_at"] <= datetime.now(UTC)
    ):
        raise NotFoundError
    drawing = await db.drawings.find_one({"_id": link["drawing_id"], "deleted_at": None})
    if drawing is None:
        raise NotFoundError
    # Members lose access through their memberships when a workspace goes;
    # links have to check.
    if not await db.workspaces.find_one(
        {"_id": drawing["workspace_id"], "deleted_at": None}, {"_id": 1}
    ):
        raise NotFoundError
    return link, drawing


async def is_unlocked(
    db: AsyncDatabase, link: ShareLinkDocument, cookies: Mapping[str, str]
) -> bool:
    if link["password_hash"] is None:
        return True
    token = cookies.get(grant_cookie(link))
    if not token:
        return False
    grant = await db.share_grants.find_one(
        {
            "token_hash": hash_token(token),
            "link_id": link["_id"],
            "epoch": link["epoch"],
            # The TTL index only sweeps about once a minute.
            "expires_at": {"$gt": datetime.now(UTC)},
        },
        {"_id": 1},
    )
    return grant is not None


async def resolve_share(
    db: AsyncDatabase,
    token: str,
    cookies: Mapping[str, str],
    need: ShareAccess = "view",
) -> SharedAccess:
    """The gate for everything reached through a public link."""
    link, drawing = await find_live_link(db, token)
    if not await is_unlocked(db, link, cookies):
        raise PasswordRequiredError
    if need == "edit" and link["access"] != "edit":
        raise ForbiddenError("This link is view-only")
    return SharedAccess(link, drawing)


async def unlock(db: AsyncDatabase, link: ShareLinkDocument, password: str) -> str:
    """Checks the link's password and returns a grant token for its cookie."""
    if link["password_hash"] is None or not await verify_password(
        password, link["password_hash"]
    ):
        raise ForbiddenError("Wrong password")
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    grant: ShareGrantDocument = {
        "_id": ObjectId(),
        "token_hash": hash_token(token),
        "link_id": link["_id"],
        "epoch": link["epoch"],
        "created_at": now,
        "expires_at": now + GRANT_TTL,
    }
    await db.share_grants.insert_one(grant)
    return token


async def member_drawing_id(
    db: AsyncDatabase, drawing: DrawingDocument, user: UserDocument | None
) -> str | None:
    """The drawing's id if `user` is a member of its workspace, so the shared
    page can send them to the full editor instead."""
    if user is None:
        return None
    membership = await db.memberships.find_one(
        {"workspace_id": drawing["workspace_id"], "user_id": user["_id"]}, {"_id": 1}
    )
    return str(drawing["_id"]) if membership else None
