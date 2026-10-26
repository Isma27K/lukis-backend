from typing import Annotated

from fastapi import Depends, Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from utils.config import Settings


def create_client(settings: Settings) -> AsyncMongoClient:
    # Fail fast instead of hanging for pymongo's default 30s when Mongo is down.
    return AsyncMongoClient(
        settings.mongo_url, tz_aware=True, serverSelectionTimeoutMS=5000
    )


async def ensure_indexes(db: AsyncDatabase) -> None:
    await db.users.create_index("email", unique=True)
    await db.sessions.create_index("token_hash", unique=True)
    # Mongo deletes sessions by itself once expires_at has passed.
    await db.sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.memberships.create_index(
        [("workspace_id", 1), ("user_id", 1)], unique=True
    )
    await db.memberships.create_index("user_id")
    await db.drawings.create_index([("workspace_id", 1), ("updated_at", -1)])


def get_db(request: Request) -> AsyncDatabase:
    return request.app.state.db


Database = Annotated[AsyncDatabase, Depends(get_db)]
