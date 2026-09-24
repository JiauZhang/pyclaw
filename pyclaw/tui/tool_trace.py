from __future__ import annotations

import logging
import time

from textual.widgets import Static

from pyclaw.tui.formatting import _format_count, duration
from pyclaw.tui.readout import _agent_tokens
from pyclaw.tui.theme import DONE_TEXT, RECENT_ACTIVITIES, STOPPED_TEXT
from pyclaw.tui.toolcard import (_collapsible_kinds, _hidden_card, _read_key,
                                 _tool_label, _tool_use_args, _tool_uses,
                                 agent_group_label, recent_rollup)
from pyclaw.tui.widgets import (_AgentGroupBlock, _Conv, _GroupBlock,
                                _ToolBlock, teammate_name)

logger = logging.getLogger(__name__)


class ToolTraceMixin:
    def _note_tool(self, name: str, data: dict):
        state = self._state(name)
        tool = str(data.get('tool', 'tool'))
        args = _tool_use_args(tool, data.get('input', ''), self._cwd())
        state['last_tool'] = f"{_tool_label(tool, data.get('input'))}: {args}"
        recent = state['recent'] + [_collapsible_kinds(tool, data.get('input'))]
        state['recent'] = recent[-RECENT_ACTIVITIES:]
    def _spawn_block(self, name: str):
        uid = self._spawns.get(name)
        if not uid:
            return None
        block = self._tools.get(uid)
        return block if hasattr(block, 'add_progress') else None
    def _finish_agent(self, name: str, *, stopped: bool = False):
        state = self._state(name)
        state['busy'] = False
        state['think'] = False
        state['idle_since'] = None
        uid = self._spawns.get(name)
        if not uid:
            return
        block = self._tools.get(uid)
        if block is None or block._done:
            return
        if not stopped and uid in self._teammate_spawns:
            agent = self._agent_by_name(name)
            if agent is not None and getattr(agent, 'is_running', False):
                return
        meta = self._tool_meta.setdefault(uid, {})
        state['stopped'] = stopped
        if 'agent_summary' not in meta:
            started = state['started_at']
            elapsed = max(0, int(time.monotonic() - started))
            agent = self._agent_by_name(name)
            tokens = _agent_tokens(agent) if agent is not None else 0
            label = STOPPED_TEXT if stopped else 'Done'
            meta['agent_summary'] = (
                f"{label} ({_tool_uses(state['tools'])} \u00b7 "
                f"{_format_count(tokens)} tokens \u00b7 "
                f"{duration(elapsed)})")
            logger.info("sub-agent %s finished: %s", name,
                        meta['agent_summary'])
        if uid in self._teammate_spawns:
            block.set_result(meta['agent_summary'], meta=meta)
        else:
            block.end_progress()
    def _spin_char(self) -> str:
        return self._SPIN[self._spin_i % len(self._SPIN)]
    async def _add_tool(self, ev):
        await self._mount_tool(ev.data.get("tool", "tool"),
                               ev.data.get("input", ""),
                               ev.data.get("tool_use_id", ""))
    def _close_read_group(self):
        if self._group is not None:
            self._group.finish()
            self._group = None
    def _reset_tool_group(self):
        self._close_read_group()
        if self._agent_group is not None:
            self._agent_group.finish()
            self._agent_group = None
        self._solo_spawn = None
    def _spawn_stats(self, member: dict) -> dict:
        name = member['name'] or next(
            (agent for agent, uid in self._spawns.items() if uid == member['uid']),
            '')
        member['name'] = name
        state = self._agent_state.get(name, {})
        sub = self._subagents.get(name, {})
        agent = self._agent_by_name(name) if name else None
        tokens = sub.get('tokens')
        if tokens is None and agent is not None:
            tokens = _agent_tokens(agent)
        status = (recent_rollup(state.get('recent', []))
                  or state.get('last_tool')
                  or recent_rollup(sub.get('recent', []))
                  or sub.get('last_tool') or '')
        running = bool(getattr(agent, 'is_running', False))
        if running:
            member['seen'] = True
        return {'tools': state.get('tools') or sub.get('tools', 0),
                'tokens': tokens,
                'running': running,
                'status': status,
                'done_text': STOPPED_TEXT if state.get('stopped')
                else DONE_TEXT,
                'error': bool(state.get('error'))}
    async def _mount_spawn(self, uid: str, raw_input):
        self._close_read_group()
        label, detail = agent_group_label('create_agent', raw_input)
        teammate = teammate_name(raw_input)
        if teammate:
            self._teammate_spawns.add(uid)
            self._spawns[teammate] = uid
        if self._agent_group is None:
            if self._solo_spawn is None:
                self._solo_spawn = uid
                card = _ToolBlock('create_agent', raw_input, cwd=self._cwd())
                self._tools[uid] = card
                await self._conv().mount(card)
                self._discard_think()
                return await self._after_mount()
            await self._open_group(self._solo_spawn)
        self._agent_group.add(uid, label, detail, agent=teammate)
        self._tools[uid] = self._agent_group.member(uid)
        self._discard_think()
        await self._after_mount()
    async def _open_group(self, first_uid: str):
        card = self._tools.get(first_uid)
        self._solo_spawn = None
        group = _AgentGroupBlock(self._spawn_stats)
        group._frame = self._spin_char()
        if isinstance(card, _ToolBlock):
            label, detail = agent_group_label('create_agent', card._input)
            group.add(first_uid, label, detail,
                      agent=teammate_name(card._input))
            self._tools[first_uid] = group.member(first_uid)
            card.display = False
        await self._conv().mount(group, before=card)
        self._agent_group = group
    async def _mount_tool(self, name, raw_input, tool_use_id):
        uid = tool_use_id or name
        if name == 'create_agent':
            return await self._mount_spawn(uid, raw_input)
        block = _ToolBlock(name, raw_input, cwd=self._cwd())
        self._tools[uid] = block
        if _hidden_card(name, raw_input):
            self._reset_tool_group()
            block.display = False
            await self._conv().mount(block)
            return
        kinds = _collapsible_kinds(name, raw_input)
        if kinds:
            if self._group is None:
                self._group = _GroupBlock()
                self._group._frame = self._spin_char()
                await self._conv().mount(self._group)
            self._group.add(kinds, _read_key(name, raw_input), uid)
            self._discard_think()
            await self._after_mount()
            return
        self._reset_tool_group()
        await self._conv().mount(block)
        self._discard_think()
        await self._after_mount()
    async def _add_think(self, ev):
        await self._mount_spinner()
    async def _mount_spinner(self):
        widget = Static(self._spinner_text(self._spin_char()), markup=True)
        await self._conv().mount(widget)
        self._discard_think()
        self._think = {"widget": widget, "agent": ""}
        await self._after_mount()
    def _discard_think(self):
        if self._think is None:
            return
        widget = self._think["widget"]
        if widget in self._conv().children:
            widget.remove()
        self._think = None
    def _tool_spin_tick(self):
        if not self._tools and not self._think and self._agents_pane is None:
            return
        conv = next(iter(self.query(_Conv)), None)
        if conv is None:
            if self._spin_timer is not None:
                self._spin_timer.stop()
            return
        self._spin_i += 1
        char = self._spin_char()
        if self._think is not None:
            self._think["widget"].update(self._spinner_text(char))
        self._sync_tool_states()
        if self._group is not None and self._group.active:
            self._group.tick(char)
        if self._agent_group is not None and self._agent_group.active:
            self._agent_group.tick(char)
        for block in self.query(_ToolBlock):
            block.tick(char)
        if self._agents_pane is not None:
            teammates = self._teammates()
            rows = bool(teammates) and self._expanded_view == 'teammates'
            idle_line = bool(teammates) and self._processing is None
            self._agents_pane.update(self._tree_markup(rows, idle_line))
        if self._follow:
            conv.scroll_end(animate=False)
    def _sync_tool_states(self):
        results = {}
        for msg in self._team.transcript():
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[block.get("tool_use_id")] = block.get("content", "")
        for uid, block in self._tools.items():
            if uid in self._teammate_spawns:
                continue
            if not block._done and uid in results:
                block.set_result(str(results[uid]),
                                 meta=self._tool_meta.get(uid))
