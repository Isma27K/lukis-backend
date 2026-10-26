from datetime import UTC, datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from data.database.drawing import DrawingDocument, SceneDocument
from data.database.user import UserDocument
from data.database.workspace import MembershipDocument, Role
from function.access import parse_id, require_role
from function.errors import NotFoundError

# No pagination yet; plenty for a personal workspace.
LIST_LIMIT = 500


async def get_drawing(
    db: AsyncDatabase, drawing_id: str, user: UserDocument, min_role: Role
) -> tuple[DrawingDocument, MembershipDocument]:
    drawing = await db.drawings.find_one(
        {"_id": parse_id(drawing_id), "deleted_at": None}
    )
    if drawing is None:
        raise NotFoundError
    membership = await require_role(db, drawing["workspace_id"], user, min_role)
    return drawing, membership


async def list_drawings(
    db: AsyncDatabase, workspace_id: ObjectId
) -> list[DrawingDocument]:
    return (
        await db.drawings.find({"workspace_id": workspace_id, "deleted_at": None})
        .sort("updated_at", -1)
        .to_list(LIST_LIMIT)
    )


async def create_drawing(
    db: AsyncDatabase,
    workspace_id: ObjectId,
    user: UserDocument,
    title: str,
    scene: SceneDocument | None = None,
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
    db: AsyncDatabase, drawing: DrawingDocument, user: UserDocument
) -> DrawingDocument:
    scene = await db.scenes.find_one({"_id": drawing["_id"]})
    title = f"Copy of {drawing['title']}"[:120]
    return await create_drawing(db, drawing["workspace_id"], user, title, scene)


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


async def delete_drawing(db: AsyncDatabase, drawing: DrawingDocument) -> None:
    await db.drawings.update_one(
        {"_id": drawing["_id"]}, {"$set": {"deleted_at": datetime.now(UTC)}}
    )
