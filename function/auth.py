import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from bson import ObjectId
from pwdlib import PasswordHash
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from data.database.session import SessionDocument
from data.database.user import UserDocument
from data.database.workspace import MembershipDocument, WorkspaceDocument

password_hash = PasswordHash.recommended()

# Checked against when the email is unknown, so a failed login takes as long
# whether or not the account exists.
_DUMMY_HASH = password_hash.hash(secrets.token_urlsafe())


class EmailTakenError(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Argon2 takes ~80ms of CPU, so it runs off the event loop.
async def _hash_password(password: str) -> str:
    return await asyncio.to_thread(password_hash.hash, password)


async def _verify_password(password: str, hashed: str) -> bool:
    return await asyncio.to_thread(password_hash.verify, password, hashed)


async def register_user(
    db: AsyncDatabase, email: str, name: str, password: str
) -> UserDocument:
    now = datetime.now(UTC)
    user: UserDocument = {
        "_id": ObjectId(),
        "email": normalize_email(email),
        "name": name,
        "password_hash": await _hash_password(password),
        "created_at": now,
    }
    try:
        await db.users.insert_one(user)
    except DuplicateKeyError as error:
        raise EmailTakenError from error

    workspace: WorkspaceDocument = {
        "_id": ObjectId(),
        "name": "Personal",
        "owner_id": user["_id"],
        "personal": True,
        "created_at": now,
    }
    membership: MembershipDocument = {
        "_id": ObjectId(),
        "workspace_id": workspace["_id"],
        "user_id": user["_id"],
        "role": "owner",
        "created_at": now,
    }
    await db.workspaces.insert_one(workspace)
    await db.memberships.insert_one(membership)
    return user


async def authenticate(
    db: AsyncDatabase, email: str, password: str
) -> UserDocument | None:
    user = await db.users.find_one({"email": normalize_email(email)})
    if user is None:
        await _verify_password(password, _DUMMY_HASH)
        return None
    if not await _verify_password(password, user["password_hash"]):
        return None
    return user


async def create_session(
    db: AsyncDatabase, user_id: ObjectId, lifetime: timedelta
) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session: SessionDocument = {
        "_id": ObjectId(),
        "token_hash": hash_token(token),
        "user_id": user_id,
        "created_at": now,
        "expires_at": now + lifetime,
    }
    await db.sessions.insert_one(session)
    return token


async def get_session_user(db: AsyncDatabase, token: str) -> UserDocument | None:
    # The TTL index only sweeps about once a minute, so check expiry here too.
    session = await db.sessions.find_one(
        {"token_hash": hash_token(token), "expires_at": {"$gt": datetime.now(UTC)}}
    )
    if session is None:
        return None
    return await db.users.find_one({"_id": session["user_id"]})


async def delete_session(db: AsyncDatabase, token: str) -> None:
    await db.sessions.delete_one({"token_hash": hash_token(token)})
