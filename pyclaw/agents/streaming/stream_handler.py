"""Stream handler for agent."""

import logging
from typing import AsyncIterator, Optional
from ..client import ClientManager
from ..tools.registry import ToolRegistryAdapter
from ..tools.parser import ToolCallParser
from ..sessions.session_manager import SessionManager
from ..core.conversation import ConversationRunner

logger = logging.getLogger(__name__)


class StreamHandler:
    """Handle streaming responses."""

    def __init__(
        self,
        client_manager: ClientManager,
        tool_registry_adapter: ToolRegistryAdapter,
        session_manager: SessionManager,
        tool_parser: ToolCallParser,
        conversation_runner: ConversationRunner
    ):
        self.client_manager = client_manager
        self.tool_registry_adapter = tool_registry_adapter
        self.session_manager = session_manager
        self.tool_parser = tool_parser
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
        session,
        context,
        max_iterations: int,
        instruction: Optional[str] = None
    ) -> AsyncIterator[str]:
        """Handle streaming response."""
        history = self.session_manager.get_session_history(session.id)
        history.append({"role": "user", "content": message})

        sys_instruction = instruction or getattr(self.client_manager, 'instruction', None)

        for iteration in range(max_iterations):
            messages = self.session_manager.build_messages(history, sys_instruction)
            last_user_msg = self.conversation_runner.get_last_user_message(messages)

            if not last_user_msg:
                yield "Error: No user message found"
                return

            # Get AI response (non-streaming for tool detection)
            try:
                response = await self.conversation_runner.get_ai_response(last_user_msg, stream=False)
                full_response = self.conversation_runner.get_response_text(response)
            except Exception as e:
                logger.error(f"AI error: {e}")
                yield f"AI Error: {str(e)}"
                return

            # Check for tool call
            tool_call = self.tool_parser.parse(full_response)

            if not tool_call:
                # No tool call, stream the response
                history.append({"role": "assistant", "content": full_response})
                async for chunk in self._stream_response(last_user_msg):
                    yield chunk
                return

            # Execute tool
            tool_name = tool_call.get("tool")
            tool_args = tool_call.get("args", {})
            yield f"正在调用{tool_name}工具...\n"

            try:
                tool_result = await self.conversation_runner.execute_tool_call(
                    tool_name, tool_args, context, history
                )
                history.append({"role": "assistant", "content": full_response})
                history.append({
                    "role": "user",
                    "content": f"Tool '{tool_result['tool_name']}' result: {tool_result['result']}"
                })
            except Exception as e:
                logger.error(f"Tool execution error: {e}")
                yield f"Tool execution failed: {str(e)}"
                return

            # Get final response after tool execution
            messages = self.session_manager.build_messages(history, sys_instruction)
            last_user_msg = self.conversation_runner.get_last_user_message(messages)

            if not last_user_msg:
                yield "Error: No user message found"
                return

            try:
                final_response = ""
                async for chunk in self._stream_response(last_user_msg):
                    cleaned = chunk.replace('```', '').replace('json', '').strip()
                    if cleaned:
                        final_response += cleaned
                        yield cleaned

                history.append({"role": "assistant", "content": final_response})
                return
            except Exception as e:
                logger.error(f"AI error after tool execution: {e}")
                yield f"AI Error: {str(e)}"
                return

        yield "Maximum tool iterations reached."
