from __future__ import annotations

from pyclaw.tui.components import _JumpToBottom, _half_page


class ScrollFollowMixin:
    def _follow_scroll(self):
        if self._follow:
            self._conv().scroll_end(animate=False)

    def _set_follow(self, follow: bool):
        if follow == self._follow:
            return
        self._follow = follow
        if follow:
            self._new_messages = 0
        self.call_later(self._refresh_follow_hint)

    async def _refresh_follow_hint(self):
        if self._follow or not self._new_messages or self._viewing is not None:
            if self._hint is not None:
                self._hint.remove()
                self._hint = None
            return
        text = f"[#B1B9F9]\u2193 Scroll to latest \u00b7 {self._new_messages} new[/]"
        if self._hint is None:
            self._hint = _JumpToBottom(text)
            await self.screen.mount(self._hint,
                                    before=self.query_one("#prompt"))
        else:
            self._hint.update(text)

    async def action_jump_to_bottom(self):
        self._follow = True
        self._new_messages = 0
        self._conv().scroll_end(animate=False)
        await self._refresh_follow_hint()

    async def action_conv_page_up(self):
        conv = self._conv()
        conv.scroll_relative(y=-_half_page(conv), animate=False)

    async def action_conv_page_down(self):
        conv = self._conv()
        conv.scroll_relative(y=_half_page(conv), animate=False)

    async def action_conv_scroll_top(self):
        self._conv().scroll_home(animate=False)
