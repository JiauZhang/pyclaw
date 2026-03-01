"""Simple agent implementation for testing."""

import logging
from datetime import datetime
from typing import Optional

from .runtime import AgentContext
from ..gateway.runtime import SessionState

logger = logging.getLogger(__name__)


class SimpleAgent:
    """
    A simple agent implementation that doesn't require external AI models.
    
    Useful for testing and development.
    """
    
    def __init__(self):
        self.system_prompt = """You are OpenClaw, a helpful AI assistant.

You can help with various tasks using these commands:
/echo <text> - Echo back the text
/time - Show current time
/date - Show current date
/help - Show this help message
/clear or /reset - Clear conversation history

For other questions, I'll do my best to help!"""
    
    async def run(self, message: str, session: SessionState) -> str:
        """
        Process a message and generate a response.
        
        Args:
            message: User's input message
            session: Session state object
            
        Returns:
            Response string
        """
        message = message.strip()
        
        # Handle commands
        if message.startswith("/"):
            return await self._handle_command(message, session)
        
        # Simple responses for common queries
        lower_msg = message.lower()
        
        if any(word in lower_msg for word in ["hello", "hi", "hey"]):
            return "Hello! How can I help you today?"
        
        if any(word in lower_msg for word in ["how are you", "how're you"]):
            return "I'm doing well, thank you for asking! How can I assist you?"
        
        if any(word in lower_msg for word in ["what is your name", "who are you"]):
            return "I'm OpenClaw, your personal AI assistant. I'm here to help you with various tasks!"
        
        if any(word in lower_msg for word in ["thank", "thanks"]):
            return "You're welcome! Let me know if you need anything else."
        
        if any(word in lower_msg for word in ["bye", "goodbye", "see you"]):
            return "Goodbye! Have a great day!"
        
        # Default response
        return f"I received your message: \"{message}\"\n\nI'm a simple agent for testing. Try using /help to see available commands, or configure an AI model for more intelligent responses."
    
    async def _handle_command(self, command: str, session: SessionState) -> str:
        """Handle slash commands."""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        if cmd == "/echo":
            return args if args else "Echo!"
        
        elif cmd == "/time":
            now = datetime.now()
            return f"Current time: {now.strftime('%H:%M:%S')}"
        
        elif cmd == "/date":
            now = datetime.now()
            return f"Today's date: {now.strftime('%Y-%m-%d %A')}"
        
        elif cmd == "/help":
            return self.system_prompt
        
        elif cmd in ["/clear", "/reset", "/new"]:
            # Session reset is handled by the caller
            return "Conversation history cleared. Starting fresh!"
        
        elif cmd == "/status":
            return f"""Session Status:
- Session ID: {session.id}
- Agent ID: {session.agent_id}
- Messages: {session.message_count}
- Created: {session.created_at.strftime('%Y-%m-%d %H:%M:%S')}
- Last Activity: {session.last_activity.strftime('%Y-%m-%d %H:%M:%S')}"""
        
        else:
            return f"Unknown command: {cmd}\n\nType /help to see available commands."
