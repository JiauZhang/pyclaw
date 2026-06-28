import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from pyclaw.version import __version__

from ..agents import Agent, IM_EXTRA
from ..channels import IMChannelAdapter, OutboundMessage
from ..channels.web import WebChannelAdapter
from .runtime import GatewayRuntimeState
from ..skills import load_skills_as_tools
from ..tools import tools as _base_tools
from .handlers import register_handlers

logger = logging.getLogger(__name__)


@dataclass
class GatewayConfig:
    port: int = 12321
    host: str = "127.0.0.1"
    cors_origins: List[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    enabled_channels: List[str] = field(default_factory=lambda: ["web"])


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
        self._session_agents: Dict[str, Agent] = {}
        self._shutdown_event = asyncio.Event()
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

    def _create_agent(self):
        skill_tools = load_skills_as_tools(
            provider=self.config.provider,
            model=self.config.model,
        )
        return Agent(
            provider=self.config.provider,
            model=self.config.model,
            http_options={'timeout': 300},
            tools=_base_tools + skill_tools,
        )

    def _get_session_agent(self, session_key: str) -> Agent:
        agent = self._session_agents.get(session_key)
        if agent is None:
            agent = self._create_agent()
            self._session_agents[session_key] = agent
        return agent

    def _remove_session_agent(self, session_key: str):
        self._session_agents.pop(session_key, None)

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
                        except:
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
            client_id = f"chat_{uuid.uuid4().hex[:8]}"
            logger.info(f"WebChat client {client_id} connected")

            agent = self._get_session_agent(client_id)
            try:
                await self.web_channel.handle_websocket(
                    websocket,
                    client_id,
                    agent,
                    self.runtime
                )
            finally:
                self._remove_session_agent(client_id)

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
        except:
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

                session_id = f"im_{_platform}_{msg.sender_id}"
                self.runtime.get_or_create_session(session_id)
                agent = self._get_session_agent(session_id)

                async def _send_progress(p):
                    text = ''
                    if p.type.value == 'agent:start':
                        text = '[PyClaw is thinking…]'
                    elif p.type.value == 'tool:start':
                        text = f'[Using: {p.tool_name}]'
                    elif p.type.value == 'tool:error':
                        text = f'[{p.tool_name} failed: {p.content}]'
                    elif p.type.value == 'agent:error':
                        text = f'[Error: {p.content}]'
                    if text:
                        await _adapter.send_message(
                            msg.sender_id,
                            OutboundMessage(text=text, reply_to=msg.id),
                        )

                loop = asyncio.get_running_loop()

                def on_progress(p):
                    asyncio.run_coroutine_threadsafe(
                        _send_progress(p), loop,
                    )

                try:
                    response = await loop.run_in_executor(
                        None, agent.chat, f"{IM_EXTRA}\n{msg.text}", on_progress,
                    )
                    outbound = OutboundMessage(
                        text=response,
                        reply_to=msg.id,
                        metadata=msg.metadata,
                    )
                    await _adapter.send_message(msg.sender_id, outbound)
                    self.runtime.increment_channel_messages(_adapter.channel_id)
                    self.runtime.increment_requests()
                except Exception as exc:
                    logger.error("Channel '%s' handler error: %s", _platform, exc)
                    self.runtime.increment_errors()
                    err_msg = str(exc)
                    if "timed out" in err_msg.lower():
                        friendly = "Request timed out. Please try again later."
                    elif "InternalServerError" in err_msg or "500" in err_msg:
                        friendly = "Service temporarily unavailable. Please try again later."
                    elif "rate" in err_msg.lower():
                        friendly = "Too many requests. Please wait a moment and try again."
                    else:
                        friendly = f"An error occurred: {err_msg[:200]}"
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
