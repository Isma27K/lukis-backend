import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from bson import ObjectId
from pwdlib import PasswordHash
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from data.database.session import SessionDocument
from data.database.user import EmailTokenDocument, EmailTokenPurpose, UserDocument
from function.errors import InvalidLinkError
from function.workspaces import create_workspace

password_hash = PasswordHash.recommended()

# How long links sent by email work.
EMAIL_TOKEN_TTL: dict[EmailTokenPurpose, timedelta] = {
    "verify_email": timedelta(days=2),
    "reset_password": timedelta(hours=1),
}

# How often a session's last_used_at is updated: often enough for "active 5
# minutes ago", without a write on every request.
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
MAX_USER_AGENT = 512

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
async def hash_password(password: str) -> str:
    return await asyncio.to_thread(password_hash.hash, password)


async def verify_password(password: str, hashed: str) -> bool:
    return await asyncio.to_thread(password_hash.verify, password, hashed)


def has_password(user: UserDocument) -> bool:
    """False for accounts made by logging in with Google, GitHub or Microsoft
    that haven't set one."""
    return bool(user.get("password_hash"))


async def register_user(
    db: AsyncDatabase,
    email: str,
    name: str,
    password: str | None,
    user_id: ObjectId | None = None,
    email_verified: bool = False,
) -> UserDocument:
    now = datetime.now(UTC)
    user: UserDocument = {
        "_id": user_id or ObjectId(),
        "email": normalize_email(email),
        "name": name,
        "password_hash": await hash_password(password) if password else None,
        "created_at": now,
        "email_verified_at": now if email_verified else None,
    }
    try:
        await db.users.insert_one(user)
    except DuplicateKeyError as error:
        raise EmailTakenError from error

    await create_workspace(db, user, "Personal", personal=True)
    return user


async def authenticate(
    db: AsyncDatabase, email: str, password: str
) -> UserDocument | None:
    user = await db.users.find_one({"email": normalize_email(email)})
    # Accounts without a password take as long to refuse as unknown ones.
    if user is None or not has_password(user):
        await verify_password(password, _DUMMY_HASH)
        return None
    if not await verify_password(password, user["password_hash"]):
        return None
    return user


async def create_session(
    db: AsyncDatabase,
    user_id: ObjectId,
    lifetime: timedelta,
    user_agent: str | None = None,
    ip: str | None = None,
) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session: SessionDocument = {
        "_id": ObjectId(),
        "token_hash": hash_token(token),
        "user_id": user_id,
        "created_at": now,
        "expires_at": now + lifetime,
        "user_agent": user_agent[:MAX_USER_AGENT] if user_agent else None,
        "ip": ip,
        "last_used_at": now,
    }
    await db.sessions.insert_one(session)
    return token


async def get_session_user(db: AsyncDatabase, token: str) -> UserDocument | None:
    now = datetime.now(UTC)
    # The TTL index only sweeps about once a minute, so check expiry here too.
    session = await db.sessions.find_one(
        {"token_hash": hash_token(token), "expires_at": {"$gt": now}}
    )
    if session is None:
        return None
    last_used = session.get("last_used_at")
    if last_used is None or now - last_used > SESSION_TOUCH_INTERVAL:
        await db.sessions.update_one(
            {"_id": session["_id"]}, {"$set": {"last_used_at": now}}
        )
    return await db.users.find_one({"_id": session["user_id"]})


async def delete_session(db: AsyncDatabase, token: str) -> None:
    await db.sessions.delete_one({"token_hash": hash_token(token)})


async def create_email_token(
    db: AsyncDatabase, user: UserDocument, purpose: EmailTokenPurpose
) -> str:
    """Returns the token for a link to email to the user. It replaces any
    earlier link for the same purpose."""
    await db.email_tokens.delete_many({"user_id": user["_id"], "purpose": purpose})
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    document: EmailTokenDocument = {
        "_id": ObjectId(),
        "token_hash": hash_token(token),
        "purpose": purpose,
        "user_id": user["_id"],
        "email": user["email"],
        "created_at": now,
        "expires_at": now + EMAIL_TOKEN_TTL[purpose],
    }
    await db.email_tokens.insert_one(document)
    return token


async def _use_email_token(
    db: AsyncDatabase, token: str, purpose: EmailTokenPurpose
) -> UserDocument:
    """Spends a token (they work once) and returns its user."""
    # The TTL index only sweeps about once a minute, so check expiry here too.
    document = await db.email_tokens.find_one_and_delete(
        {
            "token_hash": hash_token(token),
            "purpose": purpose,
            "expires_at": {"$gt": datetime.now(UTC)},
        }
    )
    if document is None:
        raise InvalidLinkError
    user = await db.users.find_one({"_id": document["user_id"]})
    if user is None or user["email"] != document["email"]:
        raise InvalidLinkError
    return user


async def verify_email(db: AsyncDatabase, token: str) -> UserDocument:
    user = await _use_email_token(db, token, "verify_email")
    if user.get("email_verified_at") is None:
        verified_at = datetime.now(UTC)
        await db.users.update_one(
            {"_id": user["_id"]}, {"$set": {"email_verified_at": verified_at}}
        )
        user = {**user, "email_verified_at": verified_at}
    return user


async def find_user_by_email(db: AsyncDatabase, email: str) -> UserDocument | None:
    return await db.users.find_one({"email": normalize_email(email)})


async def reset_password(db: AsyncDatabase, token: str, password: str) -> None:
    user = await _use_email_token(db, token, "reset_password")
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": await hash_password(password),
                # Getting the link proves the address is theirs.
                "email_verified_at": user.get("email_verified_at")
                or datetime.now(UTC),
            }
        },
    )
    # Whoever knew the old password is logged out everywhere.
    await db.sessions.delete_many({"user_id": user["_id"]})
