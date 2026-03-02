"""Command handler for agent."""

from typing import Optional
from ..client import ClientManager
from ..tools.registry import ToolRegistryAdapter
from ..sessions.session_manager import SessionManager


class CommandHandler:
    """Handle slash commands."""

    def __init__(
        self,
        client_manager: ClientManager,
        tool_registry_adapter: ToolRegistryAdapter,
        session_manager: SessionManager
    ):
        self.client_manager = client_manager
        self.tool_registry_adapter = tool_registry_adapter
        self.session_manager = session_manager

    async def handle(self, command: str, session) -> str:
        """Handle command."""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/help": self._handle_help,
            "/tools": self._handle_tools,
            "/clear": self._handle_clear,
            "/status": self._handle_status,
            "/stream": self._handle_stream,
        }

        handler = handlers.get(cmd)
        if handler:
            return await handler(session, args) if args else await handler(session)
        return f"Unknown command: {cmd}. Type /help for available commands."

    async def _handle_help(self, session=None) -> str:
        """Handle /help command."""
        return f"""Agent Help

Provider: {self.client_manager.provider}
Model: {self.client_manager.model_name or 'default'}

Commands:
/help - Show help
/tools - List tools
/clear - Clear history
/status - Show status
/stream <msg> - Stream response

Just type your message to chat!"""

    async def _handle_tools(self, session=None) -> str:
        """Handle /tools command."""
        tools = self.tool_registry_adapter.list_tools()
        return f"Available tools ({len(tools)}):\n" + "\n".join(f"• {t}" for t in tools)

    async def _handle_clear(self, session) -> str:
        """Handle /clear command."""
        if hasattr(session, 'id'):
            self.session_manager.clear_session(session.id)
        self.client_manager.clear()
        return "History cleared!"

    async def _handle_status(self, session=None) -> str:
        """Handle /status command."""
        tools = self.tool_registry_adapter.list_tools()
        history_len = 0
        if hasattr(session, 'id'):
            history = self.session_manager.get_session_history(session.id)
            history_len = len(history)
        return f"""Status:
• Provider: {self.client_manager.provider}
• Model: {self.client_manager.model_name or 'default'}
• Tools: {len(tools)}
• History: {history_len} messages"""

    async def _handle_stream(self, session=None, args: str = "") -> str:
        """Handle /stream command."""
        return "Use the /v1/chat/completions endpoint with stream=true for streaming."
