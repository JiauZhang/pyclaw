"""Chat RPC handlers."""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


async def handle_chat_send(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Send a chat message."""
    message = params.get("message", "").strip()
    session_key = params.get("sessionKey") or params.get("session_id") or "default"
    
    if not message:
        return {"error": "Message is required"}
    
    runtime = context.get("runtime")
    runtime.update_session_activity(session_key)
    
    # Delegate to agent handler
    from .agent import handle_agent
    return await handle_agent(params, context)


async def handle_chat_history(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Get chat history for a session."""
    session_key = params.get("sessionKey") or params.get("session_id") or "default"
    limit = params.get("limit", 50)
    
    runtime = context.get("runtime")
    session = runtime.get_session(session_key)
    
    if not session:
        return {"messages": [], "count": 0}
    
    # For now, return empty - in real implementation, this would
    # retrieve from persistent storage
    return {
        "session_key": session_key,
        "messages": [],
        "count": 0,
        "total_messages": session.message_count
    }
