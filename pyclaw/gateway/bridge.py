"""What happens when a message arrives from an IM channel.

A message that arrives is not just text: it may be a command, it may be the
same message twice, it may be the first one from someone who has not been
heard from before. Deciding which of those it is, and answering it, is one
job, and it is not the job of the adapter that carried the message or of the
server that started the adapter.
"""

import logging
import time

from ..channels.base import OutboundMessage
from .im import _friendly_channel_error
from ..session.store import follow_conversation, record_meta, resolve_session_id

logger = logging.getLogger(__name__)

DUPLICATE_WINDOW_SECONDS = 30


class IMBridge:
    """Answers what an IM channel receives.

    The bridge is told how to reach the session a message belongs to and how
    to answer; everything else - whether the message is a repeat, whether it
    is a command, what the model said back - it works out for itself.
    """

    def __init__(self, *, platform, adapter, runtime, get_session, run_interaction, handle_command, extra, progress_text):
        self.platform = platform
        self.adapter = adapter
        self.runtime = runtime
        self._get_session = get_session
        self._run_interaction = run_interaction
        self._handle_command = handle_command
        self._extra = extra
        self._progress_text = progress_text
        self._recent: dict[tuple, float] = {}

    async def on_message(self, msg, channel_id: str = ""):
        """Answer one message.

        The channel id comes with the message; it says which channel the
        message came from, which is not always the one this bridge serves.
        """
        platform = self.platform
        adapter = self.adapter
        await adapter.save_known_contact(msg.sender_id)
        session_id = resolve_session_id([platform, str(msg.sender_id)])
        follow_conversation(session_id)
        logger.info("IM '%s' received from %s: %s", platform, msg.sender_id, msg.text)
        record_meta(
            session_id,
            {
                "channel": "im",
                "platform": platform,
                "sender_id": str(msg.sender_id),
            },
        )

        if self._is_repeat(msg):
            logger.debug("Dropped duplicate IM message from %s", msg.sender_id)
            return

        if not adapter._greeting_sent:
            await adapter.send_greeting_on_startup()

        self.runtime.get_or_create_session(session_id)
        session = await self._get_session(session_id)
        reply = await self._handle_command(msg.text, session, session_id)
        if reply is not None:
            await self._answer_command(reply, session, session_id, msg)
            return

        try:
            await self._run_interaction(
                session,
                adapter,
                msg.sender_id,
                msg.text,
                msg.id,
                im_extra=self._extra,
                progress_fn=self._progress_text,
            )
            logger.info("IM '%s' replied to %s", platform, msg.sender_id)
            self.runtime.increment_channel_messages(adapter.channel_id)
            self.runtime.increment_requests()
        except Exception as exc:
            logger.error("Channel '%s' handler error: %s", platform, exc)
            self.runtime.increment_errors()
            friendly = _friendly_channel_error(exc)
            await adapter.send_message(msg.sender_id, OutboundMessage(text=friendly, reply_to=msg.id))

    async def _answer_command(self, reply, session, session_id, msg):
        """A command is answered directly, or handed to the model when it
        needs one."""
        adapter = self.adapter
        if isinstance(reply, tuple):
            info, follow = reply
            if info:
                await adapter.send_message(msg.sender_id, OutboundMessage(text=info, reply_to=msg.id))
            await self._run_interaction(
                session,
                adapter,
                msg.sender_id,
                follow,
                msg.id,
                im_extra=self._extra,
                progress_fn=self._progress_text,
            )
            logger.info("IM '%s' ran /init via model", self.platform)
            self.runtime.increment_requests()
            return
        await adapter.send_message(
            msg.sender_id,
            OutboundMessage(text=reply, reply_to=msg.id),
        )
        logger.info("IM '%s' sent slash reply to %s", self.platform, msg.sender_id)
        self.runtime.increment_requests()

    def _is_repeat(self, msg) -> bool:
        """The same message twice within the window is one message."""
        key = (self.platform, msg.sender_id, msg.text)
        now = time.time()
        if key in self._recent and now - self._recent[key] < DUPLICATE_WINDOW_SECONDS:
            return True
        self._recent[key] = now
        return False
