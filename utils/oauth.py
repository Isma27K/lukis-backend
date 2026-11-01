"""Logging in with Google, GitHub and Microsoft: OAuth 2.0 with PKCE.

Each provider builds the URL that sends the browser to it, and turns the code
it sends back into a Profile. That code goes back to the provider from this
server over TLS, so the ID token in its answer is trusted without checking
its signature (OpenID Connect Core 3.1.3.7); its audience, nonce and expiry
are still checked.
"""

import base64
import json
import time
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import quote, urlencode

import httpx
from fastapi import Depends, Request

from utils.config import Settings

ProviderName = Literal["google", "github", "microsoft"]

PROVIDER_LABELS: dict[ProviderName, str] = {
    "google": "Google",
    "github": "GitHub",
    "microsoft": "Microsoft",
}

TIMEOUT_SECONDS = 10
# Matches the Name rule for sign-ups.
MAX_NAME = 64


class OAuthError(Exception):
    """The provider refused the code, couldn't be reached, or answered
    something unexpected."""


@dataclass(frozen=True)
class Profile:
    """Who the provider says logged in."""

    # The provider's own ID for the account. Unlike the email, it never
    # changes, so accounts are matched on it.
    subject: str
    # None when the account has no address the provider will share.
    email: str | None
    # Whether the provider checked that the address belongs to the account.
    email_verified: bool
    name: str


