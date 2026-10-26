from datetime import datetime

from pydantic import BaseModel

from data.database.user import UserDocument
from data.database.workspace import (
    InviteDocument,
    MembershipDocument,
    Role,
    WorkspaceDocument,
)


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    personal: bool
    # The current user's role in this workspace.
    role: Role

    @classmethod
    def from_document(
        cls, workspace: WorkspaceDocument, role: Role
    ) -> "WorkspaceResponse":
        return cls(
            id=str(workspace["_id"]),
            name=workspace["name"],
            personal=workspace["personal"],
            role=role,
        )


class MemberResponse(BaseModel):
    user_id: str
    name: str
    email: str
    role: Role
    joined_at: datetime

    @classmethod
    def from_documents(
        cls, membership: MembershipDocument, user: UserDocument
    ) -> "MemberResponse":
        return cls(
            user_id=str(user["_id"]),
            name=user["name"],
            email=user["email"],
            role=membership["role"],
            joined_at=membership["created_at"],
        )


class InviteResponse(BaseModel):
    id: str
    email: str
    role: Role
    created_at: datetime
    expires_at: datetime

    @classmethod
    def from_document(cls, invite: InviteDocument) -> "InviteResponse":
        return cls(
            id=str(invite["_id"]),
            email=invite["email"],
            role=invite["role"],
            created_at=invite["created_at"],
            expires_at=invite["expires_at"],
        )


class CreatedInviteResponse(InviteResponse):
    # Only ever returned here, once: the database keeps just its hash.
    token: str


class InvitePreviewResponse(BaseModel):
    workspace_name: str
    role: Role
    email: str
    invited_by: str
    expires_at: datetime
