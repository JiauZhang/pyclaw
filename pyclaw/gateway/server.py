"""Gateway WebSocket and HTTP server implementation."""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from ..config import PyClawConfig, load_config
from ..channels.web import WebChannelManager
from .runtime import GatewayRuntimeState
from .handlers import register_handlers

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
            description="Personal AI Assistant Gateway",
            version="0.1.0"
        )
        self.runtime = GatewayRuntimeState()
        self.websocket_clients: Dict[str, WebSocket] = {}
        self.handlers: Dict[str, Callable] = {}
        self._shutdown_event = asyncio.Event()
        
        # Web Channel for browser interaction
        self.web_channel = WebChannelManager()
        
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
                "version": "0.1.0",
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
            
            logger.info(f"WebChat client {client_id} connected")
            
            # Define gateway handler for processing messages
            async def gateway_handler(message: str, session_id: str, client_id: str, channel: str):
                """Process message through PyClaw gateway with streaming support."""
                from ..agents import Agent
                from ..gateway.runtime import SessionState
                
                # Get or create session
                session = self.runtime.get_or_create_session(session_id, "default")
                
                # Create agent
                try:
                    agent = Agent(
                        provider=self.config.provider,
                        model=self.config.model
                    )
                except Exception as e:
                    logger.error(f"Agent error in WebSocket handler: {e}")
                    return {
                        "response": f"Error: {str(e)}",
                        "agent_id": "default",
                        "session_id": session_id
                    }
                
                # Create agent context
                from ..agents import AgentContext
                agent_context = AgentContext(
                    session_id=session_id,
                    agent_id="default",
                    user_id=client_id,
                    channel_id=channel
                )
                
                # Update session
                self.runtime.update_session_activity(session_id)
                
                # Return agent and context for streaming
                return {
                    "agent": agent,
                    "session": session,
                    "context": agent_context,
                    "session_id": session_id
                }
            
            # Handle WebSocket through web channel manager
            await self.web_channel.handle_websocket(
                websocket,
                client_id,
                gateway_handler
            )
        
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
            return self._get_chat_html()
        
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
    
    def _get_chat_html(self) -> str:
        """Get the WebChat HTML with direct browser interaction."""
        return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PyClaw WebChat - Direct Browser Channel</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .header {
            background: rgba(26, 26, 46, 0.95);
            color: white;
            padding: 1rem 2rem;
            display: flex;
            align-items: center;
            gap: 1rem;
            backdrop-filter: blur(10px);
        }
        .header h1 { font-size: 1.25rem; font-weight: 600; }
        .header .subtitle {
            font-size: 0.875rem;
            color: #a0aec0;
            margin-left: 1rem;
        }
        .header .status {
            margin-left: auto;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.875rem;
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #10b981;
            animation: pulse 2s infinite;
        }
        .status-dot.disconnected { 
            background: #ef4444; 
            animation: none;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .container {
            flex: 1;
            display: flex;
            overflow: hidden;
            padding: 1rem;
            gap: 1rem;
        }
        .sidebar {
            width: 280px;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 1rem;
            padding: 1.5rem;
            backdrop-filter: blur(10px);
            display: flex;
            flex-direction: column;
        }
        .sidebar h2 {
            font-size: 0.75rem;
            text-transform: uppercase;
            color: #6b7280;
            margin-bottom: 1rem;
            letter-spacing: 0.05em;
        }
        .session-list {
            flex: 1;
            overflow-y: auto;
        }
        .session-item {
            padding: 0.875rem;
            border-radius: 0.75rem;
            cursor: pointer;
            margin-bottom: 0.5rem;
            font-size: 0.875rem;
            transition: all 0.2s;
            border: 2px solid transparent;
        }
        .session-item:hover { 
            background: #f3f4f6; 
            transform: translateX(4px);
        }
        .session-item.active { 
            background: #eff6ff; 
            color: #2563eb;
            border-color: #2563eb;
        }
        .new-session-btn {
            margin-top: auto;
            padding: 0.75rem;
            background: #2563eb;
            color: white;
            border: none;
            border-radius: 0.5rem;
            cursor: pointer;
            font-weight: 500;
            transition: all 0.2s;
        }
        .new-session-btn:hover { background: #1d4ed8; }
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 1rem;
            overflow: hidden;
            backdrop-filter: blur(10px);
        }
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 2rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }
        .welcome-message {
            text-align: center;
            padding: 3rem 2rem;
            color: #6b7280;
        }
        .welcome-message h2 {
            color: #1f2937;
            margin-bottom: 1rem;
            font-size: 1.5rem;
        }
        .welcome-message p {
            margin-bottom: 0.5rem;
            line-height: 1.6;
        }
        .message {
            max-width: 80%;
            padding: 1rem 1.25rem;
            border-radius: 1rem;
            line-height: 1.6;
            animation: fadeIn 0.3s ease;
            position: relative;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .message.user {
            align-self: flex-end;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-bottom-right-radius: 0.25rem;
        }
        .message.assistant {
            align-self: flex-start;
            background: #f3f4f6;
            color: #1f2937;
            border-bottom-left-radius: 0.25rem;
        }
        .message.assistant.streaming {
            background: #f3f4f6;
            color: #1f2937;
            border-bottom-left-radius: 0.25rem;
        }
        .message.assistant.streaming .stream-content {
            min-height: 1.5rem;
        }
        .message.assistant.streaming::after {
            content: '▋';
            animation: blink 1s infinite;
            margin-left: 2px;
        }
        @keyframes blink {
            0%, 50% { opacity: 1; }
            51%, 100% { opacity: 0; }
        }
        .message.system {
            align-self: center;
            background: #fef3c7;
            color: #92400e;
            font-size: 0.875rem;
            max-width: 60%;
            text-align: center;
        }
        .message .time {
            font-size: 0.75rem;
            opacity: 0.7;
            margin-top: 0.5rem;
        }
        .message.assistant .time { color: #6b7280; }
        .input-area {
            padding: 1.5rem 2rem;
            border-top: 1px solid #e5e7eb;
            display: flex;
            gap: 0.75rem;
            background: white;
        }
        .input-area input {
            flex: 1;
            padding: 0.875rem 1rem;
            border: 2px solid #e5e7eb;
            border-radius: 0.75rem;
            font-size: 0.9375rem;
            outline: none;
            transition: all 0.2s;
        }
        .input-area input:focus { 
            border-color: #2563eb; 
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
        }
        .input-area button {
            padding: 0.875rem 1.75rem;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 0.75rem;
            cursor: pointer;
            font-weight: 500;
            font-size: 0.9375rem;
            transition: all 0.2s;
        }
        .input-area button:hover { 
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }
        .input-area button:disabled { 
            opacity: 0.5; 
            cursor: not-allowed;
            transform: none;
        }
        .typing {
            display: none;
            align-self: flex-start;
            margin-left: 2rem;
            margin-bottom: 1rem;
        }
        .typing.show { display: block; }
        .typing-bubble {
            display: flex;
            gap: 0.25rem;
            padding: 1rem 1.25rem;
            background: #f3f4f6;
            border-radius: 1rem;
            border-bottom-left-radius: 0.25rem;
        }
        .typing-bubble span {
            width: 8px;
            height: 8px;
            background: #9ca3af;
            border-radius: 50%;
            animation: typing 1.4s infinite;
        }
        .typing-bubble span:nth-child(2) { animation-delay: 0.2s; }
        .typing-bubble span:nth-child(3) { animation-delay: 0.4s; }
        @keyframes typing {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-10px); }
        }
        .command-hint {
            font-size: 0.75rem;
            color: #6b7280;
            padding: 0.5rem 2rem;
            background: #f9fafb;
            border-top: 1px solid #e5e7eb;
        }
    </style>
