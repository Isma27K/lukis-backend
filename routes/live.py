from fastapi import APIRouter, WebSocket

from function.realtime import serve_live

router = APIRouter(tags=["live"])


@router.websocket("/drawings/{drawing_id}/live")
async def live_drawing(websocket: WebSocket, drawing_id: str) -> None:
    state = websocket.app.state
    await serve_live(
        websocket,
        drawing_id,
        db=state.db,
        hub=state.live,
        allowed_origins=state.settings.frontend_origins,
    )
