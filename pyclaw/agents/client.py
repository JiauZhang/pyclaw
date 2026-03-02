"""Client management for chatchat AI."""

import os
import logging
from typing import Optional
from chatchat import AI

logger = logging.getLogger(__name__)


class ClientManager:
    """Manage chatchat AI client."""

    def __init__(self, provider: str, model_name: Optional[str], instruction: str, api_key: Optional[str] = None):
        """Initialize client manager.

        Args:
            provider: Model provider (deepseek, openai, alibaba, etc.)
            model_name: Model name
            instruction: System prompt
            api_key: API key
        """
        self.provider = provider
        self.model_name = model_name
        self.instruction = instruction
        self.api_key = api_key
        self.ai = None
        self.client = None
        
        self._init_client()

    def _init_client(self):
        """Initialize the chatchat client."""
        try:
            if self.api_key:
                env_var = f"{self.provider.upper()}_API_KEY"
                os.environ[env_var] = self.api_key

            self.ai = AI(
                provider=self.provider,
                model=self.model_name,
                instruction=self.instruction
            )
            self.client = self.ai.client
            logger.info(f"Client initialized: {self.provider}/{self.model_name or 'default'}")

        except Exception as e:
            logger.error(f"Failed to initialize chatchat client: {e}")
            raise

    def clear(self):
        """Clear client state."""
        if hasattr(self.client, 'clear'):
            self.client.clear()

    def chat(self, text: str, stream: bool = False):
        """Send chat message."""
        return self.client.chat(text=text, stream=stream)
