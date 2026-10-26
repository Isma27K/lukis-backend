from datetime import UTC, datetime

from bson import ObjectId

import function.scene as scene_module
from tests.helpers import element, personal_workspace_id, signup

# The API refuses non-JSON mutations, and httpx sends no content type on DELETE.
JSON = {"Content-Type": "application/json"}


async def create(client, workspace_id: str, title: str | None = None) -> dict:
    body = {} if title is None else {"title": title}
    response = await client.post(f"/workspaces/{workspace_id}/drawings", json=body)
    assert response.status_code == 201
    return response.json()


async def save(client, drawing_id: str, elements: list[dict], app_state=None):
    body = {"elements": elements}
    if app_state is not None:
        body["app_state"] = app_state
    return await client.put(f"/drawings/{drawing_id}/scene", json=body)


async def scene_of(client, drawing_id: str) -> dict:
    response = await client.get(f"/drawings/{drawing_id}/scene")
    assert response.status_code == 200
    return response.json()


async def test_lists_personal_workspace(client):
    await signup(client)

    workspaces = (await client.get("/workspaces")).json()

    assert [(w["name"], w["personal"], w["role"]) for w in workspaces] == [
        ("Personal", True, "owner")
    ]


async def test_create_list_rename_duplicate_delete(client):
    await signup(client)
    workspace_id = await personal_workspace_id(client)

    first = await create(client, workspace_id)
    second = await create(client, workspace_id, "  Roadmap  ")
    assert first["title"] == "Untitled"
    assert second["title"] == "Roadmap"
    assert second["role"] == "owner"

    # Saving bumps a drawing to the top of the list.
    await save(client, first["id"], [element("a")])
    listed = (await client.get(f"/workspaces/{workspace_id}/drawings")).json()
    assert [d["id"] for d in listed] == [first["id"], second["id"]]

    renamed = await client.patch(f"/drawings/{first['id']}", json={"title": "Plan"})
    assert renamed.json()["title"] == "Plan"
    blank = await client.patch(f"/drawings/{first['id']}", json={"title": "  "})
    assert blank.status_code == 422

    copy = (await client.post(f"/drawings/{first['id']}/duplicate", json={})).json()
    assert copy["title"] == "Copy of Plan"
    assert [e["id"] for e in (await scene_of(client, copy["id"]))["elements"]] == ["a"]

    deleted = await client.delete(f"/drawings/{first['id']}", headers=JSON)
    assert deleted.status_code == 204
    assert (await client.get(f"/drawings/{first['id']}")).status_code == 404
    assert (await client.get(f"/drawings/{first['id']}/scene")).status_code == 404
    listed = (await client.get(f"/workspaces/{workspace_id}/drawings")).json()
    assert first["id"] not in [d["id"] for d in listed]


async def test_scene_round_trip_and_partial_saves(client):
    await signup(client)
    drawing = await create(client, await personal_workspace_id(client))

    first = await save(
        client,
        drawing["id"],
        [element("a", index="a0", strokeColor="#1e1e1e"), element("b", index="a1")],
        app_state={"viewBackgroundColor": "#fff"},
    )
    # Only the changed element; "b" must survive.
    second = await save(client, drawing["id"], [element("a", version=2, index="a0")])
    stale = await save(
        client, drawing["id"], [element("b", version=0, index="a1", lost=True)]
    )

    assert [r.json()["rev"] for r in (first, second, stale)] == [1, 2, 3]
    scene = await scene_of(client, drawing["id"])
    assert scene["rev"] == 3
    assert scene["app_state"] == {"viewBackgroundColor": "#fff"}
    a, b = scene["elements"]
    assert (a["id"], a["version"]) == ("a", 2)
    assert "lost" not in b


async def test_unknown_element_fields_are_stored_untouched(client):
    await signup(client)
    drawing = await create(client, await personal_workspace_id(client))
    sent = element("a", type="rectangle", points=[[0, 0], [1, 2]], customData={"x": 1})

    await save(client, drawing["id"], [sent])

    assert (await scene_of(client, drawing["id"]))["elements"] == [sent]


