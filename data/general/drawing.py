from datetime import datetime
from typing import Any

from pydantic import BaseModel

from data.database.drawing import DrawingDocument, SceneDocument
from data.database.user import UserDocument
from data.database.version import VersionDocument
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


class Editor(BaseModel):
    """Someone who changed a drawing."""

    id: str
    name: str

    @classmethod
    def from_document(cls, user: UserDocument) -> "Editor":
        return cls(id=str(user["_id"]), name=user["name"])


class ListedDrawing(DrawingSummary):
    # Whoever changed it last, if they still exist.
    updated_by: Editor | None

    @classmethod
    def from_documents(
        cls, drawing: DrawingDocument, updated_by: UserDocument | None
    ) -> "ListedDrawing":
        return cls(
            **DrawingSummary.from_document(drawing).model_dump(),
            updated_by=Editor.from_document(updated_by) if updated_by else None,
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


class VersionSummary(BaseModel):
    id: str
    # None for automatic snapshots and checkpoints.
    name: str | None
    # When the drawing looked like this.
    created_at: datetime
    # Who made the change that led to it, if they still exist.
    created_by: Editor | None
    element_count: int
    # When an unnamed version will be deleted; None for named ones.
    expires_at: datetime | None

    @classmethod
    def from_documents(
        cls, version: VersionDocument, created_by: UserDocument | None
    ) -> "VersionSummary":
        return cls(
            id=str(version["_id"]),
            name=version["name"],
            created_at=version["created_at"],
            created_by=Editor.from_document(created_by) if created_by else None,
            element_count=version["element_count"],
            expires_at=version["expires_at"],
        )


class VersionResponse(VersionSummary):
    elements: list[dict[str, Any]]
    app_state: dict[str, Any]

    @classmethod
    def from_documents(
        cls, version: VersionDocument, created_by: UserDocument | None
    ) -> "VersionResponse":
        return cls(
            **VersionSummary.from_documents(version, created_by).model_dump(),
            elements=version["elements"],
            app_state=version["app_state"],
        )
