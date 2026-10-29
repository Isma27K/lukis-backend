"""The site owner and who may sign up.

The first account becomes the site owner. On a fresh install it needs a
one-time setup code from the server's logs, so a stranger who finds the site
first can't claim it. The owner decides whether anyone can sign up; when
nobody can, an invite from the owner is the only way to create an account.
"""

import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from data.database.site import (
    SITE_ID,
    RegistrationMode,
    SignupInviteDocument,
    SiteDocument,
)
from data.database.user import UserDocument
from function.access import parse_id
from function.auth import EmailTakenError, hash_token, normalize_email, register_user
from function.errors import ConflictError, ForbiddenError, NotFoundError
from function.invites import INVITE_TTL, find_invite
from utils.config import Settings

logger = logging.getLogger("lukis.site")

INVITE_ONLY = "Sign-up is invite-only. Ask the site owner for an invite link."
WRONG_SETUP_CODE = "Enter the setup code from the server logs"

# No 0/O or 1/I/L, so a code read off a screen types back right. 12 of these
# make about 59 bits, far out of reach at the sign-up rate limit.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_GROUPS = 3
_CODE_GROUP_LENGTH = 4
_MIN_CONFIGURED_CODE = 8

# Used if the site document has gone missing (say, an old backup restored
# with --drop): nobody can sign up or act as owner until a restart, when
# ensure_site sets it up again.
_MISSING_SITE: SiteDocument = {
    "_id": SITE_ID,
    "owner_id": None,
    "registration_open": False,
    "updated_at": datetime.min.replace(tzinfo=UTC),
    "updated_by": None,
}


def _normalize_code(code: str) -> str:
    # Dashes, spaces and case don't matter.
    return "".join(c for c in code.upper() if c.isalnum())


def _new_setup_code() -> str:
    groups = (
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUP_LENGTH))
        for _ in range(_CODE_GROUPS)
    )
    return "-".join(groups)


async def get_site(db: AsyncDatabase) -> SiteDocument:
    return await db.site.find_one({"_id": SITE_ID}) or _MISSING_SITE


async def ensure_site(db: AsyncDatabase, settings: Settings) -> None:
    """Run at startup. Makes sure the site has an owner, or a setup code for
    whoever registers first."""
    configured = _normalize_code(settings.setup_code)
    if settings.setup_code and len(configured) < _MIN_CONFIGURED_CODE:
        raise ValueError(
            f"SETUP_CODE needs at least {_MIN_CONFIGURED_CODE} letters or digits"
        )

    now = datetime.now(UTC)
    await db.site.update_one(
        {"_id": SITE_ID},
        {
            "$setOnInsert": {
                "owner_id": None,
                "registration_open": True,
                "updated_at": now,
                "updated_by": None,
            }
        },
        upsert=True,
    )
    if (await get_site(db))["owner_id"] is not None:
        return

    # Accounts from before there was a site owner: the oldest one becomes it.
    first = await db.users.find_one({}, sort=[("created_at", 1), ("_id", 1)])
    if first is not None:
        result = await db.site.update_one(
            {"_id": SITE_ID, "owner_id": None},
            {
                "$set": {"owner_id": first["_id"], "updated_at": now},
                "$unset": {"setup_code_hash": ""},
            },
        )
        if result.modified_count:
            logger.info(
                f"{first['email']} is now the site owner, as the first account",
                extra={"fields": {"user_id": str(first["_id"])}},
            )
        return

    code = settings.setup_code or _new_setup_code()
    await db.site.update_one(
        {"_id": SITE_ID, "owner_id": None},
        {"$set": {"setup_code_hash": hash_token(_normalize_code(code))}},
    )
    if settings.setup_code:
        logger.info(
            "No site owner yet. The first account to register needs SETUP_CODE."
        )
    else:
        # Only ever printed: the database keeps just its hash.
        logger.warning(
            f"No site owner yet. Register the first account with setup code {code}"
        )


def registration_mode(site: SiteDocument) -> RegistrationMode:
    if site["owner_id"] is None:
        return "setup"
    return "open" if site["registration_open"] else "invite_only"


async def is_site_owner(db: AsyncDatabase, user: UserDocument) -> bool:
    return (await get_site(db))["owner_id"] == user["_id"]


async def find_signup_invite(
    db: AsyncDatabase, token: str, site: SiteDocument | None = None
) -> str | None:
    """The email an invite link lets sign up, or None if it doesn't.

    That's a sign-up invite from the owner's settings page, or a workspace
    invite the site owner sent. Other admins' workspace invites only work for
    people who already have an account, so members can't open up sign-up.
    """
    # The TTL index only sweeps about once a minute, so check expiry here too.
    invite = await db.signup_invites.find_one(
        {"token_hash": hash_token(token), "expires_at": {"$gt": datetime.now(UTC)}}
    )
    if invite is not None:
        return invite["email"]

    site = site or await get_site(db)
    if site["owner_id"] is None:
        return None
    try:
        workspace_invite, _ = await find_invite(db, token)
    except NotFoundError:
        return None
    if workspace_invite["invited_by"] != site["owner_id"]:
        return None
    return workspace_invite["email"]