async def test_save_retries_when_someone_else_saves_in_between(client, monkeypatch):
    await signup(client)
    drawing = await create(client, await personal_workspace_id(client))
    original_load = scene_module.load_scene
    raced = False

    async def load_then_race(db, drawing_doc):
        nonlocal raced
        scene = await original_load(db, drawing_doc)
        if not raced:
            raced = True
            await db.scenes.update_one(
                {"_id": scene["_id"]},
                {"$push": {"elements": element("theirs")}, "$inc": {"rev": 1}},
            )
        return scene

    monkeypatch.setattr(scene_module, "load_scene", load_then_race)
    response = await save(client, drawing["id"], [element("mine", index="a1")])

    assert response.status_code == 200
    assert response.json()["rev"] == 2
    ids = {e["id"] for e in (await scene_of(client, drawing["id"]))["elements"]}
    assert ids == {"mine", "theirs"}


async def test_save_gives_up_with_409_when_always_racing(client, monkeypatch):
    await signup(client)
    drawing = await create(client, await personal_workspace_id(client))
    original_load = scene_module.load_scene

    async def always_race(db, drawing_doc):
        scene = await original_load(db, drawing_doc)
        await db.scenes.update_one({"_id": scene["_id"]}, {"$inc": {"rev": 1}})
        return scene

    monkeypatch.setattr(scene_module, "load_scene", always_race)

    assert (await save(client, drawing["id"], [element("a")])).status_code == 409


async def test_outsiders_get_404_everywhere(make_client):
    owner, outsider = await make_client(), await make_client()
    await signup(owner)
    await signup(outsider, email="eve@example.com", name="Eve")
    workspace_id = await personal_workspace_id(owner)
    drawing = await create(owner, workspace_id)

    responses = [
        await outsider.get(f"/workspaces/{workspace_id}/drawings"),
        await outsider.post(f"/workspaces/{workspace_id}/drawings", json={}),
        await outsider.get(f"/drawings/{drawing['id']}"),
        await outsider.get(f"/drawings/{drawing['id']}/scene"),
        await save(outsider, drawing["id"], [element("a")]),
        await outsider.patch(f"/drawings/{drawing['id']}", json={"title": "Mine"}),
        await outsider.post(f"/drawings/{drawing['id']}/duplicate", json={}),
        await outsider.delete(f"/drawings/{drawing['id']}", headers=JSON),
    ]

    assert [r.status_code for r in responses] == [404] * len(responses)


async def test_viewers_can_read_but_not_change(app, make_client):
    owner, viewer = await make_client(), await make_client()
    await signup(owner)
    viewer_user = await signup(viewer, email="vic@example.com", name="Vic")
    workspace_id = await personal_workspace_id(owner)
    drawing = await create(owner, workspace_id)
    await app.state.db.memberships.insert_one(
        {
            "_id": ObjectId(),
            "workspace_id": ObjectId(workspace_id),
            "user_id": ObjectId(viewer_user["id"]),
            "role": "viewer",
            "created_at": datetime.now(UTC),
        }
    )

    details = await viewer.get(f"/drawings/{drawing['id']}")
    assert details.status_code == 200
    assert details.json()["role"] == "viewer"
    assert (await viewer.get(f"/drawings/{drawing['id']}/scene")).status_code == 200
    assert (await viewer.get(f"/workspaces/{workspace_id}/drawings")).status_code == 200

    changes = [
        await viewer.post(f"/workspaces/{workspace_id}/drawings", json={}),
        await save(viewer, drawing["id"], [element("a")]),
        await viewer.patch(f"/drawings/{drawing['id']}", json={"title": "Mine"}),
        await viewer.post(f"/drawings/{drawing['id']}/duplicate", json={}),
        await viewer.delete(f"/drawings/{drawing['id']}", headers=JSON),
    ]
    assert [r.status_code for r in changes] == [403] * len(changes)


async def test_malformed_ids_are_404(client):
    await signup(client)

    assert (await client.get("/drawings/not-an-id")).status_code == 404
    assert (await client.get("/workspaces/nope/drawings")).status_code == 404
