"""Echo tool."""

from typing import Any, Dict

from ..base import Tool, ToolResult


class EchoTool(Tool):
    """Echo back the input text."""

    def __init__(self):
        super().__init__(
            name="echo",
            description="Echo back the provided text. Useful for testing and confirmation.",
            parameters={
                "text": {
                    "type": "string",
                    "description": "The text to echo back"
                }
            },
            required_params=["text"]
        )

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        text = arguments.get("text", "")
        return ToolResult(output=text)