class Provider(Protocol):
    name: ProviderName

    def authorize_url(
        self, redirect_uri: str, state: str, code_challenge: str, nonce: str
    ) -> str: ...

    async def fetch_profile(
        self, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Profile: ...


def _display_name(name: Any, email: str | None) -> str:
    if isinstance(name, str) and name.strip():
        return name.strip()[:MAX_NAME]
    if email:
        return email.split("@")[0][:MAX_NAME]
    return "Lukis user"


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise OAuthError(
            f"{response.request.url} answered {response.status_code} with no JSON"
        ) from error


async def _exchange_code(
    client: httpx.AsyncClient, url: str, data: dict[str, str]
) -> dict:
    try:
        response = await client.post(
            url, data=data, headers={"Accept": "application/json"}
        )
    except httpx.HTTPError as error:
        raise OAuthError(f"Couldn't reach {url}: {error!r}") from error
    body = _json(response)
    # GitHub reports a bad code with a 200 and an "error" field.
    if response.status_code != 200 or not isinstance(body, dict) or "error" in body:
        error = body.get("error") if isinstance(body, dict) else None
        raise OAuthError(f"{url} refused the code ({response.status_code}, {error})")
    return body


async def _get(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> Any:
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as error:
        raise OAuthError(f"Couldn't reach {url}: {error!r}") from error
    if response.status_code != 200:
        raise OAuthError(f"{url} answered {response.status_code}")
    return _json(response)


def _id_token_claims(tokens: dict, client_id: str, nonce: str) -> dict:
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        raise OAuthError("No ID token in the token response")
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
    except (IndexError, ValueError) as error:
        raise OAuthError("Unreadable ID token") from error
    if not isinstance(claims, dict):
        raise OAuthError("Unreadable ID token")

    audience = claims.get("aud")
    if audience != client_id and not (
        isinstance(audience, list) and client_id in audience
    ):
        raise OAuthError("The ID token is for another app")
    # Ties the token to the login this browser started.
    if claims.get("nonce") != nonce:
        raise OAuthError("The ID token's nonce doesn't match")
    expires = claims.get("exp")
    if not isinstance(expires, (int, float)) or expires < time.time():
        raise OAuthError("The ID token has expired")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise OAuthError("The ID token has no subject")
    return claims


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class _OpenIDProvider:
    """Google and Microsoft: OpenID Connect, so the person's details are in
    the ID token that comes with the access token."""

    name: ProviderName
    authorize_endpoint: str
    token_endpoint: str
    scope = "openid email profile"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport

    def authorize_url(
        self, redirect_uri: str, state: str, code_challenge: str, nonce: str
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # Asks which account to use, instead of quietly taking the one the
            # browser is signed in to.
            "prompt": "select_account",
        }
        return f"{self.authorize_endpoint}?{urlencode(params)}"

    async def fetch_profile(
        self, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Profile:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS, transport=self.transport
        ) as client:
            tokens = await _exchange_code(
                client,
                self.token_endpoint,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code_verifier": code_verifier,
                },
            )
        return self._profile(_id_token_claims(tokens, self.client_id, nonce))

    def _profile(self, claims: dict) -> Profile:
        raise NotImplementedError


class GoogleProvider(_OpenIDProvider):
    name = "google"
    authorize_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"

    def _profile(self, claims: dict) -> Profile:
        email = _string(claims.get("email"))
        return Profile(
            subject=claims["sub"],
            email=email,
            email_verified=email is not None
            and claims.get("email_verified") in (True, "true"),
            name=_display_name(claims.get("name"), email),
        )


# Every personal Microsoft account (Outlook, Hotmail, Xbox…) is in this tenant.
MICROSOFT_CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


class MicrosoftProvider(_OpenIDProvider):
    name = "microsoft"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant: str = "common",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(client_id, client_secret, transport)
        base = f"https://login.microsoftonline.com/{quote(tenant, safe='')}/oauth2/v2.0"
        self.authorize_endpoint = f"{base}/authorize"
        self.token_endpoint = f"{base}/token"

    def _profile(self, claims: dict) -> Profile:
        email = _string(claims.get("email"))
        # A work or school account's address is whatever its organization's
        # admin typed in, so it only counts as checked for personal accounts,
        # or when Microsoft says the domain's owner verified it (the optional
        # xms_edov claim).
        verified = claims.get("tid") == MICROSOFT_CONSUMER_TENANT or claims.get(
            "xms_edov"
        ) in (True, 1, "1", "true")
        return Profile(
            subject=claims["sub"],
            email=email,
            email_verified=email is not None and verified,
            name=_display_name(claims.get("name"), email),
        )


class GitHubProvider:
    """Plain OAuth 2.0: the details come from GitHub's API."""

    name: ProviderName = "github"
    authorize_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"
    api = "https://api.github.com"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport

    def authorize_url(
        self, redirect_uri: str, state: str, code_challenge: str, nonce: str
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            # user:email reads private addresses too, with whether they're
            # verified.
            "scope": "read:user user:email",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{self.authorize_endpoint}?{urlencode(params)}"

    async def fetch_profile(
        self, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Profile:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS, transport=self.transport
        ) as client:
            tokens = await _exchange_code(
                client,
                self.token_endpoint,
                {
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code_verifier": code_verifier,
                },
            )
            access_token = _string(tokens.get("access_token"))
            if access_token is None:
                raise OAuthError("No access token in GitHub's answer")
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Lukis",
            }
            user = await _get(client, f"{self.api}/user", headers)
            emails = await _get(client, f"{self.api}/user/emails", headers)

        if not isinstance(user, dict) or not isinstance(user.get("id"), int):
            raise OAuthError("GitHub's user has no ID")
        entries = emails if isinstance(emails, list) else []
        verified = [
            entry["email"]
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("verified") is True
            and _string(entry.get("email"))
            # Ones like 123+ada@users.noreply.github.com don't receive mail.
            and not entry["email"].endswith("@users.noreply.github.com")
        ]
        primary = next(
            (
                entry["email"]
                for entry in entries
                if isinstance(entry, dict)
                and entry.get("primary") is True
                and entry.get("email") in verified
            ),
            verified[0] if verified else None,
        )
        return Profile(
            subject=str(user["id"]),
            email=primary,
            email_verified=primary is not None,
            name=_display_name(user.get("name") or user.get("login"), primary),
        )


def create_providers(settings: Settings) -> dict[ProviderName, Provider]:
    """The providers with a client ID and secret set, in the order the login
    page shows them."""
    providers: dict[ProviderName, Provider] = {}
    if settings.google_client_id and settings.google_client_secret:
        providers["google"] = GoogleProvider(
            settings.google_client_id, settings.google_client_secret
        )
    if settings.github_client_id and settings.github_client_secret:
        providers["github"] = GitHubProvider(
            settings.github_client_id, settings.github_client_secret
        )
    if settings.microsoft_client_id and settings.microsoft_client_secret:
        providers["microsoft"] = MicrosoftProvider(
            settings.microsoft_client_id,
            settings.microsoft_client_secret,
            settings.microsoft_tenant or "common",
        )
    return providers


def get_oauth_providers(request: Request) -> dict[ProviderName, Provider]:
    return request.app.state.oauth


OAuthProviders = Annotated[dict[ProviderName, Provider], Depends(get_oauth_providers)]
