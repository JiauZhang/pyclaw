"""Base tool definitions."""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, Callable

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Result of a tool execution."""
    output: str
    error: Optional[str] = None
    exit_code: int = 0


class Tool(ABC):
    """Base class for all tools."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        required_params: Optional[list] = None
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.required_params = required_params or []

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        """Execute the tool with given arguments."""
        pass

    def to_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required_params
                }
            }
        }

    def validate_args(self, arguments: Dict[str, Any]) -> Optional[str]:
        """Validate arguments against schema. Returns error message or None."""
        for param in self.required_params:
            if param not in arguments:
                return f"Missing required parameter: {param}"
        return None


class ToolRegistry:
    """Registry for managing available tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> "ToolRegistry":
        """Register a tool."""
        self._tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")
        return self

    def unregister(self, name: str) -> bool:
        """Unregister a tool by name."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list:
        """List all registered tool names."""
        return list(self._tools.keys())

    def get_schemas(self) -> list:
        """Get schemas for all registered tools."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        context: Any
    ) -> ToolResult:
        """Execute a tool by name."""
        tool = self.get(name)
        if not tool:
            return ToolResult(
                output="",
                error=f"Tool '{name}' not found",
                exit_code=1
            )

        # Validate arguments
        validation_error = tool.validate_args(arguments)
        if validation_error:
            return ToolResult(
                output="",
                error=validation_error,
                exit_code=1
            )

        try:
            return await tool.execute(arguments, context)
        except Exception as e:
            logger.error(f"Tool execution error ({name}): {e}")
            return ToolResult(
                output="",
                error=str(e),
                exit_code=1
            )
