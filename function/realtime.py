"""Live collaboration over plain WebSockets.

Each open editor keeps one connection to /drawings/{id}/live. The server only
relays: scene changes, pointers and viewports go to everyone else on the same
drawing, stamped with the sender's id, while saving stays on
PUT /drawings/{id}/scene. The server decides who may connect, drops scene
changes from viewers, and re-checks access as the connection lives on.

Messages are JSON objects with a "type".
Client -> server: scene, pointer, follow, viewport.
Server -> client: welcome, peers, peer-joined, scene, pointer, followers,
viewport.

Connections are tracked in this process's memory, so collaboration needs a
single backend process.
"""

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from bson import ObjectId
from fastapi import WebSocket
from pymongo.asynchronous.database import AsyncDatabase

from data.database.workspace import Role
from function.access import ROLE_RANK
from function.auth import get_session_user
from function.drawings import get_drawing
from function.errors import DomainError
from middleware.auth import SESSION_COOKIE

# A connection's role is re-read from the database at most this often, so
# removing someone (or demoting them) takes effect without a reconnect.
ROLE_RECHECK_SECONDS = 30
# Messages waiting for a slow client. Past this, pointers and viewports are
# dropped, and a client that can't keep up with scene changes is disconnected
# so it reconnects and starts fresh.
OUTBOX_SIZE = 256


class CloseCode:
    """Close codes the client acts on. 44xx means don't reconnect."""

    NOT_AUTHENTICATED = 4401
    FORBIDDEN = 4403
    NOT_FOUND = 4404
    # Standard "try again later": reconnect.
    TOO_SLOW = 1013


@dataclass(eq=False)
class Connection:
    websocket: WebSocket
    drawing_id: str
    user_id: ObjectId
    name: str
    role: Role
    id: str = field(default_factory=lambda: secrets.token_hex(8))
    role_checked_at: float = field(default_factory=time.monotonic)
    # The id of the connection this one is following, if any.
    following: str | None = None
    outbox: asyncio.Queue[str] = field(
        default_factory=lambda: asyncio.Queue(OUTBOX_SIZE)
    )

    def describe(self) -> dict[str, str]:
        return {"id": self.id, "userId": str(self.user_id), "name": self.name}

    def send(self, text: str, droppable: bool = False) -> None:
        try:
            self.outbox.put_nowait(text)
        except asyncio.QueueFull:
            if not droppable:
                asyncio.create_task(self.websocket.close(CloseCode.TOO_SLOW))


def _encode(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"))


class Hub:
    """Who is connected to which drawing, in this process."""

    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, Connection]] = {}

    def room(self, drawing_id: str) -> dict[str, Connection]:
        return self.rooms.get(drawing_id, {})

    def peers(self, drawing_id: str) -> list[dict[str, str]]:
        return [c.describe() for c in self.room(drawing_id).values()]

    def broadcast(
        self, sender: Connection, message: dict[str, Any], droppable: bool = False
    ) -> None:
        """Sends to everyone on the sender's drawing except the sender."""
        text = _encode(message)
        for other in self.room(sender.drawing_id).values():
            if other is not sender:
                other.send(text, droppable)

    def send_followers(self, target_id: str, drawing_id: str) -> None:
        target = self.room(drawing_id).get(target_id)
        if target:
            followers = [
                c.id
                for c in self.room(drawing_id).values()
                if c.following == target_id
            ]
            target.send(_encode({"type": "followers", "ids": followers}))

    def join(self, connection: Connection) -> None:
        room = self.rooms.setdefault(connection.drawing_id, {})
        room[connection.id] = connection
        peers = self.peers(connection.drawing_id)
        connection.send(
            _encode(
                {
                    "type": "welcome",
                    "id": connection.id,
                    "role": connection.role,
                    "peers": peers,
                }
            )
        )
        self.broadcast(connection, {"type": "peers", "peers": peers})
        # The newcomer loaded the saved scene; edits not saved yet come from
        # the others, who answer this with their full scene.
        self.broadcast(connection, {"type": "peer-joined", "id": connection.id})

    def leave(self, connection: Connection) -> None:
        room = self.rooms.get(connection.drawing_id, {})
        if room.pop(connection.id, None) is None:
            return
        if not room:
            del self.rooms[connection.drawing_id]
            return
        for other in room.values():
            if other.following == connection.id:
                other.following = None
        if connection.following:
            self.send_followers(connection.following, connection.drawing_id)
        text = _encode({"type": "peers", "peers": self.peers(connection.drawing_id)})
        for other in room.values():
            other.send(text)


