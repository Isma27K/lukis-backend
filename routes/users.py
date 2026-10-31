from typing import Annotated

from fastapi import APIRouter, Path, Response

from function.errors import NotFoundError
from routes.files import image_response
from utils.storage import ObjectStorage, avatar_key

router = APIRouter(prefix="/users", tags=["users"])

UserId = Annotated[str, Path(pattern=r"^[0-9a-f]{24}$")]


# Public, so guests on a public link see collaborators' pictures too. Ids only
# reach people who share a workspace or drawing with the user. Reads storage
# alone: removing a picture or the account deletes the object. The frontend
# adds ?v=<avatar_version> so each picture caches separately.
@router.get("/{user_id}/avatar")
async def avatar(user_id: UserId, storage: ObjectStorage) -> Response:
    stored = await storage.get(avatar_key(user_id))
    if stored is None:
        raise NotFoundError
    return image_response(*stored, public=True)
