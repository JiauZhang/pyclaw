from __future__ import annotations

import asyncio
import json

from rich.markup import escape

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static


from chatchat.hooks.events import (
    AGENT_PROGRESS,
    AGENT_REASON_START,
    AGENT_STATE,
    AGENT_TEXT,
    AGENT_TOOL_CALL,
    AGENT_TURN_FINISHED,
    AGENT_WARN,
    register_runtime_handler,
)

from pyclaw.agents import Session, append_conv
from pyclaw.slash import suggest as slash_suggest
from pyclaw.tools.coding import next_mode


def _summarize(value, limit: int = 60) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


class _Conv(VerticalScroll):

    def watch_scroll_y(self, value):
        try:
            self.app._set_follow(bool(self.is_vertical_scroll_end))
        except Exception:
            pass


class _JumpToBottom(Static):

    def __init__(self, text: str = "", **kw):
        super().__init__(text, markup=True, **kw)

    async def on_click(self):
        await self.app.action_jump_to_bottom()


THINKING_TTL = 30.0


class _TextBlock(Static):

    def __init__(self, **kw):
        super().__init__(**kw)
        self._body = ""

    def set_body(self, text: str):
        self._body = text
        # 模型输出是不可信文本：[] 会被当成 markup 解析炸掉渲染
        self.update(escape(text))


class _ThinkingBlock(Static):

    def __init__(self, **kw):
        super().__init__(**kw)
        self._thinking = ""
        self._expanded = False

    def set_thinking(self, text: str):
        self._thinking = text
        self._draw()

    def set_expanded(self, on: bool):
        self._expanded = on
        self._draw()

    def on_click(self):
        self._expanded = not self._expanded
        self._draw()

    def _draw(self):
        if not self._thinking:
            self.update("")
        elif self._expanded:
            self.update(f"[#9A9A9A]\u2234 Thinking\u2026[/]\n{escape(self._thinking)}")
        else:
            self.update("[#9A9A9A]\u2234 Thinking (ctrl+o to expand)[/]")


class _ToolBlock(Static):

    def __init__(self, name: str, input_text: str, **kw):
        super().__init__(**kw)
        self._name = name
        self._input = input_text
        self._output = None
        self._done = False
        self._expanded = False
        self._draw()

    def tick(self, char: str):
        if not self._done:
            self.update(f"[#9A9A9A]{char} {self._name} "
                        f"({escape(_summarize(self._input))})[/]")

    def set_result(self, output: str):
        self._done = True
        self._output = output
        self._draw()

    def on_click(self):
        self._expanded = not self._expanded
        self._draw()

    def _draw(self):
        if self._expanded:
            parts = [f"[#D77757]{self._name}[/]"]
            if self._input:
                parts.append(f"input: {escape(self._input)}")
            if self._output is not None:
                parts.append(f"output: {escape(self._output)}")
            self.update("\n".join(parts))
        elif self._done:
            self.update(f"[#4EBA65]\u2713[/#4EBA65] {self._name}: "
                        f"{escape(_summarize(self._output))}")
        else:
            self.update(f"[#9A9A9A]\u2026 {self._name} ({_summarize(self._input)})[/]")


