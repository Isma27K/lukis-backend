import base64
import hashlib
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from middleware.auth import SESSION_COOKIE
from routes.oauth import STATE_COOKIE
from tests.helpers import JSON, PASSWORD, SETUP_CODE, signup
from tests.mailer import link_in
from utils.oauth import OAuthError, Profile


class FakeProvider:
    """Stands in for Google and the like. `profiles` maps the code the
    provider would send back to who it says logged in."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.profiles: dict[str, Profile] = {}
        self.challenges: dict[str, str] = {}

    def authorize_url(self, redirect_uri, state, code_challenge, nonce) -> str:
        self.challenges[state] = code_challenge
        query = urlencode(
            {"redirect_uri": redirect_uri, "state": state, "nonce": nonce}
        )
        return f"https://{self.name}.test/authorize?{query}"

    async def fetch_profile(self, code, redirect_uri, code_verifier, nonce) -> Profile:
        digest = hashlib.sha256(code_verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert challenge in self.challenges.values(), "PKCE verifier doesn't match"
        assert redirect_uri.endswith(f"/api/auth/oauth/{self.name}/callback")
        if code not in self.profiles:
            raise OAuthError("bad code")
        return self.profiles[code]


ADA = Profile("google-ada", "ada@example.com", True, "Ada Lovelace")


@pytest.fixture
def google():
    provider = FakeProvider("google")
    provider.profiles["ada"] = ADA
    return provider


@pytest.fixture
def github():
    return FakeProvider("github")


@pytest.fixture
def oauth_providers(google, github):
    return {"google": google, "github": github}


async def start(client, provider="google", **query) -> str:
    """Follows the start link; returns the state sent to the provider."""
    response = await client.get(f"/auth/oauth/{provider}/start", params=query)
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.netloc == f"{provider}.test"
    return parse_qs(location.query)["state"][0]


async def come_back(client, state, code="ada", provider="google", **extra):
    """The provider sending the browser back."""
    return await client.get(
        f"/auth/oauth/{provider}/callback",
        params={"code": code, "state": state, **extra},
    )


async def oauth_login(client, code="ada", provider="google", **query):
    state = await start(client, provider, **query)
    return await come_back(client, state, code, provider)


async def connect(client, provider="github", code="ada-gh"):
    response = await client.post(f"/auth/oauth/{provider}/link", headers=JSON)
    assert response.status_code == 200
    state = parse_qs(urlsplit(response.json()["url"]).query)["state"][0]
    return await come_back(client, state, code, provider)


def problem(response) -> str | None:
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query.get("oauth_error", [None])[0]


@pytest.fixture
async def owner(make_client):
    """Sets the site up, so others can sign up."""
    client = await make_client()
    await signup(client, "owner@example.com", "Owner")
    return client


async def test_features_list_the_providers_set_up(client):
    features = (await client.get("/features")).json()
    assert features["oauth_providers"] == ["google", "github"]


async def test_signing_up_with_google(owner, client, mailer):
    response = await oauth_login(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    me = (await client.get("/auth/me")).json()
    assert me["email"] == "ada@example.com"
    assert me["name"] == "Ada Lovelace"
    # Google checked the address, so there's no email to confirm it.
    assert me["email_verified"] is True
    assert me["has_password"] is False
    assert not mailer.to("ada@example.com")
    workspaces = (await client.get("/workspaces")).json()
    assert [w["personal"] for w in workspaces] == [True]
    assert STATE_COOKIE not in client.cookies


async def test_logging_in_again_finds_the_same_account(owner, make_client):
    first, second = await make_client(), await make_client()
    await oauth_login(first)
    user_id = (await first.get("/auth/me")).json()["id"]

    response = await oauth_login(second)

    assert response.headers["location"] == "/"
    assert (await second.get("/auth/me")).json()["id"] == user_id


async def test_unverified_addresses_get_a_confirmation_email(
    owner, client, google, mailer
):
    google.profiles["work"] = Profile("ms-1", "ada@corp.example", False, "Ada")

    await oauth_login(client, "work")

    assert (await client.get("/auth/me")).json()["email_verified"] is False
    assert mailer.to("ada@corp.example")[-1].subject.startswith("Confirm")


async def test_goes_back_where_they_were_going(owner, client):
    response = await oauth_login(client, next="/w/123")
    assert response.headers["location"] == "/w/123"


async def test_next_cant_lead_off_the_site(owner, client):
    response = await oauth_login(client, next="//evil.example")
    assert response.headers["location"] == "/"


async def test_existing_email_isnt_taken_over(owner, make_client, google):
    password_user, other = await make_client(), await make_client()
    await signup(password_user, "ada@example.com", "Ada")

    response = await oauth_login(other)

    assert problem(response) == "email_taken"
    assert response.headers["location"].startswith("/login?")
    assert SESSION_COOKIE not in other.cookies
    assert (await other.get("/auth/me")).status_code == 401


async def test_state_must_come_back_to_the_same_browser(owner, make_client):
    victim, attacker = await make_client(), await make_client()
    state = await start(attacker)

    response = await come_back(victim, state)

    assert problem(response) == "expired"
    assert (await victim.get("/auth/me")).status_code == 401


async def test_state_works_once(owner, client):
    state = await start(client)
    first = await come_back(client, state)
    assert first.headers["location"] == "/"
    await client.post("/auth/logout", headers=JSON)
    client.cookies.set(STATE_COOKIE, state)

    replay = await come_back(client, state)

    assert problem(replay) == "expired"


async def test_state_is_for_one_provider(owner, client):
    state = await start(client, "google")
    response = await come_back(client, state, provider="github")
    assert problem(response) == "expired"


async def test_cancelling_at_the_provider(owner, client):
    state = await start(client, page="register", next="/invite/abc")

    response = await come_back(client, state, code="", error="access_denied")

    location = urlsplit(response.headers["location"])
    assert location.path == "/register"
    assert parse_qs(location.query) == {
        "next": ["/invite/abc"],
        "oauth_error": ["denied"],
        "provider": ["google"],
    }


async def test_provider_refusing_the_code(owner, client):
    response = await oauth_login(client, code="forged")
    assert problem(response) == "failed"


async def test_provider_account_without_email(owner, client, github):
    github.profiles["anon"] = Profile("gh-1", None, False, "anon")
    response = await oauth_login(client, "anon", "github")
    assert problem(response) == "no_email"


async def test_provider_not_set_up(client):
    response = await client.get("/auth/oauth/microsoft/start")
    assert response.status_code == 303
    assert problem(response) == "unavailable"


async def test_first_account_needs_the_setup_code(client):
    response = await oauth_login(client)
    assert problem(response) == "setup"
    assert (await client.get("/auth/me")).status_code == 401


async def close_signup(owner):
    response = await owner.patch("/site/settings", json={"registration_open": False})
    assert response.status_code == 200


async def test_invite_only_sites_need_an_invite(owner, client):
    await close_signup(owner)
    response = await oauth_login(client)
    assert problem(response) == "invite_only"


async def signup_invite(owner, email) -> str:
    response = await owner.post("/site/invites", json={"email": email})
    assert response.status_code == 201
    return response.json()["token"]


async def test_signing_up_with_an_invite(owner, client):
    await close_signup(owner)
    token = await signup_invite(owner, "ada@example.com")

    response = await oauth_login(client, invite=token, page="register")

    assert response.headers["location"] == "/"
    assert (await client.get("/auth/me")).json()["email"] == "ada@example.com"


async def test_invite_for_someone_else(owner, client):
    await close_signup(owner)
    token = await signup_invite(owner, "bo@example.com")

    response = await oauth_login(client, invite=token, page="register")

    assert problem(response) == "invite_mismatch"
    assert parse_qs(urlsplit(response.headers["location"]).query)["invite"] == [token]


async def test_connecting_github_to_a_password_account(
    owner, make_client, github, mailer
):
    ada, later = await make_client(), await make_client()
    user = await signup(ada, "ada@example.com", "Ada")
    github.profiles["ada-gh"] = Profile("gh-ada", "ada@users.example", True, "ada")

    response = await connect(ada)

    assert response.headers["location"] == "/account?connected=github#connected"
    identities = (await ada.get("/account/identities")).json()
    assert [(i["provider"], i["email"]) for i in identities] == [
        ("github", "ada@users.example")
    ]
    assert "GitHub was connected" in mailer.to("ada@example.com")[-1].subject
    # From now on GitHub logs in to the same account.
    response = await oauth_login(later, "ada-gh", "github")
    assert (await later.get("/auth/me")).json()["id"] == user["id"]


async def test_connecting_needs_a_session(client):
    response = await client.post("/auth/oauth/github/link", headers=JSON)
    assert response.status_code == 401


async def test_connecting_is_a_json_request(owner):
    response = await owner.post("/auth/oauth/github/link")
    assert response.status_code == 415


async def test_cant_connect_someone_elses_provider_account(owner, make_client, github):
    ada, bo = await make_client(), await make_client()
    github.profiles["gh"] = Profile("gh-shared", "x@example.com", True, "x")
    await signup(ada, "ada@example.com", "Ada")
    await signup(bo, "bo@example.com", "Bo")
    await connect(ada, code="gh")

    response = await connect(bo, code="gh")

    assert problem(response) == "linked_elsewhere"
    assert response.headers["location"].startswith("/account?")
    assert (await bo.get("/account/identities")).json() == []


async def test_one_account_per_provider(owner, make_client, github):
    ada = await make_client()
    await signup(ada, "ada@example.com", "Ada")
    github.profiles["one"] = Profile("gh-1", "a@example.com", True, "a")
    github.profiles["two"] = Profile("gh-2", "b@example.com", True, "b")
    await connect(ada, code="one")

    response = await connect(ada, code="two")

    assert problem(response) == "already_linked"


async def test_connecting_twice_is_harmless(owner, github):
    github.profiles["gh"] = Profile("gh-owner", "o@example.com", True, "o")
    await connect(owner, code="gh")
    response = await connect(owner, code="gh")
    assert response.headers["location"] == "/account?connected=github#connected"


async def test_connect_finishes_only_for_the_user_who_started(
    owner, make_client, github
):
    github.profiles["gh"] = Profile("gh-owner", "o@example.com", True, "o")
    response = await owner.post("/auth/oauth/github/link", headers=JSON)
    state = parse_qs(urlsplit(response.json()["url"]).query)["state"][0]
    # Logged out and in as someone else before coming back.
    await owner.post("/auth/logout", headers=JSON)
    await signup(owner, "bo@example.com", "Bo")
    owner.cookies.set(STATE_COOKIE, state)

    response = await come_back(owner, state, "gh", "github")

    assert problem(response) == "expired"
    assert (await owner.get("/account/identities")).json() == []


async def test_password_logins_dont_work_without_a_password(owner, client):
    await oauth_login(client)
    await client.post("/auth/logout", headers=JSON)

    response = await client.post(
        "/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )

    assert response.status_code == 401


async def test_setting_a_first_password(owner, client, mailer):
    await oauth_login(client)

    response = await client.post("/account/password", json={"new_password": PASSWORD})

    assert response.status_code == 204
    assert (await client.get("/auth/me")).json()["has_password"] is True
    assert "password was added" in mailer.to("ada@example.com")[-1].subject
    await client.post("/auth/logout", headers=JSON)
    response = await client.post(
        "/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200


async def test_changing_a_password_still_needs_the_current_one(owner):
    response = await owner.post("/account/password", json={"new_password": "x" * 12})
    assert response.status_code == 403


async def test_cant_disconnect_the_only_way_in(owner, client, github):
    await oauth_login(client)

    response = await client.delete("/account/identities/google", headers=JSON)

    assert response.status_code == 409
    assert len((await client.get("/account/identities")).json()) == 1


async def test_disconnecting_with_another_way_in(owner, client, github):
    await oauth_login(client)
    github.profiles["gh"] = Profile("gh-ada", "ada@example.com", True, "ada")
    await connect(client, code="gh")

    response = await client.delete("/account/identities/google", headers=JSON)

    assert response.status_code == 204
    identities = (await client.get("/account/identities")).json()
    assert [i["provider"] for i in identities] == ["github"]
    # Google now finds no account, and the address is taken.
    other = await oauth_login(client)
    assert problem(other) == "email_taken"


async def test_password_accounts_can_disconnect_their_only_provider(owner, github):
    github.profiles["gh"] = Profile("gh-owner", "o@example.com", True, "o")
    await connect(owner, code="gh")

    response = await owner.delete("/account/identities/github", headers=JSON)

    assert response.status_code == 204


async def test_disconnecting_what_isnt_connected(owner):
    response = await owner.delete("/account/identities/github", headers=JSON)
    assert response.status_code == 404


async def test_deleting_an_account_without_a_password(owner, client, app):
    await oauth_login(client)

    wrong = await client.post("/account/delete", json={"email": "bo@example.com"})
    assert wrong.status_code == 403
    response = await client.post("/account/delete", json={"email": " Ada@Example.com"})

    assert response.status_code == 204
    assert await app.state.db.identities.count_documents({}) == 0
    # Logging in with Google again makes a fresh account.
    again = await oauth_login(client)
    assert again.headers["location"] == "/"


async def test_password_accounts_still_delete_with_their_password(owner):
    response = await owner.post("/account/delete", json={"email": "owner@example.com"})
    assert response.status_code == 403


async def test_reset_link_sets_a_password_for_oauth_accounts(owner, client, mailer):
    await oauth_login(client)
    await client.post("/auth/password-reset", json={"email": "ada@example.com"})
    _, query = link_in(mailer.to("ada@example.com")[-1])

    response = await client.post(
        "/auth/password-reset/confirm",
        json={"token": query["token"], "password": PASSWORD},
    )

    assert response.status_code == 204
    login = await client.post(
        "/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200


async def test_too_many_attempts(owner, client, app):
    from function.rate_limit import OAUTH_PER_IP

    for _ in range(OAUTH_PER_IP.attempts):
        app.state.rate_limiter.count(OAUTH_PER_IP, "127.0.0.1")

    response = await client.get("/auth/oauth/google/start")

    assert problem(response) == "rate_limited"


async def test_unused_setup_code_still_works_for_a_password_signup(client):
    # Trying Google first doesn't use up the setup code.
    await oauth_login(client)
    response = await client.post(
        "/auth/register",
        json={
            "email": "first@example.com",
            "name": "First",
            "password": PASSWORD,
            "setup_code": SETUP_CODE,
        },
    )
    assert response.status_code == 201
    assert response.json()["site_owner"] is True
