"""Stream handler for agent."""

import logging
from typing import AsyncIterator, Optional
from ..gateway.runtime import SessionState
from .client import ClientManager
from .tool_adapter import ToolRegistryAdapter
from .conversation import ConversationRunner, MessageProcessor

logger = logging.getLogger(__name__)


class StreamHandler:
    """Handle streaming responses."""

    def __init__(
        self,
        client_manager: ClientManager,
        tool_registry_adapter: ToolRegistryAdapter,
        conversation_runner: ConversationRunner
    ):
        self.client_manager = client_manager
        self.tool_registry_adapter = tool_registry_adapter
        self.conversation_runner = conversation_runner

    async def _stream_response(self, text: str) -> AsyncIterator[str]:
        """Stream response from AI."""
        try:
            response = self.client_manager.chat(text=text, stream=True)
            for chunk in response:
                chunk_text = self.conversation_runner.get_chunk_text(chunk)
                if chunk_text:
                    yield chunk_text
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"Error: {str(e)}"

    async def handle_stream(
        self,
        message: str,
        session: SessionState,
        context,
        max_iterations: int,
        instruction: Optional[str] = None
    ) -> AsyncIterator[str]:
        """Handle streaming response."""
        session.append_message("user", message)

        sys_instruction = instruction or getattr(self.client_manager, 'instruction', None)

        for iteration in range(max_iterations):
            messages = MessageProcessor.build_messages(session, sys_instruction)
            last_user_msg = MessageProcessor.get_last_user_message(messages)

            if not last_user_msg:
                yield "Error: No user message found"
                return

            # Stream the response
            try:
                final_response = ""
                async for chunk in self._stream_response(last_user_msg):
                    cleaned = chunk.replace('```', '').replace('json', '').strip()
                    if cleaned:
                        final_response += cleaned
                        yield cleaned

                MessageProcessor.append_assistant_message(session, final_response)
                return
            except Exception as e:
                logger.error(f"AI error: {e}")
                yield f"AI Error: {str(e)}"
                return

        yield "Maximum tool iterations reached."
