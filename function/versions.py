from datetime import UTC, datetime, timedelta

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from data.database.drawing import DrawingDocument, SceneDocument
from data.database.user import UserDocument
from data.database.version import VersionDocument
from function.access import parse_id
from function.drawings import users_by_id
from function.errors import NotFoundError
from utils.config import Settings

# No pagination yet; at one automatic snapshot per 10 minutes of editing, a
# month of history rarely comes near it.
LIST_LIMIT = 200
# Everything but the scene itself, for the history list.
SUMMARY_FIELDS = {"elements": 0, "app_state": 0}


def _expiry(settings: Settings, now: datetime) -> datetime:
    return now + timedelta(days=settings.version_retention_days)


async def _insert_version(
    db: AsyncDatabase,
    drawing: DrawingDocument,
    scene: SceneDocument,
    created_at: datetime,
    created_by: ObjectId | None,
    name: str | None,
    expires_at: datetime | None,
) -> VersionDocument:
    elements = [e for e in scene["elements"] if not e.get("isDeleted")]
    version: VersionDocument = {
        "_id": ObjectId(),
        "drawing_id": drawing["_id"],
        "elements": elements,
        "element_count": len(elements),
        "app_state": scene["app_state"],
        "created_at": created_at,
        "created_by": created_by,
        "name": name,
        "expires_at": expires_at,
    }
    await db.versions.insert_one(version)
    return version


async def maybe_auto_snapshot(
    db: AsyncDatabase,
    settings: Settings,
    drawing: DrawingDocument,
    scene: SceneDocument,
    now: datetime,
) -> None:
    """Keeps `scene`, the scene as it was before a save that just landed, if
    the last automatic snapshot is old enough.

    Snapshotting what a save replaces, rather than what it writes, means the
    first edit after a break preserves how the drawing was left.
    """
    last = scene.get("snapshot_at")
    if last and now - last < timedelta(minutes=settings.snapshot_interval_minutes):
        return
    # Claim the slot first, so saves landing together take one snapshot.
    claimed = await db.scenes.update_one(
        {"_id": scene["_id"], "snapshot_at": last}, {"$set": {"snapshot_at": now}}
    )
    if not claimed.modified_count:
        return
    # An empty canvas has nothing worth going back to, but still starts the
    # clock, so a new drawing isn't snapshotted after its first stroke.
    if not any(not e.get("isDeleted") for e in scene["elements"]):
        return
    await _insert_version(
        db,
        drawing,
        scene,
        created_at=drawing["updated_at"],
        created_by=drawing["updated_by"],
        name=None,
        expires_at=_expiry(settings, now),
    )


async def list_versions(
    db: AsyncDatabase, drawing: DrawingDocument
) -> list[tuple[VersionDocument, UserDocument | None]]:
    """A drawing's versions, newest first, with who made each one."""
    versions = (
        await db.versions.find({"drawing_id": drawing["_id"]}, SUMMARY_FIELDS)
        .sort([("created_at", -1), ("_id", -1)])
        .to_list(LIST_LIMIT)
    )
    users = await users_by_id(
        db, {v["created_by"] for v in versions if v["created_by"]}
    )
    return [(v, users.get(v["created_by"])) for v in versions]


async def get_version(
    db: AsyncDatabase, drawing: DrawingDocument, version_id: str
) -> VersionDocument:
    version = await db.versions.find_one(
        {"_id": parse_id(version_id), "drawing_id": drawing["_id"]}
    )
    if version is None:
        raise NotFoundError
    return version


async def version_author(
    db: AsyncDatabase, version: VersionDocument
) -> UserDocument | None:
    """Who made the version, if it was a member whose account still exists."""
    if version["created_by"] is None:
        return None
    return await db.users.find_one({"_id": version["created_by"]})


async def create_version(
    db: AsyncDatabase,
    settings: Settings,
    drawing: DrawingDocument,
    user: UserDocument,
    name: str | None,
) -> VersionDocument:
    """Keeps the scene as it is now. Without a name it's a checkpoint (say,
    before restoring an older version) that expires like automatic snapshots."""
    scene = await db.scenes.find_one({"_id": drawing["_id"]})
    now = datetime.now(UTC)
    return await _insert_version(
        db,
        drawing,
        scene,
        created_at=now,
        created_by=user["_id"],
        name=name,
        expires_at=None if name else _expiry(settings, now),
    )


async def rename_version(
    db: AsyncDatabase, version: VersionDocument, name: str
) -> VersionDocument:
    """Names a version, which also keeps it for good."""
    changes = {"name": name, "expires_at": None}
    await db.versions.update_one({"_id": version["_id"]}, {"$set": changes})
    return {**version, **changes}


async def delete_version(db: AsyncDatabase, version: VersionDocument) -> None:
    await db.versions.delete_one({"_id": version["_id"]})
