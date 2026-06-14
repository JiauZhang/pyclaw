"""Gateway RPC handlers."""

from .agent import (
    handle_agent,
    handle_agent_stream,
    handle_agent_tools,
    handle_tool_call,
    handle_chat_completions
)
from .sessions import handle_sessions_get, handle_sessions_reset, handle_sessions_list
from .chat import handle_chat_send, handle_chat_history
from .system import handle_status, handle_channel_rebind

__all__ = [
    "register_handlers",
    "handle_agent",
    "handle_agent_stream",
    "handle_agent_tools",
    "handle_tool_call",
    "handle_chat_completions",
    "handle_sessions_get",
    "handle_sessions_reset",
    "handle_sessions_list",
    "handle_chat_send",
    "handle_chat_history",
    "handle_status",
    "handle_channel_rebind",
]


def register_handlers(gateway):
    """Register all RPC handlers with the gateway."""
    gateway.register_handler("agent", handle_agent)
    gateway.register_handler("agent.stream", handle_agent_stream)
    gateway.register_handler("agent.tools", handle_agent_tools)
    gateway.register_handler("tool.call", handle_tool_call)
    gateway.register_handler("chat.completions", handle_chat_completions)
    gateway.register_handler("sessions.get", handle_sessions_get)
    gateway.register_handler("sessions.reset", handle_sessions_reset)
    gateway.register_handler("sessions.list", handle_sessions_list)
    gateway.register_handler("chat.send", handle_chat_send)
    gateway.register_handler("chat.history", handle_chat_history)
    gateway.register_handler("status", handle_status)
    gateway.register_handler("channel.rebind", handle_channel_rebind)
