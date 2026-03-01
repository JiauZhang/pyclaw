"""DateTime tool."""

from datetime import datetime
from typing import Any, Dict

from ..base import Tool, ToolResult


class DateTimeTool(Tool):
    """Get current date and/or time."""

    def __init__(self):
        super().__init__(
            name="datetime",
            description="Get the current date and/or time in various formats.",
            parameters={
                "format": {
                    "type": "string",
                    "description": "Output format (iso, date, time, human, weekday, or custom strftime format)",
                    "enum": ["iso", "date", "time", "human", "weekday"],
                    "default": "human"
                }
            },
            required_params=[]
        )

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        format_type = arguments.get("format", "human")
        now = datetime.now()

        if format_type == "iso":
            output = now.isoformat()
        elif format_type == "date":
            output = now.date().isoformat()
        elif format_type == "time":
            output = now.strftime("%H:%M:%S")
        elif format_type == "human":
            output = now.strftime("%Y-%m-%d %H:%M:%S")
        elif format_type == "weekday":
            output = now.strftime("%A")
        else:
            try:
                output = now.strftime(format_type)
            except Exception as e:
                return ToolResult(output="", error=f"Invalid format: {e}", exit_code=1)

        return ToolResult(output=output)