@dataclass(frozen=True)
class _Admission:
    # Set when the account is to become the site owner: the hash of the setup
    # code it brought.
    setup_code_hash: str | None = None


async def _admit(
    db: AsyncDatabase, email: str, invite_token: str | None, setup_code: str | None
) -> _Admission:
    """Decides whether this sign-up may go ahead. Runs before anything looks
    at existing accounts, so while sign-up is closed nobody can use it to find
    out which addresses have one."""
    site = await get_site(db)
    mode = registration_mode(site)

    if mode == "setup":
        code = _normalize_code(setup_code or "")
        code_hash = hash_token(code)
        stored = site.get("setup_code_hash")
        if not code or not stored or not hmac.compare_digest(code_hash, stored):
            raise ForbiddenError(WRONG_SETUP_CODE)
        return _Admission(setup_code_hash=code_hash)

    if mode == "open":
        return _Admission()

    invited = await find_signup_invite(db, invite_token, site) if invite_token else None
    if invited is None:
        raise ForbiddenError(INVITE_ONLY)
    if invited != normalize_email(email):
        raise ForbiddenError(f"This invite is for {invited}")
    return _Admission()


async def register_account(
    db: AsyncDatabase,
    email: str,
    name: str,
    password: str,
    invite_token: str | None = None,
    setup_code: str | None = None,
) -> tuple[UserDocument, bool]:
    """Signs someone up if the site lets them. Returns the account and whether
    it became the site owner. Raises EmailTakenError like register_user."""
    admission = await _admit(db, email, invite_token, setup_code)
    user_id = ObjectId()

    if admission.setup_code_hash:
        # One conditional write, so two people signing up at the same moment
        # can't both become owner, and the code works only once.
        claimed = await db.site.update_one(
            {
                "_id": SITE_ID,
                "owner_id": None,
                "setup_code_hash": admission.setup_code_hash,
            },
            {
                "$set": {
                    "owner_id": user_id,
                    "updated_at": datetime.now(UTC),
                    "updated_by": user_id,
                },
                "$unset": {"setup_code_hash": ""},
            },
        )
        if not claimed.modified_count:
            raise ForbiddenError(WRONG_SETUP_CODE)

    try:
        user = await register_user(db, email, name, password, user_id=user_id)
    except EmailTakenError:
        if admission.setup_code_hash:
            # Give the claim back, so the code still works.
            await db.site.update_one(
                {"_id": SITE_ID, "owner_id": user_id},
                {
                    "$set": {
                        "owner_id": None,
                        "setup_code_hash": admission.setup_code_hash,
                    }
                },
            )
        raise

    if admission.setup_code_hash:
        logger.info(
            f"{user['email']} registered as the site owner",
            extra={"fields": {"user_id": str(user_id)}},
        )
    # Any invite for this address has done its job.
    await db.signup_invites.delete_many({"email": user["email"]})
    return user, admission.setup_code_hash is not None


async def set_registration_open(
    db: AsyncDatabase, owner: UserDocument, registration_open: bool
) -> SiteDocument:
    # Only while they're still the owner, in case that changed meanwhile.
    site = await db.site.find_one_and_update(
        {"_id": SITE_ID, "owner_id": owner["_id"]},
        {
            "$set": {
                "registration_open": registration_open,
                "updated_at": datetime.now(UTC),
                "updated_by": owner["_id"],
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if site is None:
        raise ForbiddenError
    logger.info(
        f"Sign-up {'opened' if registration_open else 'closed'} by {owner['email']}",
        extra={"fields": {"user_id": str(owner["_id"])}},
    )
    return site


async def create_signup_invite(
    db: AsyncDatabase, owner: UserDocument, email: str
) -> tuple[SignupInviteDocument, str]:
    """Returns the invite and the token for its link."""
    email = normalize_email(email)
    if await db.users.find_one({"email": email}, {"_id": 1}):
        raise ConflictError(f"{email} already has an account")

    # A new invite replaces any earlier one for the same person.
    await db.signup_invites.delete_many({"email": email})
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    invite: SignupInviteDocument = {
        "_id": ObjectId(),
        "email": email,
        "token_hash": hash_token(token),
        "invited_by": owner["_id"],
        "created_at": now,
        "expires_at": now + INVITE_TTL,
    }
    await db.signup_invites.insert_one(invite)
    return invite, token


async def list_signup_invites(db: AsyncDatabase) -> list[SignupInviteDocument]:
    return (
        await db.signup_invites.find({"expires_at": {"$gt": datetime.now(UTC)}})
        .sort("created_at", -1)
        .to_list()
    )


async def revoke_signup_invite(db: AsyncDatabase, invite_id: str) -> None:
    result = await db.signup_invites.delete_one({"_id": parse_id(invite_id)})
    if not result.deleted_count:
        raise NotFoundError
