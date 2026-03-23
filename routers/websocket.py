import asyncio
import base64
import logging
import secrets
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import settings
from services.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)

router = APIRouter()


def authenticate_websocket(websocket: WebSocket) -> bool:
    """Authenticate via Authorization header or ?token query param (base64 user:pass)."""
    expected_user = settings.security.username
    expected_pass = settings.security.password

    if settings.security.debug_mode:
        return True

    # Try Authorization header first
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            user, passwd = decoded.split(":", 1)
            if (secrets.compare_digest(user, expected_user)
                    and secrets.compare_digest(passwd, expected_pass)):
                return True
        except Exception:
            pass

    # Fallback: ?token=base64(user:pass) query param
    token = websocket.query_params.get("token")
    if token:
        try:
            decoded = base64.b64decode(token).decode("utf-8")
            user, passwd = decoded.split(":", 1)
            if (secrets.compare_digest(user, expected_user)
                    and secrets.compare_digest(passwd, expected_pass)):
                return True
        except Exception:
            pass

    return False


@router.websocket("/ws/market-data")
async def market_data_websocket(websocket: WebSocket):
    await websocket.accept()

    if not authenticate_websocket(websocket):
        await websocket.send_json({"type": "error", "message": "Unauthorized"})
        await websocket.close(code=4001, reason="Unauthorized")
        return

    manager: WebSocketManager = websocket.app.state.websocket_manager
    conn_id = manager.generate_connection_id()

    await websocket.send_json({"type": "connected", "connection_id": conn_id})
    logger.info(f"WebSocket connected: {conn_id}")

    # Heartbeat task
    heartbeat_interval = settings.market_data.ws_heartbeat_interval

    async def heartbeat():
        try:
            while True:
                await asyncio.sleep(heartbeat_interval)
                await websocket.send_json({"type": "heartbeat", "timestamp": time.time()})
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    heartbeat_task = asyncio.create_task(heartbeat())

    try:
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")

            if action == "subscribe":
                await manager.handle_subscribe(conn_id, websocket, msg)
            elif action == "unsubscribe":
                sub_id = msg.get("subscription_id")
                if sub_id:
                    await manager.handle_unsubscribe(conn_id, websocket, sub_id)
                else:
                    await websocket.send_json({"type": "error", "message": "subscription_id required"})
            else:
                await websocket.send_json({"type": "error", "message": f"Unknown action: {action}"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {conn_id}")
    except Exception as e:
        logger.error(f"WebSocket error [{conn_id}]: {e}")
    finally:
        heartbeat_task.cancel()
        manager.remove_connection(conn_id)
