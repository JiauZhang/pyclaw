from __future__ import annotations

from pyclaw.tui.formatting import escape
from textual.widgets import Static

from pyclaw.tui.formatting import _content_text, _plural
from pyclaw.tui.theme import BULLET
from pyclaw.tui.components import _TextBlock, _UserBlock


class TranscriptMixin:
    async def _append_widget(self, widget):
        await self._conv().mount(widget)
        await self._after_mount()
        return widget

    async def _append_user(self, text: str):
        return await self._append_widget(
            _UserBlock(text, color_for=self._agent_color))

    async def _append_error(self, text: str):
        return await self._append_block(
            f"[#FF6B80]{BULLET}[/] [#FF6B80]{escape(str(text))}[/]")

    async def _append_block(self, text: str):
        block = Static(text, markup=True)
        conv = self._conv()
        await conv.mount(block)
        await self._after_mount()
        return block

    async def _frozen(self):
        self._live = None
        self._live_text = ""

    async def _start_live(self):
        self._live = _TextBlock()
        await self._conv().mount(self._live)
        self._live_text = ""
        await self._after_mount()

    def _update_live(self):
        if self._live is not None:
            self._live.set_body(self._live_text)
            self._follow_scroll()

    async def _replay_transcript(self):
        self._live = None
        self._live_text = ""
        self._tools = {}
        self._tool_meta = {}
        self._spawns = {}
        self._teammate_spawns = set()
        self._group = None
        self._agent_group = None
        self._solo_spawn = None
        self._work_block = None
        self._turn_start = 0
        self._conv().remove_children()
        await self._render_history()

    async def _append_note(self, text: str):
        await self._append_block(escape(text))

    async def _apply_rewind(self, mark: int, *, code: bool,
                            conversation: bool):
        result = self._session.rewind(mark, code=code,
                                      conversation=conversation)
        if conversation:
            await self._replay_transcript()
        moved = []
        if code:
            moved.append(f"{_plural(len(result['files']), 'file')} put back")
        if conversation:
            moved.append(f"{_plural(result['messages'], 'message')} dropped")
        await self._append_block(escape(
            f"Rewound to that turn{' · ' + ', '.join(moved) if moved else ''}"))

    async def _apply_resume(self, session_id: str, name: str = ''):
        count = self._session.resume_session(session_id)
        # The name belongs to the conversation that was read back, not to the
        # fresh id this one now writes under.
        title = name or session_id[:8]
        if not count:
            await self._append_note(
                f'That conversation has nothing recorded to come back to.')
            return
        await self._replay_transcript()
        await self._append_note(
            f'Continuing "{title}" · {_plural(count, "message")} read back')

    async def _render_history(self):
        for message in self._session.transcript():
            role = message.get("role")
            if role == "user":
                text = _content_text(message.get("content"))
                if text:
                    await self._append_user(text)
                continue
            if role != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) \
                            and block.get("type") == "tool_use":
                        await self._mount_tool(block.get("name", "tool"),
                                               block.get("input", ""),
                                               block.get("id", ""))
            text = _content_text(content)
            if text:
                text_block = _TextBlock()
                await self._conv().mount(text_block)
                text_block.set_body(text)
                await self._after_mount()

        self._reset_tool_group()
        self._sync_tool_states()
