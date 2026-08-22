import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


async def handle_sessions_get(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    key = params.get("key")
    runtime = context.get("runtime")
    
    if not key:
        return {"error": "Session key is required"}
    
    session = runtime.get_session(key)
    if not session:
        return {"error": "Session not found"}
    
    return {
        "key": key,
        "session": {
            "id": session.id,
            "agent_id": session.agent_id,
            "created_at": session.created_at.isoformat(),
            "last_activity": session.last_activity.isoformat(),
            "message_count": session.message_count
        }
    }


async def handle_sessions_reset(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    key = params.get("key")

    if not key:
        return {"error": "Session key is required"}

    await context.get("gateway")._remove_session(key)

    return {
        "key": key,
        "ok": True,
        "message": "Session reset successfully"
    }


async def handle_sessions_list(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    runtime = context.get("runtime")
    
    sessions: List[Dict[str, Any]] = []
    for session in runtime.sessions.values():
        sessions.append({
            "id": session.id,
            "agent_id": session.agent_id,
            "created_at": session.created_at.isoformat(),
            "last_activity": session.last_activity.isoformat(),
            "message_count": session.message_count
        })
    
    return {
        "sessions": sessions,
        "count": len(sessions)
    }
