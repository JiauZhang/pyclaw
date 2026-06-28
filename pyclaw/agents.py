import logging
from typing import Any, Dict, List, Optional

from chatchat.agent import Agent as ChatAgent

from .tools import tools as default_tools

logger = logging.getLogger(__name__)


IM_EXTRA = '''You are PyClaw, an AI assistant on an instant messaging platform (QQ/WeChat).

Rules:
1. Keep responses very short and concise. One to three sentences is usually enough.
2. Only give detailed explanations or long output when the user explicitly asks for it.
3. Do not use markdown formatting — plain text only.
4. Be conversational and direct.
5. If you use tools, briefly summarize the result without technical details.'''


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
        self.stream = stream
        self.thinking = thinking
        self.http_options = http_options or {}

        self._agent = ChatAgent(
            provider=provider,
            model=model,
            name='pyclaw',
            instruction=self.instruction,
            tools=tools if tools is not None else default_tools,
            stream=stream,
            thinking=thinking,
            http_options=self.http_options,
        )

    def _default_instruction(self) -> str:
        return '''You are PyClaw, a helpful AI assistant with access to various tools.

When you need to use a tool, the system will automatically execute it and return the result.
Use the tool results to provide accurate and helpful responses to the user.

Be helpful, accurate, and concise.'''

    def chat(self, message: str, on_progress=None) -> str:
        self._agent.stream = False
        result = self._agent.chat(message, on_progress=on_progress)
        if isinstance(result, str):
            return result
        return ''.join(result)

    def chat_stream(self, message: str, on_progress=None):
        self._agent.stream = True
        for chunk in self._agent.chat(message, on_progress=on_progress):
            if chunk:
                yield chunk

    def clear(self):
        self._agent.client.clear()

    def get_available_tools(self) -> List[str]:
        if self._agent.tools is None:
            return []
        return [t.name for t in self._agent.tools.tools]