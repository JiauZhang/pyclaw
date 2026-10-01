import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from pyclaw.version import __version__
from pyclaw import config as app_config
from pyclaw import cost

from ..session import Session
from ..session.store import (append_conv, follow_conversation, record_meta,
                             resolve_session_id, list_sessions, title_of,
                             conversation_messages, rename_session,
                             delete_conversation, create_branch)
from ..cron import run as run_cron_loop
from chatchat.tasks.cron_schedule import SchedulerLock
from pyclaw.team.builder import IM_EXTRA, build_team, configured_context_window
from pyclaw.tui.readout import git_label, git_status
from pyclaw.tui.formatting import _display_cwd
from chatchat.hooks.events import (
    AGENT_REASON_START,
    AGENT_TOOL_CALL,
    AGENT_WARN,
)
from ..channels import IMChannelAdapter, OutboundMessage
from ..channels.web import WebChannelAdapter
from ..slash import handle_slash, skill_rows, suggest
from ..channels.im_formatter import IMStatusTracker, split_long_message
from .im import (_friendly_channel_error, _im_progress_text,
                 run_im_interaction)
from .runtime import GatewayRuntimeState
from .handlers import register_handlers

logger = logging.getLogger(__name__)


def _clean_conversation_id(value) -> str:
    """Conversation ids name a directory under the PyClaw home, so only hex
    ids of the shape PyClaw itself issues are accepted from outside."""
    value = str(value or '').strip().lower()
    return value if re.fullmatch(r'[0-9a-f]{16,64}', value) else ''




