"""Agent runtime implementation."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, AsyncIterator, Callable
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """Context for agent execution."""
    session_id: str
    agent_id: str
    user_id: str
    channel_id: str
    thread_id: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentMessage:
    """A message in the agent conversation."""
    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API calls."""
        msg = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg


@dataclass
class ToolCall:
    """A tool call request."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_call_id: str
    output: str
    error: Optional[str] = None


class Tool:
    """Base class for tools."""
    
    def __init__(self, name: str, description: str, parameters: Dict[str, Any]):
        self.name = name
        self.description = description
        self.parameters = parameters
    
    async def execute(self, arguments: Dict[str, Any], context: AgentContext) -> str:
        """Execute the tool with given arguments."""
        raise NotImplementedError
    
    def to_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }


class AgentRuntime:
    """
    Runtime for executing AI agents.
    
    Manages conversation history, tool execution, and model interaction.
    """
    
    def __init__(
        self,
        model_client=None,
        tool_registry: Optional[Dict[str, Tool]] = None
    ):
        self.model_client = model_client
        self.tools = tool_registry or {}
        self.sessions: Dict[str, List[AgentMessage]] = {}
        self.max_iterations = 10
    
    def register_tool(self, tool: Tool):
        """Register a tool."""
        self.tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")
    
    def get_session_history(self, session_id: str) -> List[AgentMessage]:
        """Get conversation history for a session."""
        return self.sessions.get(session_id, [])
    
    def clear_session(self, session_id: str):
        """Clear a session's history."""
        if session_id in self.sessions:
            del self.sessions[session_id]
    
    async def run(
        self,
        context: AgentContext,
        user_message: str,
        stream: bool = False
    ) -> AsyncIterator[str]:
        """
        Run the agent on a user message.
        
        Args:
            context: Agent execution context
            user_message: User's input message
            stream: Whether to stream the response
            
        Yields:
            Response chunks (if streaming) or full response
        """
        # Initialize session
        if context.session_id not in self.sessions:
            self.sessions[context.session_id] = []
            
            # Add system message if provided
            if context.system_prompt:
                self.sessions[context.session_id].append(
                    AgentMessage(role="system", content=context.system_prompt)
                )
        
        history = self.sessions[context.session_id]
        
        # Add user message
        history.append(AgentMessage(role="user", content=user_message))
        
        # Run with tools
        if stream:
            async for chunk in self._run_with_tools_streaming(context, history):
                yield chunk
        else:
            response = await self._run_with_tools(context, history)
            yield response
    
    async def _run_with_tools(
        self,
        context: AgentContext,
        history: List[AgentMessage]
    ) -> str:
        """Run agent with tool support (non-streaming)."""
        for iteration in range(self.max_iterations):
            # Prepare messages for model
            messages = [msg.to_dict() for msg in history]
            
            # Get available tools
            available_tools = [tool.to_schema() for tool in self.tools.values()]
            
            # Call model
            if self.model_client:
                response = await self.model_client.chat_completion(
                    messages=messages,
                    tools=available_tools if available_tools else None,
                    tool_choice="auto" if available_tools else None
                )
                
                message = response.choices[0].message
                content = message.get("content", "")
                tool_calls_data = message.get("tool_calls", [])
            else:
                # Fallback when no model client
                content = self._generate_fallback_response(history)
                tool_calls_data = []
            
            # Check for tool calls
            if not tool_calls_data:
                # No tool calls, return the response
                assistant_msg = AgentMessage(role="assistant", content=content)
                history.append(assistant_msg)
                return content
            
            # Process tool calls
            assistant_msg = AgentMessage(
                role="assistant",
                content=content or "",
                tool_calls=[
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"]
                        }
                    }
                    for tc in tool_calls_data
                ]
            )
            history.append(assistant_msg)
            
            # Execute each tool
            for tc_data in tool_calls_data:
                tool_name = tc_data["function"]["name"]
                try:
                    arguments = json.loads(tc_data["function"]["arguments"])
                except json.JSONDecodeError:
                    arguments = {}
                
                result = await self._execute_tool(
                    tool_name,
                    arguments,
                    context,
                    tc_data["id"]
                )
                
                tool_msg = AgentMessage(
                    role="tool",
                    content=result.output if not result.error else f"Error: {result.error}",
                    tool_call_id=result.tool_call_id
                )
                history.append(tool_msg)
        
        return "Maximum iterations reached without completion."
    
    async def _run_with_tools_streaming(
        self,
        context: AgentContext,
        history: List[AgentMessage]
    ) -> AsyncIterator[str]:
        """Run agent with tool support (streaming)."""
        # For now, just yield the full response
        # In a real implementation, this would stream tokens
        response = await self._run_with_tools(context, history)
        yield response
    
    async def _execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: AgentContext,
        tool_call_id: str
    ) -> ToolResult:
        """Execute a tool call."""
        tool = self.tools.get(tool_name)
        
        if not tool:
            return ToolResult(
                tool_call_id=tool_call_id,
                output="",
                error=f"Tool '{tool_name}' not found"
            )
        
        try:
            output = await tool.execute(arguments, context)
            return ToolResult(tool_call_id=tool_call_id, output=output)
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return ToolResult(
                tool_call_id=tool_call_id,
                output="",
                error=str(e)
            )
    
    def _generate_fallback_response(self, history: List[AgentMessage]) -> str:
        """Generate a fallback response when no model client is available."""
        # Simple echo response for testing
        last_user_msg = None
        for msg in reversed(history):
            if msg.role == "user":
                last_user_msg = msg.content
                break
        
        if last_user_msg:
            return f"Echo: {last_user_msg}\n\n(Note: No AI model configured. This is a fallback response.)"
        
        return "Hello! How can I help you today?"
