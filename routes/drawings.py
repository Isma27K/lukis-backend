from fastapi import APIRouter, status

from data.general.drawing import DrawingResponse, SaveSceneResponse, SceneResponse
from data.request.drawing import SaveSceneRequest, UpdateDrawingRequest
from function.drawings import (
    delete_drawing,
    duplicate_drawing,
    get_drawing,
    rename_drawing,
)
from function.scene import load_scene, save_scene
from middleware.auth import CurrentUser
from utils.db import Database

router = APIRouter(prefix="/drawings", tags=["drawings"])


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


@router.post("/{drawing_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def copy_drawing(
    drawing_id: str, db: Database, user: CurrentUser
) -> DrawingResponse:
    drawing, membership = await get_drawing(db, drawing_id, user, "editor")
    copy = await duplicate_drawing(db, drawing, user)
    return DrawingResponse.from_document_with_role(copy, membership["role"])


@router.delete("/{drawing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def trash_drawing(drawing_id: str, db: Database, user: CurrentUser) -> None:
    drawing, _ = await get_drawing(db, drawing_id, user, "editor")
    await delete_drawing(db, drawing)


@router.get("/{drawing_id}/scene")
async def drawing_scene(
    drawing_id: str, db: Database, user: CurrentUser
) -> SceneResponse:
    drawing, _ = await get_drawing(db, drawing_id, user, "viewer")
    return SceneResponse.from_document(await load_scene(db, drawing))


@router.put("/{drawing_id}/scene")
async def update_scene(
    drawing_id: str, body: SaveSceneRequest, db: Database, user: CurrentUser
) -> SaveSceneResponse:
    drawing, _ = await get_drawing(db, drawing_id, user, "editor")
    elements = [element.model_dump() for element in body.elements]
    rev = await save_scene(db, drawing, user, elements, body.app_state)
    return SaveSceneResponse(rev=rev)
