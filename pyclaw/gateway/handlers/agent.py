"""Agent RPC handler."""

import logging
import os
from typing import Dict, Any, Optional

from ...agents import AgentRuntime, AgentContext, Agent
from ...config import load_config

logger = logging.getLogger(__name__)

# Cache for agent instances
_agent_cache: Dict[str, Any] = {}


def _get_or_create_agent(
    agent_id: str,
    config: Any,
    provider: Optional[str] = None,
    model: Optional[str] = None
) -> Any:
    """Get or create an agent instance."""
    cache_key = f"{agent_id}:{provider}:{model}"
    
    if cache_key in _agent_cache:
        return _agent_cache[cache_key]
    
    # Get agent config
    agent_config = config.get_agent_config(agent_id)
    
    # Determine workspace
    workspace_dir = None
    if agent_config and hasattr(agent_config, 'workspace'):
        workspace_dir = agent_config.workspace
    if not workspace_dir:
        workspace_dir = os.path.expanduser("~/.pyclaw/workspace")
    
    # Determine provider and model
    if not provider:
        provider = getattr(agent_config, 'provider', None) or os.getenv('OPENCLAW_PROVIDER', 'deepseek')
    if not model:
        model = getattr(agent_config, 'model', None) or os.getenv('OPENCLAW_MODEL')
    
    # Get instruction/system prompt
    instruction = None
    if agent_config and hasattr(agent_config, 'system_prompt'):
        instruction = agent_config.system_prompt
    
    # Create agent
    agent = Agent(
        provider=provider,
        model=model,
        instruction=instruction,
        workspace_dir=workspace_dir
    )
    logger.info(f"Created Agent for {agent_id} using {provider}/{model or 'default'}")
    
    _agent_cache[cache_key] = agent
    return agent


