from datetime import UTC, datetime

PASSWORD = "correct horse battery"


async def signup(client, email="ada@example.com", name="Ada") -> dict:
    response = await client.post(
        "/auth/register", json={"email": email, "name": name, "password": PASSWORD}
    )
    assert response.status_code == 201
    return response.json()


async def personal_workspace_id(client) -> str:
    workspaces = (await client.get("/workspaces")).json()
    return next(workspace["id"] for workspace in workspaces if workspace["personal"])


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
