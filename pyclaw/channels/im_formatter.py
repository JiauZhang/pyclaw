import re
from typing import List

_SENTENCE_RE = re.compile(r"(?<=[。！？!?])")
_PARAGRAPH_RE = re.compile(r"(\n\n+)")


def split_long_message(text: str, limit: int) -> List[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    text = text or ""
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    for block in _PARAGRAPH_RE.split(text):
        if not block:
            continue
        if len(block) <= limit:
            chunks.append(block)
        else:
            chunks.extend(_split_block(block, limit))

    merged: List[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) + len(chunk) <= limit:
            merged[-1] += chunk
        else:
            merged.append(chunk)

    return merged or [text[:limit]]


def _split_block(block: str, limit: int) -> List[str]:
    out: List[str] = []
    for line in block.split("\n"):
        if len(line) <= limit:
            out.append(line)
            continue
        for sentence in _SENTENCE_RE.split(line):
            if not sentence:
                continue
            if len(sentence) <= limit:
                out.append(sentence)
            else:
                for i in range(0, len(sentence), limit):
                    out.append(sentence[i:i + limit])
    return out


class IMStatusTracker:
    def __init__(self, refresh_interval: float = 4.0):
        self.refresh_interval = refresh_interval
        self._last_text: str | None = None
        self._pending: List[str] = []
        self._last_sent: float | None = None

    def update(self, status_text: str, now: float) -> bool:
        if status_text == self._last_text:
            return False
        self._last_text = status_text
        self._pending.append(status_text)
        return True

    def drain(self, now: float) -> List[str]:
        if not self._pending:
            return []
        if self._last_sent is not None and now - self._last_sent < self.refresh_interval:
            return []
        latest = self._pending[-1]
        self._pending = []
        self._last_sent = now
        return [latest]
