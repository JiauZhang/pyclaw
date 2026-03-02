"""AI Agent implementation using chatchat package with tool support."""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, AsyncIterator
from datetime import datetime
from chatchat import AI
from .runtime import AgentContext
from ..gateway.runtime import SessionState
from ..tools import ToolRegistry, create_default_tool_registry, ToolResult

logger = logging.getLogger(__name__)


class Agent:
    """
    AI Agent using chatchat package with tool support.

    This agent uses actual LLM models (OpenAI, DeepSeek, etc.) through
    the chatchat library and supports function calling with tools.
    
    Features:
    - Multi-provider support (deepseek, openai, alibaba, etc.)
    - Tool calling with multi-turn interactions
    - Session history management
    - Streaming responses
    """

    def __init__(
        self,
        provider: str = "deepseek",
        model: Optional[str] = None,
        instruction: Optional[str] = None,
        tool_registry: Optional[ToolRegistry] = None,
        workspace_dir: Optional[str] = None,
        api_key: Optional[str] = None,
        max_iterations: int = 10
    ):
        """
        Initialize the Agent.

        Args:
            provider: Model provider (deepseek, openai, alibaba, etc.)
            model: Model name (e.g., "deepseek-chat", "gpt-4")
            instruction: System prompt/instruction
            tool_registry: Custom tool registry (optional)
            workspace_dir: Workspace directory for file operations
            api_key: API key (optional, will read from ~/.chatchat.json if not provided)
            max_iterations: Maximum tool call iterations
        """
        self.provider = provider
        self.model_name = model
        self.max_iterations = max_iterations
        self.workspace_dir = workspace_dir

        # Initialize tool registry
        self.tool_registry = tool_registry or create_default_tool_registry(
            workspace_dir=workspace_dir,
            enable_exec=True,
            enable_file_ops=True,
            enable_python=True
        )

        # Default instruction if not provided
        self.instruction = instruction or self._default_instruction()

        # Initialize chatchat AI client
        self._init_client(api_key)
        
        # Session storage for multi-turn conversations
        self._sessions: Dict[str, List[Dict[str, Any]]] = {}

    def _init_client(self, api_key: Optional[str] = None):
        """Initialize the chatchat client."""
        try:
            if api_key:
                env_var = f"{self.provider.upper()}_API_KEY"
                os.environ[env_var] = api_key

            self.ai = AI(
                provider=self.provider,
                model=self.model_name,
                instruction=self.instruction
            )
            self.client = self.ai.client
            logger.info(f"Agent initialized: {self.provider}/{self.model_name or 'default'}")

        except Exception as e:
            logger.error(f"Failed to initialize chatchat client: {e}")
            raise

    def _default_instruction(self) -> str:
        """Generate default system instruction with tool descriptions."""
        tools_description = self._get_tools_description()

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

    def _get_tools_description(self) -> str:
        """Get description of all available tools."""
        schemas = self.tool_registry.get_schemas()
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

    def _get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Get or create session history."""
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        return self._sessions[session_id]

    async def run(
        self,
        message: str,
        session: SessionState,
        context: Optional[AgentContext] = None
    ) -> str:
        """
        Process a message and generate a response using the AI model.

        Args:
            message: User's input message
            session: Session state object
            context: Optional agent context

        Returns:
            Response string
        """
        message = message.strip()

        # Handle slash commands
        if message.startswith("/"):
            return await self._handle_command(message, session)

        # Get or create conversation history
        history = self._get_session_history(session.id)
        
        # Add user message
        history.append({"role": "user", "content": message})

        try:
            # Run the conversation with potential tool calls
            response = await self._run_conversation(history, context)
            return response

        except Exception as e:
            logger.error(f"Agent error: {e}")
            return f"I encountered an error: {str(e)}. Please try again or check your configuration."

    async def _run_conversation(
        self,
        history: List[Dict[str, Any]],
        context: Optional[AgentContext]
    ) -> str:
        """Run conversation with tool support."""
        
        for iteration in range(self.max_iterations):
            # Build messages for this turn
            messages = self._build_messages(history)
            
            # Get last user message
            last_user_msg = None
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    last_user_msg = msg.get("content", "")
                    break
            
            if not last_user_msg:
                return "No user message found."
            
            # Get AI response
            try:
                # Send complete message history to model
                response = self.client.chat(
                    text=last_user_msg,
                    stream=False
                )
                response_text = response.text if hasattr(response, 'text') else str(response)
            except Exception as e:
                logger.error(f"AI error: {e}")
                return f"AI Error: {str(e)}"
            
            # Check for tool call
            tool_call = self._parse_tool_call(response_text)
            
            if not tool_call:
                # No tool call, return response
                history.append({"role": "assistant", "content": response_text})
                return response_text
            
            # Execute tool
            tool_name = tool_call.get("tool")
            tool_args = tool_call.get("args", {})
            
            logger.info(f"Tool call: {tool_name}({tool_args})")
            
            result = await self.tool_registry.execute(tool_name, tool_args, context)
            
            # Add tool interaction to history
            history.append({"role": "assistant", "content": response_text})
            
            tool_result_text = result.output
            if result.error:
                tool_result_text += f"\nError: {result.error}"
            
            history.append({
                "role": "user",
                "content": f"Tool '{tool_name}' result: {tool_result_text}"
            })
            
            # Now get AI response based on tool result
            try:
                # Build updated messages with tool result
                messages = self._build_messages(history)
                
                # Get last user message (which is the tool result)
                last_user_msg = None
                for msg in reversed(messages):
                    if msg.get("role") == "user":
                        last_user_msg = msg.get("content", "")
                        break
                
                if not last_user_msg:
                    return "No user message found."
                
                # Get AI response
                response = self.client.chat(
                    text=last_user_msg,
                    stream=False
                )
                final_response = response.text if hasattr(response, 'text') else str(response)
                
                # Add final response to history
                history.append({"role": "assistant", "content": final_response})
                return final_response
            except Exception as e:
                logger.error(f"AI error after tool execution: {e}")
                return f"AI Error: {str(e)}"
        
        return "Maximum tool iterations reached."

    def _build_messages(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build message list for API call."""
        messages = []
        
        if self.instruction:
            messages.append({"role": "system", "content": self.instruction})
        
        messages.extend(history)
        
        return messages

    def _parse_tool_call(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse tool call from response."""
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

    async def _handle_command(self, command: str, session: SessionState) -> str:
        """Handle slash commands."""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        
        if cmd == "/help":
            return f"""Agent Help

Provider: {self.provider}
Model: {self.model_name or 'default'}

Commands:
/help - Show help
/tools - List tools
/clear - Clear history
/status - Show status
/stream <msg> - Stream response

Just type your message to chat!"""
        
        elif cmd == "/tools":
            tools = self.tool_registry.list_tools()
            return f"Available tools ({len(tools)}):\n" + "\n".join(f"• {t}" for t in tools)
        
        elif cmd == "/clear":
            if session.id in self._sessions:
                del self._sessions[session.id]
            self.client.clear()
            return "History cleared!"
        
        elif cmd == "/status":
            tools = self.tool_registry.list_tools()
            history_len = len(self._sessions.get(session.id, []))
            return f"""Status:
• Provider: {self.provider}
• Model: {self.model_name or 'default'}
• Tools: {len(tools)}
• History: {history_len} messages"""
        
        elif cmd == "/stream" and len(parts) > 1:
            return "Use the /v1/chat/completions endpoint with stream=true for streaming."
        
        else:
            return f"Unknown command: {cmd}. Type /help for available commands."

    async def chat_stream(
        self,
        message: str,
        session: SessionState,
        context: Optional[AgentContext] = None
    ) -> AsyncIterator[str]:
        """Stream chat response with tool support."""
        # Handle slash commands
        if message.startswith("/"):
            result = await self._handle_command(message, session)
            yield result
            return
        
        # Get or create conversation history
        history = self._get_session_history(session.id)
        
        # Add user message
        history.append({"role": "user", "content": message})
        
        try:
            # Run the conversation with potential tool calls
            for iteration in range(self.max_iterations):
                # Build messages for this turn
                messages = self._build_messages(history)
                
                # Get last user message
                last_user_msg = None
                for msg in reversed(messages):
                    if msg.get("role") == "user":
                        last_user_msg = msg.get("content", "")
                        break
                
                if not last_user_msg:
                    yield "No user message found."
                    return
                
                # Get AI response with streaming
                try:
                    response = self.client.chat(
                        text=last_user_msg,
                        stream=True
                    )
                    
                    # Stream the response chunks
                    response_text = ""
                    for chunk in response:
                        if hasattr(chunk, 'text'):
                            chunk_text = chunk.text
                        elif hasattr(chunk, 'content'):
                            chunk_text = chunk.content
                        elif isinstance(chunk, str):
                            chunk_text = chunk
                        else:
                            chunk_text = str(chunk)
                        
                        if chunk_text:
                            response_text += chunk_text
                            yield chunk_text
                except Exception as e:
                    logger.error(f"AI error: {e}")
                    yield f"AI Error: {str(e)}"
                    return
                
                # Check for tool call
                tool_call = self._parse_tool_call(response_text)
                
                if not tool_call:
                    # No tool call, stream the response
                    history.append({"role": "assistant", "content": response_text})
                    yield response_text
                    return
                
                # Execute tool
                tool_name = tool_call.get("tool")
                tool_args = tool_call.get("args", {})
                
                logger.info(f"Tool call: {tool_name}({tool_args})")
                
                result = await self.tool_registry.execute(tool_name, tool_args, context)
                
                # Add tool interaction to history
                history.append({"role": "assistant", "content": response_text})
                
                tool_result_text = result.output
                if result.error:
                    tool_result_text += f"\nError: {result.error}"
                
                history.append({
                    "role": "user",
                    "content": f"Tool '{tool_name}' result: {tool_result_text}"
                })
                
                # Now get AI response based on tool result
                try:
                    # Build updated messages with tool result
                    messages = self._build_messages(history)
                    
                    # Get last user message (which is the tool result)
                    last_user_msg = None
                    for msg in reversed(messages):
                        if msg.get("role") == "user":
                            last_user_msg = msg.get("content", "")
                            break
                    
                    if not last_user_msg:
                        yield "No user message found."
                        return
                    
                    # Get AI response with streaming
                    response = self.client.chat(
                        text=last_user_msg,
                        stream=True
                    )
                    
                    # Stream the response chunks
                    final_response = ""
                    for chunk in response:
                        if hasattr(chunk, 'text'):
                            chunk_text = chunk.text
                        elif hasattr(chunk, 'content'):
                            chunk_text = chunk.content
                        elif isinstance(chunk, str):
                            chunk_text = chunk
                        else:
                            chunk_text = str(chunk)
                        
                        if chunk_text:
                            final_response += chunk_text
                            yield chunk_text
                    
                    # Add final response to history
                    history.append({"role": "assistant", "content": final_response})
                    yield final_response
                    return
                except Exception as e:
                    logger.error(f"AI error after tool execution: {e}")
                    yield f"AI Error: {str(e)}"
                    return
            
            yield "Maximum tool iterations reached."
                    
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"Error: {str(e)}"

    def get_available_tools(self) -> List[str]:
        """Get available tools."""
        return self.tool_registry.list_tools()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get tool schemas."""
        return self.tool_registry.get_schemas()
