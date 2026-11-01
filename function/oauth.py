"""Logging in with Google, GitHub or Microsoft, and connecting those accounts
to a Lukis one.

Accounts are matched on the provider's ID for the person, never on the email
address alone. An address doesn't prove someone owns the Lukis account that
has it: Microsoft work accounts can claim any address, and someone could sign
up with another person's address before they do. So people with a password
account log in with it, then connect the provider on their account page.
"""

import base64
import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from data.database.identity import (
    IdentityDocument,
    OAuthIntent,
    OAuthPage,
    OAuthStateDocument,
)
from data.database.user import UserDocument
from function.auth import EmailTakenError, has_password, hash_token, normalize_email
from function.errors import ConflictError, NotFoundError
from function.site import (
    InviteMismatchError,
    InviteOnlyError,
    get_site,
    register_account,
    registration_mode,
)
from utils.oauth import PROVIDER_LABELS, Profile, Provider, ProviderName

logger = logging.getLogger("lukis.oauth")

# Time to pick an account at the provider and come back.
STATE_TTL = timedelta(minutes=10)

LAST_LOGIN_METHOD = (
    "This is the only way you can log in. Set a password or connect another "
    "account first."
)

# Why a login with a provider didn't work. The pages turn these into
# messages; codes rather than text, so a crafted link can't put words there.
OAuthErrorCode = Literal[
    "unavailable",  # the provider isn't set up on this server
    "denied",  # they cancelled at the provider
    "expired",  # took too long, or came back in another browser
    "failed",  # the provider refused the code or couldn't be reached
    "rate_limited",
    "no_email",  # the provider account has no address to sign up with
    "email_taken",  # a Lukis account has the address; connect it from there
    "setup",  # the first account needs the setup code, so a password
    "invite_only",
    "invite_mismatch",  # the invite is for another address
    "linked_elsewhere",  # connected to another Lukis account already
    "already_linked",  # this user has another account of that provider
]


class OAuthFailure(Exception):
    def __init__(self, code: OAuthErrorCode):
        super().__init__(code)
        self.code = code


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def begin(
    db: AsyncDatabase,
    provider: Provider,
    redirect_uri: str,
    intent: OAuthIntent,
    page: OAuthPage,
    user_id: ObjectId | None = None,
    next_path: str | None = None,
    invite_token: str | None = None,
) -> tuple[str, str]:
    """Returns the state, which the caller puts in a cookie, and the URL to
    send the browser to."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(16)
    now = datetime.now(UTC)
    document: OAuthStateDocument = {
        "_id": ObjectId(),
        "state_hash": hash_token(state),
        "provider": provider.name,
        "code_verifier": verifier,
        "nonce": nonce,
        "intent": intent,
        "page": page,
        "user_id": user_id,
        "next": next_path,
        "invite_token": invite_token,
        "created_at": now,
        "expires_at": now + STATE_TTL,
    }
    await db.oauth_states.insert_one(document)
    url = provider.authorize_url(redirect_uri, state, _code_challenge(verifier), nonce)
    return state, url


async def take_state(
    db: AsyncDatabase, provider: ProviderName, state: str
) -> OAuthStateDocument | None:
    """Uses up a login under way; each works once."""
    # The TTL index only sweeps about once a minute, so check expiry here too.
    return await db.oauth_states.find_one_and_delete(
        {
            "state_hash": hash_token(state),
            "provider": provider,
            "expires_at": {"$gt": datetime.now(UTC)},
        }
    )


def _identity(
    user_id: ObjectId, provider: ProviderName, profile: Profile
) -> IdentityDocument:
    return {
        "_id": ObjectId(),
        "user_id": user_id,
        "provider": provider,
        "subject": profile.subject,
        "email": normalize_email(profile.email) if profile.email else None,
        "created_at": datetime.now(UTC),
    }


async def sign_in(
    db: AsyncDatabase,
    provider: ProviderName,
    profile: Profile,
    invite_token: str | None = None,
) -> tuple[UserDocument, bool]:
    """The account this provider account logs in to, signing up if there's
    none and the site lets them. Returns it and whether it's new."""
    identity = await db.identities.find_one(
        {"provider": provider, "subject": profile.subject}
    )
    if identity is not None:
        user = await db.users.find_one({"_id": identity["user_id"]})
        if user is None:
            # Only while a sign-up with it is still being written.
            raise OAuthFailure("failed")
        return user, False

    if profile.email is None:
        raise OAuthFailure("no_email")
    # The site owner's account needs the setup code, so it's made with a
    # password.
    if registration_mode(await get_site(db)) == "setup":
        raise OAuthFailure("setup")

    # Claimed before the account is made, so the same login arriving twice
    # at once can't make two accounts.
    user_id = ObjectId()
    identity = _identity(user_id, provider, profile)
    try:
        await db.identities.insert_one(identity)
    except DuplicateKeyError:
        raise OAuthFailure("failed")
    try:
        user, _ = await register_account(
            db,
            profile.email,
            profile.name,
            password=None,
            invite_token=invite_token,
            email_verified=profile.email_verified,
            user_id=user_id,
        )
    except BaseException as error:
        await db.identities.delete_one({"_id": identity["_id"]})
        if isinstance(error, EmailTakenError):
            raise OAuthFailure("email_taken") from error
        if isinstance(error, InviteMismatchError):
            raise OAuthFailure("invite_mismatch") from error
        if isinstance(error, InviteOnlyError):
            raise OAuthFailure("invite_only") from error
        raise

    logger.info(
        f"{user['email']} signed up with {PROVIDER_LABELS[provider]}",
        extra={"fields": {"user_id": str(user_id)}},
    )
    return user, True


async def link(
    db: AsyncDatabase, user: UserDocument, provider: ProviderName, profile: Profile
) -> bool:
    """Connects a provider account to `user`. Returns False if it already
    was."""
    existing = await db.identities.find_one(
        {"provider": provider, "subject": profile.subject}
    )
    if existing is not None:
        if existing["user_id"] == user["_id"]:
            return False
        raise OAuthFailure("linked_elsewhere")
    if await db.identities.find_one({"user_id": user["_id"], "provider": provider}):
        raise OAuthFailure("already_linked")
    try:
        await db.identities.insert_one(_identity(user["_id"], provider, profile))
    except DuplicateKeyError:
        # Someone connected it at the same moment.
        raise OAuthFailure("linked_elsewhere")
    logger.info(
        f"{user['email']} connected {PROVIDER_LABELS[provider]}",
        extra={"fields": {"user_id": str(user["_id"])}},
    )
    return True


async def list_identities(
    db: AsyncDatabase, user: UserDocument
) -> list[IdentityDocument]:
    return (
        await db.identities.find({"user_id": user["_id"]})
        .sort("created_at", 1)
        .to_list()
    )


async def unlink(db: AsyncDatabase, user: UserDocument, provider: ProviderName) -> None:
    identities = await list_identities(db, user)
    identity = next((i for i in identities if i["provider"] == provider), None)
    if identity is None:
        raise NotFoundError
    # Nobody gets locked out of their own account.
    if not has_password(user) and len(identities) < 2:
        raise ConflictError(LAST_LOGIN_METHOD)
    await db.identities.delete_one({"_id": identity["_id"]})
