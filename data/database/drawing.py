from datetime import datetime
from typing import Any, TypedDict

from bson import ObjectId


class DrawingDocument(TypedDict):
    _id: ObjectId
    workspace_id: ObjectId
    title: str
    created_by: ObjectId
    created_at: datetime
    updated_by: ObjectId
    updated_at: datetime
    # Set when moved to the trash; deleted drawings are hidden everywhere.
    deleted_at: datetime | None


class SceneDocument(TypedDict):
    # Same as the drawing's _id. Kept apart from DrawingDocument so listing
    # drawings never loads element arrays.
    _id: ObjectId
    # Excalidraw elements, stored as the editor sends them.
    elements: list[dict[str, Any]]
    app_state: dict[str, Any]
    # Bumped on every save; writes only land if it hasn't moved since the read.
    rev: int
