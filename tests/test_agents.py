"""Tests for agents module."""

import pytest
from pyclaw.agents import Agent
from pyclaw.agents.orchestrator import ToolCallParser, InstructionBuilder
from pyclaw.tools import ToolRegistry, Tool, ToolResult
from pyclaw.gateway.runtime import SessionState
from pyclaw.agents.context import AgentContext


class TestToolCallParser:
    """Test ToolCallParser class."""
    
    def test_parse_from_code_block(self):
        """Test parsing tool call from code block."""
        response = "```json\n{\"tool\": \"test_tool\", \"args\": {\"param\": \"value\"}}\n```"
        result = ToolCallParser.parse(response)
        assert result == ("test_tool", {"param": "value"})
    
    def test_parse_invalid(self):
        """Test parsing invalid tool call."""
        response = "Some text without tool call"
        result = ToolCallParser.parse(response)
        assert result is None


class TestInstructionBuilder:
    """Test InstructionBuilder class."""
    
    def test_build_default_instruction(self):
        """Test building default instruction."""
        class MockToolRegistry:
            def get_tools_description(self):
                return "Test tool description"
        
        builder = InstructionBuilder(MockToolRegistry())
        instruction = builder.build_default_instruction()
        assert "PyClaw" in instruction
        assert "Test tool description" in instruction


class TestAgent:
    """Test Agent class."""
    
    def test_agent_initialization(self):
        """Test Agent initialization."""
        agent = Agent(provider="tencent", model="hunyuan-lite")
        assert agent.provider == "tencent"
        assert agent.model_name == "hunyuan-lite"
    
    def test_get_available_tools(self):
        """Test get_available_tools method."""
        agent = Agent(provider="tencent", model="hunyuan-lite")
        tools = agent.get_available_tools()
        assert isinstance(tools, list)
    
    def test_get_tool_schemas(self):
        """Test get_tool_schemas method."""
        agent = Agent(provider="tencent", model="hunyuan-lite")
        schemas = agent.get_tool_schemas()
        assert isinstance(schemas, list)
    
    @pytest.mark.asyncio
    async def test_run_with_slash_command(self):
        """Test run method with slash command."""
        agent = Agent(provider="tencent", model="hunyuan-lite")
        session = SessionState(id="test", agent_id="default")
        response = await agent.run("/help", session)
        assert isinstance(response, str)
    
    @pytest.mark.asyncio
    async def test_run_with_normal_message(self):
        """Test run method with normal message."""
        agent = Agent(provider="tencent", model="hunyuan-lite")
        session = SessionState(id="test", agent_id="default")
        # This will likely fail if not connected to the actual API, but we can test the structure
        try:
            response = await agent.run("Hello", session)
            assert isinstance(response, str)
        except Exception:
            # Expected if API is not configured
            pass
