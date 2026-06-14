import asyncio
import itertools
import logging
from datetime import datetime
from typing import AsyncIterator, Dict, Any, Optional

from .base import ChannelAdapter, InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)

_wechat_msg_counter = itertools.count()


class IMChannelAdapter(ChannelAdapter):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.platform: str = config.get("platform", "qq")
        self._client: Any = None
        self._message_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._receive_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._greeting_sent = False

    @property
    def channel_id(self) -> str:
        return f"im_{self.platform}"

    async def connect(self) -> bool:
        if self.platform == "qq":
            return await self._connect_qq()
        if self.platform == "wechat":
            return await self._connect_wechat()
        logger.error("Unsupported platform: %s", self.platform)
        return False

    async def disconnect(self):
        self._stop_event.set()
        if self._client is not None:
            try:
                if self.platform == "qq":
                    await self._client.stop()
                elif self.platform == "wechat":
                    await self._client.close()
            except Exception:
                pass
        if self._receive_task is not None and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        self._connected = False

    async def send_message(self, to: str, message: OutboundMessage) -> bool:
        if self._client is None or not self._connected:
            return False
        msg_type = message.metadata.get("type", "c2c")
        try:
            if self.platform == "qq":
                if msg_type == "group":
                    await self._client.send_group_message(to, message.text)
                else:
                    await self._client.send_c2c_message(to, message.text)
            elif self.platform == "wechat":
                await self._client.send_text(to, message.text)
            return True
        except Exception as e:
            logger.error("Failed to send %s message: %s", self.platform, e)
            return False

    async def receive_messages(self) -> AsyncIterator[InboundMessage]:
        while self._connected and not self._stop_event.is_set():
            try:
                msg = await asyncio.wait_for(
                    self._message_queue.get(), timeout=1.0
                )
                yield msg
            except asyncio.TimeoutError:
                continue

    async def wait_until_ready(self, timeout: float = 30.0) -> bool:
        if self._connected:
            return True
        if self.platform != "qq":
            return self._connected
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self._connected

    async def send_greeting_on_startup(self) -> bool:
        greeting_text = self.config.get("greeting_text", "")
        if not greeting_text or self._greeting_sent:
            return False

        from imchat.keystore import load_keys
        keys = load_keys(self.platform)

        contact_id = keys.get("user_openid") or keys.get("user_id")
        if not contact_id:
            logger.info(
                "No saved contact for '%s' — greeting will be sent on next startup "
                "after someone messages the bot",
                self.platform,
            )
            return False

        ok = await self.send_message(
            contact_id, OutboundMessage(text=greeting_text)
        )
        if ok:
            logger.info(
                "Greeting sent proactively to %s on '%s'", contact_id, self.platform
            )
            self._greeting_sent = True
            return True

        logger.warning(
            "Failed to send greeting to %s on '%s'", contact_id, self.platform
        )
        return False

    async def save_known_contact(self, sender_id: str):
        from imchat.keystore import load_keys, save_keys
        keys = load_keys(self.platform)
        if self.platform == "qq":
            if keys.get("user_openid") == sender_id:
                return
            save_keys(self.platform, {**keys, "user_openid": sender_id})
        else:
            if keys.get("user_id") == sender_id:
                return
            save_keys(self.platform, {**keys, "user_id": sender_id})

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        return {"id": user_id, "name": user_id, "channel": self.platform}

    async def rebind(self, on_qr_url=None) -> bool:
        await self.disconnect()
        from imchat.keystore import delete_keys
        delete_keys(self.platform)

        if self.platform == "wechat":
            from imchat.wechat import WeChatClient
            from imchat.wechat.auth import WeChatAuth

            client = WeChatClient()
            auth = WeChatAuth()
            try:
                start = await auth.start_login(client.account_id)
                qrcode_url = start["qrcode_url"]
                if on_qr_url:
                    on_qr_url(qrcode_url)

                result = await client.login_with_qr(verbose=True)
                if not result.connected:
                    logger.error("WeChat rebind failed")
                    await client.close()
                    return False

                self._client = client
                self._connected = True
                self._receive_task = asyncio.create_task(self._run_wechat_poller())
                logger.info("WeChat rebind success as %s", result.user_id)
                return True
            except Exception as exc:
                logger.error("WeChat rebind error: %s", exc)
                await client.close()
                return False

        return await self.connect()

    async def _dispatch_qq_message(self, msg_id, text, sender_id, sender_name, metadata):
        inbound = InboundMessage(
            id=msg_id,
            text=text,
            sender_id=sender_id,
            sender_name=sender_name,
            channel_id=self.channel_id,
            metadata=metadata,
        )
        await self._message_queue.put(inbound)
        if self._message_handler is not None:
            await self.handle_incoming(inbound)

    async def _connect_qq(self) -> bool:
        from imchat.qq import QQClient

        client = QQClient.from_saved_keys()
        if client is None:
            logger.error(
                "QQ credentials not configured. "
                "Run a setup script first or ensure ~/.imchat/qq.json exists."
            )
            return False

        @client.on_ready
        async def on_ready(data):
            logger.info("QQ bot online – session: %s", data.get("session_id"))
            self._connected = True
            self._ready_event.set()

        @client.on_c2c_message
        async def on_c2c(msg):
            await self._dispatch_qq_message(
                f"qq_c2c_{msg.id}",
                msg.content,
                msg.user_openid,
                msg.user_openid,
                {"platform": "qq", "type": "c2c"},
            )

        @client.on_group_message
        async def on_group(msg):
            await self._dispatch_qq_message(
                f"qq_group_{msg.id}",
                msg.content,
                msg.group_openid,
                msg.author_name or "",
                {"platform": "qq", "type": "group", "group_openid": msg.group_openid},
            )

        @client.on_error
        async def on_error(error):
            logger.error("QQ client error: %s", error)
            self._connected = False

        self._client = client
        self._receive_task = asyncio.create_task(self._run_qq_forever())
        return True

    async def _run_qq_forever(self):
        try:
            await self._client.start()
        except Exception as exc:
            logger.error("QQ client stopped: %s", exc)
        finally:
            self._connected = False

    async def _connect_wechat(self) -> bool:
        from imchat.wechat import WeChatClient

        client = WeChatClient.from_saved_keys()
        if client is None:
            logger.info("No saved WeChat keys – starting QR login ...")
            client = WeChatClient()
            try:
                result = await client.login_with_qr(verbose=True)
                if not result.connected:
                    logger.error("WeChat QR login failed")
                    return False
                logger.info("WeChat logged in as %s", result.user_id)
            except Exception as exc:
                logger.error("WeChat login error: %s", exc)
                return False

        self._client = client
        self._connected = True
        self._receive_task = asyncio.create_task(self._run_wechat_poller())
        return True

    async def _run_wechat_poller(self):
        try:
            async for ctx in self._client.poll_messages():
                inbound = InboundMessage(
                    id=f"wechat_{next(_wechat_msg_counter)}",
                    text=ctx.body,
                    sender_id=ctx.from_user_id,
                    sender_name=ctx.from_user_id,
                    channel_id=self.channel_id,
                    metadata={"platform": "wechat", "type": "c2c"},
                )
                await self._message_queue.put(inbound)
                if self._message_handler is not None:
                    await self.handle_incoming(inbound)
        except Exception as exc:
            logger.error("WeChat poller stopped: %s", exc)
        finally:
            self._connected = False
