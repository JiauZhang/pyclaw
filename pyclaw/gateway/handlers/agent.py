"""Agent RPC handler."""

import logging
from typing import Dict, Any

from ...agents import AgentRuntime, AgentContext
from ...config import load_config

logger = logging.getLogger(__name__)


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
    
    if not message:
        return {"error": "Message is required"}
    
    # Load configuration
    config = load_config()
    
    # Get runtime
    runtime = context.get("runtime")
    
    # Get or create session
    session = runtime.get_or_create_session(session_key, agent_id or "default")
    
    # Get agent config
    agent_config = config.get_agent_config(agent_id)
    if not agent_config:
        return {"error": f"Agent not found: {agent_id}"}
    
    # Create agent context
    agent_context = AgentContext(
        session_id=session_key,
        agent_id=agent_id or "default",
        user_id=context.get("client_id", "anonymous"),
        channel_id="web",
        system_prompt=agent_config.system_prompt
    )
    
    # Create agent runtime (simplified for now)
    from ...agents.simple_agent import SimpleAgent
    agent = SimpleAgent()
    
    # Run agent
    try:
        response = await agent.run(message, session)
        runtime.update_session_activity(session_key)
        runtime.increment_requests()
        
        return {
            "response": response,
            "sessionKey": session_key,
            "agentId": agent_id or "default"
        }
    except Exception as e:
        logger.error(f"Agent error: {e}")
        runtime.increment_errors()
        return {"error": str(e)}
