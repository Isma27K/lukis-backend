from datetime import UTC, datetime, timedelta

from middleware.auth import SESSION_COOKIE

PASSWORD = "correct horse battery"


async def register(client, email="ada@example.com", name="Ada"):
    return await client.post(
        "/auth/register", json={"email": email, "name": name, "password": PASSWORD}
    )


async def test_register_creates_account_with_personal_workspace(app, client):
    response = await register(client, email="  Ada@Example.com ", name=" Ada ")

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"
    assert body["name"] == "Ada"
    assert "password_hash" not in body

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie

    db = app.state.db
    user = await db.users.find_one({"email": "ada@example.com"})
    assert user["password_hash"].startswith("$argon2")
    workspace = await db.workspaces.find_one({"owner_id": user["_id"]})
    assert workspace["personal"] is True
    membership = await db.memberships.find_one({"user_id": user["_id"]})
    assert membership["workspace_id"] == workspace["_id"]
    assert membership["role"] == "owner"


async def test_register_rejects_taken_email_regardless_of_case(client):
    await register(client)

    response = await register(client, email="ADA@example.com")

    assert response.status_code == 409


async def test_register_validates_input(client):
    short_password = await client.post(
        "/auth/register",
        json={"email": "ada@example.com", "name": "Ada", "password": "short"},
    )
    bad_email = await client.post(
        "/auth/register",
        json={"email": "not-an-email", "name": "Ada", "password": PASSWORD},
    )
    blank_name = await client.post(
        "/auth/register",
        json={"email": "ada@example.com", "name": "   ", "password": PASSWORD},
    )

    assert short_password.status_code == 422
    assert bad_email.status_code == 422
    assert blank_name.status_code == 422


async def test_login_and_me(client):
    await register(client)
    client.cookies.clear()

    response = await client.post(
        "/auth/login", json={"email": "ADA@example.com", "password": PASSWORD}
    )

    assert response.status_code == 200
    me = await client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"


async def test_login_failures_look_the_same(client):
    await register(client)
    client.cookies.clear()

    wrong_password = await client.post(
        "/auth/login", json={"email": "ada@example.com", "password": "nope-nope"}
    )
    unknown_email = await client.post(
        "/auth/login", json={"email": "bob@example.com", "password": PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert SESSION_COOKIE not in client.cookies


async def test_me_requires_session(client):
    response = await client.get("/auth/me")

    assert response.status_code == 401


async def test_logout_revokes_session(client):
    await register(client)
    token = client.cookies[SESSION_COOKIE]

    response = await client.post("/auth/logout", json={})

    assert response.status_code == 204
    assert SESSION_COOKIE not in client.cookies
    # The old token must be dead server-side, not just dropped by the browser.
    client.cookies.set(SESSION_COOKIE, token)
    assert (await client.get("/auth/me")).status_code == 401


async def test_expired_session_is_rejected(app, client):
    await register(client)
    await app.state.db.sessions.update_many(
        {}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(minutes=1)}}
    )

    response = await client.get("/auth/me")

    assert response.status_code == 401


async def test_non_json_mutations_are_rejected(client):
    response = await client.post(
        "/auth/login", data={"email": "ada@example.com", "password": PASSWORD}
    )

    assert response.status_code == 415
