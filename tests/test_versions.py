from datetime import UTC, datetime, timedelta

from bson import ObjectId

from function.versions import maybe_auto_snapshot
from tests.helpers import JSON, element, personal_workspace_id, signup
from tests.test_trash import setup_team


async def create(client) -> str:
    workspace_id = await personal_workspace_id(client)
    response = await client.post(f"/workspaces/{workspace_id}/drawings", json={})
    return response.json()["id"]


async def save(client, drawing_id: str, elements: list[dict]) -> None:
    response = await client.put(
        f"/drawings/{drawing_id}/scene", json={"elements": elements}
    )
    assert response.status_code == 200


async def versions_of(client, drawing_id: str) -> list[dict]:
    response = await client.get(f"/drawings/{drawing_id}/versions")
    assert response.status_code == 200
    return response.json()


async def make_snapshot_due(app, drawing_id: str) -> None:
    """Moves the last automatic snapshot back past the interval."""
    await app.state.db.scenes.update_one(
        {"_id": ObjectId(drawing_id)},
        {"$set": {"snapshot_at": datetime.now(UTC) - timedelta(minutes=11)}},
    )


async def test_auto_snapshot_keeps_the_scene_a_save_replaces(app, client):
    await signup(client)
    drawing_id = await create(client)

    # The canvas was empty before this save: nothing to keep, but the clock
    # starts, so the next save doesn't snapshot either.
    await save(client, drawing_id, [element("a", index="a0")])
    await save(
        client,
        drawing_id,
        [element("b", index="a1"), element("gone", index="a2", deleted=True)],
    )
    assert await versions_of(client, drawing_id) == []

    await make_snapshot_due(app, drawing_id)
    await save(client, drawing_id, [element("c", index="a3")])
    # Saved again straight away: the snapshot isn't due yet.
    await save(client, drawing_id, [element("d", index="a4")])

    [summary] = await versions_of(client, drawing_id)
    assert summary["name"] is None
    assert summary["created_by"]["name"] == "Ada"
    assert summary["element_count"] == 2
    expires_at = datetime.fromisoformat(summary["expires_at"])
    assert timedelta(days=29) < expires_at - datetime.now(UTC) < timedelta(days=31)
    assert "elements" not in summary

    version = (
        await client.get(f"/drawings/{drawing_id}/versions/{summary['id']}")
    ).json()
    # What the drawing held before "c" arrived, without tombstones.
    assert [e["id"] for e in version["elements"]] == ["a", "b"]
    assert version["app_state"] == {}


async def test_saves_landing_together_take_one_snapshot(app, client):
    await signup(client)
    drawing_id = await create(client)
    await save(client, drawing_id, [element("a")])
    await make_snapshot_due(app, drawing_id)

    db = app.state.db
    drawing = await db.drawings.find_one({"_id": ObjectId(drawing_id)})
    scene = await db.scenes.find_one({"_id": ObjectId(drawing_id)})
    now = datetime.now(UTC)
    # Both saves read the scene before either claimed the snapshot.
    await maybe_auto_snapshot(db, app.state.settings, drawing, scene, now)
    await maybe_auto_snapshot(db, app.state.settings, drawing, scene, now)

    assert len(await versions_of(client, drawing_id)) == 1


async def test_named_versions_and_checkpoints(client):
    await signup(client)
    drawing_id = await create(client)
    await save(client, drawing_id, [element("a")])

    named = await client.post(
        f"/drawings/{drawing_id}/versions", json={"name": "  First draft "}
    )
    assert named.status_code == 201
    assert named.json()["name"] == "First draft"
    assert named.json()["expires_at"] is None

    await save(client, drawing_id, [element("b", index="a1")])
    checkpoint = (await client.post(f"/drawings/{drawing_id}/versions", json={})).json()
    assert checkpoint["name"] is None
    assert checkpoint["expires_at"] is not None
    assert checkpoint["element_count"] == 2

    # Newest first.
    listed = await versions_of(client, drawing_id)
    assert [v["id"] for v in listed] == [checkpoint["id"], named.json()["id"]]

    # Naming a checkpoint keeps it for good.
    renamed = await client.patch(
        f"/drawings/{drawing_id}/versions/{checkpoint['id']}",
        json={"name": "Before the redesign"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Before the redesign"
    assert renamed.json()["expires_at"] is None

    blank = await client.patch(
        f"/drawings/{drawing_id}/versions/{checkpoint['id']}", json={"name": " "}
    )
    assert blank.status_code == 422


async def test_roles(app, make_client):
    owner, admin, editor, viewer, _, drawing_id = await setup_team(app, make_client)
    await save(owner, drawing_id, [element("a")])
    base = f"/drawings/{drawing_id}/versions"
    version_id = (await owner.post(base, json={"name": "v1"})).json()["id"]

    # Viewers can look through the history…
    assert (await viewer.get(base)).status_code == 200
    assert (await viewer.get(f"{base}/{version_id}")).status_code == 200
    # …but not add to it or name things in it.
    assert (await viewer.post(base, json={"name": "v2"})).status_code == 403
    patch = await viewer.patch(f"{base}/{version_id}", json={"name": "Mine"})
    assert patch.status_code == 403

    # Editors can, but can't erase history.
    assert (await editor.post(base, json={})).status_code == 201
    patch = await editor.patch(f"{base}/{version_id}", json={"name": "v1 final"})
    assert patch.status_code == 200
    delete = await editor.delete(f"{base}/{version_id}", headers=JSON)
    assert delete.status_code == 403

    delete = await admin.delete(f"{base}/{version_id}", headers=JSON)
    assert delete.status_code == 204
    assert (await owner.get(f"{base}/{version_id}")).status_code == 404


async def test_outsiders_and_wrong_ids_get_404(make_client):
    owner, outsider = await make_client(), await make_client()
    await signup(owner)
    await signup(outsider, email="eve@example.com", name="Eve")
    drawing_id = await create(owner)
    other_id = await create(owner)
    await save(owner, drawing_id, [element("a")])
    version_id = (
        await owner.post(f"/drawings/{drawing_id}/versions", json={"name": "v1"})
    ).json()["id"]

    responses = [
        await outsider.get(f"/drawings/{drawing_id}/versions"),
        await outsider.get(f"/drawings/{drawing_id}/versions/{version_id}"),
        await outsider.post(f"/drawings/{drawing_id}/versions", json={}),
        # A version is only found through its own drawing.
        await owner.get(f"/drawings/{other_id}/versions/{version_id}"),
        await owner.get(f"/drawings/{drawing_id}/versions/not-an-id"),
    ]

    assert [r.status_code for r in responses] == [404] * len(responses)


async def test_deleting_a_drawing_for_good_removes_its_history(app, client):
    await signup(client)
    drawing_id = await create(client)
    await save(client, drawing_id, [element("a")])
    await client.post(f"/drawings/{drawing_id}/versions", json={"name": "v1"})

    await client.delete(f"/drawings/{drawing_id}", headers=JSON)
    response = await client.delete(f"/drawings/{drawing_id}/permanent", headers=JSON)

    assert response.status_code == 204
    remaining = await app.state.db.versions.count_documents(
        {"drawing_id": ObjectId(drawing_id)}
    )
    assert remaining == 0


async def test_duplicates_start_without_history(client):
    await signup(client)
    drawing_id = await create(client)
    await save(client, drawing_id, [element("a")])
    await client.post(f"/drawings/{drawing_id}/versions", json={"name": "v1"})

    copy = await client.post(f"/drawings/{drawing_id}/duplicate", json={})

    assert await versions_of(client, copy.json()["id"]) == []
