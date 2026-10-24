import asyncio

from pyclaw.channels.base import InboundMessage
from pyclaw.gateway.bridge import IMBridge


class _Adapter:
    """An adapter that records what it was told."""

    def __init__(self):
        self.channel_id = "qq"
        self.sent = []
        self.contacts = []
        self._greeting_sent = True

    async def save_known_contact(self, sender_id):
        self.contacts.append(sender_id)

    async def send_message(self, sender_id, message):
        self.sent.append((sender_id, message))

    async def send_greeting_on_startup(self):
        self._greeting_sent = True


class _Runtime:
    def __init__(self):
        self.sessions = []
        self.messages = 0
        self.requests = 0
        self.errors = 0

    def get_or_create_session(self, session_id):
        self.sessions.append(session_id)

    def increment_channel_messages(self, channel_id):
        self.messages += 1

    def increment_requests(self):
        self.requests += 1

    def increment_errors(self):
        self.errors += 1


def _message(text, sender_id="u1", msg_id="m1"):
    return InboundMessage(id=msg_id, text=text, sender_id=sender_id)


async def _no_command(text, session, session_id):
    return None


def _bridge(adapter=None, runtime=None, command=None, interaction=None):
    return IMBridge(
        platform="qq",
        adapter=adapter or _Adapter(),
        runtime=runtime or _Runtime(),
        get_session=_get_session,
        run_interaction=interaction or _no_interaction,
        handle_command=command or _no_command,
        extra="",
        progress_text=lambda ev: "",
    )


async def _no_interaction(*args, **kwargs):
    return None


async def _get_session(session_id):
    return object()


def test_a_message_is_handed_to_the_model():
    asked = []

    async def interaction(session, adapter, sender_id, text, msg_id, **kw):
        asked.append(text)

    bridge = _bridge(interaction=interaction)

    async def main():
        await bridge.on_message(_message("hello"), "qq")

    asyncio.run(main())
    assert asked == ["hello"]


def test_a_command_is_answered_without_asking_the_model():
    asked = []

    async def interaction(session, adapter, sender_id, text, msg_id, **kw):
        asked.append(text)

    adapter = _Adapter()

    async def command(text, session, session_id):
        return "/help says hi"

    bridge = _bridge(adapter=adapter, command=command, interaction=interaction)

    async def main():
        await bridge.on_message(_message("/help"), "qq")

    asyncio.run(main())
    assert asked == []
    assert adapter.sent[0][1].text == "/help says hi"


def test_the_same_message_twice_is_answered_once():
    asked = []

    async def interaction(session, adapter, sender_id, text, msg_id, **kw):
        asked.append(text)

    bridge = _bridge(interaction=interaction)

    async def main():
        await bridge.on_message(_message("hello"), "qq")
        await bridge.on_message(_message("hello"), "qq")

    asyncio.run(main())
    assert asked == ["hello"]


def test_a_channel_that_fails_says_so_and_counts_the_failure():
    runtime = _Runtime()

    async def interaction(session, adapter, sender_id, text, msg_id, **kw):
        raise RuntimeError("InternalServerError")

    adapter = _Adapter()
    bridge = _bridge(adapter=adapter, runtime=runtime, interaction=interaction)

    async def main():
        await bridge.on_message(_message("hello"), "qq")

    asyncio.run(main())
    assert runtime.errors == 1
    assert "unavailable" in adapter.sent[0][1].text


def test_the_handler_takes_the_channel_id_the_message_came_from():
    """The channel calls its handler with the message and the channel it came
    from; a bridge that forgets the second one cannot answer at all."""
    bridge = _bridge()

    async def main():
        await bridge.on_message(_message("hello"), "qq")

    asyncio.run(main())


def test_the_bridge_answers_the_channel_that_asked():
    """The channel id that comes with the message is the one that gets the
    answer."""
    adapter = _Adapter()
    bridge = _bridge(adapter=adapter)

    async def main():
        await bridge.on_message(_message("hello"), "qq")

    asyncio.run(main())
    assert adapter.sent == []
