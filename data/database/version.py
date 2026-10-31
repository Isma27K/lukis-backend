from datetime import datetime
from typing import Any, TypedDict

from bson import ObjectId


class VersionDocument(TypedDict):
    """A drawing's scene as it was at some moment, for its version history."""

    _id: ObjectId
    drawing_id: ObjectId
    # The live elements only; tombstones have nothing to restore.
    elements: list[dict[str, Any]]
    # Stored apart so listing versions never loads the elements.
    element_count: int
    app_state: dict[str, Any]
    # When the scene looked like this, and who made that last change.
    created_at: datetime
    created_by: ObjectId
    # Named versions are kept for good; unnamed ones are automatic snapshots
    # and checkpoints, which Mongo deletes once expires_at has passed.
    name: str | None
    expires_at: datetime | None
