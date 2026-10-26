from tests.helpers import (
    JSON,
    add_member,
    create_workspace,
    personal_workspace_id,
    signup,
)


async def team_with(app, make_client, **roles: str):
    """Olga owns a "Team" workspace; each keyword adds a member with that role.

    Returns the workspace id, a client per person and their user records.
    """
    clients = {"olga": await make_client()}
    users = {"olga": await signup(clients["olga"], "olga@example.com", "Olga")}
    team = await create_workspace(clients["olga"])
    for name, role in roles.items():
        clients[name] = await make_client()
        users[name] = await signup(clients[name], f"{name}@example.com", name.title())
        await add_member(app, team["id"], users[name]["id"], role)
    return team["id"], clients, users


async def set_role(client, workspace_id, user_id, role):
    return await client.patch(
        f"/workspaces/{workspace_id}/members/{user_id}", json={"role": role}
    )


async def remove(client, workspace_id, user_id):
    return await client.delete(
        f"/workspaces/{workspace_id}/members/{user_id}", headers=JSON
    )


async def test_create_and_list_workspaces(client):
    await signup(client)

    created = await create_workspace(client, "  Design  ")
    blank = await client.post("/workspaces", json={"name": " "})

    assert (created["name"], created["personal"], created["role"]) == (
        "Design",
        False,
        "owner",
    )
    assert blank.status_code == 422
    names = [w["name"] for w in (await client.get("/workspaces")).json()]
    assert names == ["Personal", "Design"]


async def test_renaming_needs_admin(app, make_client):
    team, c, _ = await team_with(app, make_client, alice="admin", ed="editor")

    by_admin = await c["alice"].patch(f"/workspaces/{team}", json={"name": "Crew"})
    by_editor = await c["ed"].patch(f"/workspaces/{team}", json={"name": "Mine"})

    assert by_admin.json()["name"] == "Crew"
    assert by_editor.status_code == 403


async def test_deleting_a_workspace_cuts_everyone_off(app, make_client):
    team, c, _ = await team_with(app, make_client, alice="admin", ed="editor")
    drawing = (
        await c["ed"].post(f"/workspaces/{team}/drawings", json={"title": "Plan"})
    ).json()

    by_admin = await c["alice"].delete(f"/workspaces/{team}", headers=JSON)
    by_owner = await c["olga"].delete(f"/workspaces/{team}", headers=JSON)

    assert by_admin.status_code == 403
    assert by_owner.status_code == 204

    assert (await c["ed"].get(f"/workspaces/{team}/members")).status_code == 404
    assert (await c["ed"].get(f"/drawings/{drawing['id']}")).status_code == 404
    assert [w["name"] for w in (await c["ed"].get("/workspaces")).json()] == [
        "Personal"
    ]


async def test_personal_workspace_cannot_be_deleted_or_left(client):
    user = await signup(client)
    personal = await personal_workspace_id(client)

    deleted = await client.delete(f"/workspaces/{personal}", headers=JSON)
    left = await remove(client, personal, user["id"])

    assert deleted.status_code == 403
    assert left.status_code == 409


async def test_members_are_listed_by_role(app, make_client):
    team, c, _ = await team_with(
        app, make_client, vic="viewer", ed="editor", alice="admin"
    )

    members = (await c["vic"].get(f"/workspaces/{team}/members")).json()

    assert [(m["name"], m["role"]) for m in members] == [
        ("Olga", "owner"),
        ("Alice", "admin"),
        ("Ed", "editor"),
        ("Vic", "viewer"),
    ]
    assert members[0]["email"] == "olga@example.com"


async def test_a_workspace_always_keeps_an_owner(app, make_client):
    team, c, u = await team_with(app, make_client, alice="admin")
    olga, alice = u["olga"]["id"], u["alice"]["id"]

    assert (await set_role(c["olga"], team, olga, "editor")).status_code == 409
    assert (await remove(c["olga"], team, olga)).status_code == 409

    assert (await set_role(c["olga"], team, alice, "owner")).status_code == 200
    assert (await set_role(c["olga"], team, olga, "editor")).json()["role"] == "editor"


async def test_admins_manage_only_editors_and_viewers(app, make_client):
    team, c, u = await team_with(
        app, make_client, alice="admin", bea="admin", ed="editor", vic="viewer"
    )
    admin = c["alice"]

    assert (await set_role(admin, team, u["ed"]["id"], "admin")).status_code == 200
    assert (await set_role(admin, team, u["vic"]["id"], "owner")).status_code == 403
    assert (await set_role(admin, team, u["bea"]["id"], "viewer")).status_code == 403
    assert (await set_role(admin, team, u["olga"]["id"], "viewer")).status_code == 403
    assert (await remove(admin, team, u["bea"]["id"])).status_code == 403
    assert (await remove(admin, team, u["olga"]["id"])).status_code == 403
    assert (await remove(admin, team, u["vic"]["id"])).status_code == 204


async def test_editors_and_viewers_cannot_manage_members(app, make_client):
    team, c, u = await team_with(app, make_client, ed="editor", vic="viewer")

    assert (await set_role(c["ed"], team, u["vic"]["id"], "editor")).status_code == 403
    assert (await remove(c["ed"], team, u["vic"]["id"])).status_code == 403
    assert (await remove(c["vic"], team, u["ed"]["id"])).status_code == 403


async def test_anyone_can_leave(app, make_client):
    team, c, u = await team_with(app, make_client, vic="viewer")

    assert (await remove(c["vic"], team, u["vic"]["id"])).status_code == 204
    assert (await c["vic"].get(f"/workspaces/{team}/members")).status_code == 404


async def test_unknown_members_are_404(app, make_client):
    team, c, u = await team_with(app, make_client)

    assert (await set_role(c["olga"], team, "not-an-id", "viewer")).status_code == 404
    stranger = await make_client()
    other = await signup(stranger, "zed@example.com", "Zed")
    assert (await remove(c["olga"], team, other["id"])).status_code == 404


async def test_moving_drawings_needs_editor_on_both_sides(app, make_client):
    team, c, u = await team_with(app, make_client, ed="editor")
    ed = c["ed"]
    personal = await personal_workspace_id(ed)
    drawing = (await ed.post(f"/workspaces/{personal}/drawings", json={})).json()
    # A second workspace where Ed can only look.
    gallery = await create_workspace(c["olga"], "Gallery")
    await add_member(app, gallery["id"], u["ed"]["id"], "viewer")

    to_viewer_only = await ed.post(
        f"/drawings/{drawing['id']}/move", json={"workspace_id": gallery["id"]}
    )
    to_unknown = await ed.post(
        f"/drawings/{drawing['id']}/move", json={"workspace_id": "nope"}
    )
    moved = await ed.post(
        f"/drawings/{drawing['id']}/move", json={"workspace_id": team}
    )

    assert to_viewer_only.status_code == 403
    assert to_unknown.status_code == 404
    assert moved.json()["workspace_id"] == team
    team_drawings = (await c["olga"].get(f"/workspaces/{team}/drawings")).json()
    assert [d["id"] for d in team_drawings] == [drawing["id"]]
    assert (await ed.get(f"/workspaces/{personal}/drawings")).json() == []
