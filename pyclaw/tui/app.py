from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import time
from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.theme import BUILTIN_THEMES
from textual.widgets import Input, Static
from chatchat.hooks.events import (AGENT_PROGRESS, AGENT_REASON_START,
                                   AGENT_STATE, AGENT_TEXT, AGENT_TOOL_CALL,
                                   AGENT_TOOL_RESULT, AGENT_TURN_FINISHED,
                                   AGENT_WARN, register_runtime_handler)
from pyclaw import __version__, banner, config, statusline, welcome
from pyclaw.agents import Session, append_conv
from pyclaw.spinner_verbs import PAST_TENSE_VERBS, SPINNER_VERBS
from pyclaw.slash import suggest as slash_suggest
from pyclaw.tools.coding import background, next_mode
from pyclaw.tools.coding.permission import PermissionChoice

from pyclaw.tui.agents_panel import AgentsScreen
from pyclaw.tui.agentview import agent_view_markup
from pyclaw.tui.approval import _Approval, _PermissionPrompt
from pyclaw.tui.formatting import (_content_text, _direct_message,
                                   _display_cwd, _fit, _format_count,
                                   _plural, _summarize, _token_rate, duration)
from pyclaw.tui.readout import (HUD_TICK_SECONDS, _agent_tokens, context_meter,
                                context_note, elapsed_row, git_label,
                                git_status, message_count, meter_cells,
                                mode_pill, thinking_label, usage_hud,
                                usage_meter)
from pyclaw.tui.roster import hide_row, leader_row, preview_rows, teammate_row
from pyclaw.tui.plan import (next_task_line, plan_lines,
                              recent_completions)
from pyclaw.tui.screens import (HelpScreen, HistorySearchScreen,
                                PermissionsScreen, RewindScreen,
                                TranscriptScreen)
from pyclaw.tui.suggest import (_apply_at, _at_token, _file_suggest,
                                _suggest_label)
from pyclaw.tui.theme import (AGENT_COLORS, AGENT_TEAMMATES_HINT, ASTERISK,
                              BULLET, IDLE_TEXT, INTERRUPTED_TEXT,
                              NON_MODAL_OVERLAYS, OVERLAY_GATED_ACTIONS,
                              DONE_TEXT, POINTER, RECENT_ACTIVITIES,
                              RESULT_GLYPH,
                              SPINNER_FRAMES, SPINNER_INTERVAL, STOPPED_TEXT,
                              TEAMMATE_VIEW_HINT)
from pyclaw.tui.toolcard import (_agent_progress_rows, _collapsible_kinds,
                                 _hidden_card, _last_assistant_key, _read_key,
                                 _tool_label, _tool_use_args, _tool_uses,
                                 agent_group_label, recent_rollup)
from pyclaw.tui.widgets import (_AgentGroupBlock, _AgentPane, _Conv,
                                _GroupBlock, _JumpToBottom,
                                _LogoBlock, _PagerScroll, _PromptInput,
                                _TextBlock, _ToolBlock, _UserBlock,
                                _half_page, teammate_name)


logger = logging.getLogger(__name__)

MAX_LOG_PAYLOAD = 1000


def _log_data(data) -> str:
    try:
        text = repr(data)
    except Exception:
        return "<unrepresentable>"
    return text if len(text) <= MAX_LOG_PAYLOAD else text[:MAX_LOG_PAYLOAD] + "\u2026"


def _agent_alive(agent) -> bool:
    flag = getattr(agent, 'is_running', None)
    if flag is None:
        return True
    return bool(flag)


