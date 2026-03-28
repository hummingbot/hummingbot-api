"""
WebSocket router for real-time executor data streaming.
"""
import asyncio
import base64
import logging
import secrets
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])

HEARTBEAT_INTERVAL = 30  # seconds


def _authenticate_websocket(websocket: WebSocket) -> bool:
    """
    Authenticate a WebSocket connection using Basic Auth from headers or query params.

    Returns True if authenticated (or debug mode), False otherwise.
    """
    if settings.security.debug_mode:
        return True

    # Try Authorization header first
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            ws_user, ws_pass = decoded.split(":", 1)
        except Exception:
            return False
    else:
        # Fallback to query parameters
        ws_user = websocket.query_params.get("username", "")
        ws_pass = websocket.query_params.get("password", "")

    correct_user = secrets.compare_digest(
        ws_user.encode(), settings.security.username.encode()
    )
    correct_pass = secrets.compare_digest(
        ws_pass.encode(), settings.security.password.encode()
    )
    return correct_user and correct_pass


async def _heartbeat_loop(websocket: WebSocket) -> None:
    """Send periodic heartbeat pings."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await websocket.send_json({
                "type": "heartbeat",
                "timestamp": time.time(),
            })
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


@router.websocket("/ws/executors")
async def executors_websocket(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for streaming executor data.

    Authentication: Basic Auth via Authorization header or query params
    (?username=...&password=...).

    Subscribe/unsubscribe protocol:
        -> {"action": "subscribe", "type": "executor_summary", "update_interval": 2.0}
        <- {"type": "subscribed", "subscription_id": "executor_summary", ...}
        <- {"type": "executor_summary", "subscription_id": "executor_summary", "data": {...}, ...}
        -> {"action": "unsubscribe", "subscription_id": "executor_summary"}
        <- {"type": "unsubscribed", "subscription_id": "executor_summary"}

    Subscription types:
        - executors: filtered list of executors
        - executor_detail: single executor detail
        - executor_summary: aggregate summary of active executors
        - performance: performance report (optionally per controller)
        - positions: held positions with unrealized PnL
        - executor_logs: streaming log entries for an executor
    """
    await websocket.accept()

    # Authenticate
    if not _authenticate_websocket(websocket):
        await websocket.send_json({
            "type": "error",
            "message": "Authentication failed",
        })
        await websocket.close(code=4001, reason="Authentication failed")
        return

    # Get manager from app state
    manager = websocket.app.state.executor_ws_manager
    conn_id = str(uuid.uuid4())[:12]

    await websocket.send_json({
        "type": "connected",
        "connection_id": conn_id,
        "timestamp": time.time(),
    })
    logger.info(f"[WS-Exec] Client connected: {conn_id}")

    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(websocket), name=f"ws-exec-hb-{conn_id}"
    )

    try:
        while True:
            raw = await websocket.receive_json()
            action = raw.get("action")

            if action == "subscribe":
                await manager.handle_subscribe(conn_id, websocket, raw)
            elif action == "unsubscribe":
                sub_id = raw.get("subscription_id")
                if sub_id:
                    await manager.handle_unsubscribe(conn_id, websocket, sub_id)
                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": "unsubscribe requires 'subscription_id'",
                    })
            elif action == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": time.time(),
                })
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown action: {action}. "
                               f"Valid actions: subscribe, unsubscribe, ping",
                })
    except WebSocketDisconnect:
        logger.info(f"[WS-Exec] Client disconnected: {conn_id}")
    except Exception as e:
        logger.error(f"[WS-Exec] Error for {conn_id}: {e}", exc_info=True)
    finally:
        heartbeat_task.cancel()
        manager.remove_connection(conn_id)
