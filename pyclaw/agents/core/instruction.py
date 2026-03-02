"""Instruction builder for agent."""

from typing import Optional


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

When you need to use a tool, respond with a JSON object in this format:
```json
{{"tool": "tool_name", "args": {{"param1": "value1", "param2": "value2"}}}}
```

Or use this format:
TOOL_CALL: {{"tool": "tool_name", "args": {{"param": "value"}}}}

The system will execute the tool and return the result as a message in the conversation history.
You MUST use this tool result to provide your final response to the user.
Never make up or hardcode tool results - always use the actual result provided by the system.

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
