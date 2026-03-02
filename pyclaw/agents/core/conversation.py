"""Conversation logic for agent."""

import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class ConversationRunner:
    """Handle conversation logic with tool support."""

    def __init__(self, client_manager, tool_registry_adapter, session_manager, tool_parser):
        """Initialize conversation runner.

        Args:
            client_manager: Client manager
            tool_registry_adapter: Tool registry adapter
            session_manager: Session manager
            tool_parser: Tool call parser
        """
        self.client_manager = client_manager
        self.tool_registry_adapter = tool_registry_adapter
        self.session_manager = session_manager
        self.tool_parser = tool_parser

    def get_last_user_message(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Get last user message from messages.

        Args:
            messages: List of messages

        Returns:
            Last user message content or None
        """
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return None

    async def execute_tool_call(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        context,
        history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Execute a tool call and update history.

        Args:
            tool_name: Tool name
            tool_args: Tool arguments
            context: Agent context
            history: Conversation history

        Returns:
            Tool result dictionary
        """
        logger.info(f"Tool call: {tool_name}({tool_args})")

        result = await self.tool_registry_adapter.execute(tool_name, tool_args, context)

        tool_result_text = result.output
        if result.error:
            tool_result_text += f"\nError: {result.error}"

        return {
            "tool_name": tool_name,
            "result": tool_result_text,
            "error": result.error
        }

    async def get_ai_response(self, text: str, stream: bool = False):
        """Get AI response.

        Args:
            text: Input text
            stream: Whether to stream response

        Returns:
            AI response
        """
        return self.client_manager.chat(text=text, stream=stream)

    def get_response_text(self, response) -> str:
        """Extract text from AI response.

        Args:
            response: AI response object

        Returns:
            Response text
        """
        return response.text if hasattr(response, 'text') else str(response)

    def get_chunk_text(self, chunk) -> str:
        """Get text from response chunk.

        Args:
            chunk: Response chunk

        Returns:
            Chunk text
        """
        if hasattr(chunk, 'text'):
            return chunk.text
        elif hasattr(chunk, 'content'):
            return chunk.content
        elif isinstance(chunk, str):
            return chunk
        else:
            return str(chunk)