async def _still_allowed(db: AsyncDatabase, connection: Connection) -> bool:
    if time.monotonic() - connection.role_checked_at < ROLE_RECHECK_SECONDS:
        return True
    try:
        _, membership = await get_drawing(
            db, connection.drawing_id, {"_id": connection.user_id}, "viewer"
        )
    except DomainError:
        return False
    connection.role = membership["role"]
    connection.role_checked_at = time.monotonic()
    return True


def _handle(hub: Hub, connection: Connection, message: dict[str, Any]) -> None:
    kind = message.get("type")
    if kind == "scene":
        elements = message.get("elements")
        if ROLE_RANK[connection.role] >= ROLE_RANK["editor"] and isinstance(
            elements, list
        ):
            hub.broadcast(connection, {"type": "scene", "elements": elements})
    elif kind == "pointer":
        hub.broadcast(
            connection,
            {
                "type": "pointer",
                "id": connection.id,
                "pointer": message.get("pointer"),
                "button": message.get("button"),
                "selectedElementIds": message.get("selectedElementIds"),
            },
            droppable=True,
        )
    elif kind == "follow":
        target = message.get("id")
        room = hub.room(connection.drawing_id)
        # Only people on the same drawing can be followed.
        if target is not None and (target == connection.id or target not in room):
            return
        previous, connection.following = connection.following, target
        for followed in {previous, target} - {None}:
            hub.send_followers(followed, connection.drawing_id)
    elif kind == "viewport":
        text = _encode(
            {
                "type": "viewport",
                "id": connection.id,
                "sceneBounds": message.get("sceneBounds"),
            }
        )
        for other in hub.room(connection.drawing_id).values():
            if other.following == connection.id:
                other.send(text, droppable=True)


async def _write(connection: Connection) -> None:
    while True:
        await connection.websocket.send_text(await connection.outbox.get())


async def serve_live(
    websocket: WebSocket,
    drawing_id: str,
    db: AsyncDatabase,
    hub: Hub,
    allowed_origins: list[str],
) -> None:
    # Accept first and refuse with a close code: a refused handshake looks like
    # a network error to the browser, which would retry forever.
    await websocket.accept()
    # Browsers send cookies on WebSocket handshakes from any site, so check
    # where the page came from (cross-site WebSocket hijacking).
    if websocket.headers.get("origin") not in allowed_origins:
        await websocket.close(CloseCode.FORBIDDEN)
        return
    token = websocket.cookies.get(SESSION_COOKIE)
    user = await get_session_user(db, token) if token else None
    if user is None:
        await websocket.close(CloseCode.NOT_AUTHENTICATED)
        return
    try:
        _, membership = await get_drawing(db, drawing_id, user, "viewer")
    except DomainError:
        await websocket.close(CloseCode.NOT_FOUND)
        return

    connection = Connection(
        websocket=websocket,
        drawing_id=drawing_id,
        user_id=user["_id"],
        name=user["name"],
        role=membership["role"],
    )
    hub.join(connection)
    writer = asyncio.create_task(_write(connection))
    try:
        async for raw in websocket.iter_text():
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if not await _still_allowed(db, connection):
                await websocket.close(CloseCode.FORBIDDEN)
                break
            _handle(hub, connection, message)
    finally:
        writer.cancel()
        hub.leave(connection)