</head>
<body>
    <div class="header">
        <span>🦞</span>
        <h1>PyClaw WebChat</h1>
        <span class="subtitle">Direct Browser Channel - No Social Apps Needed</span>
        <div class="status">
            <span class="status-dot" id="status-dot"></span>
            <span id="status-text">Connecting...</span>
        </div>
    </div>
    <div class="container">
        <div class="sidebar">
            <h2>Active Sessions</h2>
            <div class="session-list" id="session-list">
                <div class="session-item active" data-session="default" onclick="switchSession('default')">
                    💬 Default Session
                </div>
            </div>
            <button class="new-session-btn" onclick="createNewSession()">+ New Session</button>
        </div>
        <div class="chat-area">
            <div class="messages" id="messages">
                <div class="welcome-message">
                    <h2>👋 Welcome to PyClaw!</h2>
                    <p>This is a direct browser channel - no social apps or messaging platforms needed.</p>
                    <p>Your conversation happens right here in the browser via WebSocket.</p>
                    <p style="margin-top: 1rem; font-size: 0.875rem;">
                        Try these commands:<br>
                        <code>/help</code> - Show available commands<br>
                        <code>/time</code> - Get current time<br>
                        <code>/status</code> - Check session status
                    </p>
                </div>
            </div>
            <div class="typing" id="typing">
                <div class="typing-bubble">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
            <div class="command-hint">
                Commands: /help, /time, /date, /status, /reset
            </div>
            <div class="input-area">
                <input type="text" id="message-input" placeholder="Type a message or command..." disabled>
                <button id="send-btn" disabled>Send</button>
            </div>
        </div>
    </div>

    <script>
        // Use the dedicated chat WebSocket endpoint
        const ws = new WebSocket(`ws://${window.location.host}/chat/ws`);
        const messagesEl = document.getElementById('messages');
        const inputEl = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');
        const typingEl = document.getElementById('typing');
        const statusDot = document.getElementById('status-dot');
        const statusText = document.getElementById('status-text');
        const sessionListEl = document.getElementById('session-list');
        
        let currentSession = 'default';
        let sessions = { default: [] };
        let clientId = null;
        let currentStreamingMessage = null;
        
        ws.onopen = () => {
            statusDot.classList.remove('disconnected');
            statusText.textContent = 'Connected';
            inputEl.disabled = false;
            sendBtn.disabled = false;
            inputEl.focus();
            addMessage('system', '✅ Connected to PyClaw Gateway via Web Channel');
        };
        
        ws.onclose = () => {
            statusDot.classList.add('disconnected');
            statusText.textContent = 'Disconnected';
            inputEl.disabled = true;
            sendBtn.disabled = true;
            addMessage('system', '❌ Disconnected from gateway');
        };
        
        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            addMessage('system', '⚠️ Connection error');
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log('Received:', data);
            
            if (data.type === 'connected') {
                clientId = data.client_id;
                return;
            }
            
            if (data.type === 'pong') {
                return;
            }
            
            if (data.type === 'stream_chunk') {
                // Handle streaming chunk
                if (!currentStreamingMessage) {
                    // Create new message element for streaming
                    currentStreamingMessage = createStreamingMessage();
                    messagesEl.appendChild(currentStreamingMessage);
                    messagesEl.scrollTop = messagesEl.scrollHeight;
                }
                // Append chunk to current streaming message
                appendToStreamingMessage(currentStreamingMessage, data.text);
                return;
            }
            
            if (data.type === 'stream_complete') {
                // Streaming complete
                if (currentStreamingMessage) {
                    finalizeStreamingMessage(currentStreamingMessage, data.full_response);
                    // Save to session history
                    if (!sessions[currentSession]) sessions[currentSession] = [];
                    sessions[currentSession].push({ role: 'assistant', text: data.full_response });
                    currentStreamingMessage = null;
                }
                typingEl.classList.remove('show');
                return;
            }
            
            if (data.type === 'assistant_message' || data.type === 'response') {
                typingEl.classList.remove('show');
                addMessage('assistant', data.text);
                // Save to session history
                if (!sessions[currentSession]) sessions[currentSession] = [];
                sessions[currentSession].push({ role: 'assistant', text: data.text });
            }
            
            if (data.type === 'error') {
                typingEl.classList.remove('show');
                addMessage('system', `⚠️ ${data.text}`);
            }
            
            if (data.status === 'received') {
                // Message received confirmation
            }
        };
        
        function addMessage(role, text) {
            // Remove welcome message if it exists and we're adding our first real message
            if (role !== 'system' && messagesEl.querySelector('.welcome-message')) {
                messagesEl.innerHTML = '';
            }
            
            const div = document.createElement('div');
            div.className = `message ${role}`;
            const time = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            
            // Convert newlines to <br>
            const formattedText = escapeHtml(text).replace(/\\n/g, '<br>');
            
            div.innerHTML = `${formattedText}<div class="time">${time}</div>`;
            messagesEl.appendChild(div);
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }
        
        function createStreamingMessage() {
            // Remove welcome message if it exists
            if (messagesEl.querySelector('.welcome-message')) {
                messagesEl.innerHTML = '';
            }
            
            const div = document.createElement('div');
            div.className = 'message assistant streaming';
            div.innerHTML = '<div class="stream-content"></div>';
            return div;
        }
        
        function appendToStreamingMessage(messageEl, text) {
            const contentEl = messageEl.querySelector('.stream-content');
            if (contentEl) {
                // Escape HTML and append
                const escapedText = escapeHtml(text);
                contentEl.innerHTML += escapedText.replace(/\\n/g, '<br>');
                messagesEl.scrollTop = messagesEl.scrollHeight;
            }
        }
        
        function finalizeStreamingMessage(messageEl, fullText) {
            const time = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            messageEl.classList.remove('streaming');
            messageEl.innerHTML = `${escapeHtml(fullText).replace(/\\n/g, '<br>')}<div class="time">${time}</div>`;
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function sendMessage() {
            const text = inputEl.value.trim();
            if (!text || !clientId) return;
            
            // Add to UI
            addMessage('user', text);
            
            // Save to session
            if (!sessions[currentSession]) sessions[currentSession] = [];
            sessions[currentSession].push({ role: 'user', text: text });
            
            // Clear input
            inputEl.value = '';
            
            // Show typing indicator
            typingEl.classList.add('show');
            
            // Send via WebSocket
            ws.send(JSON.stringify({
                type: 'message',
                text: text,
                session_id: currentSession,
                timestamp: new Date().toISOString()
            }));
        }
        
        function createNewSession() {
            const sessionId = 'session_' + Date.now();
            sessions[sessionId] = [];
            
            const sessionDiv = document.createElement('div');
            sessionDiv.className = 'session-item';
            sessionDiv.setAttribute('data-session', sessionId);
            sessionDiv.onclick = () => switchSession(sessionId);
            sessionDiv.innerHTML = `💬 Session ${Object.keys(sessions).length}`;
            sessionListEl.appendChild(sessionDiv);
            
            switchSession(sessionId);
        }
        
        function switchSession(sessionId) {
            // Update UI
            document.querySelectorAll('.session-item').forEach(el => {
                el.classList.remove('active');
            });
            document.querySelector(`[data-session="${sessionId}"]`).classList.add('active');
            
            // Switch session
            currentSession = sessionId;
            
            // Clear and reload messages
            messagesEl.innerHTML = '';
            if (sessions[sessionId]) {
                sessions[sessionId].forEach(msg => {
                    addMessage(msg.role, msg.text);
                });
            }
            
            addMessage('system', `Switched to ${sessionId}`);
        }
        
        // Event listeners
        sendBtn.addEventListener('click', sendMessage);
        inputEl.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
        
        // Keep connection alive
        setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);
    </script>
</body>
</html>
        """
