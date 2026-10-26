from fastapi import APIRouter, HTTPException
from pymongo.errors import PyMongoError

from utils.db import Database

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(db: Database) -> dict[str, str]:
    try:
        await db.command("ping")
    except PyMongoError as error:
        raise HTTPException(status_code=503, detail="Database unavailable") from error
    return {"status": "ok"}