@dataclass
class GatewayConfig:
    port: int = 12321
    host: str = "127.0.0.1"
    cors_origins: List[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    enabled_channels: List[str] = field(default_factory=lambda: ["wechat"])
    use_team: bool = False
    # Headless API mode (`pyclaw api`): when set, every /chat/api and /chat/ws
    # request must present this token, and the server binds the configured port
    # even when it is 0 (OS-assigned) so the real port can be reported.
    local_token: Optional[str] = None


class LocalTokenMiddleware(BaseHTTPMiddleware):
    """Guards /chat/api routes with a per-process token in headless API mode
    (`pyclaw api`). The WebSocket route validates the token itself before
    accepting, since HTTP middleware does not cover websocket scopes."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/chat/api"):
            supplied = (request.headers.get("x-pyclaw-token")
                        or request.query_params.get("token"))
            if supplied != self._token:
                return JSONResponse(status_code=401,
                                    content={"error": "invalid or missing token"})
        return await call_next(request)


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
        self._cron_task: Optional[asyncio.Task] = None
        self._cron_lock: Optional[SchedulerLock] = None
        self._cron_busy = asyncio.Lock()
        self.web_channel = WebChannelAdapter({})
        self._git_labels: Dict[str, str] = {}
        self.bound_port: Optional[int] = None
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
        if self.config.local_token:
            self.app.add_middleware(LocalTokenMiddleware, token=self.config.local_token)

    @property
    def webchat_enabled(self) -> bool:
        return 'web' in self.config.enabled_channels

    def _new_session(self, conversation_id: str, provider=None,
                     model=None) -> Session:
        return Session(build_team(
            provider=provider or self.config.provider,
            model=model or self.config.model,
            http_options={'timeout': 300},
            conversation_id=conversation_id,
            use_team=self.config.use_team,
        ), session_id=conversation_id)

    async def _get_session(self, session_key: str, provider=None, model=None) -> Session:
        session = self._sessions.get(session_key)
        if session is None:
            session = self._new_session(session_key, provider, model)
            session.restore_transcript()
            self._sessions[session_key] = session
            self.runtime.get_or_create_session(session_key, session.name)
        return session

    async def _remove_session(self, session_key: str):
        session = self._sessions.pop(session_key, None)
        if session is not None:
            await session.close()
            self.runtime.delete_session(session_key)

    CRON_KEY = 'cron'

    def _cron_conversation(self) -> str:
        return resolve_session_id([self.CRON_KEY])

    async def _start_cron(self):
        session = await self._get_session(self._cron_conversation())
        store = session.team.cron
        self._cron_lock = SchedulerLock(store.directory,
                                        f'gateway-{os.getpid()}')
        self._cron_task = asyncio.create_task(
            run_cron_loop(store, self._cron_lock, self._deliver_cron))

    async def _stop_cron(self):
        if self._cron_task is not None:
            self._cron_task.cancel()
            self._cron_task = None
        if self._cron_lock is not None:
            self._cron_lock.release()
            self._cron_lock = None

    async def _deliver_cron(self, task: dict):
        prompt = str(task.get('prompt') or '')
        if not prompt:
            return
        session = await self._get_session(self._cron_conversation())
        name = task.get('agent')
        if name:
            agent = session.team.get_by_name(str(name))
            if agent is None:
                store = getattr(session.team, 'cron', None)
                if store is not None:
                    store.remove(task.get('id'))
                return
            agent.submit(prompt)
            return
        async with self._cron_busy:
            conversation = session.conv_session_id
            append_conv(conversation, 'user', prompt)
            record_meta(conversation, {'channel': 'cron'})
            try:
                response = await session.chat(prompt)
            finally:
                session.record_turn()
        if response:
            await self._push_to_channels(response)

    async def _push_to_channels(self, text: str):
        for adapter in list(self.channels.values()):
            contact = adapter.known_contact()
            if not contact:
                logger.info(
                    "Channel '%s' has no known contact; cron push skipped",
                    adapter.channel_id)
                continue
            for part in split_long_message(text, 1500):
                try:
                    await adapter.send_message(contact,
                                               OutboundMessage(text=part))
                except Exception as exc:
                    logger.warning("Cron push via '%s' failed: %s",
                                   adapter.channel_id, exc)

    async def commands_payload(self, q: str = "") -> dict:
        session = next(iter(self._sessions.values()), None)
        skills = skill_rows(session) if session is not None else []
        return {"commands": [
            {"name": row["name"], "desc": row["desc"],
             "hint": row.get("hint", "")}
            for row in suggest(f"/{q.strip().lstrip('/')}", skills)]}

    async def session_status(self, conversation_id: str) -> dict:
        """The numbers the status bar shows: the same readouts the TUI paints
        in its hud rows, and an idle default before the first turn."""
        session = self._sessions.get(conversation_id)
        if session is None:
            return {
                "session_id": conversation_id, "idle": True,
                "model": self.config.model or '',
                "provider": self.config.provider or '',
                "mode": "default",
                "context": {"used": 0,
                            "window": int(configured_context_window() or 0),
                            "percent": None},
                "usage": {"input": 0, "output": 0, "cached": 0, "total": 0},
                "messages": 0, "elapsed": 0, "cost": None,
            }
        usage = session.usage
        details = getattr(usage, 'prompt_tokens_details', None) or {}
        amount = cost.usage_cost(session.model, usage,
                                 app_config.load().get('pricing') or {})
        window = int(session.context_window or 0)
        used = int(session.used_context)
        cwd = session.cwd
        if cwd not in self._git_labels:
            self._git_labels[cwd] = git_label(git_status(cwd))
        return {
            "session_id": conversation_id, "idle": False,
            "model": session.model,
            "provider": session.provider,
            "mode": session.permission_mode,
            "context": {"used": used, "window": window,
                        "percent": round(used / window * 100) if window
                        else None,
                        "compact_threshold": int(session.compact_threshold),
                        "auto_compact": session.auto_compact},
            "usage": {"input": usage.prompt_tokens,
                      "output": usage.completion_tokens,
                      "cached": int(details.get('cached_tokens') or 0),
                      "total": usage.total_tokens},
            "messages": len(session.transcript()),
            "elapsed": session.elapsed_seconds,
            "cwd": _display_cwd(cwd),
            "git": self._git_labels[cwd],
            "cost": amount,
        }

    async def delete_conversation(self, conversation_id: str) -> dict:
        conversation_id = _clean_conversation_id(conversation_id)
        if not conversation_id:
            raise HTTPException(status_code=400,
                                detail="invalid conversation id")
        session = self._sessions.pop(conversation_id, None)
        if session is not None:
            await session.close()
        removed = delete_conversation(conversation_id)
        return {"id": conversation_id, "deleted": removed}

    def rename_conversation(self, conversation_id: str, title: str) -> dict:
        conversation_id = _clean_conversation_id(conversation_id)
        if not conversation_id:
            raise HTTPException(status_code=400,
                                detail="invalid conversation id")
        return {"id": conversation_id,
                "title": rename_session(conversation_id, title)}

    def branch_conversation(self, conversation_id: str) -> dict:
        conversation_id = _clean_conversation_id(conversation_id)
        if not conversation_id:
            raise HTTPException(status_code=400,
                                detail="invalid conversation id")
        try:
            return create_branch(conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

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
            if (self.config.local_token
                    and websocket.query_params.get("token") != self.config.local_token):
                await websocket.close(code=4401)
                return
            await websocket.accept()
            conversation_id = _clean_conversation_id(
                websocket.query_params.get("session_id"))
            if not conversation_id:
                conversation_id = uuid.uuid4().hex
            logger.info("WebChat client for conversation %s connected",
                        conversation_id)

            try:
                await self.web_channel.handle_websocket(
                    websocket,
                    conversation_id,
                    lambda: self._get_session(conversation_id),
                    self.runtime,
                )
            finally:
                logger.info("WebChat client for conversation %s disconnected",
                            conversation_id)

        @self.app.get("/chat/api/conversations")
        async def conversations_api():
            return JSONResponse(content={"conversations": [
                {"id": item["id"], "title": item["title"],
                 "preview": item["preview"], "messages": item["messages"],
                 "modified": item["modified"]}
                for item in list_sessions()]})

        @self.app.get("/chat/api/conversations/{conversation_id}/messages")
        async def conversation_messages_api(conversation_id: str):
            conversation_id = _clean_conversation_id(conversation_id)
            if not conversation_id:
                raise HTTPException(status_code=400,
                                    detail="invalid conversation id")
            return JSONResponse(content={
                "session_id": conversation_id,
                "title": title_of(conversation_id),
                "messages": conversation_messages(conversation_id)})

        @self.app.get("/chat/api/commands")
        async def commands_api(q: str = ""):
            return JSONResponse(content=await self.commands_payload(q))

        @self.app.get("/chat/api/session/{conversation_id}/status")
        async def session_status_api(conversation_id: str):
            conversation_id = _clean_conversation_id(conversation_id)
            if not conversation_id:
                raise HTTPException(status_code=400,
                                    detail="invalid conversation id")
            return JSONResponse(
                content=await self.session_status(conversation_id))

        @self.app.post("/chat/api/session/{conversation_id}/mode")
        async def session_mode_api(conversation_id: str, request: Request):
            conversation_id = _clean_conversation_id(conversation_id)
            if not conversation_id:
                raise HTTPException(status_code=400,
                                    detail="invalid conversation id")
            body = await request.json()
            mode = str((body or {}).get("mode") or "")
            try:
                session = await self._get_session(conversation_id)
                value = session.set_permission_mode(mode)
            except ValueError as exc:
                return JSONResponse(status_code=400,
                                    content={"error": str(exc)})
            return JSONResponse(content={"mode": value})

        @self.app.post("/chat/api/conversations/{conversation_id}/rename")
        async def conversation_rename_api(conversation_id: str,
                                          request: Request):
            body = await request.json()
            return JSONResponse(content=self.rename_conversation(
                conversation_id, str(body.get("title") or "")))

        @self.app.post("/chat/api/conversations/{conversation_id}/branch")
        async def conversation_branch_api(conversation_id: str):
            return JSONResponse(content=self.branch_conversation(
                conversation_id))

        @self.app.delete("/chat/api/conversations/{conversation_id}")
        async def conversation_delete_api(conversation_id: str):
            return JSONResponse(
                content=await self.delete_conversation(conversation_id))

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
        return {"type": "error", "error": "Unroutable message"}

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

    async def start(self, on_started=None):
        """Run the gateway until cancelled.

        `on_started(port)` fires once uvicorn finished startup with the real
        bound port — which differs from `config.port` when the caller passed
        0 to request an OS-assigned port (`pyclaw api` mode)."""
        register_handlers(self)
        await self._init_channels()
        await self._start_cron()
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
            if self.config.local_token:
                logger.info("Local API mode (token-protected, headless)")
            else:
                logger.info("Chat API available at /chat/api")
        self.runtime.mark_started()
        try:
            serve_task = asyncio.create_task(server.serve())
            while not server.started:
                if serve_task.done():
                    await serve_task  # surface the startup failure
                    return
                await asyncio.sleep(0.05)
            self.bound_port = int(server.servers[0].sockets[0].getsockname()[1])
            if on_started:
                on_started(self.bound_port)
            await serve_task
        except asyncio.CancelledError:
            logger.info("Server cancelled")
        finally:
            await self.shutdown()

    async def shutdown(self):
        logger.info("Shutting down Gateway...")
        self._shutdown_event.set()
        await self._stop_cron()
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
                follow_conversation(session_id)
                logger.info("IM '%s' received from %s: %s", _platform, msg.sender_id, msg.text)
                record_meta(session_id, {
                    "channel": "im",
                    "platform": _platform,
                    "sender_id": str(msg.sender_id),
                })

                key = (_platform, msg.sender_id, msg.text)
                now = time.time()
                if key in self._recent_im and now - self._recent_im[key] < 30:
                    logger.debug("Dropped duplicate IM message from %s", msg.sender_id)
                    return
                self._recent_im[key] = now

                if not _adapter._greeting_sent:
                    await _adapter.send_greeting_on_startup()

                self.runtime.get_or_create_session(session_id)
                session = await self._get_session(session_id)
                slash_reply = await handle_slash(msg.text, session, session_id)
                if slash_reply is not None:
                    if isinstance(slash_reply, tuple):
                        info, follow = slash_reply
                        if info:
                            await _adapter.send_message(
                                msg.sender_id, OutboundMessage(text=info, reply_to=msg.id))
                        await run_im_interaction(
                            session, _adapter, msg.sender_id,
                            follow, msg.id, im_extra=IM_EXTRA,
                            progress_fn=_im_progress_text)
                        logger.info("IM '%s' ran /init via model", _platform)
                        self.runtime.increment_requests()
                        return
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
                        msg.sender_id,
                        msg.text,
                        msg.id,
                        im_extra=IM_EXTRA,
                        progress_fn=_im_progress_text,
                    )
                    logger.info("IM '%s' replied to %s", _platform, msg.sender_id)
                    self.runtime.increment_channel_messages(_adapter.channel_id)
                    self.runtime.increment_requests()
                except Exception as exc:
                    logger.error("Channel '%s' handler error: %s", _platform, exc)
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
