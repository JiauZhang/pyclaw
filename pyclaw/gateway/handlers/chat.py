import logging
from typing import Dict, Any

from .agent import handle_agent

logger = logging.getLogger(__name__)


async def handle_chat_send(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    message = params.get("message", "").strip()
    session_key = params.get("sessionKey") or params.get("session_id") or "default"

    if not message:
        return {"error": "Message is required"}

    runtime = context.get("runtime")
    runtime.update_session_activity(session_key)

    return await handle_agent(params, context)


async def handle_chat_history(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    session_key = params.get("sessionKey") or params.get("session_id") or "default"

    runtime = context.get("runtime")
    session = runtime.get_session(session_key)

    if not session:
        return {"messages": [], "count": 0, "total_messages": 0}

    messages = []
    for entry in session.transcript():
        role = entry.get("role")
        if role not in ("user", "assistant"):
            continue
        content = entry.get("content")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
        messages.append({"role": role, "content": str(content or "")})

    return {"session_key": session_key, "messages": messages, "count": len(messages), "total_messages": session.message_count}
