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
    await db.files.create_index([("drawing_id", 1), ("file_id", 1)], unique=True)
    await db.versions.create_index([("drawing_id", 1), ("created_at", -1)])
    # Named versions have no expires_at, so only automatic snapshots go.
    await db.versions.create_index("expires_at", expireAfterSeconds=0)
    await db.share_links.create_index("drawing_id", unique=True)
    await db.share_links.create_index("token", unique=True)
    await db.share_grants.create_index("token_hash", unique=True)
    await db.share_grants.create_index("expires_at", expireAfterSeconds=0)
    await db.email_tokens.create_index("token_hash", unique=True)
    await db.email_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.email_tokens.create_index([("user_id", 1), ("purpose", 1)])
    await db.invites.create_index("token_hash", unique=True)
    await db.invites.create_index("expires_at", expireAfterSeconds=0)
    await db.invites.create_index([("workspace_id", 1), ("email", 1)])
    await db.signup_invites.create_index("token_hash", unique=True)
    await db.signup_invites.create_index("expires_at", expireAfterSeconds=0)
    await db.signup_invites.create_index("email")
    # One Lukis account per provider account, one per provider per user.
    await db.identities.create_index([("provider", 1), ("subject", 1)], unique=True)
    await db.identities.create_index([("user_id", 1), ("provider", 1)], unique=True)
    await db.oauth_states.create_index("state_hash", unique=True)
    await db.oauth_states.create_index("expires_at", expireAfterSeconds=0)


def get_db(request: Request) -> AsyncDatabase:
    return request.app.state.db


Database = Annotated[AsyncDatabase, Depends(get_db)]
