from pydantic import BaseModel

from data.database.workspace import Role, WorkspaceDocument


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
