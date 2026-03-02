"""Tool call parser."""

import json
import re
from typing import Optional, Dict, Any


class ToolCallParser:
    """Parse tool calls from AI responses."""

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """Extract JSON object from text."""
        # Find the first { and match balanced braces
        start = text.find('{')
        if start == -1:
            return None
        
        count = 0
        for i, char in enumerate(text[start:]):
            if char == '{':
                count += 1
            elif char == '}':
                count -= 1
                if count == 0:
                    return text[start:start+i+1]
        return None

    @staticmethod
    def parse(text: str) -> Optional[Dict[str, Any]]:
        """Parse tool call from response."""
        # Pattern: TOOL_CALL: {"tool": "...", "args": {...}}
        if 'TOOL_CALL:' in text:
            idx = text.find('TOOL_CALL:')
            json_str = ToolCallParser._extract_json(text[idx:])
            if json_str:
                try:
                    data = json.loads(json_str)
                    if "tool" in data:
                        return data
                except json.JSONDecodeError:
                    pass
        
        # Try JSON block
        match = re.search(r'```json\s*(.+?)\s*```', text, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
            try:
                data = json.loads(json_str)
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
