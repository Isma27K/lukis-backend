from datetime import datetime
from typing import Any

from pydantic import BaseModel

from data.database.drawing import DrawingDocument, SceneDocument
from data.database.user import UserDocument
from data.database.workspace import Role


class DrawingSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    # None until the editor has uploaded a thumbnail.
    thumbnail_version: int | None

    @classmethod
    def from_document(cls, drawing: DrawingDocument) -> "DrawingSummary":
        return cls(
            id=str(drawing["_id"]),
            title=drawing["title"],
            created_at=drawing["created_at"],
            updated_at=drawing["updated_at"],
            thumbnail_version=drawing.get("thumbnail_version"),
        )


class TrashedDrawing(DrawingSummary):
    deleted_at: datetime
    # The name of whoever moved it to the trash, if they still exist.
    deleted_by: str | None

    @classmethod
    def from_documents(
        cls, drawing: DrawingDocument, deleted_by: UserDocument | None
    ) -> "TrashedDrawing":
        return cls(
            **DrawingSummary.from_document(drawing).model_dump(),
            deleted_at=drawing["deleted_at"],
            deleted_by=deleted_by["name"] if deleted_by else None,
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
