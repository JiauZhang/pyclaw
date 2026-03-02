"""Command handler for agent."""

from typing import Optional
from ..client import ClientManager
from ..tools.registry import ToolRegistryAdapter


class CommandHandler:
    """Handle slash commands."""

    def __init__(self, client_manager: ClientManager, tool_registry_adapter: ToolRegistryAdapter):
        """Initialize command handler.

        Args:
            client_manager: Client manager
            tool_registry_adapter: Tool registry adapter
        """
        self.client_manager = client_manager
        self.tool_registry_adapter = tool_registry_adapter

    async def handle(self, command: str, session) -> str:
        """Handle command.

        Args:
            command: Command string
            session: Session state

        Returns:
            Command response
        """
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        
        if cmd == "/help":
            return self._handle_help()
        
        elif cmd == "/tools":
            return self._handle_tools()
        
        elif cmd == "/clear":
            return self._handle_clear(session)
        
        elif cmd == "/status":
            return self._handle_status(session)
        
        elif cmd == "/stream" and len(parts) > 1:
            return self._handle_stream()
        
        else:
            return f"Unknown command: {cmd}. Type /help for available commands."

    def _handle_help(self) -> str:
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

    def _handle_tools(self) -> str:
        """Handle /tools command."""
        tools = self.tool_registry_adapter.list_tools()
        return f"Available tools ({len(tools)}):\n" + "\n".join(f"• {t}" for t in tools)

    def _handle_clear(self, session) -> str:
        """Handle /clear command."""
        if hasattr(session, 'id') and hasattr(self, '_sessions') and session.id in self._sessions:
            del self._sessions[session.id]
        self.client_manager.clear()
        return "History cleared!"

    def _handle_status(self, session) -> str:
        """Handle /status command."""
        tools = self.tool_registry_adapter.list_tools()
        history_len = 0
        if hasattr(self, '_sessions') and hasattr(session, 'id'):
            history_len = len(self._sessions.get(session.id, []))
        return f"""Status:
• Provider: {self.client_manager.provider}
• Model: {self.client_manager.model_name or 'default'}
• Tools: {len(tools)}
• History: {history_len} messages"""

    def _handle_stream(self) -> str:
        """Handle /stream command."""
        return "Use the /v1/chat/completions endpoint with stream=true for streaming."
