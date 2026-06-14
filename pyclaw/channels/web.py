import asyncio
import json
import logging
from typing import Dict, Any, Optional, AsyncIterator, Callable
from datetime import datetime

from .base import ChannelAdapter, InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)


class WebChannelAdapter(ChannelAdapter):
    channel_id = "web"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._message_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._client_sessions: Dict[str, str] = {}
        self._message_handler: Optional[Callable[[InboundMessage, str], asyncio.Future]] = None

    async def connect(self) -> bool:
        self._connected = True
        logger.info("Web channel adapter initialized")
        return True

    async def disconnect(self):
        self._connected = False
        self._clients.clear()
        logger.info("Web channel adapter disconnected")

    async def send_message(self, to: str, message: OutboundMessage) -> bool:
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
        if user_id in self._clients:
            client = self._clients[user_id]
            return {
                "id": user_id,
                "name": client.get("name", "Web User"),
                "channel": "web",
                "connected_at": client.get("connected_at")
            }
        return {"id": user_id, "name": "Unknown"}

    async def register_client(self, client_id: str, websocket, name: str = "Web User"):
        self._clients[client_id] = {
            "websocket": websocket,
            "name": name,
            "connected_at": datetime.now().isoformat(),
            "session_id": client_id
        }
        logger.info(f"Web client registered: {client_id}")

    async def unregister_client(self, client_id: str):
        if client_id in self._clients:
            del self._clients[client_id]
            logger.info(f"Web client unregistered: {client_id}")

    async def handle_incoming_message(
        self,
        client_id: str,
        data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        msg_type = data.get("type", "message")

        if msg_type == "message":
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

            await self._message_queue.put(inbound)

            return {"status": "received", "message_id": inbound.id}

        elif msg_type == "ping":
            return {"type": "pong", "timestamp": datetime.now().isoformat()}

        elif msg_type == "typing":
            return None

        return None

    async def send_response(
        self,
        client_id: str,
        text: str,
        message_type: str = "response",
        extra_data: Optional[Dict] = None
    ):
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

    async def handle_websocket(
        self,
        websocket,
        client_id: str,
        agent,
        runtime
    ):
        await self.register_client(client_id, websocket)
        try:
            await websocket.send_json({
                "type": "connected",
                "client_id": client_id,
                "channel": "web",
                "timestamp": datetime.now().isoformat()
            })
            while True:
                try:
                    data = await websocket.receive_json()
                    response = await self.handle_incoming_message(client_id, data)
                    if response:
                        await websocket.send_json(response)
                    if data.get("type") == "message":
                        asyncio.create_task(
                            self._process_message(client_id, data, agent, runtime)
                        )
                except Exception as e:
                    logger.error(f"Error handling WebSocket message: {e}")
                    await websocket.send_json({"type": "error", "error": str(e)})
        finally:
            await self.unregister_client(client_id)

    async def _process_message(
        self,
        client_id: str,
        data: Dict[str, Any],
        agent,
        runtime
    ):
        try:
            session_id = self._client_sessions.get(client_id, f"web_{client_id}")
            self._client_sessions[client_id] = session_id
            runtime.get_or_create_session(session_id, "default")
            message = data.get("text", "")

            full_response = ""
            for chunk in agent.chat_stream(message):
                if chunk:
                    full_response += chunk
                    await self.send_response(
                        client_id,
                        chunk,
                        message_type="stream_chunk",
                        extra_data={"session_id": session_id, "agent_id": "default", "is_final": False},
                    )

            await self.send_response(
                client_id,
                "",
                message_type="stream_complete",
                extra_data={"session_id": session_id, "agent_id": "default", "is_final": True, "full_response": full_response},
            )

            runtime.update_session_activity(session_id)
            runtime.increment_requests()
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            await self.send_response(client_id, f"Error: {str(e)}", message_type="error")
            runtime.increment_errors()
