from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Any


@dataclass
class SessionState:
    id: str
    agent_id: str
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    message_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClientState:
    id: str
    connected_at: datetime = field(default_factory=datetime.now)
    last_ping: datetime = field(default_factory=datetime.now)


@dataclass
class ChannelState:
    id: str
    enabled: bool = False
    connected: bool = False
    last_error: Optional[str] = None
    message_count: int = 0


@dataclass
class AgentState:
    id: str
    name: str
    active: bool = False
    request_count: int = 0
    error_count: int = 0


class GatewayRuntimeState:
    def __init__(self):
        self.started_at: Optional[datetime] = None
        self.sessions: Dict[str, SessionState] = {}
        self.clients: Dict[str, ClientState] = {}
        self.channels: Dict[str, ChannelState] = {}
        self.agents: Dict[str, AgentState] = {}
        self._request_count = 0
        self._error_count = 0
    
    def mark_started(self):
        self.started_at = datetime.now()

    @property
    def uptime_seconds(self) -> float:
        if not self.started_at:
            return 0.0
        return (datetime.now() - self.started_at).total_seconds()

    def get_or_create_session(self, session_id: str, agent_id: str = "default") -> SessionState:
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState(
                id=session_id,
                agent_id=agent_id
            )
        return self.sessions[session_id]

    def get_session(self, session_id: str) -> Optional[SessionState]:
        return self.sessions.get(session_id)

    def update_session_activity(self, session_id: str):
        if session_id in self.sessions:
            self.sessions[session_id].last_activity = datetime.now()
            self.sessions[session_id].message_count += 1

    def delete_session(self, session_id: str) -> bool:
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False

    def client_connected(self, client_id: str):
        self.clients[client_id] = ClientState(id=client_id)

    def client_disconnected(self, client_id: str):
        if client_id in self.clients:
            del self.clients[client_id]

    def register_channel(self, channel_id: str, enabled: bool = False):
        self.channels[channel_id] = ChannelState(
            id=channel_id,
            enabled=enabled
        )

    def set_channel_connected(self, channel_id: str, connected: bool):
        if channel_id in self.channels:
            self.channels[channel_id].connected = connected

    def set_channel_error(self, channel_id: str, error: str):
        if channel_id in self.channels:
            self.channels[channel_id].last_error = error

    def increment_channel_messages(self, channel_id: str):
        if channel_id in self.channels:
            self.channels[channel_id].message_count += 1

    def increment_requests(self):
        self._request_count += 1

    def increment_errors(self):
        self._error_count += 1

    def get_channel_status(self) -> Dict[str, Dict]:
        return {
            channel_id: {
                "enabled": state.enabled,
                "connected": state.connected,
                "message_count": state.message_count,
                "last_error": state.last_error
            }
            for channel_id, state in self.channels.items()
        }

    def get_agent_status(self) -> Dict[str, Dict]:
        return {
            agent_id: {
                "name": state.name,
                "active": state.active,
                "request_count": state.request_count,
                "error_count": state.error_count
            }
            for agent_id, state in self.agents.items()
        }

    def get_stats(self) -> Dict[str, Any]:
        return {
            "uptime_seconds": self.uptime_seconds,
            "total_requests": self._request_count,
            "total_errors": self._error_count,
            "active_sessions": len(self.sessions),
            "connected_clients": len(self.clients),
            "registered_channels": len(self.channels),
            "registered_agents": len(self.agents)
        }
