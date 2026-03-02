"""AI Agent implementation using chatchat package with tool support."""

import logging
from typing import Any, Dict, List, Optional, AsyncIterator
from .context import AgentContext
from ..gateway.runtime import SessionState
from ..tools import ToolRegistry
from .core.orchestrator import AgentOrchestrator

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
        self.instruction = instruction
        self.max_iterations = max_iterations
        self.workspace_dir = workspace_dir

        # Initialize orchestrator to handle all agent logic
        self.orchestrator = AgentOrchestrator(
            provider=provider,
            model=model,
            instruction=instruction,
            tool_registry=tool_registry,
            workspace_dir=workspace_dir,
            api_key=api_key,
            max_iterations=max_iterations
        )

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
            return await self.orchestrator.command_handler.handle(message, session)

        # Get or create conversation history
        history = self.orchestrator.session_manager.get_session_history(session.id)
        
        # Add user message
        history.append({"role": "user", "content": message})

        try:
            # Run the conversation with potential tool calls
            response = await self.orchestrator.run_conversation(history, context)
            return response

        except Exception as e:
            logger.error(f"Agent error: {e}")
            return f"I encountered an error: {str(e)}. Please try again or check your configuration."

    async def chat_stream(
        self,
        message: str,
        session: SessionState,
        context: Optional[AgentContext] = None
    ) -> AsyncIterator[str]:
        """Stream chat response with tool support."""
        # Handle slash commands
        if message.startswith("/"):
            result = await self.orchestrator.command_handler.handle(message, session)
            yield result
            return
        
        # Delegate to orchestrator
        async for chunk in self.orchestrator.handle_stream(message, session, context):
            yield chunk

    def get_available_tools(self) -> List[str]:
        """Get available tools."""
        return self.orchestrator.get_available_tools()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get tool schemas."""
        return self.orchestrator.get_tool_schemas()
