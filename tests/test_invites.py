from datetime import UTC, datetime, timedelta

from tests.helpers import (
    JSON,
    add_member,
    confirm_email,
    create_workspace,
    personal_workspace_id,
    signup,
)
from tests.mailer import link_in


async def invite(client, workspace_id, email="bob@example.com", role="editor"):
    return await client.post(
        f"/workspaces/{workspace_id}/invites", json={"email": email, "role": role}
    )


async def owner_and_team(make_client):
    owner = await make_client()
    await signup(owner, "olga@example.com", "Olga")
    team = await create_workspace(owner)
    return owner, team["id"]


async def test_invite_link_flow(make_client, mailer):
    owner, team = await owner_and_team(make_client)
    created = await invite(owner, team, email="  Bob@Example.com ")
    assert created.status_code == 201
    token = created.json()["token"]
    assert created.json()["email"] == "bob@example.com"

    # The link is emailed, and also returned for sharing some other way.
    [email] = mailer.to("bob@example.com")
    assert email.subject == "Olga invited you to Team on Lukis"
    assert link_in(email) == (f"/invite/{token}", {})

    pending = (await owner.get(f"/workspaces/{team}/invites")).json()
    assert [(i["email"], i["role"]) for i in pending] == [("bob@example.com", "editor")]
    assert "token" not in pending[0]

    bob = await make_client()
    await signup(bob, "bob@example.com", "Bob")
    preview = (await bob.get(f"/invites/{token}")).json()
    assert preview["workspace_name"] == "Team"
    assert preview["invited_by"] == "Olga"
    assert preview["role"] == "editor"

    # Bob has to prove the address is his first.
    unverified = await bob.post(f"/invites/{token}/accept", json={})
    assert unverified.status_code == 403
    assert "Confirm your email" in unverified.json()["detail"]
    await confirm_email(bob, mailer, "bob@example.com")

    joined = await bob.post(f"/invites/{token}/accept", json={})
    assert joined.json() == {
        "id": team,
        "name": "Team",
        "personal": False,
        "role": "editor",
    }
    assert (await bob.post(f"/workspaces/{team}/drawings", json={})).status_code == 201
    # Used up.
    assert (await bob.get(f"/invites/{token}")).status_code == 404
    assert (await owner.get(f"/workspaces/{team}/invites")).json() == []


async def test_only_the_invited_email_can_accept(make_client):
    owner, team = await owner_and_team(make_client)
    token = (await invite(owner, team)).json()["token"]
    eve = await make_client()
    await signup(eve, "eve@example.com", "Eve")

    stolen = await eve.post(f"/invites/{token}/accept", json={})

    assert stolen.status_code == 403
    assert "bob@example.com" in stolen.json()["detail"]
    assert [w["name"] for w in (await eve.get("/workspaces")).json()] == ["Personal"]
    assert (await eve.get(f"/invites/{token}")).status_code == 200


async def test_reinviting_replaces_the_old_link_and_revoking_kills_it(make_client):
    owner, team = await owner_and_team(make_client)
    bob = await make_client()
    await signup(bob, "bob@example.com", "Bob")

    first = (await invite(owner, team)).json()
    second = (await invite(owner, team, role="viewer")).json()
    assert (await bob.get(f"/invites/{first['token']}")).status_code == 404
    assert (await bob.get(f"/invites/{second['token']}")).json()["role"] == "viewer"

    revoked = await owner.delete(
        f"/workspaces/{team}/invites/{second['id']}", headers=JSON
    )
    assert revoked.status_code == 204
    assert (await bob.get(f"/invites/{second['token']}")).status_code == 404
    again = await owner.delete(
        f"/workspaces/{team}/invites/{second['id']}", headers=JSON
    )
    assert again.status_code == 404


async def test_who_can_invite_whom(app, make_client):
    owner, team = await owner_and_team(make_client)
    admin, editor = await make_client(), await make_client()
    admin_user = await signup(admin, "alice@example.com", "Alice")
    editor_user = await signup(editor, "ed@example.com", "Ed")
    await add_member(app, team, admin_user["id"], "admin")
    await add_member(app, team, editor_user["id"], "editor")

    assert (await invite(editor, team)).status_code == 403
    assert (await editor.get(f"/workspaces/{team}/invites")).status_code == 403
    assert (await invite(admin, team, role="owner")).status_code == 403
    assert (await invite(admin, team, role="admin")).status_code == 201
    assert (await invite(owner, team, role="owner")).status_code == 201
    already = await invite(owner, team, email="ED@example.com")
    assert already.status_code == 409


async def test_personal_workspaces_cannot_be_shared(client):
    await signup(client)

    response = await invite(client, await personal_workspace_id(client))

    assert response.status_code == 403


async def test_expired_invites_do_nothing(app, make_client):
    owner, team = await owner_and_team(make_client)
    token = (await invite(owner, team)).json()["token"]
    await app.state.db.invites.update_many(
        {}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(minutes=1)}}
    )
    bob = await make_client()
    await signup(bob, "bob@example.com", "Bob")

    assert (await bob.get(f"/invites/{token}")).status_code == 404
    assert (await bob.post(f"/invites/{token}/accept", json={})).status_code == 404
    assert (await owner.get(f"/workspaces/{team}/invites")).json() == []


async def test_accepting_as_a_member_keeps_the_existing_role(
    app, make_client, mailer
):
    owner, team = await owner_and_team(make_client)
    token = (await invite(owner, team, role="viewer")).json()["token"]
    bob = await make_client()
    bob_user = await signup(bob, "bob@example.com", "Bob")
    await confirm_email(bob, mailer, "bob@example.com")
    # Added another way after the invite went out.
    await add_member(app, team, bob_user["id"], "admin")

    joined = await bob.post(f"/invites/{token}/accept", json={})

    assert joined.json()["role"] == "admin"


async def test_invites_need_a_login(make_client):
    owner, team = await owner_and_team(make_client)
    token = (await invite(owner, team)).json()["token"]
    anonymous = await make_client()

    assert (await anonymous.get(f"/invites/{token}")).status_code == 401
