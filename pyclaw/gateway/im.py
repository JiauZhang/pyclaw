from __future__ import annotations

import asyncio
import logging
import time

from chatchat.hooks.events import (AGENT_REASON_START, AGENT_TOOL_CALL,
                                   AGENT_WARN)

from ..channels import OutboundMessage
from ..channels.im_formatter import IMStatusTracker, split_long_message
from ..session.store import append_conv, record_meta, resolve_session_id
from ..slash import handle_slash

logger = logging.getLogger(__name__)


def _im_progress_text(ev) -> str:
    kind = ev.kind
    data = ev.data or {}
    if kind == AGENT_REASON_START:
        return '🔄 PyClaw 思考中…'
    if kind == AGENT_TOOL_CALL:
        name = data.get('tool', 'tool')
        return f'🔧 调用工具 {name}'
    if kind == AGENT_WARN:
        return f'⚠️ {data.get("text", "")}'
    return ''


def _friendly_channel_error(exc: Exception) -> str:
    err_msg = str(exc)
    lowered = err_msg.lower()
    if "timed out" in lowered:
        return "Request timed out. Please try again later."
    if "InternalServerError" in err_msg or "500" in err_msg:
        return "Service temporarily unavailable. Please try again later."
    if "rate" in lowered:
        return "Too many requests. Please wait a moment and try again."
    return f"An error occurred: {err_msg[:200]}"


async def run_im_interaction(
    session,
    adapter,
    session_id: str,
    sender_id: str,
    text: str,
    msg_id,
    *,
    im_extra: str,
    progress_fn: Callable,
    status_interval: float = 4.0,
    max_msg_len: int = 1500,
    clock=time.monotonic,
):
    session.conv_session_id = session_id
    append_conv(session_id, "user", text)

    tracker = IMStatusTracker(refresh_interval=status_interval)

    def on_event(ev):
        status = progress_fn(ev)
        if status:
            tracker.update(status, clock())

    async def pump_status():
        while True:
            await asyncio.sleep(status_interval / 2)
            for status in tracker.drain(clock()):
                await adapter.send_message(sender_id, OutboundMessage(text=status, reply_to=msg_id))

    pump = asyncio.create_task(pump_status())
    response = ""
    try:
        response = await session.chat(f"{im_extra}\n{text}", on_event=on_event)
    finally:
        pump.cancel()
        for status in tracker.drain(clock()):
            await adapter.send_message(sender_id, OutboundMessage(text=status, reply_to=msg_id))

    if response:
        for part in split_long_message(response, max_msg_len):
            await adapter.send_message(sender_id, OutboundMessage(text=part, reply_to=msg_id))
    return response
