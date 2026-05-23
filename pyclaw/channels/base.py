"""Base classes for channel adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, AsyncIterator, Callable
from datetime import datetime


@dataclass
class InboundMessage:
    """An incoming message from a channel."""
    id: str
    text: str
    sender_id: str
    sender_name: Optional[str] = None
    channel_id: str = ""
    thread_id: Optional[str] = None
    media_urls: list = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """An outgoing message to a channel."""
    text: str
    media_urls: Optional[list] = None
    reply_to: Optional[str] = None
    thread_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(ABC):
    """
    Abstract base class for channel adapters.
    
    Channel adapters connect PyClaw to messaging platforms
    like Telegram, WhatsApp, Discord, etc.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._connected = False
        self._message_handler: Optional[Callable] = None

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Return the unique channel identifier."""
        pass

    @property
    def connected(self) -> bool:
        """Check if the adapter is connected."""
        return self._connected

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the channel."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Disconnect from the channel."""
        pass

    @abstractmethod
    async def send_message(self, to: str, message: OutboundMessage) -> bool:
        """Send a message to a recipient."""
        pass

    @abstractmethod
    async def receive_messages(self) -> AsyncIterator[InboundMessage]:
        """Receive messages from the channel."""
        pass

    def set_message_handler(self, handler: Callable):
        """Set a handler for incoming messages."""
        self._message_handler = handler

    async def handle_incoming(self, message: InboundMessage):
        """Handle an incoming message."""
        if self._message_handler:
            await self._message_handler(message, self.channel_id)

    @abstractmethod
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """Get information about a user."""
        pass

    async def wait_until_ready(self, timeout: float = 30.0) -> bool:
        """Wait until the adapter is fully ready to send messages."""
        return self._connected

    async def health_check(self) -> Dict[str, Any]:
        """Perform a health check."""
        return {
            "channel_id": self.channel_id,
            "connected": self._connected,
            "healthy": self._connected
        }
