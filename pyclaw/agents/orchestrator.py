"""Agent orchestrator to coordinate components."""

import json
import re
import logging
from typing import Any, Dict, List, Optional, AsyncIterator, Tuple
from .context import AgentContext
from ..gateway.runtime import SessionState
from ..tools import ToolRegistry
from .client import ClientManager
from .tool_adapter import ToolRegistryAdapter
from .command_handler import CommandHandler
from .stream_handler import StreamHandler
from .conversation import ConversationRunner, MessageProcessor

logger = logging.getLogger(__name__)


class ToolCallParser:
    """Parse tool calls from AI responses."""

    @staticmethod
    def parse(response_text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Parse tool call from response text.

        Args:
            response_text: AI response text

        Returns:
            Tuple of (tool_name, tool_args) or None if no tool call found
        """
        if not response_text:
            return None

        tool_call = ToolCallParser._extract_from_code_block(response_text)
        if tool_call:
            return tool_call

        tool_call = ToolCallParser._extract_from_tool_call_format(response_text)
        if tool_call:
            return tool_call

        tool_call = ToolCallParser._extract_from_raw_json(response_text)
        if tool_call:
            return tool_call

        return None

    @staticmethod
    def _extract_from_code_block(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Extract tool call from markdown code block."""
        patterns = [
            r'```json\s*(.*?)\s*```',
            r'```\s*(.*?)\s*```',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    json_str = match.group(1).strip()
                    data = json.loads(json_str)
                    return ToolCallParser._validate_tool_call(data)
                except (json.JSONDecodeError, ValueError) as e:
                    logger.debug(f"Failed to parse JSON from code block: {e}")
                    continue

        return None

    @staticmethod
    def _extract_from_tool_call_format(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Extract tool call from TOOL_CALL: format."""
        pattern = r'TOOL_CALL:\s*(\{.*?\})'
        match = re.search(pattern, text, re.DOTALL)

        if match:
            try:
                json_str = match.group(1).strip()
                data = json.loads(json_str)
                return ToolCallParser._validate_tool_call(data)
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug(f"Failed to parse TOOL_CALL format: {e}")

        return None

    @staticmethod
    def _extract_from_raw_json(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Extract tool call from raw JSON in text."""
        pattern = r'\{\s*"tool"\s*:\s*"[^"]+"[^}]*\}'
        match = re.search(pattern, text, re.DOTALL)

        if match:
            try:
                json_str = match.group(0)
                data = json.loads(json_str)
                return ToolCallParser._validate_tool_call(data)
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug(f"Failed to parse raw JSON: {e}")

        return None

    @staticmethod
    def _validate_tool_call(data: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Validate and extract tool call from parsed data.

        Args:
            data: Parsed JSON data

        Returns:
            Tuple of (tool_name, tool_args) or None if invalid
        """
        if not isinstance(data, dict):
            return None

        tool_name = data.get("tool") or data.get("name")
        if not tool_name:
            return None

        tool_args = data.get("args") or data.get("arguments", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        return tool_name, tool_args


class InstructionBuilder:
    """Build system instructions for agent."""

    def __init__(self, tool_registry_manager):
        """Initialize instruction builder.

        Args:
            tool_registry_manager: Tool registry manager
        """
        self.tool_registry_manager = tool_registry_manager

    def build_default_instruction(self) -> str:
        """Generate default system instruction with tool descriptions."""
        tools_description = self.tool_registry_manager.get_tools_description()

        return f"""You are PyClaw, a helpful AI assistant with access to various tools.

You can use the following tools to help users:

{tools_description}

When you need to use a tool, respond with ONLY a JSON object in this format:
```json
{{"tool": "tool_name", "args": {{"param1": "value1", "param2": "value2"}}}}
```

The system will execute the tool and return the result to you. When you receive the tool result, you MUST:
1. Use the tool result to answer the user's question
2. Respond naturally with the information from the tool result
3. Do NOT call the same tool again with the same parameters

If no tool is needed, simply respond naturally to the user's message.

Remember to be helpful, accurate, and concise in your responses."""

    def build_instruction(self, custom_instruction: Optional[str] = None) -> str:
        """Build instruction, using custom if provided.

        Args:
            custom_instruction: Custom instruction (optional)

        Returns:
            Instruction string
        """
        if custom_instruction:
            return custom_instruction
        return self.build_default_instruction()


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

        self.conversation_runner = ConversationRunner(
            client_manager=self.client_manager,
            tool_registry_adapter=self.tool_registry_adapter
        )

        self.command_handler = CommandHandler(
            client_manager=self.client_manager,
            tool_registry_adapter=self.tool_registry_adapter
        )

        self.stream_handler = StreamHandler(
            client_manager=self.client_manager,
            tool_registry_adapter=self.tool_registry_adapter,
            conversation_runner=self.conversation_runner
        )

    async def run_conversation(
        self,
        session: SessionState,
        context: Optional[AgentContext]
    ) -> str:
        """Run conversation with tool support."""
        for iteration in range(self.max_iterations):
            messages = MessageProcessor.build_messages(session, self.instruction)
            last_user_msg = MessageProcessor.get_last_user_message(messages)

            if not last_user_msg:
                return "Error: No user message found"

            try:
                response = await self.conversation_runner.get_ai_response(last_user_msg, stream=False)
                response_text = self.conversation_runner.get_response_text(response)
            except Exception as e:
                logger.error(f"AI error: {e}")
                return f"AI Error: {str(e)}"

            tool_call = ToolCallParser.parse(response_text)

            if tool_call:
                tool_name, tool_args = tool_call
                logger.info(f"Executing tool: {tool_name} with args: {tool_args}")

                MessageProcessor.append_assistant_message(session, response_text)

                try:
                    result = await self.tool_registry_adapter.execute(tool_name, tool_args, context)
                    tool_result_text = result.output
                    if result.error:
                        tool_result_text += f"\nError: {result.error}"
                except Exception as e:
                    logger.error(f"Tool execution error: {e}")
                    tool_result_text = f"Error executing tool {tool_name}: {str(e)}"

                MessageProcessor.append_tool_result(session, tool_name, tool_result_text)

                continue
            else:
                MessageProcessor.append_assistant_message(session, response_text)
                return response_text

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
