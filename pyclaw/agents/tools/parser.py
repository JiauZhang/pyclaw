"""Tool call parser."""

import json
import re
from typing import Optional, Dict, Any


class ToolCallParser:
    """Parse tool calls from AI responses."""

    @staticmethod
    def parse(text: str) -> Optional[Dict[str, Any]]:
        """Parse tool call from response.

        Args:
            text: AI response text

        Returns:
            Tool call dictionary or None
        """
        # Pattern: TOOL_CALL: {"tool": "...", "args": {...}}
        pattern = r'TOOL_CALL:\s*(\{[^}]+\})'
        match = re.search(pattern, text)
        
        if match:
            try:
                data = json.loads(match.group(1))
                if "tool" in data:
                    return data
            except json.JSONDecodeError:
                pass
        
        # Try JSON block
        json_pattern = r'```json\s*(\{[\s\S]*?\})\s*```'
        match = re.search(json_pattern, text)
        
        if match:
            try:
                data = json.loads(match.group(1))
                if "tool" in data:
                    return data
            except json.JSONDecodeError:
                pass
        
        # Try direct JSON
        try:
            data = json.loads(text.strip())
            if "tool" in data:
                return data
        except json.JSONDecodeError:
            pass
        
        return None
