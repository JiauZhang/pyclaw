"""WebSocket-based browser channel for direct browser interaction."""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, AsyncIterator, Callable
from datetime import datetime

from .base import ChannelAdapter, InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)


class WebChannelAdapter(ChannelAdapter):
    """
    WebSocket-based channel adapter for browser interaction.
    
    This adapter allows direct communication between the browser and OpenClaw
    without requiring any external messaging platform.
    """
    
    channel_id = "web"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._message_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._message_handler: Optional[Callable[[InboundMessage, str], asyncio.Future]] = None
        
    async def connect(self) -> bool:
        """Connect the web channel (always returns True as it's passive)."""
        self._connected = True
        logger.info("Web channel adapter initialized")
        return True
    
    async def disconnect(self):
        """Disconnect the web channel."""
        self._connected = False
        self._clients.clear()
        logger.info("Web channel adapter disconnected")
    
    async def send_message(self, to: str, message: OutboundMessage) -> bool:
        """
        Send a message to a browser client.
        
        Args:
            to: Client session ID
            message: Message to send
        """
        if to not in self._clients:
            logger.warning(f"Client {to} not found")
            return False
        
        client_info = self._clients[to]
        websocket = client_info.get("websocket")
        
        if not websocket:
            return False
        
        try:
            payload = {
                "type": "message",
                "text": message.text,
                "timestamp": datetime.now().isoformat(),
                "metadata": message.metadata
            }
            
            await websocket.send_json(payload)
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {to}: {e}")
            return False
    
    async def receive_messages(self) -> AsyncIterator[InboundMessage]:
        """Receive messages from the queue."""
        while self._connected:
            try:
                message = await asyncio.wait_for(
                    self._message_queue.get(),
                    timeout=1.0
                )
                yield message
            except asyncio.TimeoutError:
                continue
    
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """Get user info for a web client."""
        if user_id in self._clients:
            client = self._clients[user_id]
            return {
                "id": user_id,
                "name": client.get("name", "Web User"),
                "channel": "web",
                "connected_at": client.get("connected_at")
            }
        return {"id": user_id, "name": "Unknown"}
    
    # WebSocket-specific methods
    
    async def register_client(self, client_id: str, websocket, name: str = "Web User"):
        """Register a new WebSocket client."""
        self._clients[client_id] = {
            "websocket": websocket,
            "name": name,
            "connected_at": datetime.now().isoformat(),
            "session_id": client_id
        }
        logger.info(f"Web client registered: {client_id}")
    
    async def unregister_client(self, client_id: str):
        """Unregister a WebSocket client."""
        if client_id in self._clients:
            del self._clients[client_id]
            logger.info(f"Web client unregistered: {client_id}")
    
    async def handle_incoming_message(
        self,
        client_id: str,
        data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Handle an incoming message from a WebSocket client.
        
        Args:
            client_id: Client identifier
            data: Message data
            
        Returns:
            Response data if immediate response needed
        """
        msg_type = data.get("type", "message")
        
        if msg_type == "message":
            # Create inbound message
            inbound = InboundMessage(
                id=f"web_{datetime.now().timestamp()}",
                text=data.get("text", ""),
                sender_id=client_id,
                sender_name=self._clients.get(client_id, {}).get("name", "Web User"),
                channel_id=self.channel_id,
                thread_id=data.get("thread_id"),
                metadata={
                    "client_id": client_id,
                    "raw_data": data
                }
            )
            
            # Add to queue for processing
            await self._message_queue.put(inbound)
            
            # If handler is set, process immediately
            if self._message_handler:
                try:
                    await self._message_handler(inbound, self.channel_id)
                except Exception as e:
                    logger.error(f"Error handling message: {e}")
            
            return {"status": "received", "message_id": inbound.id}
        
        elif msg_type == "ping":
            return {"type": "pong", "timestamp": datetime.now().isoformat()}
        
        elif msg_type == "typing":
            # Broadcast typing indicator to other clients if needed
            return None
        
        return None
    
    async def send_response(
        self,
        client_id: str,
        text: str,
        message_type: str = "response",
        extra_data: Optional[Dict] = None
    ):
        """Send a response to a specific client."""
        if client_id not in self._clients:
            logger.warning(f"Cannot send response, client {client_id} not found")
            return
        
        websocket = self._clients[client_id].get("websocket")
        if not websocket:
            return
        
        payload = {
            "type": message_type,
            "text": text,
            "timestamp": datetime.now().isoformat()
        }
        
        if extra_data:
            payload.update(extra_data)
        
        try:
            await websocket.send_json(payload)
        except Exception as e:
            logger.error(f"Failed to send response to {client_id}: {e}")
    
    async def broadcast(self, text: str, exclude_client: Optional[str] = None):
        """Broadcast a message to all connected clients."""
        for client_id, client_info in self._clients.items():
            if client_id == exclude_client:
                continue
            
            websocket = client_info.get("websocket")
            if websocket:
                try:
                    await websocket.send_json({
                        "type": "broadcast",
                        "text": text,
                        "timestamp": datetime.now().isoformat()
                    })
                except Exception as e:
                    logger.error(f"Failed to broadcast to {client_id}: {e}")
    
    def get_connected_clients(self) -> Dict[str, Dict[str, Any]]:
        """Get all connected clients."""
        return self._clients.copy()
    
    def is_client_connected(self, client_id: str) -> bool:
        """Check if a client is connected."""
        return client_id in self._clients


class WebChannelManager:
    """
    Manager for web channel connections.
    
    Handles multiple browser clients and routes messages between them
    and the OpenClaw gateway.
    """
    
    def __init__(self):
        self.adapter = WebChannelAdapter({})
        self._client_sessions: Dict[str, str] = {}  # Maps client_id to session_id
    
    async def start(self):
        """Start the web channel manager."""
        await self.adapter.connect()
        logger.info("Web channel manager started")
    
    async def stop(self):
        """Stop the web channel manager."""
        await self.adapter.disconnect()
        logger.info("Web channel manager stopped")
    
    async def handle_websocket(
        self,
        websocket,
        client_id: str,
        gateway_handler: Optional[Callable] = None
    ):
        """
        Handle a WebSocket connection.
        
        Args:
            websocket: The WebSocket object
            client_id: Unique client identifier
            gateway_handler: Handler function for processing messages
        """
        await self.adapter.register_client(client_id, websocket)
        
        try:
            # Send welcome message
            await websocket.send_json({
                "type": "connected",
                "client_id": client_id,
                "channel": "web",
                "timestamp": datetime.now().isoformat()
            })
            
            # Message loop
            while True:
                try:
                    data = await websocket.receive_json()
                    
                    # Handle the message
                    response = await self.adapter.handle_incoming_message(
                        client_id,
                        data
                    )
                    
                    # Send immediate response if any
                    if response:
                        await websocket.send_json(response)
                    
                    # Process through gateway if handler provided
                    if gateway_handler and data.get("type") == "message":
                        # Create a task to handle the message asynchronously
                        asyncio.create_task(
                            self._process_through_gateway(
                                client_id,
                                data,
                                gateway_handler
                            )
                        )
                
                except Exception as e:
                    logger.error(f"Error handling WebSocket message: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "error": str(e)
                    })
        
        finally:
            await self.adapter.unregister_client(client_id)
    
    async def _process_through_gateway(
        self,
        client_id: str,
        data: Dict[str, Any],
        gateway_handler: Callable
    ):
        """Process a message through the OpenClaw gateway."""
        try:
            # Get or create session for this client
            session_id = self._client_sessions.get(client_id, f"web_{client_id}")
            self._client_sessions[client_id] = session_id
            
            # Call gateway handler
            result = await gateway_handler(
                message=data.get("text", ""),
                session_id=session_id,
                client_id=client_id,
                channel="web"
            )
            
            # Send response back to client
            if result:
                await self.adapter.send_response(
                    client_id,
                    result.get("response", ""),
                    message_type="assistant_message",
                    extra_data={
                        "session_id": session_id,
                        "agent_id": result.get("agent_id")
                    }
                )
        
        except Exception as e:
            logger.error(f"Error processing through gateway: {e}")
            await self.adapter.send_response(
                client_id,
                f"Error: {str(e)}",
                message_type="error"
            )
    
    async def send_to_client(self, client_id: str, text: str):
        """Send a message to a specific client."""
        await self.adapter.send_response(client_id, text)
    
    async def broadcast(self, text: str, exclude_client: Optional[str] = None):
        """Broadcast to all clients."""
        await self.adapter.broadcast(text, exclude_client)
