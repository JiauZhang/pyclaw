"""System RPC handlers."""

import logging
from typing import Dict, Any
from datetime import datetime

from pyclaw.version import __version__

logger = logging.getLogger(__name__)


async def handle_status(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Get detailed status."""
    runtime = context.get("runtime")

    return {
        "gateway": {
            "version": __version__,
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


async def handle_channel_rebind(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Rebind a channel (re-authenticate, e.g. new WeChat QR login)."""
    gateway = context.get("gateway")
    platform = params.get("platform", "wechat")

    adapter = gateway.channels.get(platform)
    if not adapter:
        return {"error": f"Channel '{platform}' is not active"}

    qr_url = None

    def capture_url(url):
        nonlocal qr_url
        qr_url = url

    ok = await adapter.rebind(on_qr_url=capture_url)
    return {"ok": ok, "platform": platform, "qr_url": qr_url}
