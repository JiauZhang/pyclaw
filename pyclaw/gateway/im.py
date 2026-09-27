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


def _brief_input(data: dict) -> str:
    value = data.get('input')
    if not isinstance(value, dict):
        return ''
    for key in ('command', 'path', 'file_path', 'pattern', 'query', 'url',
                'skill'):
        v = value.get(key)
        if v:
            return ' ' + str(v).replace('\n', ' ')[:40]
    return ''


def _im_progress_text(ev) -> str:
    kind = ev.kind
    data = ev.data or {}
    if kind == AGENT_REASON_START:
        return '🔄 思考中…'
    if kind == AGENT_TOOL_CALL:
        who = getattr(ev, 'agent', '') or ''
        prefix = f'{who} ' if who and who != 'lead' else ''
        return f'🔧 {prefix}{data.get("tool", "tool")}{_brief_input(data)}'
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
    conversation = session.conv_session_id
    append_conv(conversation, "user", text)

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
        session.record_turn()
        pump.cancel()
        for status in tracker.drain(clock()):
            await adapter.send_message(sender_id, OutboundMessage(text=status, reply_to=msg_id))

    if response:
        for part in split_long_message(response, max_msg_len):
            await adapter.send_message(sender_id, OutboundMessage(text=part, reply_to=msg_id))
    return response
