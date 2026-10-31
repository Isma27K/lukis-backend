from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from data.request.drawing import SaveThumbnailRequest, UploadFileRequest
from function.drawings import get_drawing
from function.files import (
    Theme,
    clear_thumbnail,
    load_file,
    load_thumbnail,
    save_file,
    save_thumbnail,
)
from middleware.auth import CurrentUser
from utils.config import AppSettings
from utils.db import Database
from utils.storage import ObjectStorage

router = APIRouter(prefix="/drawings", tags=["files"])

FileId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


def _image_response(data: bytes, mime_type: str) -> Response:
    return Response(
        data,
        media_type=mime_type,
        headers={
            # File URLs never change content: files are keyed by content hash,
            # and thumbnail URLs carry their version.
            "Cache-Control": "private, max-age=31536000, immutable",
            # These come from users and are served from the site's own origin,
            # and an SVG can carry scripts: never render one as a page.
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; sandbox"
            ),
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put("/{drawing_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_file(
    drawing_id: str,
    file_id: FileId,
    body: UploadFileRequest,
    db: Database,
    storage: ObjectStorage,
    settings: AppSettings,
    user: CurrentUser,
) -> None:
    drawing, _ = await get_drawing(db, drawing_id, user, "editor")
    await save_file(
        db, storage, drawing, user, file_id, body.data_url, settings.max_file_bytes
    )


@router.get("/{drawing_id}/files/{file_id}")
async def download_file(
    drawing_id: str,
    file_id: FileId,
    db: Database,
    storage: ObjectStorage,
    user: CurrentUser,
) -> Response:
    drawing, _ = await get_drawing(db, drawing_id, user, "viewer")
    return _image_response(*await load_file(db, storage, drawing, file_id))


@router.put("/{drawing_id}/thumbnail", status_code=status.HTTP_204_NO_CONTENT)
async def upload_thumbnail(
    drawing_id: str,
    body: SaveThumbnailRequest,
    db: Database,
    storage: ObjectStorage,
    user: CurrentUser,
) -> None:
    drawing, _ = await get_drawing(db, drawing_id, user, "editor")
    await save_thumbnail(
        db, storage, drawing, {"light": body.light, "dark": body.dark}
    )


@router.delete("/{drawing_id}/thumbnail", status_code=status.HTTP_204_NO_CONTENT)
async def remove_thumbnail(drawing_id: str, db: Database, user: CurrentUser) -> None:
    drawing, _ = await get_drawing(db, drawing_id, user, "editor")
    await clear_thumbnail(db, drawing)


# The dashboard adds ?v=<thumbnail_version> so each version caches separately.
@router.get("/{drawing_id}/thumbnail/{theme}")
async def download_thumbnail(
    drawing_id: str,
    theme: Theme,
    db: Database,
    storage: ObjectStorage,
    user: CurrentUser,
) -> Response:
    # The trash shows thumbnails too.
    drawing, _ = await get_drawing(db, drawing_id, user, "viewer", trashed=None)
    return _image_response(*await load_thumbnail(storage, drawing, theme))
