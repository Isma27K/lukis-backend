from datetime import datetime
from typing import Literal, NotRequired, TypedDict

from bson import ObjectId

# The one document in the `site` collection.
SITE_ID = "site"

# "setup" until the first account (the site owner) exists.
RegistrationMode = Literal["setup", "open", "invite_only"]


class SiteDocument(TypedDict):
    """Settings for the whole site, run by its owner: the first account."""

    _id: str
    # None on a fresh install, until the first account claims it.
    owner_id: ObjectId | None
    # Whether anyone may sign up. When False, only an invite from the site
    # owner lets someone create an account.
    registration_open: bool
    # SHA-256 of the one-time code the first account needs, printed in the
    # logs at startup. Only there while owner_id is None.
    setup_code_hash: NotRequired[str]
    updated_at: datetime
    # None for changes made at startup or from the command line.
    updated_by: ObjectId | None


class SignupInviteDocument(TypedDict):
    """Lets one email address sign up while registration is closed."""

    _id: ObjectId
    # Only this (lowercased) address can sign up with it.
    email: str
    # SHA-256 of the token in the invite link; the token itself isn't stored.
    token_hash: str
    invited_by: ObjectId
    created_at: datetime
    expires_at: datetime
