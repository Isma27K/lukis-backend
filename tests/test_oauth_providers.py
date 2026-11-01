import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from utils.config import Settings
from utils.oauth import (
    MICROSOFT_CONSUMER_TENANT,
    GitHubProvider,
    GoogleProvider,
    MicrosoftProvider,
    OAuthError,
    create_providers,
)

REDIRECT = "https://lukis.example/api/auth/oauth/x/callback"
NONCE = "the-nonce"


def id_token(**claims) -> str:
    """An unsigned JWT; signatures aren't checked on tokens fetched directly."""
    claims = {
        "aud": "client-id",
        "nonce": NONCE,
        "exp": int(time.time()) + 300,
        "sub": "subject-1",
        **claims,
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"eyJhbGciOiJub25lIn0.{payload.decode()}.sig"


def token_endpoint(answer: dict, status: int = 200, seen: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=answer)

    return httpx.MockTransport(handle)


async def google_profile(**claims):
    provider = GoogleProvider(
        "client-id",
        "secret",
        transport=token_endpoint({"id_token": id_token(**claims)}),
    )
    return await provider.fetch_profile("code", REDIRECT, "verifier", NONCE)


def test_authorize_url_carries_pkce_and_state():
    url = GoogleProvider("client-id", "secret").authorize_url(
        REDIRECT, "the-state", "the-challenge", NONCE
    )
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert parts.netloc == "accounts.google.com"
    assert query["state"] == "the-state"
    assert query["code_challenge"] == "the-challenge"
    assert query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == REDIRECT
    assert query["nonce"] == NONCE
    assert query["scope"] == "openid email profile"


async def test_google_sends_the_verifier_and_secret():
    seen = []
    provider = GoogleProvider(
        "client-id",
        "secret",
        transport=token_endpoint({"id_token": id_token()}, seen=seen),
    )
    await provider.fetch_profile("the-code", REDIRECT, "the-verifier", NONCE)

    [request] = seen
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    assert str(request.url) == "https://oauth2.googleapis.com/token"
    assert form["code"] == "the-code"
    assert form["code_verifier"] == "the-verifier"
    assert form["client_secret"] == "secret"
    assert form["redirect_uri"] == REDIRECT


async def test_google_profile():
    profile = await google_profile(
        email="ada@example.com", email_verified=True, name="Ada Lovelace"
    )
    assert profile.subject == "subject-1"
    assert profile.email == "ada@example.com"
    assert profile.email_verified is True
    assert profile.name == "Ada Lovelace"


async def test_google_unverified_email():
    profile = await google_profile(email="ada@example.com", email_verified=False)
    assert profile.email_verified is False
    # No name: falls back to the address.
    assert profile.name == "ada"


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "another-app"},
        {"nonce": "replayed"},
        {"exp": int(time.time()) - 10},
        {"sub": ""},
    ],
)
async def test_google_rejects_bad_id_tokens(claims):
    with pytest.raises(OAuthError):
        await google_profile(**claims)


async def test_refused_code():
    provider = GoogleProvider(
        "client-id",
        "secret",
        transport=token_endpoint({"error": "invalid_grant"}, status=400),
    )
    with pytest.raises(OAuthError):
        await provider.fetch_profile("code", REDIRECT, "verifier", NONCE)


async def test_unreachable_provider():
    def fail(request):
        raise httpx.ConnectError("down")

    provider = GoogleProvider(
        "client-id", "secret", transport=httpx.MockTransport(fail)
    )
    with pytest.raises(OAuthError):
        await provider.fetch_profile("code", REDIRECT, "verifier", NONCE)


async def microsoft_profile(**claims):
    provider = MicrosoftProvider(
        "client-id",
        "secret",
        "organizations",
        transport=token_endpoint({"id_token": id_token(**claims)}),
    )
    return await provider.fetch_profile("code", REDIRECT, "verifier", NONCE)


def test_microsoft_uses_the_tenant():
    url = MicrosoftProvider(
        "client-id", "secret", "contoso.onmicrosoft.com"
    ).authorize_url(REDIRECT, "s", "c", NONCE)
    assert url.startswith(
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/authorize?"
    )


async def test_microsoft_personal_accounts_are_verified():
    profile = await microsoft_profile(
        email="ada@outlook.com", tid=MICROSOFT_CONSUMER_TENANT, name="Ada"
    )
    assert profile.email_verified is True


async def test_microsoft_work_accounts_arent_verified_by_default():
    # An organization's admin can set any address on its accounts.
    profile = await microsoft_profile(email="ceo@bank.example", tid="some-tenant")
    assert profile.email == "ceo@bank.example"
    assert profile.email_verified is False


async def test_microsoft_domain_owner_verified():
    profile = await microsoft_profile(
        email="ada@corp.example", tid="some-tenant", xms_edov=True
    )
    assert profile.email_verified is True


def github_api(user: dict, emails, token: dict | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(200, json=token or {"access_token": "gho_token"})
        assert request.headers["authorization"] == "Bearer gho_token"
        if request.url.path == "/user":
            return httpx.Response(200, json=user)
        if request.url.path == "/user/emails":
            return httpx.Response(200, json=emails)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


async def github_profile(user, emails, token=None):
    provider = GitHubProvider(
        "client-id", "secret", transport=github_api(user, emails, token)
    )
    return await provider.fetch_profile("code", REDIRECT, "verifier", NONCE)


async def test_github_picks_the_verified_primary_address():
    profile = await github_profile(
        {"id": 42, "login": "ada", "name": None},
        [
            {"email": "old@example.com", "primary": False, "verified": True},
            {"email": "ada@example.com", "primary": True, "verified": True},
        ],
    )
    assert profile.subject == "42"
    assert profile.email == "ada@example.com"
    assert profile.email_verified is True
    assert profile.name == "ada"


async def test_github_skips_unverified_and_noreply_addresses():
    profile = await github_profile(
        {"id": 42, "login": "ada", "name": "Ada"},
        [
            {"email": "new@example.com", "primary": True, "verified": False},
            {
                "email": "42+ada@users.noreply.github.com",
                "primary": False,
                "verified": True,
            },
            {"email": "spare@example.com", "primary": False, "verified": True},
        ],
    )
    assert profile.email == "spare@example.com"


async def test_github_without_a_verified_address():
    profile = await github_profile(
        {"id": 42, "login": "ada"},
        [{"email": "new@example.com", "primary": True, "verified": False}],
    )
    assert profile.email is None
    assert profile.email_verified is False


async def test_github_reports_bad_codes_with_a_200():
    with pytest.raises(OAuthError):
        await github_profile({"id": 42}, [], token={"error": "bad_verification_code"})


def test_only_providers_with_credentials_are_offered():
    settings = Settings(
        _env_file=None,
        google_client_id="g",
        google_client_secret="gs",
        github_client_id="only-an-id",
        microsoft_client_id="m",
        microsoft_client_secret="ms",
    )
    assert list(create_providers(settings)) == ["google", "microsoft"]
