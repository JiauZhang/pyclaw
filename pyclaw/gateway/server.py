"""Gateway WebSocket and HTTP server implementation."""

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

from ..channels.web import WebChannelManager
from .runtime import GatewayRuntimeState
from .handlers import register_handlers

logger = logging.getLogger(__name__)


@dataclass
class GatewayConfig:
    port: int = 12321
    host: str = "127.0.0.1"
    control_ui_enabled: bool = True
    cors_origins: List[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None


class GatewayServer:
    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig()
        self.app = FastAPI(
            title="PyClaw Gateway",
            description="Personal AI Assistant Gateway",
            version="0.1.0"
        )
        self.runtime = GatewayRuntimeState()
        self.websocket_clients: Dict[str, WebSocket] = {}
        self.handlers: Dict[str, Callable] = {}
        self._shutdown_event = asyncio.Event()
        self.web_channel = WebChannelManager()
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

    def _setup_routes(self):
        @self.app.get("/")
        async def root():
            return {
                "name": "PyClaw Gateway",
                "version": "0.1.0",
                "status": "running",
                "timestamp": datetime.now().isoformat()
            }

        @self.app.get("/health")
        async def health():
            return {
                "status": "healthy",
                "sessions": len(self.runtime.sessions),
                "clients": len(self.websocket_clients),
                "uptime": self.runtime.uptime_seconds
            }

        @self.app.get("/v1/status")
        async def status():
            return {
                "gateway": {
                    "version": "0.1.0",
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

            from ..agents import Agent
            try:
                agent = Agent(
                    provider=self.config.provider,
                    model=self.config.model
                )
            except Exception as e:
                logger.error(f"Agent error in WebSocket handler: {e}")
                await websocket.send_json({
                    "type": "error",
                    "error": f"Failed to initialize agent: {str(e)}"
                })
                return

            await self.web_channel.handle_websocket(
                websocket,
                client_id,
                agent,
                self.runtime
            )

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
        logger.info("Gateway shutdown complete")

    async def _close_websocket(self, client_id: str, websocket: WebSocket):
        try:
            await websocket.close()
        except:
            pass
        finally:
            if client_id in self.websocket_clients:
                del self.websocket_clients[client_id]
