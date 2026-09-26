from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueuedPrompt:
    """A message waiting its turn at the prompt. `meta` marks text the terminal
    wrote rather than a person, so it can be kept out of the transcript, the
    queue preview and the input box."""

    text: str
    meta: bool = False

    @property
    def editable(self) -> bool:
        return not self.meta
