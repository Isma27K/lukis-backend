from bson import ObjectId

from tests.helpers import JSON, add_member, element, personal_workspace_id, signup


async def setup_team(app, make_client):
    """An owner's workspace with one drawing, plus an admin, an editor and a
    viewer in it."""
    owner = await make_client()
    await signup(owner)
    workspace_id = await personal_workspace_id(owner)
    members = []
    for name, role in [("Al", "admin"), ("Ed", "editor"), ("Vic", "viewer")]:
        member = await make_client()
        user = await signup(member, email=f"{name.lower()}@example.com", name=name)
        await add_member(app, workspace_id, user["id"], role)
        members.append(member)
    response = await owner.post(
        f"/workspaces/{workspace_id}/drawings", json={"title": "Plan"}
    )
    return owner, *members, workspace_id, response.json()["id"]


async def test_trash_and_restore(app, make_client):
    owner, admin, editor, _, workspace_id, drawing_id = await setup_team(
        app, make_client
    )
    await editor.put(f"/drawings/{drawing_id}/scene", json={"elements": [element("a")]})

    assert (await admin.delete(f"/drawings/{drawing_id}", headers=JSON)).status_code == 204

    trash = (await editor.get(f"/workspaces/{workspace_id}/trash")).json()
    assert [(d["id"], d["title"], d["deleted_by"]) for d in trash] == [
        (drawing_id, "Plan", "Al")
    ]
    assert trash[0]["deleted_at"]
    # Trashed drawings are out of reach until restored.
    assert (await owner.get(f"/drawings/{drawing_id}/scene")).status_code == 404

    restored = await admin.post(f"/drawings/{drawing_id}/restore", json={})
    assert restored.status_code == 200
    assert restored.json()["title"] == "Plan"
    assert (await owner.get(f"/workspaces/{workspace_id}/trash")).json() == []
    scene = (await owner.get(f"/drawings/{drawing_id}/scene")).json()
    assert [e["id"] for e in scene["elements"]] == ["a"]
    # Only drawings in the trash can be restored.
    again = await owner.post(f"/drawings/{drawing_id}/restore", json={})
    assert again.status_code == 404


async def test_only_admins_and_owners_delete_or_restore(app, make_client):
    owner, _, editor, viewer, _, drawing_id = await setup_team(app, make_client)

    for member in (editor, viewer):
        response = await member.delete(f"/drawings/{drawing_id}", headers=JSON)
        assert response.status_code == 403
    assert (await owner.get(f"/drawings/{drawing_id}")).status_code == 200

    await owner.delete(f"/drawings/{drawing_id}", headers=JSON)
    for member in (editor, viewer):
        responses = [
            await member.post(f"/drawings/{drawing_id}/restore", json={}),
            await member.delete(f"/drawings/{drawing_id}/permanent", headers=JSON),
        ]
        assert [r.status_code for r in responses] == [403, 403]


async def test_deleting_for_good(app, make_client):
    owner, admin, _, _, workspace_id, drawing_id = await setup_team(
        app, make_client
    )

    # It has to be in the trash first.
    not_trashed = await owner.delete(f"/drawings/{drawing_id}/permanent", headers=JSON)
    assert not_trashed.status_code == 404

    await admin.delete(f"/drawings/{drawing_id}", headers=JSON)
    by_owner = await owner.delete(f"/drawings/{drawing_id}/permanent", headers=JSON)
    assert by_owner.status_code == 204
    assert (await owner.get(f"/workspaces/{workspace_id}/trash")).json() == []
    assert await app.state.db.drawings.find_one({"_id": ObjectId(drawing_id)}) is None
    assert await app.state.db.scenes.find_one({"_id": ObjectId(drawing_id)}) is None
    assert (await owner.post(f"/drawings/{drawing_id}/restore", json={})).status_code == 404


async def test_purge_keeps_drawing_when_storage_is_down(app, make_client, storage):
    owner, *_, workspace_id, drawing_id = await setup_team(app, make_client)
    await owner.delete(f"/drawings/{drawing_id}", headers=JSON)
    storage.down = True

    response = await owner.delete(f"/drawings/{drawing_id}/permanent", headers=JSON)

    assert response.status_code == 503
    trash = (await owner.get(f"/workspaces/{workspace_id}/trash")).json()
    assert [d["id"] for d in trash] == [drawing_id]


async def test_outsiders_cant_see_the_trash(make_client):
    owner, outsider = await make_client(), await make_client()
    await signup(owner)
    await signup(outsider, email="eve@example.com", name="Eve")
    workspace_id = await personal_workspace_id(owner)
    drawing = (await owner.post(f"/workspaces/{workspace_id}/drawings", json={})).json()
    await owner.delete(f"/drawings/{drawing['id']}", headers=JSON)

    responses = [
        await outsider.get(f"/workspaces/{workspace_id}/trash"),
        await outsider.post(f"/drawings/{drawing['id']}/restore", json={}),
        await outsider.delete(f"/drawings/{drawing['id']}/permanent", headers=JSON),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404]