class TranscriptScreen(Screen):
    """claude ctrl+o：独立 transcript 阅读屏。

    verbose 渲染全部消息（工具 input/output 全文），thinking 只显示最后
    一条 assistant 的（hidePastThinking）；q/esc/ctrl+o/ctrl+c 退出，
    退出不改动主屏状态（claude 亦不恢复滚动位置）。"""

    BINDINGS = [("escape", "exit_transcript", "Back"),
                ("q", "exit_transcript", "Back"),
                ("ctrl+o", "exit_transcript", "Back"),
                ("ctrl+c", "exit_transcript", "Back"),
                ("ctrl+e", "toggle_show_all", "Show all")]

    def __init__(self, owner, **kw):
        super().__init__(**kw)
        self._owner = owner
        self._show_all = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="transcript"):
            for entry in self._entries():
                yield Static(entry, markup=True)

    def on_mount(self):
        self.query_one("#transcript", VerticalScroll).focus()

    def _entries(self) -> list[str]:
        app = self._owner
        blocks = list(app._conv().children)
        entries: list[str] = []
        last_text_index = None
        for widget in blocks:
            if isinstance(widget, (_PermissionPrompt, _JumpToBottom)):
                continue
            if isinstance(widget, _TextBlock):
                entries.append(escape(widget._body or str(widget.content)))
                last_text_index = len(entries) - 1
            elif isinstance(widget, _ToolBlock):
                if self._show_all:
                    parts = [f"[#D77757]{widget._name}[/]"]
                    if widget._input:
                        parts.append(f"input: {escape(widget._input)}")
                    if widget._output is not None:
                        parts.append(f"output: {escape(widget._output)}")
                    entries.append("\n".join(parts))
                else:
                    summary = _summarize(widget._input or '')
                    entries.append(f"[#D77757]{widget._name}[/] "
                                   f"{escape(summary)}")
            elif isinstance(widget, _ThinkingBlock):
                continue          # thinking 统一取 transcript 最后一条
            else:
                entries.append(escape(str(widget.content)))
        thinking = app._turn_thinking()
        if thinking:
            entry = (f"[#9A9A9A]\u2234 Thinking\u2026[/]\n"
                     f"{escape(thinking)}")
            if last_text_index is None:
                entries.append(entry)
            else:
                entries.insert(last_text_index, entry)
        return entries

    def action_exit_transcript(self):
        self.app.pop_screen()

    async def action_toggle_show_all(self):
        self._show_all = not self._show_all
        scroll = self.query_one("#transcript", VerticalScroll)
        await scroll.remove_children()
        for entry in self._entries():
            await scroll.mount(Static(entry, markup=True))


class _PermissionPrompt(Static):

    can_focus = True
    BINDINGS = [
        ("y", "approve", "Approve"),
        ("n", "deny", "Deny"),
        ("a", "always", "Always allow in this session"),
    ]

    def __init__(self, tool_name: str, input_text: str,
                 rememberable: bool = True, rule: str = "", **kw):
        super().__init__(**kw)
        self._tool = tool_name
        self._input = input_text
        self._rememberable = rememberable
        self._rule = rule
        self.on_choice = None

    def on_mount(self):
        options = "[#D77757]y[/] approve  [#9A9A9A]n[/] deny"
        if self._rememberable:
            options += f"  [#4EBA65]a[/] don't ask again ({self._rule or self._tool})"
        else:
            options += "\n[#FFC107]cannot be remembered: unsafe command[/]"
        self.update(
            f"[#B1B9F9][bold]\u276f Permission needed: {self._tool}[/bold][/]\n"
            f"    {escape(self._input)}\n{options}"
        )
        self.focus()

    async def action_approve(self):
        await self._finish('approved')

    async def action_deny(self):
        await self._finish('denied')

    async def action_always(self):
        await self._finish('dont_ask' if self._rememberable else 'approved')

    async def _finish(self, decision: str):
        if self.on_choice is not None:
            self.on_choice(decision)
        self.remove()
        self.app.query_one("#input", Input).focus()


