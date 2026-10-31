import base64
import struct

from bson import ObjectId

from middleware.auth import SESSION_COOKIE
from tests.helpers import (
    JSON,
    PASSWORD,
    add_member,
    create_workspace,
    personal_workspace_id,
    signup,
)
from utils.storage import avatar_key, file_key

PNG = b"\x89PNG\r\n\x1a\nfake image bytes"
JPEG = b"\xff\xd8\xff\xe0fake image bytes"
WEBP = b"RIFF" + struct.pack("<I", 20) + b"WEBPVP8 fake image bytes"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
NEW_PASSWORD = "a brand new password"


def data_url(data: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode()}"


async def upload_avatar(client, data: bytes, mime_type: str = "image/png"):
    return await client.put(
        "/account/avatar", json={"data_url": data_url(data, mime_type)}
    )


async def login(client, email="ada@example.com", password=PASSWORD):
    return await client.post(
        "/auth/login", json={"email": email, "password": password}
    )


async def test_renaming_shows_everywhere(app, make_client):
    ada, bo = await make_client(), await make_client()
    await signup(ada)
    bo_user = await signup(bo, "bo@example.com", "Bo")
    team = await create_workspace(ada)
    await add_member(app, team["id"], bo_user["id"], "editor")

    response = await bo.patch("/account", json={"name": "  Bo Diddley "})

    assert response.status_code == 200
    assert response.json()["name"] == "Bo Diddley"
    assert (await bo.get("/auth/me")).json()["name"] == "Bo Diddley"
    members = (await ada.get(f"/workspaces/{team['id']}/members")).json()
    assert "Bo Diddley" in {member["name"] for member in members}


async def test_names_cant_be_blank(client):
    await signup(client)

    response = await client.patch("/account", json={"name": "   "})

    assert response.status_code == 422
    assert (await client.get("/auth/me")).json()["name"] == "Ada"


async def test_pictures_are_public(make_client, storage):
    ada, guest = await make_client(), await make_client()
    user = await signup(ada)
    assert user["avatar_version"] is None

    response = await upload_avatar(ada, PNG)

    assert response.status_code == 200
    version = response.json()["avatar_version"]
    assert version is not None
    picture = await guest.get(f"/users/{user['id']}/avatar?v={version}")
    assert picture.status_code == 200
    assert picture.content == PNG
    assert picture.headers["content-type"] == "image/png"
    assert picture.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in picture.headers["content-security-policy"]
    assert picture.headers["cache-control"].startswith("public")
    assert storage.objects[avatar_key(user["id"])][0] == PNG


async def test_pictures_show_with_members_and_editors(app, make_client):
    ada, bo = await make_client(), await make_client()
    await signup(ada)
    bo_user = await signup(bo, "bo@example.com", "Bo")
    team = await create_workspace(ada)
    await add_member(app, team["id"], bo_user["id"], "editor")
    drawing = (
        await bo.post(f"/workspaces/{team['id']}/drawings", json={})
    ).json()

    version = (await upload_avatar(bo, JPEG, "image/jpeg")).json()["avatar_version"]

    members = (await ada.get(f"/workspaces/{team['id']}/members")).json()
    assert {m["name"]: m["avatar_version"] for m in members} == {
        "Ada": None,
        "Bo": version,
    }
    listed = (await ada.get(f"/workspaces/{team['id']}/drawings")).json()
    assert listed[0]["id"] == drawing["id"]
    assert listed[0]["updated_by"]["avatar_version"] == version


async def test_only_real_png_jpeg_or_webp_pictures(client):
    await signup(client)

    responses = {
        "webp": await upload_avatar(client, WEBP, "image/webp"),
        "svg": await upload_avatar(client, SVG, "image/svg+xml"),
        "gif": await upload_avatar(client, b"GIF89a....", "image/gif"),
        "svg as png": await upload_avatar(client, SVG, "image/png"),
        "png as jpeg": await upload_avatar(client, PNG, "image/jpeg"),
        "too large": await upload_avatar(client, PNG + bytes(600 * 1024)),
    }

    assert {name: r.status_code for name, r in responses.items()} == {
        "webp": 200,
        "svg": 422,
        "gif": 422,
        "svg as png": 422,
        "png as jpeg": 422,
        "too large": 413,
    }
    assert responses["too large"].json()["detail"] == (
        "Files can be at most 512 KB"
    )


