from fastapi import APIRouter, WebSocket

from function.realtime import admit_guest, admit_member, serve_live

router = APIRouter(tags=["live"])


@router.websocket("/drawings/{drawing_id}/live")
async def live_drawing(websocket: WebSocket, drawing_id: str) -> None:
    state = websocket.app.state
    await serve_live(
        websocket,
        hub=state.live,
        allowed_origins=state.settings.frontend_origins,
        admit=admit_member(state.db, drawing_id),
    )


# Guests on a public link, in the same room as the drawing's members.
@router.websocket("/share/{token}/live")
async def live_shared_drawing(websocket: WebSocket, token: str) -> None:
    state = websocket.app.state
    await serve_live(
        websocket,
        hub=state.live,
        allowed_origins=state.settings.frontend_origins,
        admit=admit_guest(state.db, token),
    )
