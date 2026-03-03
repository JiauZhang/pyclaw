"""Command handler for agent."""

from typing import Optional
from ..gateway.runtime import SessionState
from .client import ClientManager
from .tool_adapter import ToolRegistryAdapter


class CommandHandler:
    """Handle slash commands."""

    def __init__(
        self,
        client_manager: ClientManager,
        tool_registry_adapter: ToolRegistryAdapter
    ):
        self.client_manager = client_manager
        self.tool_registry_adapter = tool_registry_adapter

    async def handle(self, command: str, session: SessionState) -> str:
        """Handle command."""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/help": self._handle_help,
            "/tools": self._handle_tools,
            "/clear": self._handle_clear,
            "/status": self._handle_status,
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
Just type your message to chat!"""

    async def _handle_tools(self, session=None) -> str:
        """Handle /tools command."""
        tools = self.tool_registry_adapter.list_tools()
        return f"Available tools ({len(tools)}):\n" + "\n".join(f"• {t}" for t in tools)

    async def _handle_clear(self, session: SessionState) -> str:
        """Handle /clear command."""
        session.clear_history()
        self.client_manager.clear()
        return "History cleared!"

    async def _handle_status(self, session: SessionState) -> str:
        """Handle /status command."""
        tools = self.tool_registry_adapter.list_tools()
        history_len = len(session.get_history())
        return f"""Status:
• Provider: {self.client_manager.provider}
• Model: {self.client_manager.model_name or 'default'}
• Tools: {len(tools)}
• History: {history_len} messages"""
