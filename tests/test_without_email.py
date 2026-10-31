"""Without SMTP set up, the app runs with its email features off."""

import pytest

from tests.helpers import PASSWORD, create_workspace, signup


@pytest.fixture
def mailer():
    # Overrides the conftest fixture: no mailer means email is off.
    return None


async def test_features_say_email_is_off(client):
    features = (await client.get("/features")).json()
    assert features == {"email": False, "registration": "setup", "public_links": True}


async def test_invites_work_without_verification(make_client):
    owner, bob = await make_client(), await make_client()
    await signup(owner)
    team = await create_workspace(owner)
    created = await owner.post(
        f"/workspaces/{team['id']}/invites",
        json={"email": "bob@example.com", "role": "editor"},
    )
    assert created.status_code == 201

    user = await signup(bob, "bob@example.com", "Bob")
    assert user["email_verified"] is False
    joined = await bob.post(f"/invites/{created.json()['token']}/accept", json={})

    assert joined.status_code == 200
    assert joined.json()["role"] == "editor"


async def test_email_only_actions_are_unavailable(client):
    await signup(client)

    reset = await client.post(
        "/auth/password-reset", json={"email": "ada@example.com"}
    )
    resend = await client.post("/auth/verify-email/resend", json={})

    assert [reset.status_code, resend.status_code] == [404, 404]
    assert reset.json()["detail"] == "Email isn't set up on this server"


async def test_account_changes_work_without_email(make_client):
    ada, bo = await make_client(), await make_client()
    await signup(ada)
    await signup(bo, "bo@example.com", "Bo")

    changed = await bo.post(
        "/account/password",
        json={"current_password": PASSWORD, "new_password": "another password"},
    )
    deleted = await bo.post("/account/delete", json={"password": "another password"})

    assert [changed.status_code, deleted.status_code] == [204, 204]
