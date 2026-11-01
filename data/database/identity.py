from datetime import datetime
from typing import Literal, TypedDict

from bson import ObjectId

from utils.oauth import ProviderName


class IdentityDocument(TypedDict):
    """A Google, GitHub or Microsoft account someone logs in to Lukis with.
    One per provider per user."""

    _id: ObjectId
    user_id: ObjectId
    provider: ProviderName
    # The provider's ID for the account; logins are matched on this.
    subject: str
    # The provider account's address when it was connected, so the account
    # page can tell people which one it is. May differ from the Lukis email.
    email: str | None
    created_at: datetime


# "login" logs in or signs up; "link" connects the provider account to the
# logged-in user from the account page.
OAuthIntent = Literal["login", "link"]
# The page that started it, where problems are reported.
OAuthPage = Literal["login", "register", "account"]


class OAuthStateDocument(TypedDict):
    """A login with a provider that's under way: made when the browser is sent
    to the provider, used up when it comes back."""

    _id: ObjectId
    # SHA-256 of the `state` sent to the provider, which is also in a cookie
    # on the browser that started it.
    state_hash: str
    provider: ProviderName
    # PKCE: the provider only hands over the account for this.
    code_verifier: str
    # Must come back in the ID token (Google, Microsoft).
    nonce: str
    intent: OAuthIntent
    page: OAuthPage
    # Who is connecting an account, for "link".
    user_id: ObjectId | None
    # Where to go after logging in, e.g. an invite.
    next: str | None
    # Lets someone sign up while sign-up is invite-only.
    invite_token: str | None
    created_at: datetime
    expires_at: datetime
