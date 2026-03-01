"""Gateway runtime state management."""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any


@dataclass
class SessionState:
    """Session state."""
    id: str
    agent_id: str
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    message_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClientState:
    """WebSocket client state."""
    id: str
    connected_at: datetime = field(default_factory=datetime.now)
    last_ping: datetime = field(default_factory=datetime.now)


@dataclass
class ChannelState:
    """Channel connection state."""
    id: str
    enabled: bool = False
    connected: bool = False
    last_error: Optional[str] = None
    message_count: int = 0


@dataclass
class AgentState:
    """Agent runtime state."""
    id: str
    name: str
    active: bool = False
    request_count: int = 0
    error_count: int = 0


class GatewayRuntimeState:
    """
    Manages the runtime state of the Gateway.
    
    Tracks sessions, clients, channels, and agents.
    """
    
    def __init__(self):
        self.started_at: Optional[datetime] = None
        self.sessions: Dict[str, SessionState] = {}
        self.clients: Dict[str, ClientState] = {}
        self.channels: Dict[str, ChannelState] = {}
        self.agents: Dict[str, AgentState] = {}
        self._request_count = 0
        self._error_count = 0
    
    def mark_started(self):
        """Mark the gateway as started."""
        self.started_at = datetime.now()
    
    @property
    def uptime_seconds(self) -> float:
        """Get uptime in seconds."""
        if not self.started_at:
            return 0.0
        return (datetime.now() - self.started_at).total_seconds()
    
    # Session management
    
    def get_or_create_session(self, session_id: str, agent_id: str = "default") -> SessionState:
        """Get or create a session."""
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState(
                id=session_id,
                agent_id=agent_id
            )
        return self.sessions[session_id]
    
    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Get a session by ID."""
        return self.sessions.get(session_id)
    
    def update_session_activity(self, session_id: str):
        """Update session last activity."""
        if session_id in self.sessions:
            self.sessions[session_id].last_activity = datetime.now()
            self.sessions[session_id].message_count += 1
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False
    
    # Client management
    
    def client_connected(self, client_id: str):
        """Record a client connection."""
        self.clients[client_id] = ClientState(id=client_id)
    
    def client_disconnected(self, client_id: str):
        """Record a client disconnection."""
        if client_id in self.clients:
            del self.clients[client_id]
    
    def update_client_ping(self, client_id: str):
        """Update client last ping time."""
        if client_id in self.clients:
            self.clients[client_id].last_ping = datetime.now()
    
    # Channel management
    
    def register_channel(self, channel_id: str, enabled: bool = False):
        """Register a channel."""
        self.channels[channel_id] = ChannelState(
            id=channel_id,
            enabled=enabled
        )
    
    def set_channel_connected(self, channel_id: str, connected: bool):
        """Set channel connection state."""
        if channel_id in self.channels:
            self.channels[channel_id].connected = connected
    
    def set_channel_error(self, channel_id: str, error: str):
        """Set channel error."""
        if channel_id in self.channels:
            self.channels[channel_id].last_error = error
    
    def increment_channel_messages(self, channel_id: str):
        """Increment channel message count."""
        if channel_id in self.channels:
            self.channels[channel_id].message_count += 1
    
    # Agent management
    
    def register_agent(self, agent_id: str, name: str):
        """Register an agent."""
        self.agents[agent_id] = AgentState(
            id=agent_id,
            name=name
        )
    
    def set_agent_active(self, agent_id: str, active: bool):
        """Set agent active state."""
        if agent_id in self.agents:
            self.agents[agent_id].active = active
    
    def increment_agent_requests(self, agent_id: str):
        """Increment agent request count."""
        if agent_id in self.agents:
            self.agents[agent_id].request_count += 1
    
    def increment_agent_errors(self, agent_id: str):
        """Increment agent error count."""
        if agent_id in self.agents:
            self.agents[agent_id].error_count += 1
    
    # Request tracking
    
    def increment_requests(self):
        """Increment total request count."""
        self._request_count += 1
    
    def increment_errors(self):
        """Increment total error count."""
        self._error_count += 1
    
    # Status getters
    
    def get_channel_status(self) -> Dict[str, Dict]:
        """Get status of all channels."""
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
        """Get status of all agents."""
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
        """Get runtime statistics."""
        return {
            "uptime_seconds": self.uptime_seconds,
            "total_requests": self._request_count,
            "total_errors": self._error_count,
            "active_sessions": len(self.sessions),
            "connected_clients": len(self.clients),
            "registered_channels": len(self.channels),
            "registered_agents": len(self.agents)
        }
