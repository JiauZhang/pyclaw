from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import time
from datetime import datetime

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.theme import BUILTIN_THEMES
from textual.widgets import Input, Static
from chatchat.hooks.events import (AGENT_PROGRESS, AGENT_REASON_START,
                                    AGENT_STATE, AGENT_TEXT, AGENT_TOOL_CALL,
                                    AGENT_TOOL_RESULT, AGENT_TURN_FINISHED,
                                    AGENT_WARN, register_hook_event_handler,
                                    register_runtime_handler)
from pyclaw import __version__, banner, config, statusline, welcome
from pyclaw.agents import Session, append_conv
from pyclaw.spinner_verbs import SPINNER_VERBS
from pyclaw.tools.coding import next_mode
from pyclaw.tools.coding.permission import PermissionChoice

from pyclaw import events, notify
from pyclaw.tui.approval import _Approval, _PermissionPrompt, _QuestionPrompt
from pyclaw.tui.formatting import (_content_text, _log_data, _plural,
                                   _summarize, duration)
from pyclaw.tui.readout import HUD_TICK_SECONDS, _agent_tokens
from pyclaw.tui.agent_view import RosterMixin
from pyclaw.tui.tool_trace import ToolTraceMixin
from pyclaw.tui.status_line import StatusMixin
from pyclaw.tui.task_panel import TaskPanelMixin
from pyclaw.tui.prompting import PromptMixin
from pyclaw.tui.screens import HelpScreen, HistorySearchScreen, TranscriptScreen
from pyclaw.tui.theme import (ASTERISK, BULLET, FINISHED_LINGER_SECONDS,
                              INTERRUPTED_TEXT, NON_MODAL_OVERLAYS,
                              OVERLAY_GATED_ACTIONS, POINTER, RECENT_ACTIVITIES,
                              RESULT_PREFIX, SPINNER_FRAMES, SPINNER_INTERVAL)
from pyclaw.tui.toolcard import _agent_progress_rows, _collapsible_kinds
from pyclaw.tui.widgets import (_AgentGroupBlock, _AgentPane, _Conv,
                                _GroupBlock, _JumpToBottom, _LogoBlock,
                                _PagerScroll, _PromptInput, _TextBlock,
                                _ToolBlock, _UserBlock, _half_page)

logger = logging.getLogger(__name__)



