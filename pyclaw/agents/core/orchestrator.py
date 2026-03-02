"""Agent orchestrator to coordinate components."""

import logging
from typing import Any, Dict, List, Optional, AsyncIterator
from ..context import AgentContext
from ...gateway.runtime import SessionState
from ...tools import ToolRegistry
from ..client import ClientManager
from ..tools.registry import ToolRegistryAdapter
from ..tools.parser import ToolCallParser
from ..commands.command_handler import CommandHandler
from ..sessions.session_manager import SessionManager
from ..streaming.stream_handler import StreamHandler
from .conversation import ConversationRunner
from .instruction import InstructionBuilder

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Orchestrates agent components."""

    def __init__(
        self,
        provider: str,
        model: Optional[str],
        instruction: Optional[str],
        tool_registry: Optional[ToolRegistry],
        workspace_dir: Optional[str],
        api_key: Optional[str],
        max_iterations: int
    ):
        self.provider = provider
        self.model_name = model
        self.max_iterations = max_iterations
        self.workspace_dir = workspace_dir
        self._api_key = api_key
        self._tool_registry = tool_registry

        # Initialize components
        self.tool_registry_adapter = ToolRegistryAdapter(
            workspace_dir=workspace_dir,
            tool_registry=tool_registry
        )

        self.instruction = InstructionBuilder(self.tool_registry_adapter).build_instruction(instruction)

        self.client_manager = ClientManager(
            provider=provider,
            model_name=model,
            instruction=self.instruction,
            api_key=api_key
        )

        self.session_manager = SessionManager()
        self.tool_parser = ToolCallParser()

        self.conversation_runner = ConversationRunner(
            client_manager=self.client_manager,
            tool_registry_adapter=self.tool_registry_adapter,
            session_manager=self.session_manager,
            tool_parser=self.tool_parser
        )

        self.command_handler = CommandHandler(
            client_manager=self.client_manager,
            tool_registry_adapter=self.tool_registry_adapter,
            session_manager=self.session_manager
        )

        self.stream_handler = StreamHandler(
            client_manager=self.client_manager,
            tool_registry_adapter=self.tool_registry_adapter,
            session_manager=self.session_manager,
            tool_parser=self.tool_parser,
            conversation_runner=self.conversation_runner
        )

    async def run_conversation(
        self,
        history: List[Dict[str, Any]],
        context: Optional[AgentContext]
    ) -> str:
        """Run conversation with tool support."""
        for iteration in range(self.max_iterations):
            messages = self.session_manager.build_messages(history, self.instruction)
            last_user_msg = self.conversation_runner.get_last_user_message(messages)
            
            if not last_user_msg:
                return "Error: No user message found"

            try:
                response = await self.conversation_runner.get_ai_response(last_user_msg, stream=False)
                response_text = self.conversation_runner.get_response_text(response)
            except Exception as e:
                logger.error(f"AI error: {e}")
                return f"AI Error: {str(e)}"

            tool_call = self.tool_parser.parse(response_text)

            if not tool_call:
                history.append({"role": "assistant", "content": response_text})
                return response_text

            # Execute tool call
            tool_name = tool_call.get("tool")
            tool_args = tool_call.get("args", {})
            logger.info(f"Executing tool: {tool_name} with args: {tool_args}")

            tool_result = await self.conversation_runner.execute_tool_call(
                tool_name, tool_args, context, history
            )

            history.append({"role": "assistant", "content": response_text})
            history.append({
                "role": "user",
                "content": f"Tool '{tool_result['tool_name']}' result: {tool_result['result']}"
            })

        return "Maximum tool iterations reached."

    async def handle_stream(
        self,
        message: str,
        session: SessionState,
        context: Optional[AgentContext]
    ) -> AsyncIterator[str]:
        """Handle streaming response."""
        async for chunk in self.stream_handler.handle_stream(
            message=message,
            session=session,
            context=context,
            max_iterations=self.max_iterations,
            instruction=self.instruction
        ):
            yield chunk

    def get_available_tools(self) -> List[str]:
        """Get available tools."""
        return self.tool_registry_adapter.list_tools()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get tool schemas."""
        return self.tool_registry_adapter.get_schemas()
