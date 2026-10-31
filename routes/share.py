from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response, status

from data.general.drawing import SaveSceneResponse, SceneResponse
from data.general.share import ShareInfoResponse, ShareLinkResponse
from data.request.drawing import (
    SaveSceneRequest,
    SaveThumbnailRequest,
    UploadFileRequest,
)
from data.request.share import ShareSettingsRequest, UnlockShareRequest
from function.drawings import get_drawing
from function.errors import ForbiddenError, NotFoundError, TooManyRequestsError
from function.files import clear_thumbnail, load_file, save_file, save_thumbnail
from function.rate_limit import (
    SHARE_UNLOCK_FAILURES_PER_LINK,
    SHARE_UNLOCK_PER_IP,
    RateLimiter,
    client_ip,
    get_rate_limiter,
)
from function.scene import load_scene, save_scene
from function.share import (
    GRANT_TTL,
    UNCHANGED,
    SharedAccess,
    find_live_link,
    get_link,
    grant_cookie,
    is_unlocked,
    member_drawing_id,
    reset_link,
    resolve_share,
    save_link,
    unlock,
)
from middleware.auth import CurrentUser, OptionalUser
from routes.auth import cookie_options
from routes.files import FileId, image_response
from utils.config import AppSettings
from utils.db import Database
from utils.storage import ObjectStorage

router = APIRouter(tags=["share"])

Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
ShareToken = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{16,64}$")]

# Anyone who can change a drawing can share it. Viewers never see the link:
# an edit link would let them edit.
SHARE_ROLE = "editor"


async def _shared_view(token: ShareToken, request: Request, db: Database):
    return await resolve_share(db, token, request.cookies, "view")


async def _shared_edit(token: ShareToken, request: Request, db: Database):
    return await resolve_share(db, token, request.cookies, "edit")


SharedView = Annotated[SharedAccess, Depends(_shared_view)]
SharedEdit = Annotated[SharedAccess, Depends(_shared_edit)]


# Managing a drawing's link, for its workspace's editors and up.


@router.get("/drawings/{drawing_id}/share")
async def drawing_share_link(
    drawing_id: str, db: Database, user: CurrentUser
) -> ShareLinkResponse | None:
    drawing, _ = await get_drawing(db, drawing_id, user, SHARE_ROLE)
    link = await get_link(db, drawing)
    return ShareLinkResponse.from_document(link) if link else None


@router.put("/drawings/{drawing_id}/share")
async def update_share_link(
    drawing_id: str, body: ShareSettingsRequest, db: Database, user: CurrentUser
) -> ShareLinkResponse:
    drawing, _ = await get_drawing(db, drawing_id, user, SHARE_ROLE)
    password = body.password if "password" in body.model_fields_set else UNCHANGED
    link = await save_link(
        db, drawing, user, body.enabled, body.access, body.expires_at, password
    )
    return ShareLinkResponse.from_document(link)


@router.post("/drawings/{drawing_id}/share/reset")
async def reset_share_link(
    drawing_id: str, db: Database, user: CurrentUser
) -> ShareLinkResponse:
    drawing, _ = await get_drawing(db, drawing_id, user, SHARE_ROLE)
    link = await get_link(db, drawing)
    if link is None:
        raise NotFoundError
    return ShareLinkResponse.from_document(await reset_link(db, link))


# The drawing as anyone with the link reaches it. These mirror the drawing
# routes the editor uses, so it only swaps /drawings/{id} for /share/{token}.


@router.get("/share/{token}")
async def share_info(
    token: ShareToken, request: Request, db: Database, user: OptionalUser
) -> ShareInfoResponse:
    link, drawing = await find_live_link(db, token)
    unlocked = await is_unlocked(db, link, request.cookies)
    return ShareInfoResponse(
        access=link["access"],
        password_required=link["password_hash"] is not None,
        unlocked=unlocked,
        title=drawing["title"] if unlocked else None,
        has_thumbnail=drawing.get("thumbnail_version") is not None,
        member_drawing_id=await member_drawing_id(db, drawing, user),
        viewer_name=user["name"] if user else None,
    )


@router.post("/share/{token}/unlock", status_code=status.HTTP_204_NO_CONTENT)
async def unlock_share(
    token: ShareToken,
    body: UnlockShareRequest,
    request: Request,
    response: Response,
    db: Database,
    limiter: Limiter,
    settings: AppSettings,
) -> None:
    limiter.check(SHARE_UNLOCK_PER_IP, client_ip(request))
    link, _ = await find_live_link(db, token)
    if link["password_hash"] is None:
        return
    # Only wrong passwords count against the link, so a popular link never
    # locks out the people who know its password.
    key = str(link["_id"])
    if retry_after := limiter.retry_after(SHARE_UNLOCK_FAILURES_PER_LINK, key):
        raise TooManyRequestsError(retry_after)
    try:
        grant = await unlock(db, link, body.password)
    except ForbiddenError:
        limiter.count(SHARE_UNLOCK_FAILURES_PER_LINK, key)
        raise
    response.set_cookie(
        grant_cookie(link),
        grant,
        max_age=int(GRANT_TTL.total_seconds()),
        **cookie_options(settings),
    )


@router.get("/share/{token}/scene")
async def shared_scene(shared: SharedView, db: Database) -> SceneResponse:
    return SceneResponse.from_document(await load_scene(db, shared.drawing))


@router.put("/share/{token}/scene")
async def update_shared_scene(
    body: SaveSceneRequest,
    shared: SharedEdit,
    db: Database,
    settings: AppSettings,
    user: OptionalUser,
) -> SaveSceneResponse:
    elements = [element.model_dump() for element in body.elements]
    # Guests aren't credited; logged-in visitors are.
    editor_id = user["_id"] if user else None
    rev = await save_scene(
        db, settings, shared.drawing, editor_id, elements, body.app_state
    )
    return SaveSceneResponse(rev=rev)


@router.get("/share/{token}/files/{file_id}")
async def shared_file(
    file_id: FileId, shared: SharedView, db: Database, storage: ObjectStorage
) -> Response:
    return image_response(*await load_file(db, storage, shared.drawing, file_id))


@router.put("/share/{token}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_shared_file(
    file_id: FileId,
    body: UploadFileRequest,
    shared: SharedEdit,
    db: Database,
    storage: ObjectStorage,
    settings: AppSettings,
    user: OptionalUser,
) -> None:
    await save_file(
        db,
        storage,
        shared.drawing,
        user["_id"] if user else None,
        file_id,
        body.data_url,
        settings.max_file_bytes,
    )


@router.put("/share/{token}/thumbnail", status_code=status.HTTP_204_NO_CONTENT)
async def upload_shared_thumbnail(
    body: SaveThumbnailRequest,
    shared: SharedEdit,
    db: Database,
    storage: ObjectStorage,
) -> None:
    await save_thumbnail(
        db, storage, shared.drawing, {"light": body.light, "dark": body.dark}
    )


@router.delete("/share/{token}/thumbnail", status_code=status.HTTP_204_NO_CONTENT)
async def remove_shared_thumbnail(shared: SharedEdit, db: Database) -> None:
    await clear_thumbnail(db, shared.drawing)
