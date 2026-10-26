from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from data.database.drawing import DrawingDocument, SceneDocument
from data.database.user import UserDocument
from function.errors import ConflictError

Element = dict[str, Any]

# Deleted elements are kept this long so the deletion wins over stale copies
# still held by other clients, then dropped.
TOMBSTONE_TTL = timedelta(days=1)
MAX_SAVE_ATTEMPTS = 5


def _incoming_wins(stored: Element, incoming: Element) -> bool:
    # Same rule as shouldDiscardRemoteElement in Excalidraw's data/reconcile.ts:
    # the higher version wins, and a tie goes to the lower versionNonce.
    if incoming["version"] != stored["version"]:
        return incoming["version"] > stored["version"]
    return incoming["versionNonce"] < stored["versionNonce"]


def merge_elements(
    stored: list[Element], incoming: list[Element], now: datetime
) -> list[Element]:
    merged = {element["id"]: element for element in stored}
    for element in incoming:
        current = merged.get(element["id"])
        if current is None or _incoming_wins(current, element):
            merged[element["id"]] = element

    cutoff_ms = (now - TOMBSTONE_TTL).timestamp() * 1000
    kept = [
        element
        for element in merged.values()
        if not (element.get("isDeleted") and element.get("updated", 0) < cutoff_ms)
    ]
    # Fractional indexes are plain strings ordered by code point, as in the editor.
    return sorted(kept, key=lambda element: (element.get("index") or "", element["id"]))


async def load_scene(db: AsyncDatabase, drawing: DrawingDocument) -> SceneDocument:
    return await db.scenes.find_one({"_id": drawing["_id"]})


async def save_scene(
    db: AsyncDatabase,
    drawing: DrawingDocument,
    user: UserDocument,
    elements: list[Element],
    app_state: dict[str, Any] | None,
) -> int:
    """Merges elements into the stored scene and returns the new rev.

    Uses optimistic concurrency instead of transactions: the write only lands
    if nobody else saved since we read, otherwise we re-read and merge again.
    """
    for _ in range(MAX_SAVE_ATTEMPTS):
        scene = await load_scene(db, drawing)
        now = datetime.now(UTC)
        merged = merge_elements(scene["elements"], elements, now)
        changes: dict[str, Any] = {"elements": merged}
        if app_state is not None:
            changes["app_state"] = app_state

        result = await db.scenes.update_one(
            {"_id": scene["_id"], "rev": scene["rev"]},
            {"$set": changes, "$inc": {"rev": 1}},
        )
        if result.matched_count:
            await db.drawings.update_one(
                {"_id": drawing["_id"]},
                {"$set": {"updated_by": user["_id"], "updated_at": now}},
            )
            return scene["rev"] + 1
    raise ConflictError("Someone else is saving at the same moment, try again")