async def test_removing_a_picture_takes_it_offline(make_client, storage):
    ada, guest = await make_client(), await make_client()
    user = await signup(ada)
    await upload_avatar(ada, PNG)

    response = await ada.delete("/account/avatar", headers=JSON)

    assert response.status_code == 200
    assert response.json()["avatar_version"] is None
    assert (await guest.get(f"/users/{user['id']}/avatar")).status_code == 404
    assert avatar_key(user["id"]) not in storage.objects


async def test_picture_urls_need_a_valid_user_id(client):
    unknown = await client.get(f"/users/{ObjectId()}/avatar")
    malformed = await client.get("/users/..%2Fdrawings/avatar")

    assert unknown.status_code == 404
    assert malformed.status_code in (404, 422)


async def test_changing_the_password_logs_out_other_devices(make_client, mailer):
    laptop, phone = await make_client(), await make_client()
    await signup(laptop)
    await login(phone)
    old_cookie = laptop.cookies[SESSION_COOKIE]

    response = await laptop.post(
        "/account/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 204
    # This device carries on with a new session; the old token is dead.
    assert laptop.cookies[SESSION_COOKIE] != old_cookie
    assert (await laptop.get("/auth/me")).status_code == 200
    assert (await phone.get("/auth/me")).status_code == 401
    stale = await make_client()
    stale.cookies.set(SESSION_COOKIE, old_cookie)
    assert (await stale.get("/auth/me")).status_code == 401

    assert (await login(stale)).status_code == 401
    assert (await login(stale, password=NEW_PASSWORD)).status_code == 200
    [notice] = [e for e in mailer.to("ada@example.com") if "changed" in e.subject]
    assert "/forgot-password" in notice.text


async def test_changing_the_password_needs_the_current_one(client):
    await signup(client)

    wrong = await client.post(
        "/account/password",
        json={"current_password": "not it", "new_password": NEW_PASSWORD},
    )
    short = await client.post(
        "/account/password",
        json={"current_password": PASSWORD, "new_password": "short"},
    )

    assert wrong.status_code == 403
    assert wrong.json()["detail"] == "That's not your current password"
    assert short.status_code == 422
    assert (await login(client)).status_code == 200


async def test_wrong_passwords_are_limited(client):
    await signup(client)
    for _ in range(10):
        response = await client.post(
            "/account/password",
            json={"current_password": "guess", "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 403

    # Even the right one, until the limit passes.
    response = await client.post(
        "/account/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 429


async def test_sessions_list_this_device_first(make_client):
    laptop, phone = await make_client(), await make_client()
    await signup(laptop)
    phone.headers["User-Agent"] = "Mozilla/5.0 (iPhone) Mobile Safari"
    await login(phone)

    sessions = (await laptop.get("/account/sessions")).json()

    assert len(sessions) == 2
    assert [s["current"] for s in sessions] == [True, False]
    assert sessions[0]["user_agent"].startswith("python-httpx")
    assert sessions[1]["user_agent"] == "Mozilla/5.0 (iPhone) Mobile Safari"
    assert sessions[1]["ip"] == "127.0.0.1"
    assert {"id", "created_at", "last_used_at"} <= sessions[0].keys()


async def test_logging_out_another_device(make_client):
    laptop, phone, tablet = [await make_client() for _ in range(3)]
    await signup(laptop)
    await login(phone)
    await login(tablet)
    sessions = (await laptop.get("/account/sessions")).json()
    others = [s["id"] for s in sessions if not s["current"]]

    response = await laptop.delete(f"/account/sessions/{others[0]}", headers=JSON)

    assert response.status_code == 204
    statuses = [(await c.get("/auth/me")).status_code for c in (phone, tablet)]
    assert sorted(statuses) == [200, 401]

    response = await laptop.delete("/account/sessions", headers=JSON)

    assert response.status_code == 204
    assert (await phone.get("/auth/me")).status_code == 401
    assert (await tablet.get("/auth/me")).status_code == 401
    assert (await laptop.get("/auth/me")).status_code == 200
    assert len((await laptop.get("/account/sessions")).json()) == 1


async def test_other_peoples_sessions_cant_be_ended(make_client):
    ada, bo = await make_client(), await make_client()
    await signup(ada)
    await signup(bo, "bo@example.com", "Bo")
    [bos_session] = (await bo.get("/account/sessions")).json()

    response = await ada.delete(
        f"/account/sessions/{bos_session['id']}", headers=JSON
    )

    assert response.status_code == 404
    assert (await bo.get("/auth/me")).status_code == 200


async def test_deleting_an_account(app, make_client, storage, mailer):
    ada, bo = await make_client(), await make_client()
    await signup(ada)
    bo_user = await signup(bo, "bo@example.com", "Bo")
    db = app.state.db

    # Bo's personal drawing, with an image.
    personal = await personal_workspace_id(bo)
    mine = (await bo.post(f"/workspaces/{personal}/drawings", json={})).json()
    await bo.put(
        f"/drawings/{mine['id']}/files/pic", json={"data_url": data_url(PNG)}
    )
    # A workspace only Bo is in.
    solo = await create_workspace(bo, "Solo")
    await bo.post(f"/workspaces/{solo['id']}/drawings", json={})
    # Ada's team, where Bo edits a drawing.
    team = await create_workspace(ada)
    await add_member(app, team["id"], bo_user["id"], "editor")
    shared = (await bo.post(f"/workspaces/{team['id']}/drawings", json={})).json()
    await upload_avatar(bo, PNG)

    response = await bo.post("/account/delete", json={"password": PASSWORD})

    assert response.status_code == 204
    assert (await bo.get("/auth/me")).status_code == 401
    assert (await login(bo, "bo@example.com")).status_code == 401
    bo_id = ObjectId(bo_user["id"])
    assert await db.users.find_one({"_id": bo_id}) is None
    assert await db.sessions.count_documents({"user_id": bo_id}) == 0
    assert await db.memberships.count_documents({"user_id": bo_id}) == 0
    # Workspaces nobody else was in are gone, with their drawings and files.
    for workspace_id in (personal, solo["id"]):
        assert await db.workspaces.find_one({"_id": ObjectId(workspace_id)}) is None
        assert await db.drawings.count_documents(
            {"workspace_id": ObjectId(workspace_id)}
        ) == 0
    assert file_key(mine["id"], "pic") not in storage.objects
    assert avatar_key(bo_user["id"]) not in storage.objects
    # The team keeps Bo's drawing, now by nobody in particular.
    listed = (await ada.get(f"/workspaces/{team['id']}/drawings")).json()
    assert [(d["id"], d["updated_by"]) for d in listed] == [(shared["id"], None)]
    members = (await ada.get(f"/workspaces/{team['id']}/members")).json()
    assert [m["name"] for m in members] == ["Ada"]
    assert any("deleted" in e.subject for e in mailer.to("bo@example.com"))

    # The address is free again.
    again = await signup(await make_client(), "bo@example.com", "Bo")
    assert again["id"] != bo_user["id"]


async def test_deleting_an_account_needs_the_password(make_client):
    ada, bo = await make_client(), await make_client()
    await signup(ada)
    await signup(bo, "bo@example.com", "Bo")

    response = await bo.post("/account/delete", json={"password": "not it"})

    assert response.status_code == 403
    assert (await bo.get("/auth/me")).status_code == 200


async def test_the_site_owner_cant_delete_their_account(client):
    await signup(client)

    response = await client.post("/account/delete", json={"password": PASSWORD})

    assert response.status_code == 409
    assert "site owner" in response.json()["detail"]
    assert (await client.get("/auth/me")).status_code == 200


async def test_a_shared_workspace_cant_lose_its_last_owner(app, make_client):
    ada, bo = await make_client(), await make_client()
    ada_user = await signup(ada)
    await signup(bo, "bo@example.com", "Bo")
    team = await create_workspace(bo, "Bo's team")
    await add_member(app, team["id"], ada_user["id"], "editor")

    blocked = await bo.post("/account/delete", json={"password": PASSWORD})

    assert blocked.status_code == 409
    assert "Bo's team" in blocked.json()["detail"]
    assert (await bo.get("/auth/me")).status_code == 200

    await app.state.db.memberships.update_one(
        {"workspace_id": ObjectId(team["id"]), "user_id": ObjectId(ada_user["id"])},
        {"$set": {"role": "owner"}},
    )
    allowed = await bo.post("/account/delete", json={"password": PASSWORD})

    assert allowed.status_code == 204
    # Ada now owns the team on her own, drawings and all.
    workspaces = (await ada.get("/workspaces")).json()
    assert "Bo's team" in {w["name"] for w in workspaces}
