from fastapi import APIRouter, status

from data.general.drawing import DrawingResponse, DrawingSummary
from data.general.workspace import WorkspaceResponse
from data.request.drawing import CreateDrawingRequest
from function.access import parse_id, require_role
from function.drawings import create_drawing, list_drawings
from function.workspaces import list_workspaces
from middleware.auth import CurrentUser
from utils.db import Database

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("")
async def my_workspaces(db: Database, user: CurrentUser) -> list[WorkspaceResponse]:
    return [
        WorkspaceResponse.from_document(workspace, role)
        for workspace, role in await list_workspaces(db, user)
    ]


@router.get("/{workspace_id}/drawings")
async def workspace_drawings(
    workspace_id: str, db: Database, user: CurrentUser
) -> list[DrawingSummary]:
    workspace_oid = parse_id(workspace_id)
    await require_role(db, workspace_oid, user, "viewer")
    return [
        DrawingSummary.from_document(drawing)
        for drawing in await list_drawings(db, workspace_oid)
    ]


@router.post("/{workspace_id}/drawings", status_code=status.HTTP_201_CREATED)
async def new_drawing(
    workspace_id: str, body: CreateDrawingRequest, db: Database, user: CurrentUser
) -> DrawingResponse:
    workspace_oid = parse_id(workspace_id)
    membership = await require_role(db, workspace_oid, user, "editor")
    drawing = await create_drawing(db, workspace_oid, user, body.title)
    return DrawingResponse.from_document_with_role(drawing, membership["role"])
