from fastapi import APIRouter

from data.general.workspace import InvitePreviewResponse, WorkspaceResponse
from function.invites import accept_invite, find_invite
from middleware.auth import CurrentUser
from utils.db import Database

router = APIRouter(prefix="/invites", tags=["invites"])


@router.get("/{token}")
async def invite_details(
    token: str, db: Database, user: CurrentUser
) -> InvitePreviewResponse:
    invite, workspace = await find_invite(db, token)
    inviter = await db.users.find_one({"_id": invite["invited_by"]}, {"name": 1})
    return InvitePreviewResponse(
        workspace_name=workspace["name"],
        role=invite["role"],
        email=invite["email"],
        invited_by=inviter["name"] if inviter else "Someone",
        expires_at=invite["expires_at"],
    )


@router.post("/{token}/accept")
async def join_workspace(
    token: str, db: Database, user: CurrentUser
) -> WorkspaceResponse:
    workspace, membership = await accept_invite(db, token, user)
    return WorkspaceResponse.from_document(workspace, membership["role"])