async def handle_agent(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle agent RPC call.

    Args:
        params: RPC parameters
        context: Request context

    Returns:
        Response dictionary
    """
    message = params.get("message", "").strip()
    session_key = params.get("sessionKey") or params.get("session_id") or "default"
    agent_id = params.get("agentId") or params.get("agent_id")
    provider = params.get("provider")
    model = params.get("model")
    stream = params.get("stream", False)
    
    if not message:
        return {"error": "Message is required"}
    
    # Load configuration
    config = load_config()
    
    # Get runtime and gateway
    runtime = context.get("runtime")
    gateway = context.get("gateway")
    
    # Use gateway config provider/model if not specified in params
    if not provider and gateway and gateway.config.provider:
        provider = gateway.config.provider
    if not model and gateway and gateway.config.model:
        model = gateway.config.model
    
    # Get or create session
    session = runtime.get_or_create_session(session_key, agent_id or "default")
    
    # Get or create agent
    try:
        agent = _get_or_create_agent(agent_id or "default", config, provider, model)
    except Exception as e:
        logger.error(f"Failed to create agent: {e}")
        return {"error": f"Failed to initialize agent: {str(e)}"}
    
    # Create agent context
    agent_context = AgentContext(
        session_id=session_key,
        agent_id=agent_id or "default",
        user_id=context.get("client_id", "anonymous"),
        channel_id="web",
        system_prompt=getattr(agent, 'instruction', None)
    )
    
    # Run agent
    try:
        if stream:
            # Return stream iterator info
            return {
                "stream": True,
                "sessionKey": session_key,
                "agentId": agent_id or "default",
                "message": "Use /v1/chat/completions for streaming"
            }
        else:
            response = await agent.run(message, session, agent_context)
            runtime.update_session_activity(session_key)
            runtime.increment_requests()
            
            return {
                "response": response,
                "sessionKey": session_key,
                "agentId": agent_id or "default",
                "tools_available": agent.get_available_tools(),
                "provider": getattr(agent, 'provider', 'simple'),
                "model": getattr(agent, 'model_name', None)
            }
    except Exception as e:
        logger.error(f"Agent error: {e}")
        runtime.increment_errors()
        return {"error": str(e)}


async def handle_agent_stream(params: Dict[str, Any], context: Dict[str, Any]):
    """
    Handle streaming agent RPC call.

    Returns an async iterator for streaming responses.
    """
    message = params.get("message", "").strip()
    session_key = params.get("sessionKey") or params.get("session_id") or "default"
    agent_id = params.get("agentId") or params.get("agent_id")
    provider = params.get("provider")
    model = params.get("model")
    
    if not message:
        yield {"error": "Message is required"}
        return
    
    config = load_config()
    runtime = context.get("runtime")
    gateway = context.get("gateway")
    
    # Use gateway config provider/model if not specified in params
    if not provider and gateway and gateway.config.provider:
        provider = gateway.config.provider
    if not model and gateway and gateway.config.model:
        model = gateway.config.model
    
    session = runtime.get_or_create_session(session_key, agent_id or "default")
    
    try:
        agent = _get_or_create_agent(agent_id or "default", config, provider, model)
    except Exception as e:
        yield {"error": f"Failed to initialize agent: {str(e)}"}
        return
    
    agent_context = AgentContext(
        session_id=session_key,
        agent_id=agent_id or "default",
        user_id=context.get("client_id", "anonymous"),
        channel_id="web"
    )
    
    try:
        async for chunk in agent.chat_stream(message, session, agent_context):
            yield {"chunk": chunk}
        
        runtime.update_session_activity(session_key)
        runtime.increment_requests()
        
    except Exception as e:
        logger.error(f"Stream error: {e}")
        yield {"error": str(e)}


async def handle_agent_tools(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get available tools for an agent.

    Args:
        params: RPC parameters
        context: Request context

    Returns:
        Response dictionary with tool schemas
    """
    agent_id = params.get("agentId") or params.get("agent_id")
    provider = params.get("provider")
    model = params.get("model")
    
    config = load_config()
    
    try:
        agent = _get_or_create_agent(agent_id or "default", config, provider, model)
        
        return {
            "agentId": agent_id or "default",
            "provider": getattr(agent, 'provider', 'simple'),
            "model": getattr(agent, 'model_name', None),
            "tools": agent.get_available_tools(),
            "schemas": agent.get_tool_schemas()
        }
    except Exception as e:
        return {"error": str(e)}


async def handle_tool_call(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a tool call directly.

    Args:
        params: RPC parameters
        context: Request context

    Returns:
        Response dictionary with tool result
    """
    tool_name = params.get("tool")
    tool_args = params.get("args", {})
    agent_id = params.get("agentId") or params.get("agent_id")
    provider = params.get("provider")
    model = params.get("model")
    
    if not tool_name:
        return {"error": "Tool name is required"}
    
    config = load_config()
    
    try:
        agent = _get_or_create_agent(agent_id or "default", config, provider, model)
        
        agent_context = AgentContext(
            session_id=params.get("sessionKey", "tool_call"),
            agent_id=agent_id or "default",
            user_id=context.get("client_id", "anonymous"),
            channel_id="web"
        )
        
        result = await agent.tool_registry.execute(tool_name, tool_args, agent_context)
        
        return {
            "tool": tool_name,
            "args": tool_args,
            "output": result.output,
            "error": result.error,
            "exit_code": result.exit_code
        }
    except Exception as e:
        logger.error(f"Tool call error: {e}")
        return {
            "tool": tool_name,
            "args": tool_args,
            "error": str(e),
            "exit_code": 1
        }


async def handle_chat_completions(params: Dict[str, Any], context: Dict[str, Any]):
    """
    OpenAI-compatible chat completions endpoint.

    Supports both streaming and non-streaming responses.
    """
    messages = params.get("messages", [])
    model = params.get("model", "default")
    stream = params.get("stream", False)
    session_key = params.get("sessionKey") or params.get("session_id") or "default"
    agent_id = params.get("agentId") or params.get("agent_id")
    
    if not messages:
        error_msg = "Messages are required"
        yield {"error": error_msg}
        return
    
    config = load_config()
    runtime = context.get("runtime")
    gateway = context.get("gateway")
    session = runtime.get_or_create_session(session_key, agent_id or "default")
    
    # Parse model string (format: "provider/model" or just "model")
    provider = None
    model_name = model
    if "/" in model:
        parts = model.split("/", 1)
        provider = parts[0]
        model_name = parts[1] if parts[1] else None
    
    # Use gateway config provider/model if not specified
    if not provider and gateway and gateway.config.provider:
        provider = gateway.config.provider
    if not model_name and gateway and gateway.config.model:
        model_name = gateway.config.model
    
    try:
        agent = _get_or_create_agent(agent_id or "default", config, provider, model_name)
        
        # Get the last user message
        last_message = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_message = msg.get("content", "")
                break
        
        if not last_message:
            error_msg = "No user message found"
            yield {"error": error_msg}
            return
        
        agent_context = AgentContext(
            session_id=session_key,
            agent_id=agent_id or "default",
            user_id=context.get("client_id", "anonymous"),
            channel_id="web"
        )
        
        if stream:
            # Streaming response
            full_response = ""
            async for chunk in agent.chat_stream(last_message, session, agent_context):
                full_response += chunk
                yield {
                    "choices": [{
                        "delta": {"content": chunk},
                        "index": 0
                    }]
                }
            
            runtime.update_session_activity(session_key)
            runtime.increment_requests()
            
        else:
            # Non-streaming response
            response = await agent.run(last_message, session, agent_context)
            
            runtime.update_session_activity(session_key)
            runtime.increment_requests()
            
            yield {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": response
                    },
                    "index": 0,
                    "finish_reason": "stop"
                }],
                "model": model,
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }
            }
            
    except Exception as e:
        logger.error(f"Chat completions error: {e}")
        yield {"error": str(e)}
