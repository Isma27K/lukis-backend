from datetime import UTC, datetime, timedelta

from bson import ObjectId

from function.rate_limit import SHARE_UNLOCK_PER_IP
from function.share import GRANT_COOKIE_PREFIX
from tests.helpers import (
    JSON,
    create_workspace,
    element,
    personal_workspace_id,
    signup,
)
from tests.test_files import PNG, data_url
from tests.test_trash import setup_team


async def create(client, workspace_id: str | None = None) -> str:
    workspace_id = workspace_id or await personal_workspace_id(client)
    response = await client.post(f"/workspaces/{workspace_id}/drawings", json={})
    return response.json()["id"]


async def share(client, drawing_id: str, **settings) -> dict:
    body = {"enabled": True, "access": "view", **settings}
    response = await client.put(f"/drawings/{drawing_id}/share", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def owner_with_drawing(make_client):
    owner = await make_client()
    await signup(owner)
    drawing_id = await create(owner)
    await owner.put(f"/drawings/{drawing_id}/scene", json={"elements": [element("a")]})
    return owner, drawing_id


def has_grant(client) -> bool:
    return any(name.startswith(GRANT_COOKIE_PREFIX) for name in client.cookies)


async def test_editors_manage_links_and_viewers_never_see_them(app, make_client):
    owner, _, editor, viewer, _, drawing_id = await setup_team(app, make_client)
    path = f"/drawings/{drawing_id}/share"
    assert (await editor.get(path)).json() is None

    link = await share(editor, drawing_id, access="edit")
    assert link["enabled"] is True
    assert link["access"] == "edit"
    assert link["has_password"] is False
    assert link["expires_at"] is None
    assert (await owner.get(path)).json()["token"] == link["token"]

    # An edit link would let a viewer edit, so they can't even see it.
    responses = [
        await viewer.get(path),
        await viewer.put(path, json={"enabled": False}),
        await viewer.post(f"{path}/reset", json={}),
    ]
    assert [r.status_code for r in responses] == [403, 403, 403]

    outsider = await make_client()
    await signup(outsider, email="eve@example.com", name="Eve")
    assert (await outsider.get(path)).status_code == 404


async def test_view_links_open_the_drawing_without_an_account(make_client):
    owner, drawing_id = await owner_with_drawing(make_client)
    await owner.put(
        f"/drawings/{drawing_id}/files/img1", json={"data_url": data_url(PNG)}
    )
    token = (await share(owner, drawing_id))["token"]
    guest = await make_client()

    info = (await guest.get(f"/share/{token}")).json()
    assert info == {
        "access": "view",
        "password_required": False,
        "unlocked": True,
        "title": "Untitled",
        "has_thumbnail": False,
        "member_drawing_id": None,
        "viewer_name": None,
    }
    scene = (await guest.get(f"/share/{token}/scene")).json()
    assert [e["id"] for e in scene["elements"]] == ["a"]
    assert (await guest.get(f"/share/{token}/files/img1")).content == PNG

    # Changing anything takes an edit link.
    responses = [
        await guest.put(f"/share/{token}/scene", json={"elements": [element("b")]}),
        await guest.put(
            f"/share/{token}/files/img2", json={"data_url": data_url(PNG)}
        ),
        await guest.delete(f"/share/{token}/thumbnail", headers=JSON),
    ]
    assert [r.status_code for r in responses] == [403, 403, 403]
    # The drawing's own routes still need an account.
    assert (await guest.get(f"/drawings/{drawing_id}/scene")).status_code == 401


async def test_edit_links_let_anyone_save(app, make_client):
    owner, drawing_id = await owner_with_drawing(make_client)
    token = (await share(owner, drawing_id, access="edit"))["token"]
    db = app.state.db

    guest = await make_client()
    saved = await guest.put(
        f"/share/{token}/scene", json={"elements": [element("g", index="a1")]}
    )
    assert saved.status_code == 200
    drawing = await db.drawings.find_one({"_id": ObjectId(drawing_id)})
    # Nobody to credit for an anonymous guest's change.
    assert drawing["updated_by"] is None
    listed = (await owner.get(f"/workspaces/{drawing['workspace_id']}/drawings")).json()
    assert listed[0]["updated_by"] is None

    # A logged-in visitor who isn't a member is credited, and named.
    bob = await make_client()
    bob_user = await signup(bob, email="bob@example.com", name="Bob")
    await bob.put(f"/share/{token}/scene", json={"elements": [element("b", index="a2")]})
    drawing = await db.drawings.find_one({"_id": ObjectId(drawing_id)})
    assert str(drawing["updated_by"]) == bob_user["id"]
    info = (await bob.get(f"/share/{token}")).json()
    assert info["viewer_name"] == "Bob"
    assert info["member_drawing_id"] is None
    # Members are sent to the full editor.
    assert (await owner.get(f"/share/{token}")).json()["member_drawing_id"] == drawing_id

    # Guests' changes still go into the history, without an author.
    await db.scenes.update_one(
        {"_id": ObjectId(drawing_id)},
        {"$set": {"snapshot_at": datetime.now(UTC) - timedelta(hours=1)}},
    )
    await db.drawings.update_one(
        {"_id": ObjectId(drawing_id)}, {"$set": {"updated_by": None}}
    )
    await guest.put(f"/share/{token}/scene", json={"elements": [element("c", index="a3")]})
    [version] = (await owner.get(f"/drawings/{drawing_id}/versions")).json()
    assert version["created_by"] is None
    assert version["element_count"] == 3


async def test_password_protected_links(make_client):
    owner, drawing_id = await owner_with_drawing(make_client)
    link = await share(owner, drawing_id, password="hunter22")
    assert link["has_password"] is True
    token = link["token"]
    guest = await make_client()

    info = (await guest.get(f"/share/{token}")).json()
    assert info["password_required"] is True
    assert info["unlocked"] is False
    # The title stays hidden until the password is in.
    assert info["title"] is None
    assert (await guest.get(f"/share/{token}/scene")).status_code == 401

    wrong = await guest.post(f"/share/{token}/unlock", json={"password": "nope"})
    assert wrong.status_code == 403
    right = await guest.post(f"/share/{token}/unlock", json={"password": "hunter22"})
    assert right.status_code == 204
    assert has_grant(guest)
    assert (await guest.get(f"/share/{token}/scene")).status_code == 200
    assert (await guest.get(f"/share/{token}")).json()["title"] == "Untitled"

    # Other changes leave the password, and those who entered it, alone.
    assert (await share(owner, drawing_id, access="edit"))["has_password"] is True
    assert (await guest.get(f"/share/{token}/scene")).status_code == 200

    # A new password signs everyone out.
    await share(owner, drawing_id, access="edit", password="correct horse")
    assert (await guest.get(f"/share/{token}/scene")).status_code == 401

    # Without one, the link is open again.
    opened = await share(owner, drawing_id, access="edit", password=None)
    assert opened["has_password"] is False
    assert (await guest.get(f"/share/{token}/scene")).status_code == 200

    too_short = await owner.put(
        f"/drawings/{drawing_id}/share", json={"enabled": True, "password": "abc"}
    )
    assert too_short.status_code == 422


async def test_guessing_link_passwords_is_rate_limited(make_client):
    owner, drawing_id = await owner_with_drawing(make_client)
    token = (await share(owner, drawing_id, password="hunter22"))["token"]
    guest = await make_client()

    for _ in range(SHARE_UNLOCK_PER_IP.attempts):
        response = await guest.post(f"/share/{token}/unlock", json={"password": "x"})
        assert response.status_code == 403
    response = await guest.post(f"/share/{token}/unlock", json={"password": "x"})
    assert response.status_code == 429


async def test_turning_off_and_resetting_links(make_client):
    owner, drawing_id = await owner_with_drawing(make_client)
    token = (await share(owner, drawing_id, password="hunter22"))["token"]
    guest = await make_client()
    await guest.post(f"/share/{token}/unlock", json={"password": "hunter22"})

    await share(owner, drawing_id, enabled=False)
    assert (await guest.get(f"/share/{token}")).status_code == 404
    assert (await guest.get(f"/share/{token}/scene")).status_code == 404

    # Back on, it's the same link, still unlocked for this guest.
    assert (await share(owner, drawing_id))["token"] == token
    assert (await guest.get(f"/share/{token}/scene")).status_code == 200

    reset = await owner.post(f"/drawings/{drawing_id}/share/reset", json={})
    new_token = reset.json()["token"]
    assert new_token != token
    assert (await guest.get(f"/share/{token}/scene")).status_code == 404
    # A new link has to be unlocked again.
    assert (await guest.get(f"/share/{new_token}/scene")).status_code == 401


async def test_links_stop_working_with_their_drawing(app, make_client):
    owner, drawing_id = await owner_with_drawing(make_client)
    guest = await make_client()

    def scene(token):
        return guest.get(f"/share/{token}/scene")

    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    token = (await share(owner, drawing_id, expires_at=past))["token"]
    assert (await scene(token)).status_code == 404
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    await share(owner, drawing_id, expires_at=future)
    assert (await scene(token)).status_code == 200

    await owner.delete(f"/drawings/{drawing_id}", headers=JSON)
    assert (await scene(token)).status_code == 404
    await owner.post(f"/drawings/{drawing_id}/restore", json={})
    assert (await scene(token)).status_code == 200

    # The site owner can turn public links off everywhere.
    off = await owner.patch("/site/settings", json={"public_links": False})
    assert off.json()["public_links"] is False
    assert (await guest.get("/features")).json()["public_links"] is False
    assert (await scene(token)).status_code == 404
    await owner.patch("/site/settings", json={"public_links": True})
    assert (await scene(token)).status_code == 200

    team = await create_workspace(owner)
    team_drawing = await create(owner, team["id"])
    team_token = (await share(owner, team_drawing))["token"]
    assert (await scene(team_token)).status_code == 200
    await owner.delete(f"/workspaces/{team['id']}", headers=JSON)
    assert (await scene(team_token)).status_code == 404


async def test_deleting_a_drawing_for_good_removes_its_link(app, make_client):
    owner, drawing_id = await owner_with_drawing(make_client)
    token = (await share(owner, drawing_id, password="hunter22"))["token"]
    guest = await make_client()
    await guest.post(f"/share/{token}/unlock", json={"password": "hunter22"})
    db = app.state.db
    assert await db.share_grants.count_documents({}) == 1

    await owner.delete(f"/drawings/{drawing_id}", headers=JSON)
    await owner.delete(f"/drawings/{drawing_id}/permanent", headers=JSON)

    assert await db.share_links.count_documents({}) == 0
    assert await db.share_grants.count_documents({}) == 0


async def test_duplicates_are_not_shared(make_client):
    owner, drawing_id = await owner_with_drawing(make_client)
    await share(owner, drawing_id)

    copy = (await owner.post(f"/drawings/{drawing_id}/duplicate", json={})).json()

    assert (await owner.get(f"/drawings/{copy['id']}/share")).json() is None