class PyClawApp(App[None]):
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
                 resume_from=None):
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
        self._unreg = register_runtime_handler(self._on_event)
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
        await self._refresh_agents()

    async def on_unmount(self):
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

    def _teammates(self) -> list:
        team = self._team
        if team is None:
            return []
        lead = getattr(team, 'lead', None)
        found = []
        for agent in getattr(team, 'agents', {}).values():
            if agent is lead or getattr(agent, '_internal', False):
                continue
            if _agent_alive(agent):
                found.append(agent)
        found.sort(key=lambda a: str(getattr(a, 'name', '')))
        return found

    def _agent_by_name(self, name):
        for agent in getattr(self._team, 'agents', {}).values():
            if getattr(agent, 'name', '') == name:
                return agent
        return None

    def _completion_verb(self) -> str:
        return random.choice(PAST_TENSE_VERBS)

    def _state(self, name: str) -> dict:
        state = self._agent_state.get(name)
        if state is None:
            state = {'tools': 0, 'think': False, 'busy': False,
                     'last_tool': '', 'error': '', 'recent': [],
                     'verb': random.choice(SPINNER_VERBS),
                     'past': self._completion_verb(),
                     'started_at': time.monotonic(), 'idle_since': None}
            self._agent_state[name] = state
        return state

    def _agent_color(self, name: str) -> str:
        color = self._agent_colors.get(name)
        if color is None:
            index = len(self._agent_colors) % len(AGENT_COLORS)
            color = AGENT_COLORS[index]
            self._agent_colors[name] = color
        return color

    def _agent_running(self, agent) -> bool:
        if agent is None:
            return False
        return bool(getattr(agent, 'busy', False))

    def _plan(self) -> list:
        return [] if self._team.tasks is None else self._team.tasks.all()

    def _plan_activity(self) -> dict:
        running = {}
        for agent in self._teammates():
            if not self._agent_running(agent):
                continue
            state = self._state(str(getattr(agent, 'name', '')))
            text = recent_rollup(state['recent']) or state['last_tool']
            if text:
                running[str(agent.name)] = text
        return running

    def _active_forms(self) -> dict:
        return {task.owner: task.active_form for task in self._plan()
                if task.status == 'in_progress' and task.active_form}

    def _tree_markup(self, rows: bool, idle_line: bool) -> str:
        teammates = self._teammates()
        if not teammates:
            return ''
        selecting = self._view_selection == 'selecting-agent'
        selected = self._selected_index if selecting else None
        all_idle = all(not self._agent_running(a) for a in teammates)
        now = time.monotonic()
        columns = self.screen.size.width or self.size.width
        awaiting = {approval.prompt._agent for approval in self._approvals
                    if approval.prompt._agent}
        work = self._active_forms()
        lines = []
        if idle_line:
            suffix = '' if all_idle else f" \u00b7 {AGENT_TEAMMATES_HINT}"
            lines.append(f"[dim]{ASTERISK} {IDLE_TEXT}{suffix}[/]")
        if rows:
            lines.append(leader_row(
                selected=selected, foreground=self._viewing is None,
                busy=self._turn_verb if self._processing is not None else None,
                tokens=_agent_tokens(self._team.lead), columns=columns))
            for index, agent in enumerate(teammates):
                name = str(getattr(agent, 'name', ''))
                chosen = selected == index
                last = index == len(teammates) - 1 and not selecting
                lines.append(teammate_row(
                    agent, self._state(name), running=self._agent_running(agent),
                    color='#B1B9F9' if chosen else self._agent_color(name),
                    chosen=chosen, last=last, all_idle=all_idle, now=now,
                    columns=columns, foregrounded=self._viewing == name,
                    stopping=bool(self._state(name).get('stopping')),
                    awaiting=name in awaiting,
                    queued=len(agent.inbox.unread()),
                    work=work.get(name, '')))
                if self._preview:
                    lines += preview_rows(agent, last=last, cwd=self._cwd())
            if selecting:
                lines.append(hide_row(selected == len(teammates)))
        return '\n'.join(lines)

    async def _refresh_agents(self):
        known = {str(getattr(a, 'name', ''))
                 for a in getattr(self._team, 'agents', {}).values()}
        for name in list(self._agent_state):
            if name not in known:
                self._agent_state.pop(name, None)
        for name in list(self._spawns):
            if name not in known:
                self._spawns.pop(name, None)
        if self._viewing is not None:
            viewed = self._agent_by_name(self._viewing)
            if viewed is None or not _agent_alive(viewed):
                await self._exit_agent_view()
        teammates = self._teammates()
        rows = bool(teammates) and self._expanded_view == 'teammates'
        idle_line = bool(teammates) and self._processing is None
        text = self._tree_markup(rows, idle_line) or next_task_line(
            self._plan())
        if not text:
            if self._agents_pane is not None:
                self._agents_pane.remove()
                self._agents_pane = None
            return
        if self._agents_pane is None or self._agents_pane.parent is None:
            self._agents_pane = _AgentPane()
            await self.screen.mount(self._agents_pane,
                                    before=self.query_one("#prompt"))
        self._agents_pane.display = True
        self._agents_pane.update(text)

    def _step_selection(self, delta: int):
        teammates = self._teammates()
        if not teammates:
            return
        if self._expanded_view != 'teammates':
            self._selected_index = -1
        else:
            last = len(teammates)
            current = self._selected_index
            if delta == 1:
                self._selected_index = -1 if current >= last else current + 1
            else:
                self._selected_index = last if current <= -1 else current - 1
        self._view_selection = 'selecting-agent'
        self._expanded_view = 'teammates'

    async def action_agent_next(self):
        self._step_selection(1)

    async def action_agent_prev(self):
        self._step_selection(-1)

    async def action_agent_preview(self):
        self._preview = not self._preview
        await self._refresh_agents()

    async def _confirm_selection(self):
        teammates = self._teammates()
        index = self._selected_index
        if index == -1:
            await self._exit_agent_view()
        elif index >= len(teammates):
            self._expanded_view = 'none'
            self._view_selection = 'none'
            self._selected_index = -1
        else:
            await self._enter_agent_view(teammates[index])
        self._render_status()
        await self._refresh_agents()

    async def _enter_agent_view(self, agent):
        self._viewing = str(getattr(agent, 'name', ''))
        self._view_selection = 'viewing-agent'
        self.query_one("#conv").display = False
        view = self.query_one("#view", VerticalScroll)
        view.display = True
        await self._render_agent_view()
        view.scroll_end(animate=False)
        await self._render_queued()
        self._render_status()

    async def _exit_agent_view(self):
        if self._viewing is None:
            return
        self._viewing = None
        self._view_selection = 'none'
        self._selected_index = -1
        self.query_one("#view", VerticalScroll).display = False
        self.query_one("#conv").display = True
        self._follow_scroll()
        await self._render_queued()
        self._render_status()


    async def _render_agent_view(self):
        if self._viewing is None:
            return
        markup = agent_view_markup(self._agent_by_name(self._viewing),
                                   cwd=self._cwd(),
                                   color_for=self._agent_color)
        view = self.query_one("#view", VerticalScroll)
        if self._view_pane is None or self._view_pane.parent is None:
            self._view_pane = Static(markup, markup=True)
            await view.mount(self._view_pane)
        else:
            self._view_pane.update(markup)

    async def action_stop_agent(self):
        teammates = self._teammates()
        index = self._selected_index
        if index < 0 or index >= len(teammates):
            return
        agent = teammates[index]
        name = str(getattr(agent, 'name', ''))
        logger.info("stopping teammate %s by user request", name)
        if self._viewing == name:
            await self._exit_agent_view()
        self._state(name)['stopping'] = True
        await self._refresh_agents()
        stop = getattr(self._team, 'stop_agent', None)
        if stop is not None:
            await stop(agent)
        self._finish_agent(name, stopped=True)
        self._selected_index = -1
        await self._refresh_agents()

    async def _send_direct(self, agent, message: str):
        lead = getattr(self._team, 'lead', None)
        sender = str(getattr(lead, 'name', 'team-lead'))
        agent.inbox.write(sender, message)
        logger.info("direct message %s -> @%s", sender,
                    getattr(agent, 'name', ''))
        await self._append_block(
            f"[#B1B9F9]Sent to @{escape(str(agent.name))}[/]")

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

    _SPIN = "".join(SPINNER_FRAMES)

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


    def _spinner_text(self, char: str) -> str:
        viewed = self._viewing
        if viewed is not None:
            if self._agent_running(self._agent_by_name(viewed)):
                verb = self._state(viewed)['verb']
                return (f"[{self.brand}]{char}[/] {escape(verb)}\u2026 "
                        f"[dim](esc stops the turn [/]"
                        f"[{self._agent_color(viewed)}]@{escape(viewed)}[/]"
                        f"[dim])[/]")
            return self._idle_row(self._state(viewed))
        elif self._processing is None and self._teammates_running():
            return self._idle_row()
        parts = []
        running = self._teammates_running()
        elapsed = self._elapsed_seconds()
        parts.append(duration(elapsed))
        tokens = self._turn_tokens(running=running)
        if tokens:
            arrow = '' if running else '\u2193 '
            parts.append(f"{arrow}{_format_count(tokens)} tokens")
            if elapsed:
                parts.append(_token_rate(tokens, elapsed))
        if self._leader_thinking():
            parts.append('thinking')
        head = f"[{self.brand}]{char}[/] {self._turn_verb}\u2026"
        if not parts:
            return head
        return (f"{head} [dim]([/]"
                + "[dim] \u00b7 [/]".join(parts) + "[dim])[/]")

    def _idle_row(self, state: dict | None = None) -> str:
        if state is None:
            return (f"[dim]{ASTERISK} {IDLE_TEXT} \u00b7 "
                    f"{AGENT_TEAMMATES_HINT}[/]")
        teammates = self._teammates()
        if teammates and all(not self._agent_running(a) for a in teammates):
            seconds = max(0, int(time.monotonic() - state['started_at']))
            return (f"[dim]{ASTERISK} {state['past']} for "
                    f"{duration(seconds)}[/]")
        return f"[dim]{ASTERISK} {IDLE_TEXT}[/]"

    def _elapsed_seconds(self) -> int:
        if not self._turn_started_at:
            return 0
        return max(0, int(time.monotonic() - self._turn_started_at))

    def _turn_tokens(self, *, running: bool) -> int:
        tokens = max(0, _agent_tokens(self._team.lead) - self._turn_usage)
        if running and self._expanded_view != 'teammates':
            for agent in self._teammates():
                if self._agent_running(agent):
                    tokens += _agent_tokens(agent)
        return tokens

    def _leader_thinking(self) -> bool:
        return bool(self._state(str(self._team.lead.name)).get('think'))

    @staticmethod
    def _duration(seconds: int) -> str:
        if seconds < 60:
            return f'{seconds}s'
        return f'{seconds // 60}m {seconds % 60}s'

    async def _finish_work(self):
        self._discard_think()
        elapsed = self._elapsed_seconds()
        text = (f"[#9A9A9A]{ASTERISK} {self._turn_past} for "
                f"{duration(elapsed)}[/]")
        if self._work_block is None or self._work_block.parent is None:
            self._work_block = await self._append_block(text)
        else:
            self._work_block.update(text)
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

    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        self._history_index = None
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        if self._view_selection == 'selecting-agent':
            await self._confirm_selection()
            return
        if self._viewing is not None and text and not text.startswith('/'):
            agent = self._agent_by_name(self._viewing)
            if agent is not None:
                self.query_one("#input", Input).value = ""
                agent.submit(text)
                await self._render_agent_view()
                self._render_status()
                return
        direct = None if self._viewing is not None else _direct_message(text)
        if direct is not None:
            target = self._agent_by_name(direct[0])
            if target is not None:
                self.query_one("#input", Input).value = ""
                await self._send_direct(target, direct[1])
                return
        if text == '/permissions':
            self.query_one("#input", Input).value = ""
            await self._append_user(text)
            self.push_screen(PermissionsScreen(self._session))
            return
        if text == '/rewind':
            self.query_one("#input", Input).value = ""
            await self._append_user(text)
            if self._processing:
                await self._append_block(escape(
                    'PyClaw is still working. Press esc to stop it first, '
                    'then rewind.'))
                return
            if not self._session.turns():
                await self._append_block(escape(
                    'PyClaw has not recorded any turn to go back to.'))
                return
            self.push_screen(RewindScreen(self))
            return
        if text == '/agents':
            self.query_one("#input", Input).value = ""
            await self._append_user(text)
            self.push_screen(AgentsScreen(self._session))
            return
        if self._suggest_items:
            item = self._suggest_items[min(self._suggest_selected,
                                           len(self._suggest_items) - 1)]
            if self._typeahead == 'at':
                inp = self.query_one("#input", Input)
                inp.value = _apply_at(inp.value, item['name'],
                                      item.get('dir', False))
                inp.cursor_position = len(inp.value)
                return
            if item.get('hint'):
                inp = self.query_one("#input", Input)
                inp.value = f"/{item['name']} "
                inp.cursor_position = len(inp.value)
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
            await self._append_user(text)
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
        self._render_status()
        if value == '?':
            self.query_one("#input", Input).value = ""
            self.action_toggle_help()
            return
        items = []
        kind = None
        if value.startswith('/'):
            items = slash_suggest(value)
            kind = 'slash'
        else:
            token = _at_token(value[:event.input.cursor_position])
            if token is not None:
                items = _file_suggest(self._cwd(), token)
                kind = 'at'
        if not items or self._suggest_dismissed == value:
            self._suggest_items = []
            self._typeahead = None
            self._show_suggest_widget(False)
            return
        self._suggest_dismissed = None
        self._suggest_items = items
        self._typeahead = kind
        self._suggest_selected = 0
        self._show_suggest_widget(True)

    def _show_suggest_widget(self, show: bool):
        if show:
            self.register_overlay('autocomplete')
        else:
            self.unregister_overlay('autocomplete')
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
        start = max(0, min(self._suggest_selected - 2, len(items) - 6))
        window = items[start:start + 6]
        labels = [_suggest_label(i) for i in window]
        width = max((len(l) for l in labels), default=0)
        lines = []
        for i, item in enumerate(window):
            index = start + i
            desc = item.get('desc', '')
            row = escape(labels[i].ljust(width) + (f"  {desc}" if desc else ''))
            lines.append(f"[#B1B9F9]{row}[/]" if index == self._suggest_selected
                         else f"[dim]{row}[/]")
        widget.update('\n'.join(lines))

    def action_prompt_prev(self):
        if self._suggest_items:
            self.action_suggest_prev()
            return
        self._history_step(-1)

    def action_prompt_next(self):
        if self._suggest_items:
            self.action_suggest_next()
            return
        self._history_step(1)

    def _history_step(self, delta: int):
        if not self._history:
            return
        inp = self.query_one("#input", Input)
        if self._history_index is None:
            if delta > 0:
                return
            self._draft = inp.value
            self._history_index = len(self._history)
        index = min(max(0, self._history_index + delta), len(self._history))
        self._history_index = index
        inp.value = (self._draft if index == len(self._history)
                     else self._history[index])
        inp.cursor_position = len(inp.value)

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
        inp = self.query_one("#input", Input)
        if self._typeahead == 'at':
            inp.value = _apply_at(inp.value, item['name'],
                                  item.get('dir', False))
            inp.cursor_position = len(inp.value)
            return
        inp.value = f"/{item['name']} "
        inp.cursor_position = len(inp.value)

    def action_suggest_dismiss(self):
        self._suggest_dismissed = self.query_one("#input", Input).value
        self._suggest_items = []
        self._typeahead = None
        self._show_suggest_widget(False)

    async def _render_queued(self):
        inp = self.query_one("#input", Input)
        queued = [] if self._viewing is not None else self._peek_queue()
        inp.placeholder = ("up edits what you queued" if queued
                           else "Message PyClaw\u2026")
        if not queued:
            if self._queued is not None:
                self._queued.remove()
                self._queued = None
            return
        text = "\n\n".join(escape(str(t)) for t in queued)
        if self._queued is None:
            self._queued = Static(text, markup=True, classes="user")
            await self.screen.mount(self._queued,
                                    before=self.query_one("#prompt"))
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
                await self._refresh_agents()
                await self._append_user(text)
                self._begin_turn()
                await self._mount_spinner()
                await self._converse(text)
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

    def _begin_turn(self):
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
            await self._append_error(str(exc))
            return
        await self._wait_session_idle()
        await self._queue.join()
        if out.strip() and not self._wrote_body and not self._interrupted_call \
                and len(self._team.transcript()) > self._turn_start:
            await self._append_block(escape(out))
        self._render_status()

    def _note(self, name, tools=None, think=None, busy=None):
        st = self._state(name)
        if tools:
            st["tools"] += tools
        if think is not None:
            st["think"] = think
        if busy is not None:
            st["busy"] = busy

    def _render_status(self):
        s = self._session
        if s is None or not self.is_running:
            return
        self._schedule_statusline()
        viewing = self._viewing is not None
        viewing_busy = viewing and self._agent_running(
            self._agent_by_name(self._viewing))
        parts = []
        show_hint = not self._statusline_cmd and not self._prompt_has_text()
        if show_hint:
            if viewing and not viewing_busy:
                parts.append(TEAMMATE_VIEW_HINT)
            else:
                if self._processing is not None or viewing_busy:
                    parts.append("esc stops the turn")
                hint = self._tasks_hint()
                if hint:
                    parts.append(hint)
        if show_hint and not parts:
            parts.append("? lists the keys")
        self.query_one("#status", Static).update(" \u00b7 ".join(parts))
        self._paint_prompt()
        self._render_readouts()

    def _render_readouts(self):
        s = self._session
        if s is None or not self.is_running:
            return
        width = self.screen.size.width or self.size.width
        cells = meter_cells(width)
        background = (self._viewing is not None or self._teammates_running()
                      or self._subagents_running())
        self.query_one("#status-right", Static).update(context_note(s))
        self.query_one("#hud", Static).update(_fit((
            s.model, thinking_label(s),
            context_meter(self._triple, s.used_context, s.context_window,
                          cells),
            usage_meter(self._triple, s.usage, cells), usage_hud(s.usage),
            message_count(s), elapsed_row(self._hud_started)), width - 2))
        self.query_one("#hud2", Static).update(_fit((
            _display_cwd(s.cwd), self._git,
            mode_pill(s, background=background)), width - 2))

    async def _refresh_git(self):
        cwd = self._cwd()
        status = await asyncio.to_thread(git_status, cwd)
        if cwd == self._cwd():
            self._git = git_label(status)

    def _prompt_has_text(self) -> bool:
        return bool(self.query_one("#input", Input).value)

    def _statusline_state(self) -> tuple:
        s = self._session
        return (_last_assistant_key(s.transcript()), s.permission_mode,
                s.model, statusline.user_command())

    def _schedule_statusline(self):
        state = self._statusline_state()
        if state == self._statusline_seen:
            return
        self._statusline_seen = state
        if self._statusline_timer is not None:
            self._statusline_timer.stop()
        self._statusline_timer = self.set_timer(
            statusline.STATUS_LINE_DEBOUNCE_SECONDS,
            self._refresh_statusline)

    async def _refresh_statusline(self):
        self._statusline_timer = None
        self._statusline_cmd = statusline.user_command()
        if not self._statusline_cmd:
            self._statusline_text = ""
            self._paint_statusline()
            self._render_status()
            return
        if self._session is None:
            return
        previous = self._statusline_task
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(
            statusline.run(self._session, self._statusline_cmd))
        self._statusline_task = task
        try:
            text = await task
        except (asyncio.CancelledError, Exception):
            return
        if self._statusline_task is not task:
            return
        self._statusline_text = text
        self._paint_statusline()
        self._render_status()

    def _paint_statusline(self):
        if not self.is_running:
            return
        widget = self.query_one("#statusline", Static)
        widget.display = bool(self._statusline_text)
        widget.update(Text.from_ansi(self._statusline_text, style="dim",
                                     no_wrap=True))

    def _subagents_running(self) -> bool:
        return any(not st['done'] for st in self._subagents.values())

    def _paint_prompt(self):
        viewed = self._viewing
        accent = (self._agent_color(viewed) if viewed is not None
                  else banner.dimmed(self._triple, banner.RULE_LIGHTNESS))
        frame = self.query_one("#prompt", Horizontal)
        frame.styles.border_top = ("round", accent)
        frame.styles.border_bottom = ("round", accent)
        pointer = self.query_one("#prompt-pointer", Static)
        pointer.styles.color = accent if viewed is not None else self.brand
        pointer.styles.text_style = "dim" if self._processing else "none"

    def _tasks_hint(self) -> str:
        if not self._teammates():
            return ''
        if self._expanded_view == 'none':
            action = 'shows the task list'
        elif self._expanded_view == 'tasks':
            action = 'shows the teammate tree'
        else:
            action = 'hides them'
        return f"[dim]ctrl+t {action}[/]"

    def _render_tasks(self):
        if self._team is None:
            return
        lines = plan_lines(self._plan(), columns=self.screen.size.width
                           or self.size.width,
                           rows=self.screen.size.height or self.size.height,
                           brand=self.brand,
                           colors={str(getattr(a, 'name', '')):
                                   self._agent_color(str(getattr(a, 'name', '')))
                                   for a in self._teammates()},
                           activity=self._plan_activity(),
                           alive={str(getattr(a, 'name', ''))
                                  for a in self._teammates()},
                           recent=recent_completions(self._plan(),
                                                     self._task_seen,
                                                     time.monotonic()))
        if lines:
            lines += ['']
        lines += ["[bold]Agents[/bold]"]

        def walk(agent_id, prefix, is_last):
            a = self._team.agents.get(agent_id)
            if a is None:
                return
            st = self._agent_state.get(a.name, {})
            marker = "\u2514\u2500" if is_last else "\u251c\u2500"
            status = ("working\u2026" if st.get("think")
                      else ("busy" if st.get("busy") else "idle"))
            lines.append(f"{prefix}{marker} {escape(a.name)} \u00b7 "
                         f"{_tool_uses(st.get('tools', 0))} ({status})")
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
                tokens = (f" \u00b7 {_format_count(st['tokens'])} tokens"
                          if st['tokens'] is not None else "")
                status = ("Done" if st['done']
                          else (recent_rollup(st['recent'])
                                or st['last_tool'] or "Initializing\u2026"))
                stat_pre = "   " if is_last else "\u2502  "
                label = escape(str(st['type'])) or "Agent"
                lines.append(f"{tc} [bold]{label}[/] \u00b7 "
                             f"{_tool_uses(st['tools'])}{tokens}")
                lines.append(f"{stat_pre}{RESULT_GLYPH}  {escape(status)}")
        shells = background.snapshot()
        if shells:
            lines.append("")
            lines.append(f"[bold]Background shells[/bold] {len(shells)}")
            for row in shells:
                state = (f"exited {row['exit']}" if row['exit'] is not None
                         else f"running {duration(row['seconds'])}")
                lines.append(f"  {escape(str(row['id']))} \u00b7 "
                             f"{escape(_summarize(row['command'], 60))} "
                             f"\u00b7 {state}")
        lines.append("")
        lines.append(f"[bold]Tools[/bold] {len(self._session.available_tools)}")
        lines += [f"  {escape(str(t['name']))}"
                  for t in self._team.tool_schemas(self._team.tool_context)[:40]]
        self._tasks_pane.update("\n".join(lines))

    async def action_toggle_tasks(self):
        teammates = bool(self._teammates())
        view = self._expanded_view
        if teammates:
            order = {'none': 'tasks', 'tasks': 'teammates'}
            nxt = order.get(view, 'none')
        else:
            nxt = 'none' if view == 'tasks' else 'tasks'
        self._expanded_view = nxt
        if nxt != 'teammates':
            self._view_selection = 'none'
            self._selected_index = -1
        pane = self.query_one("#tasks", VerticalScroll)
        pane.display = nxt == 'tasks'
        if nxt == 'tasks':
            self._render_tasks()
        await self._refresh_agents()

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
