from fastapi import APIRouter, HTTPException
from pymongo.errors import PyMongoError

from utils.db import Database
from utils.storage import ObjectStorage, StorageError

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(db: Database, storage: ObjectStorage) -> dict[str, str]:
    try:
        await db.command("ping")
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail="Database unavailable") from error
    try:
        await storage.ping()
    except StorageError as error:
        raise HTTPException(
            status_code=503, detail="File storage unavailable"
        ) from error
    return {"status": "ok"}
