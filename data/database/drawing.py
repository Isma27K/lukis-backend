from datetime import datetime
from typing import Any, NotRequired, TypedDict

from bson import ObjectId


class DrawingDocument(TypedDict):
    _id: ObjectId
    workspace_id: ObjectId
    title: str
    created_by: ObjectId
    created_at: datetime
    # None when the last change came from a guest on a public link.
    updated_by: ObjectId | None
    updated_at: datetime
    # Set when moved to the trash; trashed drawings are hidden everywhere but
    # the trash itself.
    deleted_at: datetime | None
    # Fields added after the first release are missing from older drawings.
    deleted_by: NotRequired[ObjectId | None]
    # Epoch ms of the latest thumbnail, None until there is one. Part of the
    # thumbnail's URL, so browsers can cache each version for good.
    thumbnail_version: NotRequired[int | None]


class FileDocument(TypedDict):
    """An image used in a drawing; the bytes live in object storage."""

    _id: ObjectId
    drawing_id: ObjectId
    # Excalidraw's id for the file, by default a SHA-1 of its contents. Unique
    # per drawing.
    file_id: str
    mime_type: str
    size: int
    # None for files added by a guest on a public link.
    created_by: ObjectId | None
    created_at: datetime


class SceneContent(TypedDict):
    # Excalidraw elements, stored as the editor sends them.
    elements: list[dict[str, Any]]
    app_state: dict[str, Any]


class SceneDocument(SceneContent):
    # Same as the drawing's _id. Kept apart from DrawingDocument so listing
    # drawings never loads element arrays.
    _id: ObjectId
    # Bumped on every save; writes only land if it hasn't moved since the read.
    rev: int
    # When the version history last took an automatic snapshot. Missing from
    # drawings that have never had one.
    snapshot_at: NotRequired[datetime | None]