class PyClawApp(RosterMixin, ToolTraceMixin, StatusMixin, PromptMixin,
                TaskPanelMixin, App[None]):
    TITLE = "PyClaw"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    $background: #101010;
    $text: #FFFFFF;
    $inactive: #999999;
    $subtle: #505050;
    $success: #4EBA65;
    $error: #FF6B80;
    $warning: #FFC107;
    $suggestion: #B1B9F9;
    $permission: #B1B9F9;
    $plan-mode: #48968C;
    $auto-accept: #AF87FF;
    $bash-border: #FD5DB1;
    $ide: #4782C8;
    $diff-added: #225C2B;
    $diff-removed: #7A2936;
    $selection: #264F78;

    Screen { layout: vertical; background: $background; }
    #conv { width: 1fr; height: 1fr; background: $background; overflow-y: auto;
            scrollbar-gutter: stable; padding: 0 1; }
    #conv > Static { width: 100%; margin-bottom: 1; }
    .user { background: $user-message; }
    .diff { border-top: dashed $subtle; border-bottom: dashed $subtle;
            border-left: none; border-right: none; padding: 0 1; }
    .permission { width: 100%; height: auto; margin-bottom: 1; }
    .permission Input { display: none; width: 100%; height: 1;
                        border: round $permission; background: $background;
                        color: $text; padding: 0 1; }
    .question { width: 100%; height: auto; margin-bottom: 1; }
    .question Input { display: none; width: 100%; height: 1;
                      border: round $permission; background: $background;
                      color: $text; padding: 0 1; }
    .logo { width: auto; margin-bottom: 1; }
    .text-block { width: 100%; height: auto; margin-bottom: 1; }
    .text-row { width: 100%; height: auto; }
    .text-bullet { width: 2; height: 1; color: $brand; }
    .text-body { width: 1fr; height: auto; color: $text; background: $background; }
    #transcript { width: 1fr; height: 1fr; background: $background; padding: 0 1; }
    #view { display: none; width: 1fr; height: 1fr; background: $background;
            overflow-y: auto; scrollbar-gutter: stable; padding: 0 1; }
    #view > Static { width: 100%; height: auto; }
    .agents { display: none; width: 100%; height: auto; background: $background;
              margin: 0 1; padding: 0 1; }
    #help { width: 1fr; height: 1fr; background: $background; padding: 0 1; }
    #help > Static { width: 100%; margin-bottom: 1; }
    #transcript > Static { width: 100%; margin-bottom: 1; }
    #history { width: 1fr; height: 1fr; background: $background;
               padding: 0 1; }
    #rewind { width: 1fr; height: auto; max-height: 20;
              background: $background; padding: 1 2; }
    #rewind > Static { width: 100%; height: auto; }
    #tasks-panel { width: 1fr; height: auto; max-height: 20;
                   background: $background; padding: 1 2; }
    #tasks-panel > Static { width: 100%; height: auto; }
    #hs-input { height: 1; margin-bottom: 1; border: none;
                background: $background; color: $text; }
    #hs-list { width: 100%; height: auto; margin-bottom: 1; }
    #suggest { display: none; height: auto; max-height: 8; background: $background;
               margin: 0 1; padding: 0 1; }
    #tasks { display: none; height: auto; max-height: 12; background: $background;
             border-top: round $permission; margin: 0 1; padding: 0 1; }
    #prompt { height: 3; border-top: round $prompt-border;
              border-bottom: round $prompt-border; }
    #prompt-pointer { width: 2; height: 1; color: $brand; }
    #input { height: 1; width: 1fr; border: none; padding: 0;
             background: $background; color: $text; }
    #footer { height: 1; }
    #statusline { height: auto; width: 1fr; color: $inactive; padding: 0 1;
                  display: none; }
    #hud { height: 1; width: 1fr; color: $subtle; padding: 0 1; }
    #hud2 { height: 1; width: 1fr; color: $subtle; padding: 0 1; }
    #status { height: 1; width: auto; background: $background;
              color: $inactive; padding: 0 1; }
    #status-right { height: 1; width: 1fr; text-align: right;
                    background: $background; color: $subtle; padding: 0 1; }
    """
    BINDINGS = [Binding("ctrl+d", "quit", "Exit", priority=True),
                Binding("ctrl+c", "interrupt", "Stop current work",
                        priority=True),
                Binding("escape", "escape", "Cancel / dismiss"),
                ("ctrl+q", "quit", "Exit (fallback)"),
                ("ctrl+t", "toggle_tasks", "Show/hide tasks"),
                ("ctrl+l", "redraw", "Redraw"),
                ("ctrl+o", "toggle_transcript", "Transcript"),
                Binding("ctrl+shift+o", "agent_preview",
                        "Preview teammate activity", priority=True),
                ("ctrl+r", "history_search", "Search history"),
                ("ctrl+s", "stash", "Stash prompt"),
                ("pageup", "conv_page_up", "Scroll up"),
                ("pagedown", "conv_page_down", "Scroll down"),
                Binding("ctrl+home", "conv_scroll_top", "Scroll to top"),
                Binding("ctrl+end", "jump_to_bottom", "Scroll to latest"),
                Binding("shift+up", "agent_prev", "Previous agent",
                        priority=True),
                Binding("shift+down", "agent_next", "Next agent",
                        priority=True),
                Binding("k", "stop_agent", "Stop selected agent",
                        priority=True),
                Binding("shift+tab", "cycle_permission", "Cycle permission mode",
                        priority=True),
                Binding("down", "prompt_next", "Next", priority=True),
                Binding("up", "prompt_prev", "Previous", priority=True),
                Binding("tab", "suggest_tab", "Complete suggestion",
                        priority=True)]

    def check_action(self, action: str, parameters) -> bool:
        if action in OVERLAY_GATED_ACTIONS and self.modal_overlay_active:
            return False
        if action in ('suggest_tab', 'suggest_dismiss'):
            return bool(self._suggest_items)
        if action in ('prompt_next', 'prompt_prev'):
            return len(self.screen_stack) <= 1
        if action == 'stop_agent':
            return (self._view_selection == 'selecting-agent'
                    and 0 <= self._selected_index < len(self._teammates()))
        if action in ('agent_next', 'agent_prev'):
            return bool(self._teammates())
        if action == 'quit':
            return not isinstance(self.focused, _PagerScroll)
        return True

    def register_overlay(self, name: str):
        self._overlays.add(name)

    def unregister_overlay(self, name: str):
        self._overlays.discard(name)

    @property
    def modal_overlay_active(self) -> bool:
        return bool(self._overlays - NON_MODAL_OVERLAYS)

    def __init__(self, *, builder, session_id=None, resume=False,
                 resume_from=None, hook_events=False):
        super().__init__()
        self._triple = banner.palette(config.load()["banner"])
        self.brand = banner.brand(self._triple)
        self.register_theme(dataclasses.replace(
            BUILTIN_THEMES["textual-dark"], name="pyclaw",
            variables={"brand": self.brand,
                       "user-message": banner.dimmed(self._triple,
                                                     banner.BAND_LIGHTNESS),
                       "prompt-border": banner.dimmed(self._triple,
                                                      banner.RULE_LIGHTNESS)}))
        self.theme = "pyclaw"
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
        self._turn_usage = 0
        self._work_block: Static | None = None
        self._turn_started_at = 0.0
        self._hud_started = time.monotonic()
        self._git = ''
        self._statusline_cmd = ""
        self._statusline_text = ""
        self._statusline_seen: tuple | None = None
        self._statusline_timer = None
        self._statusline_task = None
        self._turn_verb = SPINNER_VERBS[0]
        self._turn_past = self._completion_verb()
        self._tools: dict[str, _ToolBlock] = {}
        self._spin_timer = None
        self._spin_i = 0
        self._think: dict | None = None
        self._overlays: set[str] = set()
        self._approvals: list[_Approval] = []
        self._interrupted_call = False
        self._subagents: dict[str, dict] = {}
        self._agent_state: dict[str, dict] = {}
        self._known_teammates: dict[str, object] = {}
        self._lingering: dict[str, tuple] = {}
        setting = config.load().get('notifications') or {}
        self._notify_backend = str(setting.get('backend') or 'auto')
        self._notify_after = float(setting.get('idleSeconds', 60))
        self._last_interaction = time.monotonic()
        self._last_notified = 0.0
        self._linger_task: asyncio.Task | None = None
        self._cron_task: asyncio.Task | None = None
        self._cron_lock = None
        self._task_seen: dict[str, float] = {}
        self._expanded_view = 'none'
        self._view_selection = 'none'
        self._preview = False
        self._selected_index = -1
        self._viewing: str | None = None
        self._agents_pane: _AgentPane | None = None
        self._view_pane: Static | None = None
        self._spawns: dict[str, str] = {}
        self._teammate_spawns: set[str] = set()
        self._hook_events = hook_events
        self._unreg_hooks = None
        self._unreg_events = None
        self._events = None
        self._agent_colors: dict[str, str] = {}
        self._suggest_items: list[dict] = []
        self._suggest_selected = 0
        self._suggest_dismissed: str | None = None
        self._typeahead: str | None = None
        self._tool_meta: dict[str, dict] = {}
        self._stashed: str | None = None
        self._group: _GroupBlock | None = None
        self._agent_group: _AgentGroupBlock | None = None
        self._solo_spawn: str | None = None
        self._last_interrupt = 0.0
        self._history: list[str] = []
        self._history_index: int | None = None
        self._draft = ''

    def compose(self) -> ComposeResult:
        yield _Conv(id="conv")
        yield VerticalScroll(id="view")
        yield Static('', id='suggest')
        yield VerticalScroll(id="tasks")
        with Horizontal(id="prompt"):
            yield Static(POINTER, id="prompt-pointer")
            yield _PromptInput(placeholder="Message PyClaw\u2026", id="input")
        yield Static('', id='statusline')
        yield Static('', id='hud')
        yield Static('', id='hud2')
        with Horizontal(id="footer"):
            yield Static(id="status")
            yield Static(id="status-right")

    async def on_mount(self):
        self._team = self._builder()
        self._session = Session(self._team, session_id=self._session_id,
                                resume_from=self._resume_from)
        self._session.attach_approval(self._ask_permission)
        self._session.attach_question(self._ask_questions)
        self._start_cron()
        self._unreg = register_runtime_handler(self._on_event)
        self._events = events.open_stream(
            session=str(self._session.conv_session_id))
        self._unreg_events = register_runtime_handler(self._events)
        if self._hook_events:
            self._unreg_hooks = register_hook_event_handler(self._on_hook_event)
        self._tasks_pane = Static("", markup=True)
        await self.query_one("#tasks", VerticalScroll).mount(self._tasks_pane)
        asyncio.create_task(self._pump())
        asyncio.create_task(self._drive())
        self._spin_timer = self.set_interval(SPINNER_INTERVAL, self._tool_spin_tick)
        self.set_interval(HUD_TICK_SECONDS, self._render_readouts)
        greeting, shown_onboarding = self._greeting()
        logo = _LogoBlock(self._triple, greeting=greeting)
        await self._conv().mount(logo)
        welcome.remember(seen_onboarding=shown_onboarding,
                         version=str(__version__))
        if self._resume:
            self._session.restore_transcript()
            await self._render_history()
        self.query_one(Input).focus()
        self._statusline_cmd = statusline.user_command()
        await self._refresh_git()
        self._render_status()
        self._render_tasks()
        await self._note_missed_prompts()
        await self._refresh_agents()

    async def on_unmount(self):
        if self._cron_task is not None:
            self._cron_task.cancel()
            self._cron_task = None
        if self._cron_lock is not None:
            self._cron_lock.release()
            self._cron_lock = None
        if self._unreg_events is not None:
            self._unreg_events()
            self._unreg_events = None
        if self._events is not None:
            self._events.close()
            self._events = None
        if self._unreg_hooks is not None:
            self._unreg_hooks()
            self._unreg_hooks = None
        if self._spin_timer is not None:
            self._spin_timer.stop()
        if self._statusline_timer is not None:
            self._statusline_timer.stop()
        task = self._statusline_task
        if task is not None and not task.done():
            task.cancel()
        if self._unreg is not None:
            self._unreg()
            self._unreg = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _handle_exception(self, error: Exception) -> None:
        if self._events is not None:
            self._events.note_error(
                f'{type(error).__name__}: {error}')
        logger.error('the app stopped on an unhandled error',
                   exc_info=error)
        super()._handle_exception(error)

    def _conv(self) -> _Conv:
        return self.query_one("#conv", _Conv)

    def _cwd(self) -> str:
        if self._session is None:
            return "."
        value = str(getattr(self._session, "cwd", "") or "")
        if value and value != ".":
            return value
        context = getattr(self._team, "tool_context", None)
        return str(getattr(context, "cwd", "") or value or ".")

    def _greeting(self) -> tuple[str, bool]:
        session = self._session
        version = str(__version__)
        cwd = self._cwd()
        feeds, shown = welcome.feeds_for(
            cwd=cwd, version=version,
            session_id=getattr(session, 'conv_session_id', None))
        text = welcome.block(
            version=version,
            model=str(getattr(session, 'model', '') or ''),
            provider=str(getattr(session, 'provider', '') or ''),
            cwd=cwd, feeds=feeds, brand=self.brand)
        return text, shown

    def _note_lingering(self):
        alive = {str(getattr(agent, 'name', '')): agent
                 for agent in self._alive_teammates()}
        now = time.monotonic()
        for name, agent in self._known_teammates.items():
            if name not in alive and name not in self._lingering:
                self._lingering[name] = (agent, now + FINISHED_LINGER_SECONDS)
        self._known_teammates = alive
        for name in [name for name, (_, deadline) in self._lingering.items()
                     if now >= deadline]:
            self._lingering.pop(name, None)
            self._agent_state.pop(name, None)

    def _schedule_linger(self):
        if not self._lingering or self._linger_task is not None:
            return
        self._linger_task = asyncio.create_task(self._expire_lingering())

    async def _expire_lingering(self):
        try:
            while self._lingering:
                deadline = min(end for _, end in self._lingering.values())
                await asyncio.sleep(max(0.1, deadline - time.monotonic()))
                await self._refresh_agents()
        finally:
            self._linger_task = None

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
        conv = self._conv()
        conv.scroll_relative(y=-_half_page(conv), animate=False)

    async def action_conv_page_down(self):
        conv = self._conv()
        conv.scroll_relative(y=_half_page(conv), animate=False)

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
        self._live = _TextBlock()
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
                if self._viewing is not None:
                    await self._render_agent_view()
                await self._refresh_agents()
            except Exception as exc:
                logger.exception(
                    "render failed for %s agent=%r data=%s",
                    ev.kind, ev.agent, _log_data(ev.data))
                try:
                    await self._append_error(
                        f"render error: {exc}")
                except Exception:
                    logger.exception("failed to report a render error")
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
        logger.debug("event %s agent=%r data=%s", ev.kind, ev.agent,
                     _log_data(ev.data))
        name = ev.agent or ""
        lead = str(getattr(self._team.lead, 'name', ''))
        mine = not name or name == lead
        if ev.kind == AGENT_REASON_START:
            self._note(name, think=True)
            if mine:
                await self._add_think(ev)
        elif ev.kind == AGENT_TEXT:
            self._note(name, think=False, busy=True)
            delta = ev.data.get("delta", "")
            if not delta:
                return
            if not mine:
                return
            if self._live is None:
                if not delta.strip():
                    return
                await self._start_live()
                self._discard_think()
            self._live_text += delta
            self._update_live()
            self._wrote_body = True
            self._reset_tool_group()
        elif ev.kind == AGENT_TOOL_CALL:
            self._note(name, tools=1, think=False)
            if not mine:
                self._note_tool(name, ev.data)
                return
            await self._frozen()
            await self._add_tool(ev)
        elif ev.kind == AGENT_PROGRESS:
            self._note_progress(ev)
        elif ev.kind == AGENT_WARN:
            if not mine:
                self._state(name)['error'] = str(ev.data.get('text', ''))
                return
            await self._frozen()
            await self._append_error(ev.data.get('text', ''))
        elif ev.kind == AGENT_TOOL_RESULT:
            uid = ev.data.get('tool_use_id') or ev.data.get('tool', '')
            self._tool_meta[uid] = dict(ev.data)
        elif ev.kind == AGENT_TURN_FINISHED:
            self._note(name, think=False, busy=False)
            self._finish_agent(name)
            if not mine:
                return
            if not self._teammates_running():
                self._discard_think()
            self._reset_tool_group()
            await self._frozen()
            self._sync_tool_states()
            self._log_turn()
            asyncio.create_task(self._finish_work_when_settled())
        elif ev.kind == AGENT_STATE:
            self._note(name, busy=bool(ev.data.get("busy", False)))

    def _on_hook_event(self, event) -> None:
        if getattr(event, 'type', '') != 'response':
            return
        outcome = str(getattr(event, 'outcome', '') or 'done')
        line = (f"[dim]{RESULT_PREFIX}hook {escape(str(event.hook_event))}"
                f" \u00b7 {escape(str(event.hook_name))} \u00b7 "
                f"{escape(outcome)}[/]")
        asyncio.create_task(self._append_block(line))

    _SPIN = "".join(SPINNER_FRAMES)

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

    async def _finish_work(self):
        self._discard_think()
        elapsed = self._elapsed_seconds()
        text = (f"[#9A9A9A]{ASTERISK} {self._turn_past} for "
                f"{duration(elapsed)}[/]")
        if self._work_block is None or self._work_block.parent is None:
            self._work_block = await self._append_block(text)
        else:
            self._work_block.update(text)
        self._set_title(False)
        self._note_finished()
        await self._refresh_git()
        self._render_readouts()

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
        if data.get('tool_use_id'):
            self._spawns[name] = str(data['tool_use_id'])
            state = self._state(name)
            state['busy'] = True
            state['started_at'] = time.monotonic()
            state['idle_since'] = None
            logger.info("sub-agent %s started (tool_use_id=%s, type=%s)",
                        name, data['tool_use_id'],
                        data.get('subagent_type') or '')
        st = self._subagents.get(name)
        if st is None:
            st = {'type': data.get('subagent_type') or 'Agent', 'tools': 0,
                  'tokens': None, 'last_tool': None, 'done': False,
                  'recent': [], 'tool_names': {}}
            self._subagents[name] = st
        if data.get('done'):
            st['done'] = True
            self._finish_agent(name)
            return
        msg = data.get('message')
        if msg is None:
            return
        if msg.get('role') == 'assistant':
            usage = data.get('usage')
            if usage:
                st['tokens'] = int(usage.get('prompt_tokens') or 0) + \
                    int(usage.get('completion_tokens') or 0)
            content = msg.get('content')
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        tool = str(b.get('name', 'tool'))
                        st['tool_names'][b.get('id', '')] = tool
                        recent = st['recent'] + [_collapsible_kinds(
                            tool, b.get('input'))]
                        st['recent'] = recent[-RECENT_ACTIVITIES:]
            block = self._spawn_block(name)
            if block is not None:
                rows, uses = _agent_progress_rows(msg, self._cwd(),
                                                  block._width())
                if rows or uses:
                    block.add_progress(rows, uses)
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
                await self._refresh_agents()
                await self._append_user(text)
                self._begin_turn()
                await self._mount_spinner()
                await self._converse(text)
                self._session.record_turn()
                await self._settle_paint()
                self._processing = None
                self._render_status()
                await self._render_queued()
                await self._refresh_agents()
        finally:
            self._driving = False

    async def _ask_permission(self, tool_name: str, tool_input,
                              *, tool_use_id: str = '',
                              agent: str | None = None) -> PermissionChoice:
        inp = tool_input if isinstance(tool_input, dict) else {}
        rule = self._session.permission_rule(tool_name, inp)
        prompt = _PermissionPrompt(tool_name, inp, cwd=self._cwd(),
                                   rememberable=bool(rule) or tool_name != 'Bash',
                                   rule=rule, agent=self._badge(agent))
        block = self._tools.get(tool_use_id) if tool_use_id else None
        approval = _Approval(tool_use_id, tool_name, prompt, block,
                             asyncio.get_running_loop().create_future())
        prompt.on_choice = lambda choice: self._answer_approval(approval, choice)
        self._approvals.append(approval)
        if self._approvals[0] is approval:
            await self._mount_approval(approval)
        try:
            return await approval.future
        except asyncio.CancelledError:
            self._withdraw_approval(approval)
            raise

    def _missed_prompts(self, now=None) -> list:
        from chatchat.core.cron_schedule import find_missed

        store = getattr(self._team, 'cron', None)
        if store is None:
            return []
        return find_missed(store.durable(), now or datetime.now())

    async def _note_missed_prompts(self):
        missed = self._missed_prompts()
        if not missed:
            return
        await self._append_note(
            f'{len(missed)} scheduled prompt(s) came due while PyClaw was '
            'not running: '
            + ', '.join(str(task['prompt'])[:40] for task in missed))

    def _start_cron(self):
        from chatchat.core.cron_schedule import SchedulerLock

        from pyclaw.cron import run
        store = getattr(self._team, 'cron', None)
        if store is None:
            return
        self._cron_lock = SchedulerLock(store.directory,
                                        str(self._session.conv_session_id))
        self._cron_lock.acquire()
        self._cron_task = asyncio.create_task(
            run(store, self._cron_lock, self._deliver_cron))

    async def _deliver_cron(self, task: dict):
        prompt = str(task.get('prompt') or '')
        if not prompt:
            return
        agent = (self._team.get_by_name(task['agent'])
                 if task.get('agent') else None)
        if agent is not None:
            agent.submit(prompt)
            return
        await self._pending_inputs.put(prompt)

    async def _ask_questions(self, agent, questions) -> list[str]:
        name = getattr(agent, 'name', agent)
        prompt = _QuestionPrompt(list(questions), agent=self._badge(name))
        future = asyncio.get_running_loop().create_future()
        prompt.on_answer = lambda answers: (
            None if future.done() else future.set_result(answers))
        await self._conv().mount(prompt)
        await self._after_mount()
        try:
            return await future
        finally:
            prompt.remove()
            for field in self.query("#input"):
                field.focus()

    def _withdraw_approval(self, approval: _Approval):
        if approval in self._approvals:
            self._approvals.remove(approval)
        approval.prompt.remove()
        block = approval.block
        if isinstance(block, _ToolBlock):
            block.set_waiting_permission(False)
        if not approval.future.done():
            approval.future.cancel()

    def _badge(self, agent: str | None) -> str:
        if not agent:
            return ''
        return '' if agent == self._team.lead.name else agent

    async def _mount_approval(self, approval: _Approval):
        block = approval.block
        if isinstance(block, _ToolBlock):
            block.set_waiting_permission(True)
        await self._conv().mount(approval.prompt)
        await self._after_mount()

    def _answer_approval(self, approval: _Approval, choice: PermissionChoice):
        self._settle_approval(approval, choice)
        if self._approvals:
            asyncio.create_task(self._mount_approval(self._approvals[0]))

    def _settle_approval(self, approval: _Approval, choice: PermissionChoice):
        if approval in self._approvals:
            self._approvals.remove(approval)
        approval.prompt.remove()
        block = approval.block
        if isinstance(block, _ToolBlock):
            block.set_waiting_permission(False)
        if not approval.future.done():
            approval.future.set_result(choice)
        self.query_one("#input", Input).focus()

    async def _deny_pending_permission(self) -> bool:
        if not self._approvals:
            return False
        for approval in list(self._approvals):
            self._settle_approval(approval, PermissionChoice("denied"))
            block = approval.block
            if isinstance(block, _ToolBlock) and not block._done:
                block.reject()
        self._interrupted_call = True
        return True

    async def action_cycle_permission(self):
        if self._session is None:
            return
        nxt = next_mode(self._session.permission_mode,
                        bypass_available=self._session.bypass_available)
        self._session.set_permission_mode(nxt.value)
        self._render_status()

    async def action_escape(self):
        if self._suggest_items:
            self.action_suggest_dismiss()
            return
        if self._viewing is not None:
            agent = self._agent_by_name(self._viewing)
            if agent is not None and self._agent_running(agent):
                agent.abort_work()
                await self._render_agent_view()
                return
            await self._exit_agent_view()
            await self._refresh_agents()
            return
        if self._view_selection == 'selecting-agent':
            self._view_selection = 'none'
            self._selected_index = -1
            await self._refresh_agents()
            return
        if self._processing is None:
            return
        await self._interrupt()

    async def action_interrupt(self):
        now = asyncio.get_running_loop().time()
        if self._processing is None and now - self._last_interrupt < 2.0:
            self.exit()
            return
        self._last_interrupt = now
        await self._interrupt()

    async def _interrupt(self):
        rejected = await self._deny_pending_permission()
        if self._team is not None:
            self._team.lead.abort_work()
        if self._processing and not self._wrote_body:
            self.query_one("#input", Input).value = self._processing
            self._processing = None
        if not rejected:
            await self._append_block(
                f"[#9A9A9A]{INTERRUPTED_TEXT}[/]")

    def _terminal(self, sequence: str) -> None:
        driver = self._driver
        if driver is not None and not self.is_headless:
            driver.write(sequence)

    def _title(self, working: bool) -> str:
        brand = self.brand if isinstance(self.brand, str) else 'PyClaw'
        return f'{brand} \u00b7 working' if working else brand

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

    def _begin_turn(self):
        self._set_title(True)
        self._live = None
        self._live_text = ""
        self._turn_usage = _agent_tokens(self._team.lead)
        self._wrote_body = False
        self._interrupted_call = False
        self._turn_start = len(self._team.transcript())
        self._turn_verb = random.choice(SPINNER_VERBS)
        self._turn_past = self._completion_verb()
        self._turn_started_at = time.monotonic()

    async def _settle_paint(self):
        done = asyncio.Event()
        self.call_after_refresh(done.set)
        await done.wait()

    async def _wait_session_idle(self):
        lead = self._team.lead
        while True:
            if not getattr(lead, 'busy', False) and await lead.idle():
                return
            await asyncio.sleep(0.05)

    def _teammates_running(self) -> bool:
        return any(a is not self._team.lead and getattr(a, 'busy', False)
                   for a in self._team.agents.values())

    async def _finish_work_when_settled(self):
        while self._teammates_running():
            await asyncio.sleep(0.1)
        await self._finish_work()

    async def _converse(self, text: str):
        try:
            out = await self._session.chat(text)
        except Exception as exc:
            logger.exception("chat failed for prompt %r", _log_data(text))
            if self._events is not None:
                self._events.note_error(str(exc))
            await self._append_error(str(exc))
            return
        await self._wait_session_idle()
        await self._queue.join()
        if out.strip() and not self._wrote_body and not self._interrupted_call \
                and len(self._team.transcript()) > self._turn_start:
            await self._append_block(escape(out))
        self._render_status()

    def action_redraw(self):
        self.refresh()

    def action_toggle_transcript(self):
        self.push_screen(TranscriptScreen(self))

    def action_history_search(self):
        if self._history:
            self.push_screen(HistorySearchScreen(self))

    def action_stash(self):
        inp = self.query_one("#input", Input)
        text = inp.value
        if text.strip():
            self._stashed = text
            inp.value = ""
        elif self._stashed is not None:
            inp.value = self._stashed
            inp.cursor_position = len(self._stashed)
            self._stashed = None

    def action_toggle_help(self):
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
            return
        self.push_screen(HelpScreen())

    async def action_quit(self):
        if self._spin_timer is not None:
            self._spin_timer.stop()
        self.exit()
