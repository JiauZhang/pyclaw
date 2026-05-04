"""Agent implementation using chatchat."""

import logging
from typing import Any, Dict, List, Optional

from chatchat.agent import Agent as ChatAgent
from chatchat.tool import Tools

from .tools import tools as default_tools

logger = logging.getLogger(__name__)


class Agent:
    def __init__(
        self,
        provider: str = 'tencent',
        model: Optional[str] = None,
        instruction: Optional[str] = None,
        tools: Optional[List[Any]] = None,
        generation_options: Optional[Dict[str, Any]] = None,
        http_options: Optional[Dict[str, Any]] = None,
    ):
        self.provider = provider
        self.model = model
        self.instruction = instruction or self._default_instruction()
        tools_list = tools if tools else default_tools
        self.tools = Tools(*tools_list)
        self.generation_options = generation_options or {'stream': False}
        self.http_options = http_options or {}

        self._agent = ChatAgent(
            provider=provider,
            model=model,
            instruction=self.instruction,
            tools=tools_list,
            generation_options=self.generation_options,
            http_options=self.http_options,
        )

    def _default_instruction(self) -> str:
        return '''You are PyClaw, a helpful AI assistant with access to various tools.

When you need to use a tool, the system will automatically execute it and return the result.
Use the tool results to provide accurate and helpful responses to the user.

Be helpful, accurate, and concise.'''

    def chat(self, message: str) -> str:
        """Send a message and get a response (non-streaming)."""
        response = self._agent.client.chat(message, generation_options=self._agent.generation_options, tools=self.tools)
        result = ''
        for chunk in response:
            result += chunk
        return result

    def chat_stream(self, message: str):
        """Send a message and get a streaming response."""
        response = self._agent.client.chat(
            message,
            generation_options={**self._agent.generation_options, 'stream': True},
            tools=self.tools,
        )
        for chunk in response:
            yield chunk

    def clear(self):
        """Clear conversation history."""
        self._agent.client.clear()

    def get_available_tools(self) -> List[str]:
        """Get list of available tool names."""
        return list(self.tools.name_to_tool.keys())

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get tool schemas."""
        return self.tools.to_dict()
