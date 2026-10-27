from fastapi import APIRouter, status

from data.general.drawing import DrawingResponse, DrawingSummary, TrashedDrawing
from data.general.workspace import (
    CreatedInviteResponse,
    InviteResponse,
    MemberResponse,
    WorkspaceResponse,
)
from data.request.drawing import CreateDrawingRequest
from data.request.workspace import (
    CreateInviteRequest,
    CreateWorkspaceRequest,
    UpdateMemberRequest,
    UpdateWorkspaceRequest,
)
from function.drawings import create_drawing, list_drawings, list_trash
from function.invites import create_invite, list_invites, revoke_invite
from function.workspaces import (
    change_member_role,
    create_workspace,
    delete_workspace,
    get_workspace,
    list_members,
    list_workspaces,
    remove_member,
    rename_workspace,
)
from middleware.auth import CurrentUser
from utils.db import Database

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("")
async def my_workspaces(db: Database, user: CurrentUser) -> list[WorkspaceResponse]:
    return [
        WorkspaceResponse.from_document(workspace, role)
        for workspace, role in await list_workspaces(db, user)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def new_workspace(
    body: CreateWorkspaceRequest, db: Database, user: CurrentUser
) -> WorkspaceResponse:
    workspace, membership = await create_workspace(db, user, body.name)
    return WorkspaceResponse.from_document(workspace, membership["role"])


@router.patch("/{workspace_id}")
async def update_workspace(
    workspace_id: str, body: UpdateWorkspaceRequest, db: Database, user: CurrentUser
) -> WorkspaceResponse:
    workspace, membership = await get_workspace(db, workspace_id, user, "admin")
    workspace = await rename_workspace(db, workspace, body.name)
    return WorkspaceResponse.from_document(workspace, membership["role"])


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_workspace(workspace_id: str, db: Database, user: CurrentUser) -> None:
    workspace, _ = await get_workspace(db, workspace_id, user, "owner")
    await delete_workspace(db, workspace)


@router.get("/{workspace_id}/drawings")
async def workspace_drawings(
    workspace_id: str, db: Database, user: CurrentUser
) -> list[DrawingSummary]:
    workspace, _ = await get_workspace(db, workspace_id, user, "viewer")
    return [
        DrawingSummary.from_document(drawing)
        for drawing in await list_drawings(db, workspace["_id"])
    ]


@router.get("/{workspace_id}/trash")
async def workspace_trash(
    workspace_id: str, db: Database, user: CurrentUser
) -> list[TrashedDrawing]:
    workspace, _ = await get_workspace(db, workspace_id, user, "viewer")
    return [
        TrashedDrawing.from_documents(drawing, deleted_by)
        for drawing, deleted_by in await list_trash(db, workspace["_id"])
    ]


@router.post("/{workspace_id}/drawings", status_code=status.HTTP_201_CREATED)
async def new_drawing(
    workspace_id: str, body: CreateDrawingRequest, db: Database, user: CurrentUser
) -> DrawingResponse:
    workspace, membership = await get_workspace(db, workspace_id, user, "editor")
    drawing = await create_drawing(db, workspace["_id"], user, body.title)
    return DrawingResponse.from_document_with_role(drawing, membership["role"])


@router.get("/{workspace_id}/members")
async def workspace_members(
    workspace_id: str, db: Database, user: CurrentUser
) -> list[MemberResponse]:
    workspace, _ = await get_workspace(db, workspace_id, user, "viewer")
    return [
        MemberResponse.from_documents(membership, member)
        for membership, member in await list_members(db, workspace["_id"])
    ]


@router.patch("/{workspace_id}/members/{user_id}")
async def update_member(
    workspace_id: str,
    user_id: str,
    body: UpdateMemberRequest,
    db: Database,
    user: CurrentUser,
) -> MemberResponse:
    workspace, actor = await get_workspace(db, workspace_id, user, "admin")
    membership, member = await change_member_role(
        db, workspace, actor, user_id, body.role
    )
    return MemberResponse.from_documents(membership, member)


@router.delete(
    "/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_member(
    workspace_id: str, user_id: str, db: Database, user: CurrentUser
) -> None:
    # Any member may get this far, to leave; remove_member checks the rest.
    workspace, actor = await get_workspace(db, workspace_id, user, "viewer")
    await remove_member(db, workspace, actor, user_id)


@router.get("/{workspace_id}/invites")
async def workspace_invites(
    workspace_id: str, db: Database, user: CurrentUser
) -> list[InviteResponse]:
    workspace, _ = await get_workspace(db, workspace_id, user, "admin")
    return [
        InviteResponse.from_document(invite)
        for invite in await list_invites(db, workspace["_id"])
    ]


@router.post("/{workspace_id}/invites", status_code=status.HTTP_201_CREATED)
async def new_invite(
    workspace_id: str, body: CreateInviteRequest, db: Database, user: CurrentUser
) -> CreatedInviteResponse:
    workspace, actor = await get_workspace(db, workspace_id, user, "admin")
    invite, token = await create_invite(db, workspace, actor, body.email, body.role)
    return CreatedInviteResponse(
        **InviteResponse.from_document(invite).model_dump(), token=token
    )


@router.delete(
    "/{workspace_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_invite(
    workspace_id: str, invite_id: str, db: Database, user: CurrentUser
) -> None:
    workspace, _ = await get_workspace(db, workspace_id, user, "admin")
    await revoke_invite(db, workspace["_id"], invite_id)
