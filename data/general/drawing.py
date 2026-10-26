from datetime import datetime
from typing import Any

from pydantic import BaseModel

from data.database.drawing import DrawingDocument, SceneDocument
from data.database.workspace import Role


class DrawingSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, drawing: DrawingDocument) -> "DrawingSummary":
        return cls(
            id=str(drawing["_id"]),
            title=drawing["title"],
            created_at=drawing["created_at"],
            updated_at=drawing["updated_at"],
        )


class DrawingResponse(DrawingSummary):
    workspace_id: str
    # The current user's role in the drawing's workspace.
    role: Role

    @classmethod
    def from_document_with_role(
        cls, drawing: DrawingDocument, role: Role
    ) -> "DrawingResponse":
        return cls(
            **DrawingSummary.from_document(drawing).model_dump(),
            workspace_id=str(drawing["workspace_id"]),
            role=role,
        )


class SceneResponse(BaseModel):
    elements: list[dict[str, Any]]
    app_state: dict[str, Any]
    rev: int

    @classmethod
    def from_document(cls, scene: SceneDocument) -> "SceneResponse":
        return cls(
            elements=scene["elements"], app_state=scene["app_state"], rev=scene["rev"]
        )


class SaveSceneResponse(BaseModel):
    rev: int
