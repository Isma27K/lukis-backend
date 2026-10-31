from fastapi import APIRouter, status

from data.general.drawing import DrawingResponse, SaveSceneResponse, SceneResponse
from data.request.drawing import (
    MoveDrawingRequest,
    SaveSceneRequest,
    UpdateDrawingRequest,
)
from function.drawings import (
    delete_drawing,
    duplicate_drawing,
    get_drawing,
    move_drawing,
    purge_drawing,
    rename_drawing,
    restore_drawing,
)
from function.scene import load_scene, save_scene
from middleware.auth import CurrentUser
from utils.config import AppSettings
from utils.db import Database
from utils.storage import ObjectStorage

router = APIRouter(prefix="/drawings", tags=["drawings"])

# Removing a drawing from its workspace (trash, delete forever, move out) and
# restoring it are kept to admins and owners. Editors change what's in a
# drawing, but can't make it disappear.
REMOVE_ROLE = "admin"


@router.get("/{drawing_id}")
async def drawing_details(
    drawing_id: str, db: Database, user: CurrentUser
) -> DrawingResponse:
    drawing, membership = await get_drawing(db, drawing_id, user, "viewer")
    return DrawingResponse.from_document_with_role(drawing, membership["role"])


@router.patch("/{drawing_id}")
async def update_drawing(
    drawing_id: str, body: UpdateDrawingRequest, db: Database, user: CurrentUser
) -> DrawingResponse:
    drawing, membership = await get_drawing(db, drawing_id, user, "editor")
    drawing = await rename_drawing(db, drawing, user, body.title)
    return DrawingResponse.from_document_with_role(drawing, membership["role"])


@router.post("/{drawing_id}/move")
async def relocate_drawing(
    drawing_id: str, body: MoveDrawingRequest, db: Database, user: CurrentUser
) -> DrawingResponse:
    # Taking a drawing out of a workspace removes it for everyone there, and
    # would let an editor delete it from a workspace they own.
    drawing, _ = await get_drawing(db, drawing_id, user, REMOVE_ROLE)
    moved, membership = await move_drawing(db, drawing, user, body.workspace_id)
    return DrawingResponse.from_document_with_role(moved, membership["role"])


@router.post("/{drawing_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def copy_drawing(
    drawing_id: str, db: Database, storage: ObjectStorage, user: CurrentUser
) -> DrawingResponse:
    drawing, membership = await get_drawing(db, drawing_id, user, "editor")
    copy = await duplicate_drawing(db, storage, drawing, user)
    return DrawingResponse.from_document_with_role(copy, membership["role"])


@router.delete("/{drawing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def trash_drawing(drawing_id: str, db: Database, user: CurrentUser) -> None:
    drawing, _ = await get_drawing(db, drawing_id, user, REMOVE_ROLE)
    await delete_drawing(db, drawing, user)


@router.post("/{drawing_id}/restore")
async def untrash_drawing(
    drawing_id: str, db: Database, user: CurrentUser
) -> DrawingResponse:
    drawing, membership = await get_drawing(
        db, drawing_id, user, REMOVE_ROLE, trashed=True
    )
    drawing = await restore_drawing(db, drawing)
    return DrawingResponse.from_document_with_role(drawing, membership["role"])


@router.delete("/{drawing_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def destroy_drawing(
    drawing_id: str, db: Database, storage: ObjectStorage, user: CurrentUser
) -> None:
    drawing, _ = await get_drawing(db, drawing_id, user, REMOVE_ROLE, trashed=True)
    await purge_drawing(db, storage, drawing)


@router.get("/{drawing_id}/scene")
async def drawing_scene(
    drawing_id: str, db: Database, user: CurrentUser
) -> SceneResponse:
    drawing, _ = await get_drawing(db, drawing_id, user, "viewer")
    return SceneResponse.from_document(await load_scene(db, drawing))


@router.put("/{drawing_id}/scene")
async def update_scene(
    drawing_id: str,
    body: SaveSceneRequest,
    db: Database,
    settings: AppSettings,
    user: CurrentUser,
) -> SaveSceneResponse:
    drawing, _ = await get_drawing(db, drawing_id, user, "editor")
    elements = [element.model_dump() for element in body.elements]
    rev = await save_scene(
        db, settings, drawing, user["_id"], elements, body.app_state
    )
    return SaveSceneResponse(rev=rev)
