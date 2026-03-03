"""Gateway WebSocket and HTTP server implementation."""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from ..config import PyClawConfig, load_config
from .runtime import GatewayRuntimeState
from .handlers import register_handlers
from .handlers.agent import _get_or_create_agent, create_agent_context
from ..version import __version__

logger = logging.getLogger(__name__)


@dataclass
class GatewayConfig:
    """Gateway server configuration."""
    port: int = 12321
    host: str = "127.0.0.1"
    control_ui_enabled: bool = True
    cors_origins: List[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None


class GatewayServer:
    """
    PyClaw Gateway Server - Core control plane.
    
    Manages WebSocket connections, HTTP API, sessions, channels, and agents.
    """
    
    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig()
        self.app = FastAPI(
            title="PyClaw Gateway",
            description="Personal AI Assistant",
            version=__version__
        )
        self.runtime = GatewayRuntimeState()
        self.websocket_clients: Dict[str, WebSocket] = {}
        self.handlers: Dict[str, Callable] = {}
        self._shutdown_event = asyncio.Event()

        self._setup_middleware()
        self._setup_routes()
    
    def _setup_middleware(self):
        """Setup FastAPI middleware."""
        if self.config.cors_origins:
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=self.config.cors_origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
    
    def _setup_routes(self):
        """Setup HTTP and WebSocket routes."""
        
        @self.app.get("/")
        async def root():
            """Root endpoint - Gateway info."""
            return {
                "name": "PyClaw Gateway",
                "version": __version__,
                "status": "running",
                "timestamp": datetime.now().isoformat()
            }
        
        @self.app.get("/health")
        async def health():
            """Health check endpoint."""
            return {
                "status": "healthy",
                "sessions": len(self.runtime.sessions),
                "clients": len(self.websocket_clients),
                "uptime": self.runtime.uptime_seconds
            }
        
        @self.app.get("/v1/status")
        async def status():
            """Detailed status endpoint."""
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
            """WebSocket endpoint for real-time communication."""
            await websocket.accept()
            client_id = str(uuid.uuid4())
            self.websocket_clients[client_id] = websocket
            self.runtime.client_connected(client_id)
            
            logger.info(f"WebSocket client {client_id} connected")
            
            try:
                # Send welcome message
                await websocket.send_json({
                    "type": "connected",
                    "client_id": client_id,
                    "timestamp": datetime.now().isoformat()
                })
                
                # Message loop
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
                        # Send ping to keep connection alive
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
            """WebSocket endpoint for WebChat - direct browser interaction."""
            await websocket.accept()
            client_id = f"chat_{uuid.uuid4().hex[:8]}"
            session_id = client_id

            logger.info(f"WebChat client {client_id} connected")

            # Send connected message
            await websocket.send_json({
                "type": "connected",
                "client_id": client_id,
                "timestamp": datetime.now().isoformat()
            })

            # Get or create session
            session = self.runtime.get_or_create_session(session_id, "default")

            try:
                while not self._shutdown_event.is_set():
                    try:
                        data = await asyncio.wait_for(
                            websocket.receive_json(),
                            timeout=30.0
                        )

                        msg_type = data.get("type", "message")

                        if msg_type == "ping":
                            await websocket.send_json({"type": "pong"})
                            continue

                        if msg_type == "message":
                            message = data.get("text", "").strip()
                            if not message:
                                continue

                            # Process message through agent
                            try:
                                config = load_config()
                                agent = _get_or_create_agent(
                                    "default", config,
                                    self.config.provider,
                                    self.config.model
                                )
                                agent_context = create_agent_context(
                                    session_id=session_id,
                                    agent_id="default",
                                    user_id=client_id,
                                    channel_id="web"
                                )

                                # Run agent
                                response = await agent.run(message, session, agent_context)
                                self.runtime.update_session_activity(session_id)

                                await websocket.send_json({
                                    "type": "assistant_message",
                                    "text": response,
                                    "timestamp": datetime.now().isoformat()
                                })

                            except Exception as e:
                                logger.error(f"Agent error: {e}")
                                await websocket.send_json({
                                    "type": "error",
                                    "error": str(e)
                                })

                    except asyncio.TimeoutError:
                        # Send ping to keep connection alive
                        try:
                            await websocket.send_json({"type": "ping"})
                        except:
                            break
                    except WebSocketDisconnect:
                        break
                    except Exception as e:
                        logger.error(f"WebSocket error: {e}")
                        break

            finally:
                logger.info(f"WebChat client {client_id} disconnected")
        
        @self.app.post("/v1/{method}")
        async def rpc_endpoint(method: str, request: Request):
            """HTTP RPC endpoint."""
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
            """WebChat UI."""
            static_dir = os.path.join(os.path.dirname(__file__), "static")
            return FileResponse(os.path.join(static_dir, "chat.html"))
        
        @self.app.get("/control")
        async def control_ui():
            """Control UI (placeholder)."""
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
        """Handle a WebSocket message."""
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
        """Handle an RPC message."""
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
            return {
                "id": msg_id,
                "result": result
            }
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
        """Handle an RPC method call."""
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
        """Register an RPC handler."""
        self.handlers[method] = handler
        logger.debug(f"Registered handler for method: {method}")
    
    async def start(self):
        """Start the Gateway server."""
        # Register default handlers
        register_handlers(self)
        
        # Load configuration
        config = load_config()
        logger.info(f"Loaded configuration from {config}")
        
        # Start server
        config = uvicorn.Config(
            self.app,
            host=self.config.host,
            port=self.config.port,
            log_level="info",
            access_log=False
        )
        
        server = uvicorn.Server(config)
        
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
        """Gracefully shutdown the server."""
        logger.info("Shutting down Gateway...")
        self._shutdown_event.set()
        
        # Close all WebSocket connections
        close_tasks = []
        for client_id, websocket in list(self.websocket_clients.items()):
            close_tasks.append(self._close_websocket(client_id, websocket))
        
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        
        logger.info("Gateway shutdown complete")
    
    async def _close_websocket(self, client_id: str, websocket: WebSocket):
        """Close a WebSocket connection."""
        try:
            await websocket.close()
        except:
            pass
        finally:
            if client_id in self.websocket_clients:
                del self.websocket_clients[client_id]
