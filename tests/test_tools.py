"""Tests for tools module."""

import pytest
from pyclaw.tools import Tool, ToolRegistry, ToolResult
from pyclaw.tools.builtin import BashTool, DateTimeTool, PythonTool, ReadFileTool, WriteFileTool
from pyclaw.agents.context import AgentContext
import tempfile
import os


def create_test_context():
    """Create a test AgentContext instance."""
    return AgentContext(
        session_id="test_session",
        agent_id="test_agent",
        user_id="test_user",
        channel_id="test_channel"
    )


class MockTool(Tool):
    """Mock tool implementation for testing."""
    
    async def execute(self, arguments, context):
        return ToolResult(output="Mock output")


class TestTool:
    """Test Tool class."""
    
    def test_tool_initialization(self):
        """Test Tool initialization."""
        tool = MockTool(
            name="test_tool",
            description="Test tool",
            parameters={"param": {"type": "string", "description": "Test parameter"}},
            required_params=["param"]
        )
        assert tool.name == "test_tool"
        assert tool.description == "Test tool"
        assert tool.parameters == {"param": {"type": "string", "description": "Test parameter"}}
        assert tool.required_params == ["param"]
    
    def test_to_schema(self):
        """Test to_schema method."""
        tool = MockTool(
            name="test_tool",
            description="Test tool",
            parameters={"param": {"type": "string", "description": "Test parameter"}},
            required_params=["param"]
        )
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test_tool"
        assert schema["function"]["description"] == "Test tool"
    
    def test_validate_args(self):
        """Test validate_args method."""
        tool = MockTool(
            name="test_tool",
            description="Test tool",
            parameters={"param": {"type": "string", "description": "Test parameter"}},
            required_params=["param"]
        )
        # Valid args
        error = tool.validate_args({"param": "value"})
        assert error is None
        # Missing required param
        error = tool.validate_args({})
        assert error == "Missing required parameter: param"


class TestToolRegistry:
    """Test ToolRegistry class."""
    
    def test_register(self):
        """Test register method."""
        registry = ToolRegistry()
        tool = MockTool(
            name="test_tool",
            description="Test tool",
            parameters={}
        )
        registry.register(tool)
        assert "test_tool" in registry._tools
    
    def test_unregister(self):
        """Test unregister method."""
        registry = ToolRegistry()
        tool = MockTool(
            name="test_tool",
            description="Test tool",
            parameters={}
        )
        registry.register(tool)
        assert "test_tool" in registry._tools
        registry.unregister("test_tool")
        assert "test_tool" not in registry._tools
    
    def test_get(self):
        """Test get method."""
        registry = ToolRegistry()
        tool = MockTool(
            name="test_tool",
            description="Test tool",
            parameters={}
        )
        registry.register(tool)
        assert registry.get("test_tool") == tool
        assert registry.get("non_existent") is None
    
    def test_list_tools(self):
        """Test list_tools method."""
        registry = ToolRegistry()
        tool1 = MockTool(
            name="tool1",
            description="Test tool 1",
            parameters={}
        )
        tool2 = MockTool(
            name="tool2",
            description="Test tool 2",
            parameters={}
        )
        registry.register(tool1)
        registry.register(tool2)
        tools = registry.list_tools()
        assert "tool1" in tools
        assert "tool2" in tools
    
    def test_get_schemas(self):
        """Test get_schemas method."""
        registry = ToolRegistry()
        tool = MockTool(
            name="test_tool",
            description="Test tool",
            parameters={}
        )
        registry.register(tool)
        schemas = registry.get_schemas()
        assert len(schemas) == 1
    
    @pytest.mark.asyncio
    async def test_execute(self):
        """Test execute method."""
        registry = ToolRegistry()
        
        class TestToolImplementation(Tool):
            async def execute(self, arguments, context):
                return ToolResult(output="Test output")
        
        tool = TestToolImplementation(
            name="test_tool",
            description="Test tool",
            parameters={}
        )
        registry.register(tool)
        result = await registry.execute("test_tool", {}, create_test_context())
        assert result.output == "Test output"
    
    @pytest.mark.asyncio
    async def test_execute_nonexistent_tool(self):
        """Test execute method with non-existent tool."""
        registry = ToolRegistry()
        result = await registry.execute("non_existent", {}, create_test_context())
        assert result.error == "Tool 'non_existent' not found"
    
    @pytest.mark.asyncio
    async def test_execute_validation_error(self):
        """Test execute method with validation error."""
        registry = ToolRegistry()
        
        class TestToolImplementation(Tool):
            async def execute(self, arguments, context):
                return ToolResult(output="Test output")
        
        tool = TestToolImplementation(
            name="test_tool",
            description="Test tool",
            parameters={"param": {"type": "string"}},
            required_params=["param"]
        )
        registry.register(tool)
        result = await registry.execute("test_tool", {}, create_test_context())
        assert "Missing required parameter" in result.error


class TestBuiltinTools:
    """Test built-in tools."""
    
    @pytest.mark.asyncio
    async def test_bash_tool(self):
        """Test BashTool."""
        tool = BashTool()
        result = await tool.execute({"command": "echo 'Hello'"}, create_test_context())
        assert "Hello" in result.output
    
    @pytest.mark.asyncio
    async def test_datetime_tool(self):
        """Test DateTimeTool."""
        tool = DateTimeTool()
        result = await tool.execute({}, create_test_context())
        assert result.output != ""
    
    @pytest.mark.asyncio
    async def test_python_tool(self):
        """Test PythonTool."""
        tool = PythonTool()
        result = await tool.execute({"code": "print('Hello from Python')"}, create_test_context())
        assert "Hello from Python" in result.output
    
    @pytest.mark.asyncio
    async def test_read_file_tool(self):
        """Test ReadFileTool."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file = os.path.join(temp_dir, "test.txt")
            with open(temp_file, 'w') as f:
                f.write("Test file content")
            
            tool = ReadFileTool(base_dir=temp_dir)
            result = await tool.execute({"path": "test.txt"}, create_test_context())
            assert "Test file content" in result.output
    
    @pytest.mark.asyncio
    async def test_write_file_tool(self):
        """Test WriteFileTool."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file = os.path.join(temp_dir, "test.txt")
            
            tool = WriteFileTool(base_dir=temp_dir)
            result = await tool.execute({"path": "test.txt", "content": "Test content"}, create_test_context())
            assert "Successfully wrote" in result.output
            
            # Verify file content
            with open(temp_file, 'r') as f:
                content = f.read()
            assert content == "Test content"
