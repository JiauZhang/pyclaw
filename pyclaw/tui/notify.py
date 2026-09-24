from __future__ import annotations

import time

from pyclaw import notify


class NotifyMixin:
    def _terminal(self, sequence: str) -> None:
        driver = self._driver
        if driver is not None and not self.is_headless:
            driver.write(sequence)

    def _title(self, working: bool) -> str:
        brand = self.brand if isinstance(self.brand, str) else 'PyClaw'
        return f'{brand} · working' if working else brand

    def _set_title(self, working: bool) -> None:
        notify.set_title(self._terminal, self._title(working))

    def _note_finished(self) -> None:
        idle = time.monotonic() - self._last_interaction
        if idle < self._notify_after:
            return
        body = notify.message(self._live_text, len(self._tools))
        self._last_notified = time.monotonic()
        notify.notify(self._terminal, title=self._title(False), body=body,
                      backend=self._notify_backend)

    async def on_key(self, event) -> None:
        self._last_interaction = time.monotonic()
