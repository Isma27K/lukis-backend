import asyncio
import base64
import binascii
import re
from datetime import UTC, datetime
from typing import Literal

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from data.database.drawing import DrawingDocument, FileDocument
from function.errors import FileTooLargeError, InvalidFileError, NotFoundError
from utils.storage import Storage, file_key, thumbnail_key

Theme = Literal["light", "dark"]
THEMES: tuple[Theme, ...] = ("light", "dark")

# The image types the editor accepts (IMAGE_MIME_TYPES in Excalidraw's
# packages/common/src/constants.ts).
IMAGE_TYPES = {
    "image/svg+xml",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/x-icon",
    "image/avif",
    "image/jfif",
}
THUMBNAIL_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_THUMBNAIL_BYTES = 1024 * 1024

_DATA_URL = re.compile(r"data:([\w.+-]+/[\w.+-]+)(?:;[^,;]*)*;base64,(.*)", re.DOTALL)


def decode_data_url(
    data_url: str, allowed_types: set[str], max_bytes: int
) -> tuple[str, bytes]:
    """Returns the mime type and bytes of a base64 data URL."""
    match = _DATA_URL.fullmatch(data_url)
    if match is None:
        raise InvalidFileError("Expected a base64 data URL")
    mime_type = match.group(1).lower()
    if mime_type not in allowed_types:
        raise InvalidFileError
    # Checked before decoding, so oversized uploads cost no extra memory.
    if len(match.group(2)) * 3 // 4 > max_bytes + 2:
        raise FileTooLargeError(f"Files can be at most {max_bytes // 1024 // 1024} MB")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except binascii.Error as error:
        raise InvalidFileError("Expected a base64 data URL") from error
    if len(data) > max_bytes:
        raise FileTooLargeError(f"Files can be at most {max_bytes // 1024 // 1024} MB")
    return mime_type, data


async def save_file(
    db: AsyncDatabase,
    storage: Storage,
    drawing: DrawingDocument,
    editor_id: ObjectId | None,
    file_id: str,
    data_url: str,
    max_bytes: int,
) -> None:
    """Stores an image; `editor_id` is None for a guest on a public link."""
    mime_type, data = decode_data_url(data_url, IMAGE_TYPES, max_bytes)
    # File ids are content hashes, so a file that's already stored is the same
    # file; the first upload wins and the object never changes.
    if await db.files.find_one(
        {"drawing_id": drawing["_id"], "file_id": file_id}, {"_id": 1}
    ):
        return
    # Bytes first: metadata without its object would be a broken image.
    await storage.put(file_key(drawing["_id"], file_id), data, mime_type)
    document: FileDocument = {
        "_id": ObjectId(),
        "drawing_id": drawing["_id"],
        "file_id": file_id,
        "mime_type": mime_type,
        "size": len(data),
        "created_by": editor_id,
        "created_at": datetime.now(UTC),
    }
    try:
        await db.files.insert_one(document)
    except DuplicateKeyError:
        # Someone uploaded the same file at the same moment.
        pass


async def load_file(
    db: AsyncDatabase, storage: Storage, drawing: DrawingDocument, file_id: str
) -> tuple[bytes, str]:
    if not await db.files.find_one(
        {"drawing_id": drawing["_id"], "file_id": file_id}, {"_id": 1}
    ):
        raise NotFoundError
    stored = await storage.get(file_key(drawing["_id"], file_id))
    if stored is None:
        raise NotFoundError
    return stored


async def save_thumbnail(
    db: AsyncDatabase,
    storage: Storage,
    drawing: DrawingDocument,
    images: dict[Theme, str],
) -> int:
    """Stores a thumbnail per theme and returns its new version."""
    decoded = {
        theme: decode_data_url(images[theme], THUMBNAIL_TYPES, MAX_THUMBNAIL_BYTES)
        for theme in THEMES
    }
    await asyncio.gather(
        *(
            storage.put(thumbnail_key(drawing["_id"], theme), data, mime_type)
            for theme, (mime_type, data) in decoded.items()
        )
    )
    version = int(datetime.now(UTC).timestamp() * 1000)
    # Doesn't touch updated_at: a new thumbnail isn't an edit.
    await db.drawings.update_one(
        {"_id": drawing["_id"]}, {"$set": {"thumbnail_version": version}}
    )
    return version


async def clear_thumbnail(db: AsyncDatabase, drawing: DrawingDocument) -> None:
    # The stored images are simply overwritten by the next thumbnail.
    await db.drawings.update_one(
        {"_id": drawing["_id"]}, {"$set": {"thumbnail_version": None}}
    )


async def load_thumbnail(
    storage: Storage, drawing: DrawingDocument, theme: Theme
) -> tuple[bytes, str]:
    stored = None
    if drawing.get("thumbnail_version") is not None:
        stored = await storage.get(thumbnail_key(drawing["_id"], theme))
    if stored is None:
        raise NotFoundError
    return stored


async def copy_files(
    db: AsyncDatabase,
    storage: Storage,
    source: DrawingDocument,
    target: DrawingDocument,
) -> None:
    """Copies a drawing's images and thumbnail onto a duplicate of it."""
    files = await db.files.find({"drawing_id": source["_id"]}).to_list()
    copies = [
        (file_key(source["_id"], f["file_id"]), file_key(target["_id"], f["file_id"]))
        for f in files
    ]
    version = source.get("thumbnail_version")
    if version is not None:
        copies += [
            (thumbnail_key(source["_id"], theme), thumbnail_key(target["_id"], theme))
            for theme in THEMES
        ]
    await asyncio.gather(*(storage.copy(src, dst) for src, dst in copies))

    if files:
        await db.files.insert_many(
            [{**f, "_id": ObjectId(), "drawing_id": target["_id"]} for f in files]
        )
    if version is not None:
        await db.drawings.update_one(
            {"_id": target["_id"]}, {"$set": {"thumbnail_version": version}}
        )
