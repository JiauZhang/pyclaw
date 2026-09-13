from __future__ import annotations

import asyncio
import json

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
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
from pyclaw.tools.coding import next_mode
from pyclaw.tools.coding.shell_rules import is_dangerous_removal


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
        self.update(text)


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
            self.update(f"[#9A9A9A]\u2234 Thinking\u2026[/]\n{self._thinking}")
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
            self.update(f"[#9A9A9A]{char} {self._name} ({_summarize(self._input)})[/]")

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
                parts.append(f"input: {self._input}")
            if self._output is not None:
                parts.append(f"output: {self._output}")
            self.update("\n".join(parts))
        elif self._done:
            self.update(f"[#4EBA65]\u2713[/#4EBA65] {self._name}: {_summarize(self._output)}")
        else:
            self.update(f"[#9A9A9A]\u2026 {self._name} ({_summarize(self._input)})[/]")


class _PermissionPrompt(Static):

    can_focus = True
    BINDINGS = [
        ("y", "approve", "Approve"),
        ("n", "deny", "Deny"),
        ("a", "always", "Always allow in this session"),
    ]

    def __init__(self, tool_name: str, input_text: str,
                 rememberable: bool = True, **kw):
        super().__init__(**kw)
        self._tool = tool_name
        self._input = input_text
        self._rememberable = rememberable
        self.on_choice = None

    def on_mount(self):
        options = "[#D77757]y[/] approve  [#9A9A9A]n[/] deny"
        if self._rememberable:
            options += "  [#4EBA65]a[/] always allow"
        else:
            options += "\n[#FFC107]cannot be remembered: dangerous command[/]"
        self.update(
            f"[#B1B9F9][bold]\u276f Permission needed: {self._tool}[/bold][/]\n"
            f"    {self._input}\n{options}"
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
    #tasks { width: 36; border: round $secondary 40%; background: $surface; overflow-y: auto; }
    #input { height: 3; background: $panel; border: round $primary; color: $text-muted; }
    #input:focus { border: round $primary; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit"),
                ("ctrl+o", "toggle_expand", "Expand/Collapse all"),
                ("ctrl+c", "interrupt", "Stop current work"),
                ("pageup", "conv_page_up", "Scroll up"),
                ("pagedown", "conv_page_down", "Scroll down"),
                ("ctrl+home", "conv_scroll_top", "Scroll to top"),
                ("ctrl+end", "jump_to_bottom", "Jump to bottom"),
                Binding("shift+tab", "cycle_permission", "Cycle permission mode",
                        priority=True)]

    def __init__(self, *, builder):
        super().__init__()
        self._builder = builder
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
        self._all_expanded = False
        self._subagents: dict[str, dict] = {}
        self._agent_state: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield _Conv(id="conv")
            yield VerticalScroll(id="tasks")
        yield Input(placeholder="Message PyClaw, or '/help'…  (ctrl+q to quit)", id="input")
        yield Static(id="status")

    async def on_mount(self):
        self._team = self._builder()
        self._session = Session(self._team)
        self._session.attach_approval(self._ask_permission)
        self._unreg = register_runtime_handler(self._on_event)
        self._tasks_pane = Static("", markup=True)
        await self.query_one("#tasks", VerticalScroll).mount(self._tasks_pane)
        asyncio.create_task(self._pump())
        asyncio.create_task(self._drive())
        self._spin_timer = self.set_interval(0.08, self._tool_spin_tick)
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
                        f"[#FF6B80]\u26a0\ufe0f render error: {exc}[/]")
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
        elif ev.kind == AGENT_TOOL_CALL:
            self._note(name, tools=1, think=False)
            self._discard_think()
            await self._frozen()
            await self._add_tool(ev)
        elif ev.kind == AGENT_PROGRESS:
            self._note_progress(ev)
        elif ev.kind == AGENT_WARN:
            await self._frozen()
            await self._append_block(f"[#FF6B80]\u26a0\ufe0f {ev.data.get('text', '')}[/]")
        elif ev.kind == AGENT_TURN_FINISHED:
            self._note(name, think=False, busy=False)
            self._discard_think()
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
        name = ev.data.get("tool", "tool")
        inp = ev.data.get("input", "")
        input_text = inp if isinstance(inp, str) else str(inp)
        uid = ev.data.get("tool_use_id", "") or name
        block = _ToolBlock(name, input_text)
        conv = self._conv()
        await conv.mount(block)
        self._tools[uid] = block
        await self._after_mount()

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

    async def _show_turn_thinking(self):
        text = self._turn_thinking()
        if not text:
            return
        if self._thought is None:
            self._thought = _ThinkingBlock(markup=True)
            await self._conv().mount(self._thought)
            await self._after_mount()
        self._thought.set_thinking(text)
        if self._all_expanded:
            self._thought.set_expanded(True)
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
        self.query_one("#input", Input).value = ""
        if not text:
            return
        if text.startswith("/"):
            from pyclaw.slash import handle_slash

            reply = await handle_slash(text, self._session)
            await self._append_block(f"[#7AB4E8]You:[/#7AB4E8] {text}")
            if reply:
                await self._append_block(reply)
            self._render_status()
            await self._render_queued()
            return
        self._pending_inputs.put_nowait(text)
        self._render_status()
        await self._render_queued()

    async def _render_queued(self):
        inp = self.query_one("#input", Input)
        inp.placeholder = (f"\u23f3 {self._processing}" if self._processing
                           else "Message PyClaw, or '/help'\u2026  (ctrl+q to quit)")
        items = [f"[#7AB4E8]You:[/#7AB4E8] {t}" for t in self._peek_queue()]
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
                await self._append_block(f"[#7AB4E8]You:[/#7AB4E8] {text}")
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
        locked = tool_name == 'Bash' and is_dangerous_removal(
            str(inp.get('command') or ''))
        fut = asyncio.get_running_loop().create_future()
        prompt = _PermissionPrompt(tool_name, summary, rememberable=not locked)
        prompt.on_choice = fut.set_result
        conv = self._conv()
        await conv.mount(prompt)
        await self._after_mount()
        return await fut

    async def action_cycle_permission(self):
        if self._session is None:
            return
        nxt = next_mode(self._session.permission_mode)
        self._session.set_permission_mode(nxt.value)
        self._render_status()

    async def action_interrupt(self):
        if self._team is not None:
            self._team.lead.abort_work()
        if self._processing and not self._wrote_body:
            self.query_one("#input", Input).value = self._processing
            self._processing = None
        await self._append_block("[#FFC107]\u26a0\ufe0f interrupted[/]")

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
            await self._append_block(f"[#FF6B80]\u26a0\ufe0f {exc}[/]")
            return
        await self._wait_session_idle()
        await self._queue.join()
        if out.strip() and not self._wrote_body \
                and len(self._team.transcript()) > self._turn_start:
            await self._append_block(out)
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
        perm_color = {"plan": "#B1B9F9", "acceptEdits": "#4EBA65"
                      }.get(perm, "#9A9A9A")
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

    async def action_toggle_expand(self):
        self._all_expanded = not self._all_expanded
        for b in self._conv().query(_ThinkingBlock):
            b.set_expanded(self._all_expanded)
        for b in self._tools.values():
            b._expanded = self._all_expanded
            b._draw()

    async def action_quit(self):
        if self._spin_timer is not None:
            self._spin_timer.stop()
        self.exit()
