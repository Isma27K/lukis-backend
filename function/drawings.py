from datetime import UTC, datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from data.database.drawing import DrawingDocument, SceneContent, SceneDocument
from data.database.user import UserDocument
from data.database.workspace import MembershipDocument, Role
from function.access import parse_id, require_role
from function.errors import NotFoundError
from function.files import copy_files
from function.share import delete_links
from utils.storage import Storage, drawing_prefix

# No pagination yet; plenty for a personal workspace.
LIST_LIMIT = 500


async def get_drawing(
    db: AsyncDatabase,
    drawing_id: str,
    user: UserDocument,
    min_role: Role,
    trashed: bool | None = False,
) -> tuple[DrawingDocument, MembershipDocument]:
    """A drawing the user can reach with at least min_role. Only drawings in
    the trash are found with `trashed`, only others without it, and either
    with `trashed=None`."""
    query: dict = {"_id": parse_id(drawing_id)}
    if trashed is not None:
        query["deleted_at"] = {"$ne": None} if trashed else None
    drawing = await db.drawings.find_one(query)
    if drawing is None:
        raise NotFoundError
    membership = await require_role(db, drawing["workspace_id"], user, min_role)
    return drawing, membership


async def users_by_id(
    db: AsyncDatabase, user_ids: set[ObjectId]
) -> dict[ObjectId, UserDocument]:
    """The users among these ids whose accounts still exist."""
    if not user_ids:
        return {}
    users = await db.users.find({"_id": {"$in": list(user_ids)}}).to_list()
    return {user["_id"]: user for user in users}


async def list_drawings(
    db: AsyncDatabase, workspace_id: ObjectId
) -> list[tuple[DrawingDocument, UserDocument | None]]:
    """Drawings, most recently changed first, with who changed them last."""
    drawings = (
        await db.drawings.find({"workspace_id": workspace_id, "deleted_at": None})
        .sort("updated_at", -1)
        .to_list(LIST_LIMIT)
    )
    users = await users_by_id(
        db, {d["updated_by"] for d in drawings if d.get("updated_by")}
    )
    return [(d, users.get(d.get("updated_by"))) for d in drawings]


async def create_drawing(
    db: AsyncDatabase,
    workspace_id: ObjectId,
    user: UserDocument,
    title: str,
    scene: SceneContent | None = None,
) -> DrawingDocument:
    now = datetime.now(UTC)
    drawing: DrawingDocument = {
        "_id": ObjectId(),
        "workspace_id": workspace_id,
        "title": title,
        "created_by": user["_id"],
        "created_at": now,
        "updated_by": user["_id"],
        "updated_at": now,
        "deleted_at": None,
    }
    new_scene: SceneDocument = {
        "_id": drawing["_id"],
        "elements": scene["elements"] if scene else [],
        "app_state": scene["app_state"] if scene else {},
        "rev": 0,
    }
    # Scene first, so every drawing that exists has a scene.
    await db.scenes.insert_one(new_scene)
    await db.drawings.insert_one(drawing)
    return drawing


async def duplicate_drawing(
    db: AsyncDatabase, storage: Storage, drawing: DrawingDocument, user: UserDocument
) -> DrawingDocument:
    scene = await db.scenes.find_one({"_id": drawing["_id"]})
    title = f"Copy of {drawing['title']}"[:120]
    copy = await create_drawing(db, drawing["workspace_id"], user, title, scene)
    await copy_files(db, storage, drawing, copy)
    return {**copy, "thumbnail_version": drawing.get("thumbnail_version")}


async def rename_drawing(
    db: AsyncDatabase, drawing: DrawingDocument, user: UserDocument, title: str
) -> DrawingDocument:
    changes = {
        "title": title,
        "updated_by": user["_id"],
        "updated_at": datetime.now(UTC),
    }
    await db.drawings.update_one({"_id": drawing["_id"]}, {"$set": changes})
    return {**drawing, **changes}


async def move_drawing(
    db: AsyncDatabase, drawing: DrawingDocument, user: UserDocument, workspace_id: str
) -> tuple[DrawingDocument, MembershipDocument]:
    """Returns the moved drawing and the user's membership in its new workspace."""
    target_oid = parse_id(workspace_id)
    membership = await require_role(db, target_oid, user, "editor")
    await db.drawings.update_one(
        {"_id": drawing["_id"]}, {"$set": {"workspace_id": target_oid}}
    )
    return {**drawing, "workspace_id": target_oid}, membership


async def delete_drawing(
    db: AsyncDatabase, drawing: DrawingDocument, user: UserDocument
) -> None:
    """Moves a drawing to the trash."""
    await db.drawings.update_one(
        {"_id": drawing["_id"]},
        {"$set": {"deleted_at": datetime.now(UTC), "deleted_by": user["_id"]}},
    )


async def list_trash(
    db: AsyncDatabase, workspace_id: ObjectId
) -> list[tuple[DrawingDocument, UserDocument | None]]:
    """Trashed drawings, most recently deleted first, with who deleted them."""
    drawings = (
        await db.drawings.find(
            {"workspace_id": workspace_id, "deleted_at": {"$ne": None}}
        )
        .sort("deleted_at", -1)
        .to_list(LIST_LIMIT)
    )
    users = await users_by_id(
        db, {d["deleted_by"] for d in drawings if d.get("deleted_by")}
    )
    return [(d, users.get(d.get("deleted_by"))) for d in drawings]


async def restore_drawing(
    db: AsyncDatabase, drawing: DrawingDocument
) -> DrawingDocument:
    changes = {"deleted_at": None, "deleted_by": None}
    await db.drawings.update_one({"_id": drawing["_id"]}, {"$set": changes})
    return {**drawing, **changes}


async def purge_drawing(
    db: AsyncDatabase, storage: Storage, drawing: DrawingDocument
) -> None:
    """Deletes a trashed drawing for good, with its scene, files, history and
    public link."""
    # Storage first: if it fails, the drawing stays in the trash to retry,
    # rather than leaving files nothing points to.
    await storage.delete_prefix(drawing_prefix(drawing["_id"]))
    await db.drawings.delete_one({"_id": drawing["_id"]})
    await db.scenes.delete_one({"_id": drawing["_id"]})
    await db.files.delete_many({"drawing_id": drawing["_id"]})
    await db.versions.delete_many({"drawing_id": drawing["_id"]})
    await delete_links(db, drawing["_id"])
