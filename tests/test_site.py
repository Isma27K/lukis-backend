import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from pymongo import MongoClient

import function.site
from function.site import INVITE_ONLY, WRONG_SETUP_CODE, ensure_site
from scripts.site import describe, set_owner, set_registration
from tests.helpers import (
    JSON,
    PASSWORD,
    SETUP_CODE,
    confirm_email,
    create_workspace,
    signup,
)
from tests.mailer import link_in
from utils.config import Settings


async def register(client, email, name="Someone", **extra):
    return await client.post(
        "/auth/register",
        json={"email": email, "name": name, "password": PASSWORD, **extra},
    )


async def registration(client) -> str:
    return (await client.get("/features")).json()["registration"]


async def owner_with_sign_up_closed(make_client):
    owner = await make_client()
    await signup(owner, "olga@example.com", "Olga")
    closed = await owner.patch("/site/settings", json={"registration_open": False})
    assert closed.json()["registration_open"] is False
    return owner


async def invite_to_sign_up(owner, email="bob@example.com") -> str:
    created = await owner.post("/site/invites", json={"email": email})
    assert created.status_code == 201
    return created.json()["token"]


async def test_the_first_account_needs_the_setup_code(app, make_client):
    first = await make_client()
    assert await registration(first) == "setup"

    missing = await register(first, "olga@example.com")
    wrong = await register(first, "olga@example.com", setup_code="WRONG-CODE-HERE")
    assert missing.status_code == wrong.status_code == 403
    assert missing.json()["detail"] == WRONG_SETUP_CODE
    assert await app.state.db.users.count_documents({}) == 0

    # Case and dashes don't matter.
    owner = await register(first, "olga@example.com", setup_code="testsetupcode")
    assert owner.status_code == 201
    assert owner.json()["site_owner"] is True
    assert (await first.get("/auth/me")).json()["site_owner"] is True
    site = await app.state.db.site.find_one({"_id": "site"})
    assert "setup_code_hash" not in site

    # From now on sign-up is open, and nobody else becomes owner.
    assert await registration(first) == "open"
    second = await make_client()
    bob = await register(second, "bob@example.com")
    assert bob.status_code == 201
    assert bob.json()["site_owner"] is False
    assert (await second.get("/auth/me")).json()["site_owner"] is False


async def test_two_first_sign_ups_at_once_make_one_owner(app, make_client):
    clients = [await make_client() for _ in range(2)]

    responses = await asyncio.gather(
        register(clients[0], "olga@example.com", setup_code=SETUP_CODE),
        register(clients[1], "bob@example.com", setup_code=SETUP_CODE),
    )

    created = [r.json() for r in responses if r.status_code == 201]
    owners = [user for user in created if user["site_owner"]]
    assert len(owners) == 1
    # The other either lost the race for the code or came in once sign-up
    # was open.
    assert all(r.status_code in (201, 403) for r in responses)
    site = await app.state.db.site.find_one({"_id": "site"})
    assert str(site["owner_id"]) == owners[0]["id"]


async def test_a_sign_up_that_loses_the_race_for_the_code_is_refused(
    app, make_client, monkeypatch
):
    # Both read the site while it still had no owner, before either claimed it.
    before = await function.site.get_site(app.state.db)

    async def stale_site(db):
        return before

    monkeypatch.setattr(function.site, "get_site", stale_site)
    olga, bob = await make_client(), await make_client()
    first = await register(olga, "olga@example.com", setup_code=SETUP_CODE)
    late = await register(bob, "bob@example.com", setup_code=SETUP_CODE)

    assert first.json()["site_owner"] is True
    assert late.status_code == 403
    assert late.json()["detail"] == WRONG_SETUP_CODE
    assert await app.state.db.users.find_one({"email": "bob@example.com"}) is None


async def test_a_failed_first_sign_up_gives_the_claim_back(app, client):
    # Only possible if the address somehow has an account already.
    await app.state.db.users.insert_one(
        {
            "_id": ObjectId(),
            "email": "olga@example.com",
            "created_at": datetime.now(UTC),
        }
    )

    taken = await register(client, "olga@example.com", setup_code=SETUP_CODE)

    assert taken.status_code == 409
    assert await registration(client) == "setup"
    owner = await register(client, "bob@example.com", setup_code=SETUP_CODE)
    assert owner.json()["site_owner"] is True


