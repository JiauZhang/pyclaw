from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import time

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.theme import BUILTIN_THEMES
from textual.widgets import Input, Static
from chatchat.hooks.events import (register_hook_event_handler,
                                    register_runtime_handler)
from pyclaw import __version__, banner, config, statusline, welcome
from pyclaw.session import Session
from pyclaw.spinner_verbs import SPINNER_VERBS

from pyclaw import events
from pyclaw.tui.permission_card import _Approval
from pyclaw.tui.actions import ActionMixin
from pyclaw.tui.approval_flow import ApprovalFlowMixin
from pyclaw.tui.formatting import _log_data
from pyclaw.tui.readout import HUD_TICK_SECONDS, _agent_tokens
from pyclaw.tui.agent_view import RosterMixin
from pyclaw.tui.delivery import DeliveryMixin
from pyclaw.tui.event_flow import EventRouterMixin
from pyclaw.tui.notify import NotifyMixin
from pyclaw.tui.scroll_follow import ScrollFollowMixin
from pyclaw.tui.tool_trace import ToolTraceMixin
from pyclaw.tui.status_line import StatusMixin
from pyclaw.tui.task_panel import TaskPanelMixin
from pyclaw.tui.prompting import PromptMixin
from pyclaw.tui.transcript import TranscriptMixin
from pyclaw.tui.turn_flow import TurnFlowMixin
from pyclaw.tui.screens import HelpScreen, HistorySearchScreen, TranscriptScreen
from pyclaw.tui import keys
from pyclaw.tui.theme import (FINISHED_LINGER_SECONDS, INTERRUPTED_TEXT,
                              OVERLAY_GATED_ACTIONS, POINTER, SPINNER_FRAMES,
                              SPINNER_INTERVAL)
from pyclaw.tui.components import (_AgentGroupBlock, _AgentPane, _Conv,
                                _GroupBlock, _LogoBlock, _PagerScroll,
                                _PromptInput, _ToolBlock)

logger = logging.getLogger(__name__)

class PyClawApp(ActionMixin, TurnFlowMixin, RosterMixin, ToolTraceMixin, StatusMixin, PromptMixin,
                TaskPanelMixin, EventRouterMixin, ApprovalFlowMixin,
                TranscriptMixin, ScrollFollowMixin, DeliveryMixin,
                NotifyMixin, App[None]):
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
    BINDINGS = keys.app_bindings()

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

    def focused_overlay(self) -> str | None:
        if len(self.screen_stack) > 1:
            return type(self.screen_stack[-1]).__name__
        for name in ('select', 'autocomplete'):
            if name in self._overlays:
                return name
        return None

    @property
    def modal_overlay_active(self) -> bool:
        focus = self.focused_overlay()
        return focus is not None and focus != 'autocomplete'

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




    async def _after_mount(self):
        if self._follow:
            self._follow_scroll()
        else:
            self._new_messages += 1
        await self._refresh_follow_hint()

    _SPIN = "".join(SPINNER_FRAMES)
















