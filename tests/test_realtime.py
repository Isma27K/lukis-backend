import asyncio
import json
import uuid

import pytest
import uvicorn
from httpx import AsyncClient
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

import function.realtime as realtime
from function.realtime import CloseCode
from main import create_app
from middleware.auth import SESSION_COOKIE
from tests.helpers import PASSWORD, add_member
from utils.config import Settings

FRONTEND = "http://frontend.test"


@pytest.fixture
async def server():
    """A real server on a free port, so WebSockets go over actual connections."""
    db_name = f"lukis_test_{uuid.uuid4().hex[:12]}"
    app = create_app(Settings(mongo_db=db_name, frontend_origin=FRONTEND))
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    task = asyncio.create_task(uvicorn_server.serve())
    while not uvicorn_server.started:
        await asyncio.sleep(0.01)
    port = uvicorn_server.servers[0].sockets[0].getsockname()[1]
    yield f"127.0.0.1:{port}", app
    await app.state.db.client.drop_database(db_name)
    uvicorn_server.should_exit = True
    await task


class Peer:
    """One open editor: a WebSocket that sorts what it receives by type."""

    def __init__(self, socket: ClientConnection):
        self.socket = socket
        self.received: dict[str, asyncio.Queue] = {}
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.socket:
                message = json.loads(raw)
                await self.queue(message["type"]).put(message)
        except ConnectionClosed:
            pass

    def queue(self, kind: str) -> asyncio.Queue:
        return self.received.setdefault(kind, asyncio.Queue())

    async def next(self, kind: str) -> dict:
        return await asyncio.wait_for(self.queue(kind).get(), timeout=5)

    async def send(self, **message) -> None:
        await self.socket.send(json.dumps(message))

    async def close(self) -> None:
        await self.socket.close()
        await self.reader


async def open_socket(host: str, drawing_id: str, token: str | None, origin=FRONTEND):
    headers = {"Cookie": f"{SESSION_COOKIE}={token}"} if token else {}
    return await connect(
        f"ws://{host}/drawings/{drawing_id}/live",
        origin=origin,
        additional_headers=headers,
    )


async def close_code(socket: ClientConnection) -> int:
    with pytest.raises(ConnectionClosed) as closed:
        await asyncio.wait_for(socket.recv(), timeout=5)
    return closed.value.rcvd.code


async def signup(host: str, email: str, name: str) -> tuple[str, str]:
    """Returns (user id, session token)."""
    async with AsyncClient(base_url=f"http://{host}") as http:
        response = await http.post(
            "/auth/register",
            json={"email": email, "name": name, "password": PASSWORD},
        )
        return response.json()["id"], response.cookies[SESSION_COOKIE]


@pytest.fixture
async def team(server):
    """Olga owns a team drawing; Ed edits there, Vic views, Zed is an outsider.

    Yields an `open(name)` that connects that person to the drawing.
    """
    host, app = server
    tokens, ids = {}, {}
    for name in ("olga", "ed", "vic", "zed"):
        ids[name], tokens[name] = await signup(
            host, f"{name}@example.com", name.title()
        )
    async with AsyncClient(
        base_url=f"http://{host}", cookies={SESSION_COOKIE: tokens["olga"]}
    ) as olga:
        workspace = (await olga.post("/workspaces", json={"name": "Team"})).json()
        drawing = (
            await olga.post(f"/workspaces/{workspace['id']}/drawings", json={})
        ).json()
    await add_member(app, workspace["id"], ids["ed"], "editor")
    await add_member(app, workspace["id"], ids["vic"], "viewer")

    peers: list[Peer] = []

    async def open_peer(name: str) -> tuple[Peer, dict]:
        peer = Peer(await open_socket(host, drawing["id"], tokens[name]))
        peers.append(peer)
        return peer, await peer.next("welcome")

    yield {
        "open": open_peer,
        "drawing_id": drawing["id"],
        "host": host,
        "tokens": tokens,
        "app": app,
    }
    for peer in peers:
        await peer.close()


