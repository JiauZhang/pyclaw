import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from pyclaw.version import __version__

from ..agents import (
    Session, build_team, IM_EXTRA, append_conv, record_meta, session_logger,
    resolve_session_id,
)
from ..channels import IMChannelAdapter, OutboundMessage
from ..channels.web import WebChannelAdapter
from ..slash import handle_slash
from ..channels.im_formatter import IMStatusTracker, split_long_message
from .runtime import GatewayRuntimeState
from .handlers import register_handlers

logger = logging.getLogger(__name__)


def _im_progress_text(ev) -> str:
    topic = ev.topic
    data = ev.data or {}
    if topic in ('lifecycle:team:start', 'lifecycle:agent:start'):
        return '🔄 PyClaw 思考中…'
    if topic == 'lifecycle:tool:start':
        name = data.get('name', 'tool')
        arg = data.get('input') or data.get('arguments')
        if arg:
            return f'🔧 调用工具 {name}：{str(arg)[:80]}'
        return f'🔧 调用工具 {name}'
    if topic == 'lifecycle:tool:end':
        name = data.get('name', 'tool')
        out = data.get('output') or data.get('result')
        if out:
            return f'✅ {name} 完成：{str(out)[:80]}'
        return f'✅ {name} 完成'
    if topic == 'lifecycle:tool:error':
        return f'⚠️ {data.get("name", "tool")} 失败：{data.get("error")}'
    if topic in ('lifecycle:team:error', 'lifecycle:agent:error'):
        return f'⚠️ 错误：{data.get("error")}'
    return ''


def _friendly_channel_error(exc: Exception) -> str:
    err_msg = str(exc)
    lowered = err_msg.lower()
    if "timed out" in lowered:
        return "Request timed out. Please try again later."
    if "InternalServerError" in err_msg or "500" in err_msg:
        return "Service temporarily unavailable. Please try again later."
    if "rate" in lowered:
        return "Too many requests. Please wait a moment and try again."
    return f"An error occurred: {err_msg[:200]}"


async def run_im_interaction(
    session,
    adapter,
    session_id: str,
    sender_id: str,
    text: str,
    msg_id,
    *,
    im_extra: str,
    progress_fn: Callable,
    status_interval: float = 4.0,
    max_msg_len: int = 1500,
    clock=time.monotonic,
):
    session.conv_session_id = session_id
    append_conv(session_id, "user", text)

    tracker = IMStatusTracker(refresh_interval=status_interval)

    def on_event(ev):
        status = progress_fn(ev)
        if status:
            tracker.update(status, clock())

    async def pump_status():
        while True:
            await asyncio.sleep(status_interval / 2)
            for status in tracker.drain(clock()):
                await adapter.send_message(sender_id, OutboundMessage(text=status, reply_to=msg_id))

    pump = asyncio.create_task(pump_status())
    response = ""
    try:
        response = await session.chat(f"{im_extra}\n{text}", on_event=on_event)
    finally:
        pump.cancel()
        for status in tracker.drain(clock()):
            await adapter.send_message(sender_id, OutboundMessage(text=status, reply_to=msg_id))

    if response:
        for part in split_long_message(response, max_msg_len):
            await adapter.send_message(sender_id, OutboundMessage(text=part, reply_to=msg_id))
    return response


