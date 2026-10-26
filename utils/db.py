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


def get_db(request: Request) -> AsyncDatabase:
    return request.app.state.db


Database = Annotated[AsyncDatabase, Depends(get_db)]
