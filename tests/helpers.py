from datetime import UTC, datetime

from bson import ObjectId

PASSWORD = "correct horse battery"

# The API refuses non-JSON mutations, and httpx sends no content type on DELETE.
JSON = {"Content-Type": "application/json"}


async def signup(client, email="ada@example.com", name="Ada") -> dict:
    response = await client.post(
        "/auth/register", json={"email": email, "name": name, "password": PASSWORD}
    )
    assert response.status_code == 201
    return response.json()


async def personal_workspace_id(client) -> str:
    workspaces = (await client.get("/workspaces")).json()
    return next(workspace["id"] for workspace in workspaces if workspace["personal"])


async def create_workspace(client, name="Team") -> dict:
    response = await client.post("/workspaces", json={"name": name})
    assert response.status_code == 201
    return response.json()


async def add_member(app, workspace_id: str, user_id: str, role: str) -> None:
    """Adds a membership straight to the database, skipping the invite flow."""
    await app.state.db.memberships.insert_one(
        {
            "_id": ObjectId(),
            "workspace_id": ObjectId(workspace_id),
            "user_id": ObjectId(user_id),
            "role": role,
            "created_at": datetime.now(UTC),
        }
    )


def element(
    id: str,
    version: int = 1,
    nonce: int = 1,
    index: str = "a0",
    deleted: bool = False,
    updated: int | None = None,
    **extra,
) -> dict:
    """An Excalidraw element with just the fields the merge cares about."""
    return {
        "id": id,
        "version": version,
        "versionNonce": nonce,
        "index": index,
        "isDeleted": deleted,
        "updated": updated
        if updated is not None
        else int(datetime.now(UTC).timestamp() * 1000),
        **extra,
    }