@dataclass
class GatewayConfig:
    port: int = 12321
    host: str = "127.0.0.1"
    cors_origins: List[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    enabled_channels: List[str] = field(default_factory=lambda: ["wechat"])


class GatewayServer:
    def __init__(self, config: Optional[GatewayConfig] = None, app_config: Optional[dict] = None):
        self.config = config or GatewayConfig()
        self._app_config = app_config or {}
        self.app = FastAPI(
            title="PyClaw Gateway",
            description="Personal AI Assistant Gateway",
            version="0.1.0"
        )
        self.runtime = GatewayRuntimeState()
        self.websocket_clients: Dict[str, WebSocket] = {}
        self.handlers: Dict[str, Callable] = {}
        self.channels: Dict[str, IMChannelAdapter] = {}
        self._sessions: Dict[str, Session] = {}
        self._shutdown_event = asyncio.Event()
        self._recent_im: Dict[tuple, float] = {}
        self.web_channel = WebChannelAdapter({})
        self._setup_middleware()
        self._setup_routes()

    def _setup_middleware(self):
        if self.config.cors_origins:
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=self.config.cors_origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

    @property
    def webchat_enabled(self) -> bool:
        return 'web' in self.config.enabled_channels

    def _new_session(self, provider=None, model=None) -> Session:
        return Session(build_team(
            provider=provider or self.config.provider,
            model=model or self.config.model,
            http_options={'timeout': 300},
        ))

    async def _get_session(self, session_key: str, provider=None, model=None) -> Session:
        session = self._sessions.get(session_key)
        if session is None:
            session = self._new_session(provider, model)
            self._sessions[session_key] = session
            self.runtime.get_or_create_session(session_key, session.name)
        return session

    async def _remove_session(self, session_key: str):
        session = self._sessions.pop(session_key, None)
        if session is not None:
            await session.close()
            self.runtime.delete_session(session_key)

    def _setup_routes(self):
        @self.app.get("/")
        async def root():
            return {
                "name": "PyClaw Gateway",
                "version": __version__,
                "status": "running",
                "timestamp": datetime.now().isoformat()
            }

        @self.app.get("/v1/status")
        async def status():
            return {
                "gateway": {
                    "version": __version__,
                    "started_at": self.runtime.started_at.isoformat(),
                    "uptime_seconds": self.runtime.uptime_seconds
                },
                "connections": {
                    "websocket_clients": len(self.websocket_clients),
                    "active_sessions": len(self.runtime.sessions)
                },
                "channels": self.runtime.get_channel_status(),
                "agents": self.runtime.get_agent_status()
            }

        @self.app.post("/v1/{method}")
        async def rpc_endpoint(method: str, request: Request):
            try:
                params = await request.json()
                result = await self._handle_rpc(method, params)
                return JSONResponse(content={"result": result})
            except Exception as e:
                logger.error(f"RPC error: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"error": {"code": -32603, "message": str(e)}}
                )

        if self.webchat_enabled:
            self._setup_webchat_routes()

    def _setup_webchat_routes(self):
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            client_id = str(uuid.uuid4())
            self.websocket_clients[client_id] = websocket
            self.runtime.client_connected(client_id)
            logger.info(f"WebSocket client {client_id} connected")

            try:
                await websocket.send_json({
                    "type": "connected",
                    "client_id": client_id,
                    "timestamp": datetime.now().isoformat()
                })

                while not self._shutdown_event.is_set():
                    try:
                        message = await asyncio.wait_for(
                            websocket.receive_json(),
                            timeout=1.0
                        )
                        response = await self._handle_websocket_message(
                            message, client_id
                        )
                        if response:
                            await websocket.send_json(response)
                    except asyncio.TimeoutError:
                        try:
                            await websocket.send_json({"type": "ping"})
                        except Exception:
                            break
                    except WebSocketDisconnect:
                        logger.info(f"Client {client_id} disconnected")
                        break
                    except Exception as e:
                        logger.error(f"Error handling message: {e}")
                        await websocket.send_json({
                            "type": "error",
                            "error": str(e)
                        })
            finally:
                if client_id in self.websocket_clients:
                    del self.websocket_clients[client_id]
                self.runtime.client_disconnected(client_id)
                logger.info(f"WebSocket client {client_id} removed")

        @self.app.websocket("/chat/ws")
        async def chat_websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            client_id = uuid.uuid4().hex
            logger.info(f"WebChat client {client_id} connected")

            session = await self._get_session(client_id)
            try:
                await self.web_channel.handle_websocket(
                    websocket,
                    client_id,
                    session,
                    self.runtime
                )
            finally:
                await self._remove_session(client_id)

        @self.app.get("/chat", response_class=HTMLResponse)
        async def chat_ui():
            static_dir = os.path.join(os.path.dirname(__file__), "static")
            return FileResponse(os.path.join(static_dir, "chat.html"))

        @self.app.get("/control")
        async def control_ui():
            return HTMLResponse("""
            <!DOCTYPE html>
            <html>
            <head><title>PyClaw Control</title></head>
            <body>
                <h1>PyClaw Control Panel</h1>
                <p>Gateway is running.</p>
                <a href="/chat">Open WebChat</a>
            </body>
            </html>
            """)

    async def _handle_websocket_message(
        self,
        message: Dict[str, Any],
        client_id: str
    ) -> Optional[Dict[str, Any]]:
        msg_type = message.get("type", "request")
        if msg_type == "ping":
            return {"type": "pong"}
        if msg_type == "request" or "method" in message:
            return await self._handle_rpc_message(message, client_id)
        return {"type": "error", "error": "Unknown message type"}

    async def _handle_rpc_message(
        self,
        message: Dict[str, Any],
        client_id: str
    ) -> Dict[str, Any]:
        msg_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})
        if not method:
            return {
                "id": msg_id,
                "error": {"code": -32600, "message": "Method not specified"}
            }
        try:
            result = await self._handle_rpc(method, params, client_id)
            return {"id": msg_id, "result": result}
        except Exception as e:
            logger.error(f"RPC error for method {method}: {e}")
            return {
                "id": msg_id,
                "error": {"code": -32603, "message": str(e)}
            }

    async def _handle_rpc(
        self,
        method: str,
        params: Dict[str, Any],
        client_id: Optional[str] = None
    ) -> Any:
        handler = self.handlers.get(method)
        if not handler:
            raise ValueError(f"Unknown method: {method}")
        context = {
            "client_id": client_id,
            "runtime": self.runtime,
            "gateway": self
        }
        return await handler(params, context)

    def register_handler(self, method: str, handler: Callable):
        self.handlers[method] = handler
        logger.debug(f"Registered handler for method: {method}")

    async def start(self):
        register_handlers(self)
        await self._init_channels()
        uvicorn_config = uvicorn.Config(
            self.app,
            host=self.config.host,
            port=self.config.port,
            log_level="info",
            access_log=False
        )
        server = uvicorn.Server(uvicorn_config)
        logger.info(f"🦞 PyClaw Gateway starting on http://{self.config.host}:{self.config.port}")
        if self.webchat_enabled:
            logger.info(f"WebChat available at http://{self.config.host}:{self.config.port}/chat")
        self.runtime.mark_started()
        try:
            await server.serve()
        except asyncio.CancelledError:
            logger.info("Server cancelled")
        finally:
            await self.shutdown()

    async def shutdown(self):
        logger.info("Shutting down Gateway...")
        self._shutdown_event.set()
        close_tasks = [
            self._close_websocket(cid, ws)
            for cid, ws in list(self.websocket_clients.items())
        ]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

        for name, adapter in list(self.channels.items()):
            try:
                await adapter.disconnect()
                logger.info("IM channel '%s' disconnected", name)
            except Exception as exc:
                logger.warning("Error disconnecting channel '%s': %s", name, exc)

        logger.info("Gateway shutdown complete")

    async def _close_websocket(self, client_id: str, websocket: WebSocket):
        try:
            await websocket.close()
        except Exception:
            pass
        finally:
            if client_id in self.websocket_clients:
                del self.websocket_clients[client_id]

    async def _init_channels(self):
        active = self.config.enabled_channels

        im_platforms = {p for p in active if p != "web"}
        if not im_platforms:
            return

        channels_cfg = self._app_config.get("channels", {})

        for platform in im_platforms:
            cfg = {}
            for _, v in channels_cfg.items():
                if v.get("platform") == platform:
                    cfg = v
                    break
            if not cfg.get("enabled", True):
                continue

            adapter = IMChannelAdapter({"platform": platform, **(cfg.get("options") or {})})

            if "greeting_text" not in adapter.config and "greeting_text" in self._app_config:
                adapter.config["greeting_text"] = self._app_config["greeting_text"]

            async def _on_message(
                msg,
                _channel_id,
                    _adapter=adapter,
                    _platform=platform,
            ):
                await _adapter.save_known_contact(msg.sender_id)
                session_id = resolve_session_id([_platform, str(msg.sender_id)])
                s_log = session_logger(session_id)
                s_log.info("IM '%s' received from %s: %s", _platform, msg.sender_id, msg.text)
                record_meta(session_id, {
                    "channel": "im",
                    "platform": _platform,
                    "sender_id": str(msg.sender_id),
                })

                key = (_platform, msg.sender_id, msg.text)
                now = time.time()
                if key in self._recent_im and now - self._recent_im[key] < 30:
                    s_log.debug("Dropped duplicate IM message from %s", msg.sender_id)
                    return
                self._recent_im[key] = now

                if not _adapter._greeting_sent:
                    await _adapter.send_greeting_on_startup()

                self.runtime.get_or_create_session(session_id)
                session = await self._get_session(session_id)
                session.deliver = lambda text: _adapter.send_message(
                    msg.sender_id, OutboundMessage(text=text),
                )

                slash_reply = await handle_slash(msg.text, session, session_id)
                if slash_reply is not None:
                    await _adapter.send_message(
                        msg.sender_id, OutboundMessage(text=slash_reply, reply_to=msg.id),
                    )
                    logger.info("IM '%s' sent slash reply to %s", _platform, msg.sender_id)
                    self.runtime.increment_requests()
                    return

                try:
                    response = await run_im_interaction(
                        session,
                        _adapter,
                        session_id,
                        msg.sender_id,
                        msg.text,
                        msg.id,
                        im_extra=IM_EXTRA,
                        progress_fn=_im_progress_text,
                    )
                    s_log.info("IM '%s' replied to %s", _platform, msg.sender_id)
                    self.runtime.increment_channel_messages(_adapter.channel_id)
                    self.runtime.increment_requests()
                except Exception as exc:
                    s_log.error("Channel '%s' handler error: %s", _platform, exc)
                    self.runtime.increment_errors()
                    friendly = _friendly_channel_error(exc)
                    err_out = OutboundMessage(text=friendly, reply_to=msg.id)
                    await _adapter.send_message(msg.sender_id, err_out)

            adapter.set_message_handler(_on_message)
            self.runtime.register_channel(adapter.channel_id, enabled=True)

            ok = await adapter.connect()
            self.runtime.set_channel_connected(adapter.channel_id, ok)

            if ok:
                self.channels[platform] = adapter
                logger.info("IM channel '%s' connected", platform)

                # Wait for the adapter to be fully ready, then try proactive greeting
                ready = await adapter.wait_until_ready()
                if ready:
                    await adapter.send_greeting_on_startup()
                else:
                    logger.warning(
                        "Channel '%s' not ready after connect, greeting skipped", platform
                    )
            else:
                logger.warning("IM channel '%s' failed to connect", platform)
                self.runtime.set_channel_error(adapter.channel_id, "connect failed")