async def test_existing_accounts_make_the_oldest_one_owner(app, client):
    db = app.state.db
    now = datetime.now(UTC)
    older, newer = ObjectId(), ObjectId()
    await db.users.insert_many(
        [
            {"_id": newer, "email": "new@example.com", "created_at": now},
            {
                "_id": older,
                "email": "old@example.com",
                "created_at": now - timedelta(days=9),
            },
        ]
    )

    await ensure_site(db, app.state.settings)

    site = await db.site.find_one({"_id": "site"})
    assert site["owner_id"] == older
    assert "setup_code_hash" not in site
    # Nothing changes for an existing install until the owner closes sign-up.
    assert await registration(client) == "open"


async def test_without_a_configured_code_one_is_logged(app, client, caplog):
    db = app.state.db
    await db.site.delete_many({})

    with caplog.at_level(logging.WARNING, logger="lukis.site"):
        await ensure_site(db, Settings(mongo_db=db.name))

    code = re.search(r"setup code (\S+)", caplog.text).group(1)
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
    configured = await register(client, "olga@example.com", setup_code=SETUP_CODE)
    assert configured.status_code == 403
    owner = await register(client, "olga@example.com", setup_code=code)
    assert owner.json()["site_owner"] is True


async def test_a_weak_configured_setup_code_is_refused(app):
    with pytest.raises(ValueError, match="SETUP_CODE"):
        await ensure_site(app.state.db, Settings(setup_code="ab-12"))


async def test_only_the_site_owner_reaches_site_settings(make_client):
    owner, member, stranger = [await make_client() for _ in range(3)]
    await signup(owner, "olga@example.com", "Olga")
    await signup(member, "bob@example.com", "Bob")

    assert (await owner.get("/site/settings")).json()["registration_open"] is True
    created = await owner.post("/site/invites", json={"email": "c@example.com"})
    invite_id = created.json()["id"]

    for client, status in ((member, 403), (stranger, 401)):
        requests = [
            client.get("/site/settings"),
            client.patch("/site/settings", json={"registration_open": False}),
            client.get("/site/invites"),
            client.post("/site/invites", json={"email": "eve@example.com"}),
            client.delete(f"/site/invites/{invite_id}", headers=JSON),
        ]
        for response in await asyncio.gather(*requests):
            assert response.status_code == status
    assert (await owner.get("/site/settings")).json()["registration_open"] is True
    assert len((await owner.get("/site/invites")).json()) == 1

    # Like every change, it has to be JSON, so a cross-site form can't send it.
    form = await owner.patch("/site/settings", data={"registration_open": "false"})
    assert form.status_code == 415


async def test_closed_sign_up_turns_strangers_away(app, make_client):
    owner = await owner_with_sign_up_closed(make_client)
    stranger = await make_client()
    assert await registration(stranger) == "invite_only"
    users = await app.state.db.users.count_documents({})

    plain = await register(stranger, "eve@example.com")
    bogus = await register(stranger, "eve@example.com", invite_token="made-up")
    # Same answer for an address that has an account, so it can't be probed.
    taken = await register(stranger, "olga@example.com")
    for response in (plain, bogus, taken):
        assert response.status_code == 403
        assert response.json()["detail"] == INVITE_ONLY
    assert await app.state.db.users.count_documents({}) == users

    reopened = await owner.patch("/site/settings", json={"registration_open": True})
    assert reopened.json()["registration_open"] is True
    assert (await register(stranger, "eve@example.com")).status_code == 201


async def test_sign_up_invite_flow(make_client, mailer):
    owner = await owner_with_sign_up_closed(make_client)
    token = await invite_to_sign_up(owner, "  Bob@Example.com ")

    [email] = mailer.to("bob@example.com")
    assert email.subject == "Olga invited you to Lukis"
    assert link_in(email) == ("/register", {"invite": token})
    pending = (await owner.get("/site/invites")).json()
    assert [invite["email"] for invite in pending] == ["bob@example.com"]
    assert "token" not in pending[0]

    bob = await make_client()
    preview = await bob.get(f"/auth/signup-invites/{token}")
    assert preview.json() == {"email": "bob@example.com"}

    wrong = await register(bob, "eve@example.com", invite_token=token)
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == "This invite is for bob@example.com"

    joined = await register(bob, "bob@example.com", name="Bob", invite_token=token)
    assert joined.status_code == 201
    assert joined.json()["site_owner"] is False
    # The link could have been passed on, so it doesn't confirm the address.
    assert joined.json()["email_verified"] is False

    # Used up.
    assert (await owner.get("/site/invites")).json() == []
    assert (await bob.get(f"/auth/signup-invites/{token}")).status_code == 404
    again = await register(await make_client(), "bob2@example.com", invite_token=token)
    assert again.status_code == 403