async def test_connecting_needs_a_session_membership_and_the_right_origin(team):
    host, drawing_id = team["host"], team["drawing_id"]
    tokens = team["tokens"]

    cases = [
        (None, drawing_id, FRONTEND, CloseCode.NOT_AUTHENTICATED),
        ("forged", drawing_id, FRONTEND, CloseCode.NOT_AUTHENTICATED),
        (tokens["olga"], drawing_id, "http://evil.test", CloseCode.FORBIDDEN),
        (tokens["olga"], drawing_id, None, CloseCode.FORBIDDEN),
        (tokens["zed"], drawing_id, FRONTEND, CloseCode.NOT_FOUND),
        (tokens["olga"], "not-an-id", FRONTEND, CloseCode.NOT_FOUND),
    ]
    for token, target, origin, expected in cases:
        socket = await open_socket(host, target, token, origin=origin)
        assert await close_code(socket) == expected


async def test_members_see_each_other_and_each_others_changes(team):
    olga, welcome = await team["open"]("olga")
    assert welcome["role"] == "owner"
    assert [p["name"] for p in welcome["peers"]] == ["Olga"]

    ed, ed_welcome = await team["open"]("ed")
    assert {p["name"] for p in ed_welcome["peers"]} == {"Olga", "Ed"}
    assert {p["name"] for p in (await olga.next("peers"))["peers"]} == {
        "Olga",
        "Ed",
    }
    assert (await olga.next("peer-joined"))["id"] == ed_welcome["id"]

    await ed.send(type="scene", elements=[{"id": "a", "version": 2}])
    assert (await olga.next("scene"))["elements"] == [{"id": "a", "version": 2}]

    await ed.send(type="pointer", pointer={"x": 1, "y": 2}, button="up", name="X")
    relayed = await olga.next("pointer")
    assert relayed["id"] == ed_welcome["id"]
    assert relayed["pointer"] == {"x": 1, "y": 2}
    # Identity comes from the server's peer list, never from the message.
    assert "name" not in relayed


async def test_viewers_cannot_change_the_scene(team):
    olga, _ = await team["open"]("olga")
    vic, welcome = await team["open"]("vic")
    assert welcome["role"] == "viewer"

    await vic.send(type="scene", elements=[{"id": "a", "version": 9}])
    await vic.send(type="pointer", pointer={"x": 0, "y": 0})

    # One sender's messages arrive in order, so a relayed scene would be
    # queued before this pointer.
    assert (await olga.next("pointer"))["id"] == welcome["id"]
    assert olga.queue("scene").empty()


async def test_following_relays_the_viewport(team):
    olga, olga_welcome = await team["open"]("olga")
    ed, ed_welcome = await team["open"]("ed")

    await ed.send(type="follow", id=olga_welcome["id"])
    assert (await olga.next("followers"))["ids"] == [ed_welcome["id"]]

    await olga.send(type="viewport", sceneBounds=[0, 0, 100, 50])
    assert await ed.next("viewport") == {
        "type": "viewport",
        "id": olga_welcome["id"],
        "sceneBounds": [0, 0, 100, 50],
    }

    await ed.send(type="follow", id=None)
    assert (await olga.next("followers"))["ids"] == []


async def test_leaving_updates_everyone(team):
    olga, olga_welcome = await team["open"]("olga")
    ed, ed_welcome = await team["open"]("ed")
    await olga.next("peers")  # Ed arriving.
    await ed.send(type="follow", id=olga_welcome["id"])
    assert (await olga.next("followers"))["ids"] == [ed_welcome["id"]]

    await ed.close()

    assert (await olga.next("followers"))["ids"] == []
    assert [p["name"] for p in (await olga.next("peers"))["peers"]] == ["Olga"]


async def test_removed_members_are_cut_off(team, monkeypatch):
    olga, _ = await team["open"]("olga")
    ed, _ = await team["open"]("ed")
    await olga.next("peers")  # Ed arriving.
    monkeypatch.setattr(realtime, "ROLE_RECHECK_SECONDS", 0)

    await team["app"].state.db.memberships.delete_many({"role": "editor"})
    await ed.send(type="scene", elements=[{"id": "a", "version": 1}])

    await ed.reader
    assert ed.socket.close_code == CloseCode.FORBIDDEN
    assert [p["name"] for p in (await olga.next("peers"))["peers"]] == ["Olga"]
    assert olga.queue("scene").empty()
