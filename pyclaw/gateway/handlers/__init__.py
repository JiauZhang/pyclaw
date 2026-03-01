"""Gateway RPC handlers."""

from .agent import handle_agent
from .sessions import handle_sessions_get, handle_sessions_reset, handle_sessions_list
from .chat import handle_chat_send, handle_chat_history
from .system import handle_health, handle_status

__all__ = [
    "register_handlers",
    "handle_agent",
    "handle_sessions_get",
    "handle_sessions_reset",
    "handle_sessions_list",
    "handle_chat_send",
    "handle_chat_history",
    "handle_health",
    "handle_status",
]


def register_handlers(gateway):
    """Register all RPC handlers with the gateway."""
    # Agent handlers
    gateway.register_handler("agent", handle_agent)
    
    # Session handlers
    gateway.register_handler("sessions.get", handle_sessions_get)
    gateway.register_handler("sessions.reset", handle_sessions_reset)
    gateway.register_handler("sessions.list", handle_sessions_list)
    
    # Chat handlers
    gateway.register_handler("chat.send", handle_chat_send)
    gateway.register_handler("chat.history", handle_chat_history)
    
    # System handlers
    gateway.register_handler("health", handle_health)
    gateway.register_handler("status", handle_status)
