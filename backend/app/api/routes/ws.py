from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.api.deps import get_current_user_ws

router = APIRouter(prefix="/ws", tags=["ws"])


@router.websocket("/ping")
async def ws_ping(websocket: WebSocket) -> None:
    try:
        user = await get_current_user_ws(websocket)
    except WebSocketDisconnect:
        return
    await websocket.accept()
    try:
        await websocket.receive_text()
        await websocket.send_json({"pong": str(user.id), "role": user.role.value})
    except WebSocketDisconnect:
        return