async def test_revoked_and_expired_sign_up_invites_fail(app, make_client):
    owner = await owner_with_sign_up_closed(make_client)
    revoked = await invite_to_sign_up(owner, "bob@example.com")
    expired = await invite_to_sign_up(owner, "carol@example.com")
    pending = (await owner.get("/site/invites")).json()
    [bob_invite] = [i for i in pending if i["email"] == "bob@example.com"]
    response = await owner.delete(f"/site/invites/{bob_invite['id']}", headers=JSON)
    assert response.status_code == 204
    await app.state.db.signup_invites.update_one(
        {"email": "carol@example.com"},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(minutes=1)}},
    )

    client = await make_client()
    for email, token in (("bob@example.com", revoked), ("carol@example.com", expired)):
        assert (await client.get(f"/auth/signup-invites/{token}")).status_code == 404
        assert (await register(client, email, invite_token=token)).status_code == 403
    for invite_id in (bob_invite["id"], "not-an-id"):
        response = await owner.delete(f"/site/invites/{invite_id}", headers=JSON)
        assert response.status_code == 404


async def test_no_sign_up_invite_for_an_existing_account(make_client):
    owner = await owner_with_sign_up_closed(make_client)

    response = await owner.post("/site/invites", json={"email": "OLGA@example.com"})

    assert response.status_code == 409


async def test_the_owners_workspace_invite_lets_someone_sign_up(make_client, mailer):
    owner = await owner_with_sign_up_closed(make_client)
    team = await create_workspace(owner)
    created = await owner.post(
        f"/workspaces/{team['id']}/invites",
        json={"email": "bob@example.com", "role": "editor"},
    )
    token = created.json()["token"]

    bob = await make_client()
    preview = await bob.get(f"/auth/signup-invites/{token}")
    assert preview.json() == {"email": "bob@example.com"}
    joined = await register(bob, "bob@example.com", name="Bob", invite_token=token)
    assert joined.status_code == 201

    # The workspace invite is still there, to join with.
    await confirm_email(bob, mailer, "bob@example.com")
    accepted = await bob.post(f"/invites/{token}/accept", json={})
    assert accepted.json()["id"] == team["id"]


async def test_other_admins_workspace_invites_dont_open_sign_up(make_client):
    owner, admin = await make_client(), await make_client()
    await signup(owner, "olga@example.com", "Olga")
    await signup(admin, "ada@example.com", "Ada")
    team = await create_workspace(admin)
    created = await admin.post(
        f"/workspaces/{team['id']}/invites",
        json={"email": "carol@example.com", "role": "editor"},
    )
    token = created.json()["token"]
    await owner.patch("/site/settings", json={"registration_open": False})

    carol = await make_client()
    assert (await carol.get(f"/auth/signup-invites/{token}")).status_code == 404
    response = await register(carol, "carol@example.com", invite_token=token)
    assert response.status_code == 403
    assert response.json()["detail"] == INVITE_ONLY


@pytest.fixture
def sync_db(app):
    """The test database, the way the command line reaches it."""
    client = MongoClient(Settings().mongo_url, tz_aware=True)
    yield client[app.state.db.name]
    client.close()


async def test_command_line_recovery(make_client, sync_db):
    olga, bob = await make_client(), await make_client()
    await signup(olga, "olga@example.com", "Olga")
    await signup(bob, "bob@example.com", "Bob")

    set_owner(sync_db, "BOB@example.com")
    set_registration(sync_db, False)

    assert (await bob.get("/auth/me")).json()["site_owner"] is True
    assert (await olga.get("/site/settings")).status_code == 403
    assert await registration(bob) == "invite_only"
    assert describe(sync_db) == "Owner: bob@example.com\nSign-up: invite-only"
    with pytest.raises(SystemExit, match="nobody@example.com"):
        set_owner(sync_db, "nobody@example.com")
