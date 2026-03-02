"""Tool registry adapter."""

from typing import Optional, Dict, Any
from ...tools import ToolRegistry, create_default_tool_registry, ToolResult


class ToolRegistryAdapter:
    """Adapter for tool registry."""

    def __init__(self, workspace_dir: Optional[str] = None, tool_registry: Optional[ToolRegistry] = None):
        """Initialize tool registry adapter.

        Args:
            workspace_dir: Workspace directory for file operations
            tool_registry: Custom tool registry (optional)
        """
        self.workspace_dir = workspace_dir
        self.tool_registry = tool_registry or create_default_tool_registry(
            workspace_dir=workspace_dir,
            enable_exec=True,
            enable_file_ops=True,
            enable_python=True
        )

    def get_schemas(self) -> list:
        """Get tool schemas."""
        return self.tool_registry.get_schemas()

    def list_tools(self) -> list:
        """List available tools."""
        return self.tool_registry.list_tools()

    async def execute(self, tool_name: str, tool_args: Dict[str, Any], context) -> ToolResult:
        """Execute tool.

        Args:
            tool_name: Tool name
            tool_args: Tool arguments
            context: Agent context

        Returns:
            Tool result
        """
        return await self.tool_registry.execute(tool_name, tool_args, context)

    def get_tools_description(self) -> str:
        """Get description of all available tools."""
        schemas = self.get_schemas()
        descriptions = []

        for schema in schemas:
            func = schema.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            params = func.get("parameters", {})
            props = params.get("properties", {})
            required = params.get("required", [])

            param_desc = []
            for param_name, param_info in props.items():
                param_type = param_info.get("type", "any")
                param_desc_text = param_info.get("description", "")
                req_marker = " (required)" if param_name in required else ""
                param_desc.append(f"  - {param_name}: {param_type}{req_marker} - {param_desc_text}")

            tool_desc = f"{name}: {desc}"
            if param_desc:
                tool_desc += "\n" + "\n".join(param_desc)

            descriptions.append(tool_desc)

        return "\n\n".join(descriptions)