class PyClawApp(App[None]):
    TITLE = "PyClaw"
    CSS = """
    $background: #101010;
    $surface: #171717;
    $panel: #222222;
    $primary: #D77757;
    $secondary: #B1B9F9;
    $success: #4EBA65;
    $warning: #FFC107;
    $error: #FF6B80;
    $text: #FFFFFF;
    $text-muted: #9A9A9A;

    Screen { layout: vertical; background: $background; }
    #status { height: 1; background: #2b2b2b; color: #c9c9c9; padding: 0 1; }
    #body { height: 1fr; }
    #conv { width: 1fr; border: round $primary 40%; background: $surface; overflow-y: auto;
            scrollbar-gutter: stable; padding-right: 1; }
    /* 块填满容器宽，超长文本自动换行而非撑宽；右侧留 1 列空隙吸收 emoji
       二义宽度（wcwidth 判 1、终端画 2）的 1 列溢出，避免压到边框/滚动条。 */
    #conv > Static { width: 100%; }
    #transcript { width: 1fr; height: 1fr; background: $background; padding: 0 1; }
    #transcript > Static { width: 100%; margin-bottom: 1; }
    #suggest { display: none; height: auto; max-height: 7; background: $panel;
               margin: 0 1; padding: 0 1; }
    #tasks { width: 36; border: round $secondary 40%; background: $surface; overflow-y: auto; }
    #input { height: 3; background: $panel; border: round $primary; color: $text-muted; }
    #input:focus { border: round $primary; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit"),
                ("ctrl+o", "toggle_transcript", "Transcript"),
                ("ctrl+c", "interrupt", "Stop current work"),
                ("pageup", "conv_page_up", "Scroll up"),
                ("pagedown", "conv_page_down", "Scroll down"),
                ("ctrl+home", "conv_scroll_top", "Scroll to top"),
                ("ctrl+end", "jump_to_bottom", "Jump to bottom"),
                Binding("shift+tab", "cycle_permission", "Cycle permission mode",
                        priority=True),
                # slash 建议菜单打开时接管方向键/tab/esc（claude 的 typeahead）。
                Binding("down", "suggest_next", "Next suggestion", priority=True),
                Binding("up", "suggest_prev", "Previous suggestion", priority=True),
                Binding("tab", "suggest_tab", "Complete suggestion", priority=True),
                Binding("escape", "suggest_dismiss", "Dismiss suggestions",
                        priority=True)]

    def check_action(self, action: str, parameters) -> bool:
        if action in ('suggest_next', 'suggest_prev', 'suggest_tab',
                      'suggest_dismiss'):
            return bool(self._suggest_items)
        return True

    def __init__(self, *, builder, session_id=None, resume=False,
                 resume_from=None):
        super().__init__()
        self._builder = builder
        self._session_id = session_id
        self._resume = resume
        self._resume_from = resume_from
        self._team = None
        self._session: Session | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._pending_inputs: asyncio.Queue = asyncio.Queue()
        self._processing: str | None = None
        self._driving = False
        self._follow = True
        self._unreg = None
        self._queued: Static | None = None
        self._hint: _JumpToBottom | None = None
        self._new_messages = 0
        self._wrote_body = False
        self._live: _TextBlock | None = None
        self._turn_start = 0
        self._live_text = ""
        self._thought: _ThinkingBlock | None = None
        self._thought_timer = None
        self._tools: dict[str, _ToolBlock] = {}
        self._spin_timer = None
        self._spin_i = 0
        self._think: dict | None = None
        self._subagents: dict[str, dict] = {}
        self._agent_state: dict[str, dict] = {}
        self._suggest_items: list[dict] = []
        self._suggest_selected = 0
        self._suggest_dismissed: str | None = None
        self._group_tool = ''
        self._group_count = 0
        self._group_block: Static | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield _Conv(id="conv")
            yield VerticalScroll(id="tasks")
        yield Static('', id='suggest')
        yield Input(placeholder="Message PyClaw, or '/help'…  (ctrl+q to quit)", id="input")
        yield Static(id="status")

    async def on_mount(self):
        self._team = self._builder()
        self._session = Session(self._team, session_id=self._session_id,
                                resume_from=self._resume_from)
        self._session.attach_approval(self._ask_permission)
        self._unreg = register_runtime_handler(self._on_event)
        self._tasks_pane = Static("", markup=True)
        await self.query_one("#tasks", VerticalScroll).mount(self._tasks_pane)
        asyncio.create_task(self._pump())
        asyncio.create_task(self._drive())
        self._spin_timer = self.set_interval(0.08, self._tool_spin_tick)
        if self._resume:
            self._session.restore_transcript()
            await self._render_history()
        self.query_one(Input).focus()
        self._render_status()
        self._render_tasks()

    async def on_unmount(self):
        if self._spin_timer is not None:
            self._spin_timer.stop()
        if self._thought_timer is not None:
            self._thought_timer.stop()
            self._thought_timer = None
        if self._unreg is not None:
            self._unreg()
            self._unreg = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _conv(self) -> _Conv:
        return self.query_one("#conv", _Conv)

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
        inp = self.query_one("#input", Input)
        if self._follow or not self._new_messages:
            if self._hint is not None:
                self._hint.remove()
                self._hint = None
            return
        text = f"[#B1B9F9]\u2193 Jump to bottom \u00b7 {self._new_messages} new[/]"
        if self._hint is None:
            self._hint = _JumpToBottom(text)
            await self.screen.mount(self._hint, before=inp)
        else:
            self._hint.update(text)

    async def _after_mount(self):
        if self._follow:
            self._follow_scroll()
        else:
            self._new_messages += 1
        await self._refresh_follow_hint()

    async def action_jump_to_bottom(self):
        self._follow = True
        self._new_messages = 0
        self._conv().scroll_end(animate=False)
        await self._refresh_follow_hint()

    async def action_conv_page_up(self):
        self._conv().scroll_page_up(animate=False)

    async def action_conv_page_down(self):
        self._conv().scroll_page_down(animate=False)

    async def action_conv_scroll_top(self):
        self._conv().scroll_home(animate=False)

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
        self._live = _TextBlock(markup=True)
        await self._conv().mount(self._live)
        self._live_text = ""
        await self._after_mount()

    def _update_live(self):
        if self._live is not None:
            self._live.set_body(self._live_text)
            self._follow_scroll()

    async def _pump(self):
        while True:
            ev = await self._queue.get()
            try:
                await self._handle(ev)
                self._render_status()
                self._render_tasks()
            except Exception as exc:
                try:
                    await self._append_block(
                        f"[#FF6B80]\u26a0\ufe0f render error: {escape(str(exc))}[/]")
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    def _on_event(self, ev):
        if ev.agent and ev.agent not in self._member_names():
            if ev.kind != AGENT_PROGRESS:
                return
        self._queue.put_nowait(ev)

    def _member_names(self) -> set:
        return {a.name for a in self._team.agents.values()}

    async def _handle(self, ev):
        name = ev.agent or ""
        if ev.kind == AGENT_REASON_START:
            self._note(name, think=True)
            await self._add_think(ev)
        elif ev.kind == AGENT_TEXT:
            self._note(name, think=False, busy=True)
            delta = ev.data.get("delta", "")
            if not delta:
                return
            self._discard_think()
            if self._live is None:
                if not delta.strip():
                    return
                await self._start_live()
            self._live_text += delta
            self._update_live()
            self._wrote_body = True
            self._reset_tool_group()
        elif ev.kind == AGENT_TOOL_CALL:
            self._note(name, tools=1, think=False)
            self._discard_think()
            await self._frozen()
            await self._add_tool(ev)
        elif ev.kind == AGENT_PROGRESS:
            self._note_progress(ev)
        elif ev.kind == AGENT_WARN:
            await self._frozen()
            await self._append_block(f"[#FF6B80]\u26a0\ufe0f "
                                     f"{escape(ev.data.get('text', ''))}[/]")
        elif ev.kind == AGENT_TURN_FINISHED:
            self._note(name, think=False, busy=False)
            self._discard_think()
            self._reset_tool_group()
            if name == self._team.lead.name:
                await self._frozen()
                self._sync_tool_states()
                await self._show_turn_thinking()
                self._log_turn()
        elif ev.kind == AGENT_STATE:
            self._note(name, busy=bool(ev.data.get("busy", False)))

    _SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    @staticmethod
    def _fmt(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}m"
        if n >= 1000:
            s = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
            return f"{s}k"
        return str(n)

    async def _add_tool(self, ev):
        await self._mount_tool(ev.data.get("tool", "tool"),
                               ev.data.get("input", ""),
                               ev.data.get("tool_use_id", ""))

    _GROUP_TOOLS = frozenset({'Read', 'Glob', 'Grep', 'LS'})
    _GROUP_LIMIT = 3

    def _reset_tool_group(self):
        self._group_tool = ''
        self._group_count = 0
        self._group_block = None

    async def _mount_tool(self, name, raw_input, tool_use_id):
        input_text = raw_input if isinstance(raw_input, str) else str(raw_input)
        if name in self._GROUP_TOOLS and name == self._group_tool:
            self._group_count += 1
            if self._group_count > self._GROUP_LIMIT:
                extra = self._group_count - self._GROUP_LIMIT
                label = f"[#9A9A9A]\u22ef {extra} more {name} calls[/]"
                if self._group_block is None:
                    self._group_block = Static(label)
                    await self._conv().mount(self._group_block)
                else:
                    self._group_block.update(label)
                await self._after_mount()
                return
        else:
            self._reset_tool_group()
            if name in self._GROUP_TOOLS:
                self._group_tool = name
                self._group_count = 1
        uid = tool_use_id or name
        block = _ToolBlock(name, input_text)
        await self._conv().mount(block)
        self._tools[uid] = block
        await self._after_mount()

    async def _render_history(self):
        for message in self._session.transcript():
            role = message.get("role")
            if role == "user":
                text = _content_text(message.get("content"))
                if text:
                    await self._append_block(f"[#7AB4E8]You:[/#7AB4E8] {escape(text)}")
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
                text_block = _TextBlock(markup=True)
                await self._conv().mount(text_block)
                text_block.set_body(text)
                await self._after_mount()
            thinking = message.get("thinking")
            if thinking:
                await self._mount_thinking(thinking)
        self._sync_tool_states()

    async def _add_think(self, ev):
        self._discard_think()
        widget = Static("", markup=True)
        await self._conv().mount(widget)
        self._think = {"widget": widget, "agent": ev.agent or ""}

    def _discard_think(self):
        if self._think is None:
            return
        widget = self._think["widget"]
        if widget in self._conv().children:
            widget.remove()
        self._think = None

    def _turn_thinking(self) -> str:
        text = ""
        for m in self._team.transcript()[self._turn_start:]:
            if m.get('role') == 'assistant' and m.get('thinking'):
                text = m['thinking']
        return text

    async def _mount_thinking(self, text: str) -> _ThinkingBlock:
        block = _ThinkingBlock(markup=True)
        await self._conv().mount(block)
        block.set_thinking(text)
        return block

    async def _show_turn_thinking(self):
        text = self._turn_thinking()
        if not text:
            return
        if self._thought is None:
            self._thought = await self._mount_thinking(text)
            await self._after_mount()
        else:
            self._thought.set_thinking(text)
        if self._thought_timer is not None:
            self._thought_timer.stop()
        self._thought_timer = self.set_timer(THINKING_TTL, self._hide_thinking)

    def _hide_thinking(self):
        self._thought_timer = None
        block, self._thought = self._thought, None
        if block is not None and block.parent is not None:
            block.remove()

    def _log_turn(self):
        if self._session is None:
            return
        for m in reversed(self._team.transcript()[self._turn_start:]):
            if m.get('role') != 'assistant':
                continue
            text = _content_text(m.get('content'))
            if text:
                append_conv(self._session.conv_session_id, "assistant", text,
                            reasoning_content=m.get('thinking') or None)
            return

    def _note_progress(self, ev):
        name = ev.agent or ""
        data = ev.data
        st = self._subagents.get(name)
        if st is None:
            st = {'type': data.get('subagent_type') or 'Agent', 'tools': 0,
                  'tokens': None, 'last_tool': None, 'done': False,
                  'tool_names': {}}
            self._subagents[name] = st
        if data.get('done'):
            st['done'] = True
            return
        msg = data.get('message')
        if msg is None:
            return
        if msg.get('role') == 'assistant':
            usage = data.get('usage') or {}
            st['tokens'] = ((usage.get('prompt_tokens') or 0)
                            + (usage.get('completion_tokens') or 0))
            content = msg.get('content')
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        st['tool_names'][b.get('id', '')] = b.get('name', 'tool')
        elif msg.get('role') == 'user':
            content = msg.get('content')
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get('type') == 'tool_result':
                        st['tools'] += 1
                        uid = b.get('tool_use_id', '')
                        tname = st['tool_names'].get(uid, 'tool')
                        st['last_tool'] = (f"{tname}: "
                                           f"{_summarize(b.get('content', ''))}")

    def _tool_spin_tick(self):
        if not self._tools and not self._think:
            return
        conv = next(iter(self.query(_Conv)), None)
        if conv is None:
            if self._spin_timer is not None:
                self._spin_timer.stop()
            return
        self._spin_i += 1
        char = self._SPIN[self._spin_i % len(self._SPIN)]
        if self._think is not None:
            self._think["widget"].update(
                f"[dim]{char} {self._think['agent']}… thinking[/dim]")
        self._sync_tool_states()
        for block in self._tools.values():
            block.tick(char)
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
            if not block._done and uid in results:
                block.set_result(str(results[uid]))

    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        # claude：菜单打开时 Enter 补全选中项——带参命令停在输入框等参数，
        # 无参命令直接执行。
        if self._suggest_items and text.startswith('/'):
            item = self._suggest_items[min(self._suggest_selected,
                                           len(self._suggest_items) - 1)]
            if item.get('hint'):
                self.query_one("#input", Input).value = f"/{item['name']} "
                self._suggest_items = []
                self._show_suggest_widget(False)
                return
            text = f"/{item['name']}"
        self.query_one("#input", Input).value = ""
        if not text:
            return
        if text.startswith("/"):
            from pyclaw.slash import handle_slash

            reply = await handle_slash(text, self._session)
            await self._append_block(f"[#7AB4E8]You:[/#7AB4E8] {escape(text)}")
            follow = None
            if isinstance(reply, tuple):
                reply, follow = reply
            if reply:
                await self._append_block(escape(reply))
            self._render_status()
            await self._render_queued()
            if follow:
                self._pending_inputs.put_nowait(follow)
            return
        self._pending_inputs.put_nowait(text)
        self._render_status()
        await self._render_queued()

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        items = []
        if value.startswith('/'):
            items = slash_suggest(value)
        if not items or self._suggest_dismissed == value:
            self._suggest_items = []
            self._show_suggest_widget(False)
            return
        self._suggest_dismissed = None
        self._suggest_items = items
        self._suggest_selected = 0
        self._show_suggest_widget(True)

    def _show_suggest_widget(self, show: bool):
        try:
            self.query_one('#suggest', Static).display = show
        except Exception:
            pass
        if show:
            self._render_suggestions()

    def _render_suggestions(self):
        try:
            widget = self.query_one('#suggest', Static)
        except Exception:
            return
        items = self._suggest_items
        start = max(0, min(self._suggest_selected - 2, len(items) - 5))
        window = items[start:start + 5]
        lines = []
        for i, item in enumerate(window):
            index = start + i
            marker = '\u203a' if index == self._suggest_selected else ' '
            line = f"{marker} /{item['name']}  {item['desc']}"
            lines.append(f'[reverse]{line}[/]' if index == self._suggest_selected
                         else f'[dim]{line}[/]')
        widget.update('\n'.join(lines))

    def action_suggest_next(self):
        if self._suggest_items:
            self._suggest_selected = ((self._suggest_selected + 1)
                                      % len(self._suggest_items))
            self._render_suggestions()

    def action_suggest_prev(self):
        if self._suggest_items:
            self._suggest_selected = ((self._suggest_selected - 1)
                                      % len(self._suggest_items))
            self._render_suggestions()

    def action_suggest_tab(self):
        if not self._suggest_items:
            return
        item = self._suggest_items[self._suggest_selected]
        self.query_one("#input", Input).value = f"/{item['name']} "

    def action_suggest_dismiss(self):
        self._suggest_dismissed = self.query_one("#input", Input).value
        self._suggest_items = []
        self._show_suggest_widget(False)

    async def _render_queued(self):
        inp = self.query_one("#input", Input)
        inp.placeholder = (f"\u23f3 {self._processing}" if self._processing
                           else "Message PyClaw, or '/help'\u2026  (ctrl+q to quit)")
        items = [f"[#7AB4E8]You:[/#7AB4E8] {escape(t)}"
                 for t in self._peek_queue()]
        if not items:
            if self._queued is not None:
                self._queued.remove()
                self._queued = None
            return
        text = "\n".join(f"[bold]#{i + 1}[/] {it}" for i, it in enumerate(items))
        if self._queued is None:
            self._queued = Static(text, markup=True)
            await self.screen.mount(self._queued, before=inp)
        else:
            self._queued.update(text)

    def _peek_queue(self) -> list:
        return list(self._pending_inputs._queue)

    async def _drive(self):
        if self._driving:
            return
        self._driving = True
        try:
            while True:
                text = await self._pending_inputs.get()
                self._processing = text
                self._render_status()
                await self._render_queued()
                await self._append_block(f"[#7AB4E8]You:[/#7AB4E8] {escape(text)}")
                self._begin_turn()
                await self._converse(text)
                await self._settle_paint()
                self._processing = None
                self._render_status()
                await self._render_queued()
        finally:
            self._driving = False

    async def _ask_permission(self, tool_name: str, tool_input) -> str:
        inp = tool_input if isinstance(tool_input, dict) else {}
        summary = _summarize(json.dumps(inp, ensure_ascii=False), 120)
        rule = ""
        if tool_name == 'Bash':
            from pyclaw.tools.coding.shell_rules import suggested_rule
            rule = suggested_rule(str(inp.get('command') or '')) or ""
        # 与 PermissionController._rememberable 同口径：建议规则存在才可记住
        prompt = _PermissionPrompt(tool_name, summary,
                                   rememberable=bool(rule) or tool_name != 'Bash',
                                   rule=rule if tool_name == 'Bash' else tool_name)
        prompt.on_choice = fut.set_result
        conv = self._conv()
        await conv.mount(prompt)
        await self._after_mount()
        return await fut

    async def action_cycle_permission(self):
        if self._session is None:
            return
        nxt = next_mode(self._session.permission_mode,
                        bypass_available=self._session.bypass_available)
        self._session.set_permission_mode(nxt.value)
        self._render_status()

    async def action_interrupt(self):
        if self._team is not None:
            self._team.lead.abort_work()
        if self._processing and not self._wrote_body:
            self.query_one("#input", Input).value = self._processing
            self._processing = None
        # claude 语义：无 emoji 的纯文本，避免 emoji 宽度歧义把会话区/任务区
        # 边框顶偏（U+26A0 U+FE0F 终端画 2 列，wcwidth 判宽不一致）。
        await self._append_block("[#FFC107]Interrupted by user[/]")

    def _begin_turn(self):
        self._live = None
        self._live_text = ""
        self._wrote_body = False
        self._turn_start = len(self._team.transcript())

    async def _settle_paint(self):
        done = asyncio.Event()
        self.call_after_refresh(done.set)
        await done.wait()

    async def _wait_session_idle(self):
        while True:
            members = list(self._team.agents.values())
            if not any(getattr(a, 'busy', False) for a in members) \
                    and await self._team.lead.idle():
                return
            await asyncio.sleep(0.05)

    async def _converse(self, text: str):
        try:
            out = await self._session.chat(text)
        except Exception as exc:
            await self._append_block(f"[#FF6B80]\u26a0\ufe0f {escape(str(exc))}[/]")
            return
        await self._wait_session_idle()
        await self._queue.join()
        if out.strip() and not self._wrote_body \
                and len(self._team.transcript()) > self._turn_start:
            await self._append_block(escape(out))
        self._render_status()

    def _note(self, name, tools=None, think=None, busy=None):
        st = self._agent_state.setdefault(name, {"tools": 0, "think": False, "busy": False})
        if tools:
            st["tools"] += tools
        if think is not None:
            st["think"] = think
        if busy is not None:
            st["busy"] = busy

    def _render_status(self):
        s = self._session
        if s is None:
            return
        u = s.usage
        cached = (u.prompt_tokens_details or {}).get('cached_tokens', 0)
        think = "[#D77757]on[/]" if s.thinking else "off"
        perm = s.permission_mode
        perm_color = {"plan": "#B1B9F9", "acceptEdits": "#4EBA65",
                      "bypassPermissions": "#FF6B80"}.get(perm, "#9A9A9A")
        cache_display = (f"[#4EBA65]{self._fmt(cached)}[/] cached"
                         if cached else f"{self._fmt(cached)} cached")
        self.query_one("#status", Static).update(
            f"  [bold][#D77757]{s.mode}[/][/]  |  {s.provider}/{s.model}"
            f"  |  thinking {think}"
            f"  |  [{perm_color}]perm {perm}[/]"
            f"  |  [#D77757]{self._fmt(u.prompt_tokens)}[/] in · "
            f"[#D77757]{self._fmt(u.completion_tokens)}[/] out · "
            f"[#D77757]{self._fmt(u.total_tokens)}[/] total · {cache_display}"
        )

    def _render_tasks(self):
        if self._team is None:
            return
        lines = ["[bold]Agents[/bold]"]

        def walk(agent_id, prefix, is_last):
            a = self._team.agents.get(agent_id)
            if a is None:
                return
            st = self._agent_state.get(a.name, {})
            marker = "\u2514\u2500" if is_last else "\u251c\u2500"
            status = ("working\u2026" if st.get("think")
                      else ("busy" if st.get("busy") else "idle"))
            lines.append(f"{prefix}{marker} {a.name} \u00b7 {st.get('tools', 0)} tools [{status}]")
            kids = sorted(self._team.children.get(agent_id, ()))
            sub = prefix + ("   " if is_last else "\u2502  ")
            for i, k in enumerate(kids):
                walk(k, sub, i == len(kids) - 1)

        walk(self._team.lead.agent_id, "", True)
        if self._subagents:
            lines.append("")
            lines.append("[bold]Sub-agents[/bold]")
            subs = list(self._subagents.items())
            for i, (name, st) in enumerate(subs):
                is_last = i == len(subs) - 1
                tc = "\u2514\u2500" if is_last else "\u251c\u2500"
                uses = "use" if st['tools'] == 1 else "uses"
                tokens = (f" \u00b7 {self._fmt(st['tokens'])} tokens"
                          if st['tokens'] is not None else "")
                status = ("Done" if st['done']
                          else (st['last_tool'] or "Initializing\u2026"))
                stat_pre = "   " if is_last else "\u2502  "
                lines.append(f"{tc} [{st['type']}] \u00b7 {st['tools']} tool {uses}"
                             f"{tokens}")
                lines.append(f"{stat_pre}\u23bf  {status}")
        lines.append("")
        lines.append(f"[bold]Tools[/bold] {len(self._session.available_tools)}")
        lines += [f"  {t['name']}" for t in self._team.tool_schemas()[:40]]
        self._tasks_pane.update("\n".join(lines))

    def action_toggle_transcript(self):
        self.push_screen(TranscriptScreen(self))

    async def action_quit(self):
        if self._spin_timer is not None:
            self._spin_timer.stop()
        self.exit()
