import asyncio
import json
import logging
from typing import Dict, Any, Optional, AsyncIterator, Callable
from datetime import datetime

from chatchat.hooks.events import AGENT_WARN
from starlette.websockets import WebSocketDisconnect
from .base import ChannelAdapter, InboundMessage, OutboundMessage
from ..slash import handle_slash
from ..session.store import (append_conv, follow_conversation,
                             record_meta)

logger = logging.getLogger(__name__)


class WebChannelAdapter(ChannelAdapter):
    channel_id = "web"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._message_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._client_sessions: Dict[str, str] = {}
        self._busy: set = set()
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
            logger.error(f"Web send to {to} failed: {e}")
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
            if message_type in ("stream_chunk", "stream_complete", "message"):
                logger.info("web sent to %s [%s]: %s", client_id, message_type, text)
        except Exception as e:
            logger.error(f"Failed to send response to {client_id}: {e}")

    async def handle_websocket(
        self,
        websocket,
        client_id: str,
        get_session,
        runtime
    ):
        await self.register_client(client_id, websocket)
        session = None
        try:
            await websocket.send_json({
                "type": "connected",
                "client_id": client_id,
                "session_id": client_id,
                "channel": "web",
                "timestamp": datetime.now().isoformat()
            })
            # 会话在连接时立即创建：环境/磁盘类错误（如 HOME 解析不出、
            # 目录不可写）在连接阶段就暴露给客户端，而不是第一条消息后。
            try:
                session = await get_session()
            except Exception as exc:
                logger.exception("Failed to create web session")
                await websocket.send_json({
                    "type": "error",
                    "text": f"Failed to start the session: {exc}",
                })
            while True:
                try:
                    data = await websocket.receive_json()
                except WebSocketDisconnect:
                    logger.info("Web client disconnected: %s", client_id)
                    break
                msg_type = data.get("type", "message")
                if msg_type == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": datetime.now().isoformat(),
                    })
                    continue
                if msg_type == "abort":
                    if session is not None:
                        session._team.lead.abort_work()
                    await websocket.send_json({"type": "aborted"})
                    continue
                if msg_type != "message":
                    continue
                if session is None:
                    try:
                        session = await get_session()
                    except Exception as exc:
                        logger.exception("Failed to create web session")
                        await websocket.send_json({
                            "type": "error",
                            "text": f"Failed to start the session: {exc}",
                        })
                        continue
                if client_id in self._busy:
                    await websocket.send_json({
                        "type": "error",
                        "text": "A turn is already running in this conversation.",
                    })
                    continue
                self._busy.add(client_id)
                asyncio.create_task(
                    self._process_message(client_id, data, session, runtime)
                )
        finally:
            await self.unregister_client(client_id)

    async def _finish_stream(self, client_id, session_id, session,
                            full_response):
        entry = session.record_turn() or {}
        await self.send_response(
            client_id,
            "",
            message_type="stream_complete",
            extra_data={
                "session_id": session_id,
                "agent_id": session.name,
                "is_final": True,
                "full_response": full_response,
                "model": entry.get('model') or getattr(session, 'model', ''),
                "mode": getattr(session, 'mode', ''),
                "usage": {"input": entry.get('input', 0),
                          "output": entry.get('output', 0),
                          "cached": entry.get('cached', 0),
                          "seconds": entry.get('seconds', 0)},
            },
        )

    async def _process_message(
        self,
        client_id: str,
        data: Dict[str, Any],
        session,
        runtime
    ):
        try:
            session_id = session.conv_session_id
            self._client_sessions[client_id] = session_id
            record_meta(session_id, {"channel": "web", "client_id": client_id})
            runtime.get_or_create_session(session_id, session.name)
            message = data.get("text", "")
            append_conv(session_id, "user", message)

            slash_reply = await handle_slash(message, session, session_id)
            if slash_reply is not None:
                if isinstance(slash_reply, tuple):
                    info, follow = slash_reply
                    if info:
                        await self.send_response(
                            client_id,
                            text=info,
                            message_type="stream_chunk",
                            extra_data={"session_id": session_id, "agent_id": session.name, "is_final": False},
                        )
                    message = follow
                else:
                    await self.send_response(
                        client_id,
                        text=slash_reply,
                        message_type="stream_chunk",
                        extra_data={"session_id": session_id, "agent_id": session.name, "is_final": False},
                    )
                    await self._finish_stream(client_id, session_id, session, slash_reply)
                    runtime.update_session_activity(session_id)
                    runtime.increment_requests()
                    return

            async def on_event(ev):
                if ev.kind == AGENT_WARN:
                    await self.send_response(client_id, text=ev.data.get('text', ''),
                                             message_type="error")
                    return
                if ev.kind in ('agent.text',):
                    return
                await self.send_response(
                    client_id,
                    text='',
                    message_type="progress",
                    extra_data={
                        "kind": ev.kind,
                        "tool": ev.data.get('tool') or ev.data.get('name', ''),
                        "input": ev.data.get('input'),
                        "agent": ev.agent,
                    },
                )

            full_response = ""
            async for chunk in session.stream(message, on_event=on_event):
                if chunk:
                    full_response += chunk
                    await self.send_response(
                        client_id,
                        chunk,
                        message_type="stream_chunk",
                        extra_data={"session_id": session_id, "agent_id": session.name, "is_final": False},
                    )

            await self._finish_stream(client_id, session_id, session, full_response)

            runtime.update_session_activity(session_id)
            runtime.increment_requests()
            logger.info("web replied to %s (%d chars)", client_id, len(full_response))
        except Exception as e:
            logger.exception("Error processing message")
            await self.send_response(client_id, f"Error: {e}", message_type="error")
            runtime.increment_errors()
        finally:
            self._busy.discard(client_id)
