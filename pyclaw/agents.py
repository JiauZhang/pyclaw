"""Agent implementation using chatchat."""

import logging
from typing import Any, Dict, List, Optional

from chatchat.agent import Agent as ChatAgent

from .tools import tools as default_tools

logger = logging.getLogger(__name__)


class Agent:
    def __init__(
        self,
        provider: str = 'agnes',
        model: Optional[str] = 'agnes-2.0-flash',
        instruction: Optional[str] = None,
        tools: Optional[List[Any]] = None,
        stream: bool = False,
        thinking: bool = False,
        http_options: Optional[Dict[str, Any]] = None,
    ):
        self.provider = provider
        self.model = model
        self.instruction = instruction or self._default_instruction()
        self.tools = tools if tools else default_tools
        self.stream = stream
        self.thinking = thinking
        self.http_options = http_options or {}

        self._agent = ChatAgent(
            provider=provider,
            model=model,
            name='pyclaw',
            description='A helpful AI assistant.',
            instruction=self.instruction,
            tools=self.tools,
            stream=stream,
            thinking=thinking,
            http_options=self.http_options,
        )

    def _default_instruction(self) -> str:
        return '''You are PyClaw, a helpful AI assistant with access to various tools.

When you need to use a tool, the system will automatically execute it and return the result.
Use the tool results to provide accurate and helpful responses to the user.

Be helpful, accurate, and concise.'''

    def chat(self, message: str) -> str:
        result = self._call_with_mode(message, stream=False)
        if isinstance(result, str):
            return result
        return ''.join(result)

    def chat_stream(self, message: str):
        """Send a message and get a streaming response."""
        for chunk in self._call_with_mode(message, stream=True):
            if chunk:
                yield chunk

    def _call_with_mode(self, message: str, stream: bool):
        original = self._agent.stream
        self._agent.stream = stream
        try:
            return self._agent(message)
        finally:
            self._agent.stream = original

    def clear(self):
        """Clear conversation history."""
        self._agent.client.clear()

    def get_available_tools(self) -> List[str]:
        """Get list of available tool names."""
        return [t.name for t in self._agent.tools]
