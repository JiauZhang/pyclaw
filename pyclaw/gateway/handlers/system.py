"""System RPC handlers."""

from typing import Dict, Any
from datetime import datetime


async def handle_health(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Health check."""
    runtime = context.get("runtime")
    
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": runtime.uptime_seconds,
        "active_sessions": len(runtime.sessions),
        "connected_clients": len(runtime.clients)
    }


async def handle_status(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Get detailed status."""
    runtime = context.get("runtime")
    
    return {
        "gateway": {
            "version": "0.1.0",
            "started_at": runtime.started_at.isoformat() if runtime.started_at else None,
            "uptime_seconds": runtime.uptime_seconds
        },
        "connections": {
            "websocket_clients": len(runtime.clients),
            "active_sessions": len(runtime.sessions)
        },
        "channels": runtime.get_channel_status(),
        "agents": runtime.get_agent_status(),
        "stats": runtime.get_stats()
    }
