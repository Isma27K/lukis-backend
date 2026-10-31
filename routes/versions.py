from fastapi import APIRouter, status

from data.general.drawing import VersionResponse, VersionSummary
from data.request.drawing import CreateVersionRequest, RenameVersionRequest
from function.drawings import get_drawing
from function.versions import (
    create_version,
    delete_version,
    get_version,
    list_versions,
    rename_version,
    version_author,
)
from middleware.auth import CurrentUser
from routes.drawings import REMOVE_ROLE
from utils.config import AppSettings
from utils.db import Database

router = APIRouter(prefix="/drawings/{drawing_id}/versions", tags=["versions"])


@router.get("")
async def drawing_versions(
    drawing_id: str, db: Database, user: CurrentUser
) -> list[VersionSummary]:
    drawing, _ = await get_drawing(db, drawing_id, user, "viewer")
    return [
        VersionSummary.from_documents(version, author)
        for version, author in await list_versions(db, drawing)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def save_version(
    drawing_id: str,
    body: CreateVersionRequest,
    db: Database,
    settings: AppSettings,
    user: CurrentUser,
) -> VersionSummary:
    drawing, _ = await get_drawing(db, drawing_id, user, "editor")
    version = await create_version(db, settings, drawing, user, body.name)
    return VersionSummary.from_documents(version, user)


@router.get("/{version_id}")
async def version_details(
    drawing_id: str, version_id: str, db: Database, user: CurrentUser
) -> VersionResponse:
    drawing, _ = await get_drawing(db, drawing_id, user, "viewer")
    version = await get_version(db, drawing, version_id)
    return VersionResponse.from_documents(version, await version_author(db, version))


@router.patch("/{version_id}")
async def name_version(
    drawing_id: str,
    version_id: str,
    body: RenameVersionRequest,
    db: Database,
    user: CurrentUser,
) -> VersionSummary:
    drawing, _ = await get_drawing(db, drawing_id, user, "editor")
    version = await rename_version(
        db, await get_version(db, drawing, version_id), body.name
    )
    return VersionSummary.from_documents(version, await version_author(db, version))


@router.delete("/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_version(
    drawing_id: str, version_id: str, db: Database, user: CurrentUser
) -> None:
    # Like trashing a drawing: editors add to the history, but can't erase it.
    drawing, _ = await get_drawing(db, drawing_id, user, REMOVE_ROLE)
    await delete_version(db, await get_version(db, drawing, version_id))
