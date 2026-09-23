import asyncio
import logging
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from textual.widgets import Input, Static

from chatchat.hooks.events import (
    AGENT_PROGRESS,
    AGENT_REASON_START,
    AGENT_TEXT,
    AGENT_TOOL_CALL,
    AGENT_TOOL_RESULT,
    AGENT_TURN_FINISHED,
    RuntimeEvent,
    emit,
)
from chatchat.tool import ToolContext
from pyclaw import agents, banner, config, statusline, welcome
from pyclaw.tools.coding import background
from pyclaw.tui import app as tui
from pyclaw.spinner_verbs import PAST_TENSE_VERBS, SPINNER_VERBS
from pyclaw.tui import PyClawApp
from pyclaw.tui.approval import _PermissionPrompt
from pyclaw.tui.diff import _diff_block
from pyclaw.tui.formatting import _token_rate
from pyclaw.tui.roster import (hide_row, leader_row, status_text,
                               teammate_row)
from pyclaw.tui.screens import HistorySearchScreen
from pyclaw.tui.widgets import (_AgentGroupBlock, _TextBlock,
                                _ToolBlock)
from chatchat.core.tasks import TaskList
from fakes import Usage
from markup import plain


class _NullHooks:

    async def execute_config_change_hooks(self, agent=None, source=''):
        return None

    async def execute_permission_denied_hooks(self, agent, tool_name,
                                             tool_input):
        return None

    async def execute_elicitation_hooks(self, agent, question=''):
        return None

    async def execute_elicitation_result_hooks(self, agent, text=''):
        return None


class _FakeTeam:
    provider = "p"
    model = "m"
    thinking = False
    name = "t"
    compact_threshold = 0
    auto_compact = False

    def last_usage(self):
        class _U:
            prompt_tokens = 0
            completion_tokens = 0
            prompt_tokens_details = None
        return _U()

    def __init__(self):
        self._running = False
        self.hooks = _NullHooks()
        owner = self

        class _A:
            name = "lead"
            agent_id = "lead@t"
            busy = False
            def abort_work(self):
                pass
            async def idle(self):
                return not owner._running
        self.agents = {"lead@t": _A()}
        self.tasks = None
        self.children = {}
        self.lead = self.agents["lead@t"]
        self._messages = []
        self.tool_context = ToolContext(cwd=Path.cwd())

    def provided_tools(self):
        return []

    def tool_schemas(self, context):
        return [{"name": "k", "description": "d", "input_schema": {}}]

    def set_model(self, model):
        self.model = model

    def usage(self):
        class _U:
            prompt_tokens = 1901
            completion_tokens = 500
            total_tokens = 2401
            prompt_tokens_details = {"cached_tokens": 1200}
        return _U()

    def transcript(self):
        return list(self._messages)

    def record(self, role, content, thinking=None):
        msg = {"role": role, "content": content}
        if thinking:
            msg["thinking"] = thinking
        self._messages.append(msg)
        return msg

    def restore(self, messages):
        self._messages = list(messages)

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        emit(AGENT_TOOL_CALL, agent="lead", tool="k",
             input={"x": 1}, tool_use_id="t1")
        emit(AGENT_TEXT, agent="lead", delta="hi ")
        emit(AGENT_TEXT, agent="lead", delta="there")
        self.record("user", [{"type": "tool_result", "tool_use_id": "t1",
                              "content": "工作完成"}])
        self.record("assistant", "hi there")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "\n\nhi there\n"


class _ManyReadsTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        for index in range(5):
            emit(AGENT_TOOL_CALL, agent="lead", tool="Read",
                 input={"file_path": f"f{index}.py"},
                 tool_use_id=f"t{index}")
        self.record("assistant", "done")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "done"


class _ThinkTeam(_FakeTeam):
    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        emit(AGENT_REASON_START, agent="lead")
        emit(AGENT_TEXT, agent="lead", delta="answer")
        self.record("assistant", "answer", thinking="inner monologue")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "answer"


class _SubTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        emit(AGENT_PROGRESS, agent="sub-1", prompt="调研", subagent_type="coder")
        emit(AGENT_PROGRESS, agent="sub-1",
             message={"role": "assistant",
                      "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                   "input": {"path": "a.py"}}]},
             usage={"prompt_tokens": 1800, "completion_tokens": 50})
        emit(AGENT_PROGRESS, agent="sub-1",
             message={"role": "user",
                      "content": [{"type": "tool_result", "tool_use_id": "t1",
                                   "content": "file content..."}]})
        emit(AGENT_PROGRESS, agent="sub-1",
             message={"role": "assistant", "content": "完成"},
             usage={"prompt_tokens": 1900, "completion_tokens": 100})
        emit(AGENT_PROGRESS, agent="sub-1", done=True)
        self.record("assistant", "完成")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "完成"


class _LongToolTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        emit(AGENT_TOOL_CALL, agent="lead", tool="k",
             input={"path": "x" * 80}, tool_use_id="t1")
        self.record("user", [{"type": "tool_result", "tool_use_id": "t1",
                              "content": "y" * 400}])
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "ok"


class _StaleTeam(_FakeTeam):

    def transcript(self):
        return [{"role": "assistant", "content": "旧回复"}]

    async def query(self, prompt, timeout=60):
        return "旧回复"


class _BusyTeam(_FakeTeam):

    def __init__(self):
        super().__init__()
        self.calls = []

    async def query(self, prompt, timeout=60):
        self.calls.append(prompt)
        self.record("user", prompt)
        await asyncio.sleep(0.05)
        emit(AGENT_TEXT, agent="lead", delta=f"reply:{prompt} ")
        self.record("assistant", f"reply:{prompt}")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return f"reply:{prompt}"


def _builder():
    return _FakeTeam()


class _GateTeam(_FakeTeam):

    def __init__(self):
        super().__init__()
        from pyclaw.tools.coding import PermissionController
        self._tmp = tempfile.TemporaryDirectory()
        from pyclaw.tools.coding import CODING_TOOLS
        self._pyclaw_gate = PermissionController(mode="default",
                                                 cwd=self._tmp.name,
                                                 tools=CODING_TOOLS)
        from chatchat.hooks.manager import HookManager
        self.hooks = HookManager(self)


def test_shift_tab_cycles_permission_mode():
    async def scenario():
        async with PyClawApp(builder=lambda: _GateTeam()).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            assert app._session.permission_mode == "default"
            await pilot.press("shift+tab")
            assert app._session.permission_mode == "acceptEdits"
            await pilot.press("shift+tab")
            assert app._session.permission_mode == "plan"
            row = str(app.query_one("#hud2").content)
            assert "plan mode on" in row
            assert "shift+tab to cycle" in row
    asyncio.run(scenario())


def _block_text(w) -> str:
    if isinstance(w, _TextBlock):
        return w._body
    if isinstance(w, Static):
        return str(w.content)
    return "".join(_block_text(c) for c in w.children)


def _flatten(app) -> str:
    return "".join(_block_text(w) for w in app.query_one("#conv").children)


def _plain(text) -> str:
    if not isinstance(text, str):
        text = _flatten(text)
    return plain(text)


def test_ui_launches_and_renders_panels():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            assert app.query_one("#conv") is not None
            assert app.query_one("#input", Input) is not None
            assert "? lists the keys" in str(app.query_one("#status").content)
            tasks = app.query_one("#tasks")
            assert tasks.display is False
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert tasks.display is True
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert tasks.display is False
    asyncio.run(scenario())


def test_submit_streams_single_block_and_no_duplicate():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hello"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            flat = _flatten(app)
            assert "hello" in flat
            assert flat.count("hi there") == 1
            blocks = [_block_text(w) for w in app.query_one("#conv").children]
            body_blocks = [b for b in blocks if "hi there" in b]
            assert len(body_blocks) == 1
    asyncio.run(scenario())


def test_slash_renders_block():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "/status"
            await pilot.press("enter")
            await pilot.pause()
            flat = _flatten(app)
            assert "Provider: p" in flat
    asyncio.run(scenario())


def test_consecutive_reads_collapse_into_one_line():
    async def scenario():
        async with PyClawApp(
                builder=lambda: _ManyReadsTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "go"
            await pilot.press("enter")
            for _ in range(8):
                await pilot.pause()
            return _flatten(app)

    flat = asyncio.run(scenario())
    assert "Opened [bold]5[/] files" in flat
    assert "(ctrl+o for the list)" in flat


class _ReadBodyTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        emit(AGENT_TOOL_CALL, agent="lead", tool="Read",
             input={"file_path": "a.txt"}, tool_use_id="t1")
        self.record("user", [{"type": "tool_result", "tool_use_id": "t1",
                              "content": "a.txt:\n1\talpha\n2\tbeta"}])
        self.record("assistant", "read it")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "read it"


def test_transcript_expands_every_tool_call():
    async def scenario():
        async with PyClawApp(
                builder=lambda: _ReadBodyTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "go"
            await pilot.press("enter")
            for _ in range(8):
                await pilot.pause()
            assert "alpha" not in _flatten(app)
            await pilot.press("ctrl+o")
            await pilot.pause()
            expanded = _transcript_text(app)
            assert "Read(a.txt)" in _plain(expanded)
            assert "alpha" in expanded
            assert "\\[#9A9A9A]" not in expanded
            assert "Handled for" in expanded
    asyncio.run(scenario())


def test_permissions_screen_lists_rules_and_closes():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "/permissions"
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "PermissionsScreen"
            body = str(app.screen.query_one("#permissions-body").content)
            assert "Permission mode" in body
            assert "No permission rules." in body

            await pilot.press("d")
            await pilot.pause()
            assert type(app.screen).__name__ == "PermissionsScreen"

            await pilot.press("escape")
            await pilot.pause()
            assert type(app.screen).__name__ != "PermissionsScreen"
    asyncio.run(scenario())


def test_tool_card_resolves_to_done_from_transcript():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "go"
            await pilot.press("enter")
            for _ in range(8):
                await pilot.pause()
            flat = _flatten(app)
            assert "k" in flat
            assert "工作完成" in flat
    asyncio.run(scenario())


def test_thinking_is_hidden_and_the_turn_ends_with_worked_for():
    async def scenario():
        async with PyClawApp(builder=lambda: _ThinkTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            for _ in range(5):
                await pilot.pause()
            conv = app.query_one("#conv")
            blocks = [_block_text(w) for w in conv.children]
            assert "\u273b Handled for" in blocks[-1]
            assert "inner monologue" not in "".join(blocks)
            assert "answer" in "".join(blocks)
    asyncio.run(scenario())


def _working_spinner(app, *, seconds: int = 0, tokens: int = 0):
    app._processing = "hi"
    app._turn_verb = "Thinking"
    app._turn_started_at = time.monotonic() - seconds
    app._team.lead.total_usage = SimpleNamespace(total_tokens=tokens)


def _rendered_spinner(builder, prepare) -> str:
    async def scenario():
        async with PyClawApp(builder=builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            prepare(app)
            return app._spinner_text("\u273b")

    return asyncio.run(scenario())


def test_the_turn_timer_and_rate_show_from_the_first_second():
    """The clock and the rate are why a turn is worth watching, so PyClaw
    reads them from the first second rather than after a warm-up gate."""
    text = _rendered_spinner(_builder, lambda app: _working_spinner(
        app, seconds=4, tokens=1000))
    assert _plain(text) == "\u273b Thinking\u2026 (4s \u00b7 \u2193 1k tokens \u00b7 250 tok/s)"


def test_a_slow_turn_reads_its_timer_tokens_and_rate():
    text = _rendered_spinner(_builder, lambda app: _working_spinner(
        app, seconds=31, tokens=1200))
    assert _plain(text) == \
        "\u273b Thinking\u2026 (31s \u00b7 \u2193 1.2k tokens \u00b7 39 tok/s)"


def test_a_fractional_rate_keeps_one_decimal():
    assert _token_rate(13, 31) == "0.4 tok/s"
    assert _token_rate(1200, 31) == "39 tok/s"


def test_the_token_counter_reads_what_the_api_reported():
    text = _rendered_spinner(_builder, lambda app: _working_spinner(
        app, seconds=31, tokens=1000))
    assert _plain(text) == \
        "\u273b Thinking\u2026 (31s \u00b7 \u2193 1k tokens \u00b7 32 tok/s)"


def test_spinner_row_drops_the_arrow_while_teammates_run():
    def prepare(app):
        _working_spinner(app, seconds=3, tokens=400)
        app._team.worker_busy = True
        app._team.worker.total_usage = SimpleNamespace(total_tokens=2000)

    team = _SwarmTeam()
    text = _rendered_spinner(lambda: team, prepare)
    assert _plain(text) == "\u273b Thinking\u2026 (3s \u00b7 2.4k tokens \u00b7 800 tok/s)"


def test_spinner_row_leaves_the_teammate_counts_to_the_tree():
    def prepare(app):
        _working_spinner(app, seconds=3, tokens=400)
        app._team.worker_busy = True
        app._team.worker.total_usage = SimpleNamespace(total_tokens=2000)
        app._expanded_view = 'teammates'

    team = _SwarmTeam()
    text = _rendered_spinner(lambda: team, prepare)
    assert _plain(text) == "\u273b Thinking\u2026 (3s \u00b7 400 tokens \u00b7 133 tok/s)"


def test_spinner_row_names_the_teammate_being_viewed():
    seen = {}

    def prepare(app):
        _working_spinner(app, seconds=3, tokens=400)
        app._team.worker_busy = True
        app._viewing = "worker"
        app._state("worker")["verb"] = "Reviewing"
        seen["color"] = app._agent_color("worker")

    team = _SwarmTeam()
    text = _rendered_spinner(lambda: team, prepare)
    assert _plain(text) == "\u273b Reviewing\u2026 (esc stops the turn @worker)"
    assert f"[{seen['color']}]@worker[/]" in text


def test_spinner_row_goes_static_while_teammates_carry_on():
    def prepare(app):
        app._turn_verb = "Thinking"
        app._team.worker_busy = True

    team = _SwarmTeam()
    text = _rendered_spinner(lambda: team, prepare)
    assert _plain(text) == "\u273b Idle \u00b7 subagents are active"


def test_a_stopped_teammate_view_reads_how_long_it_worked():
    def prepare(app):
        app._turn_verb = "Thinking"
        app._viewing = "worker"
        app._state("worker")["started_at"] = time.monotonic() - 42

    team = _SwarmTeam()
    text = _rendered_spinner(lambda: team, prepare)
    assert _plain(text) == "\u273b Handled for 42s"


def test_a_stopped_teammate_view_only_says_idle_while_others_run():
    def prepare(app):
        app._turn_verb = "Thinking"
        app._viewing = "worker"
        app._team.agents["other@t"] = SimpleNamespace(
            name="other", agent_id="other@t", is_running=True, busy=True)

    team = _SwarmTeam()
    text = _rendered_spinner(lambda: team, prepare)
    assert _plain(text) == "\u273b Idle"


def test_a_busy_teammate_row_reads_present_and_an_idle_one_past():

    team = _SwarmTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            state = app._state("worker")
            state["last_tool"] = ""
            now = time.monotonic()
            busy = status_text(state, running=True, all_idle=False,
                               highlighted=False, now=now)
            idle = status_text(state, running=False, all_idle=True,
                               highlighted=False, now=now)
            return state, busy, idle

    state, busy, idle = asyncio.run(scenario())
    assert busy == f"{state['verb']}\u2026"
    assert state["verb"] in SPINNER_VERBS
    assert idle.startswith(f"{state['past']} for ")
    assert state["past"] in PAST_TENSE_VERBS


def _idle_state(**kw):
    state = {"tools": 0, "think": False, "busy": False, "last_tool": "",
             "error": "", "verb": "Thinking", "past": "Handled",
             "started_at": time.monotonic(), "idle_since": None,
             "recent": []}
    state.update(kw)
    return state


def test_a_run_of_searches_and_reads_rolls_up_into_one_activity():
    text = status_text(_idle_state(recent=[{"search"}, {"search"}, {"read"}]),
                       running=True, all_idle=False, highlighted=False,
                       now=time.monotonic())
    assert text == "Looking for 2 patterns, opening 1 file…"


def test_a_single_read_is_not_yet_a_rollup():
    text = status_text(_idle_state(last_tool="Opening a.py",
                                   recent=[{"read"}]),
                       running=True, all_idle=False, highlighted=False,
                       now=time.monotonic())
    assert text == "Opening a.py…"


def test_a_tool_that_is_not_a_read_breaks_the_run():
    text = status_text(_idle_state(last_tool="git status",
                                   recent=[{"search"}, {"bash"}]),
                       running=True, all_idle=False, highlighted=False,
                       now=time.monotonic())
    assert text == "git status…"


def test_a_teammate_being_stopped_reads_stopping():
    text = status_text(_idle_state(recent=[{"search"}, {"search"}]),
                       running=True, all_idle=False, highlighted=False,
                       now=time.monotonic(), stopping=True)
    assert text == "Stopping…"


def test_a_teammate_waiting_on_your_approval_reads_that():
    text = status_text(_idle_state(recent=[{"search"}, {"search"}]),
                       running=True, all_idle=False, highlighted=False,
                       now=time.monotonic(), awaiting=True)
    assert text == "Needs your approval…"


def _row(columns, **kw):
    agent = SimpleNamespace(name="worker",
                            total_usage=SimpleNamespace(total_tokens=2000),
                            messages=[])
    kwargs = dict(running=True, color="#FF6B80", chosen=False,
                  foregrounded=False, last=False, all_idle=False,
                  now=time.monotonic(), columns=columns)
    kwargs.update(kw)
    return _plain(teammate_row(agent, _idle_state(tools=3), **kwargs))


def test_a_wide_teammate_row_carries_the_name_and_the_stats():
    assert _row(120) == ("     \u251c\u2500 @worker: Thinking\u2026"
                         " \u00b7 3 tool calls \u00b7 2k tokens")


def test_a_teammate_row_drops_the_stats_before_the_name():
    row = _row(62)
    assert "@worker" in row
    assert "2k tokens" not in row


def test_a_narrow_teammate_row_keeps_only_the_activity():
    row = _row(50)
    assert "@worker" not in row
    assert "Thinking" in row


def test_a_selected_row_hides_its_activity_and_shows_the_hints():
    row = _row(120, chosen=True)
    assert row.startswith("   \u276f \u255e\u2550 @worker")
    assert "Thinking" not in row
    assert "3 tool calls" in row
    assert "shift+\u2191/\u2193 picks a row" in row and "enter opens it" in row


def test_a_viewed_row_is_highlighted_but_offers_no_view_hint():
    row = _row(120, foregrounded=True)
    assert row.startswith("     \u255e\u2550 @worker")
    assert "enter opens it" not in row
    assert "picks a row" in row


def test_only_the_hide_row_ends_the_tree_with_a_corner():
    assert "\u2558\u2550" not in _row(120, chosen=True)
    assert _plain(hide_row(True)).startswith("   \u276f \u2558\u2550 hide")
    assert _plain(hide_row(False)).startswith("     \u2514\u2500 hide")


def test_a_selected_leader_is_highlighted_while_only_viewed_is_not():
    assert _plain(leader_row(selected=-1, foreground=False, busy="Thinking",
                             tokens=400,
                             columns=120)).startswith("   \u276f \u2552\u2550")
    assert _plain(leader_row(selected=None, foreground=False,
                             busy="Thinking", tokens=400,
                             columns=120)).startswith("     \u250c\u2500")


def test_the_leader_row_counts_the_lead_and_not_the_whole_team():
    row = _plain(leader_row(selected=None, foreground=True, busy="Thinking",
                            tokens=400, columns=120))
    assert row == ("     \u2552\u2550 team-lead \u00b7 400 tokens"
                   " \u00b7 shift+\u2191/\u2193 picks a row")
    assert "Thinking" not in row


def test_spinner_row_says_thinking_while_the_leader_is_reasoning():
    def prepare(app):
        _working_spinner(app, seconds=31, tokens=1200)
        app._note("lead", think=True)

    text = _rendered_spinner(_builder, prepare)
    assert _plain(text) == ("\u273b Thinking\u2026 (31s \u00b7 \u2193 1.2k tokens"
                            " \u00b7 39 tok/s \u00b7 thinking)")


def _status_right(builder, window: int = 0, size=(120, 40)) -> str:
    """The footer's right edge with the model window pinned to `window`."""
    base = dict(config.load())

    async def scenario():
        with mock.patch.object(config, 'load',
                               lambda: {**base, 'contextWindow': window}):
            async with PyClawApp(builder=builder).run_test(size=size) as pilot:
                await pilot.pause()
                await pilot.pause()
                return str(pilot.app.query_one("#status-right").content)

    return asyncio.run(scenario())


def _status_left(builder, prepare=None) -> str:
    async def scenario():
        async with PyClawApp(builder=builder).run_test(size=(120, 40)) as pilot:
            if prepare is not None:
                prepare(pilot.app)
            pilot.app._render_status()
            return str(pilot.app.query_one("#status").content)

    return asyncio.run(scenario())


def test_default_mode_shows_only_the_shortcut_hint():
    assert _plain(_status_left(_builder)) == "? lists the keys"


def test_the_mode_pill_is_on_the_second_row_with_the_directory():
    team = _GateTeam()

    def prepare(app):
        app._team = team
        app._session.set_permission_mode("plan")

    row = _status_row2(lambda: team, prepare)
    assert row.endswith('\u23f8 plan mode on (shift+tab to cycle)')
    assert len(row.split(' \u00b7 ')) == 2
    assert "\u23f8" not in _plain(_status_left(lambda: team, prepare))


def test_the_running_hint_sits_alone_in_the_footer():
    team = _GateTeam()

    def prepare(app):
        app._team = team
        app._session.set_permission_mode("plan")
        app._processing = "hello"

    assert _plain(_status_left(lambda: team, prepare)) == "esc stops the turn"
    assert "plan mode on" in _status_row2(lambda: team, prepare)


def test_the_cycle_hint_steps_back_once_a_second_item_shares_the_row():
    team = _GateTeam()

    def prepare(app):
        app._team = team
        app._session.set_permission_mode("plan")
        app._subagents["a@t"] = {"type": "Agent", "done": False, "tools": 0,
                                 "tokens": 0, "last_tool": "", "label": ""}

    assert _status_row2(lambda: team, prepare).endswith("\u23f8 plan mode on")


def test_a_teammate_view_counts_as_a_second_item_and_shows_its_hint():
    team = _GateSwarm()

    def prepare(app):
        app._team = team
        app._session.set_permission_mode("plan")
        app._viewing = "worker"
        app._expanded_view = "teammates"

    assert _plain(_status_left(lambda: team, prepare)) == \
        "esc goes back to the lead"


def test_a_running_teammate_keeps_the_interrupt_and_toggle_hints():
    team = _SwarmTeam()

    def prepare(app):
        app._team = team
        team.worker_busy = True
        app._viewing = "worker"
        app._expanded_view = "teammates"

    assert _plain(_status_left(lambda: team, prepare)) == \
        "esc stops the turn \u00b7 ctrl+t hides them"


def test_the_shortcut_hint_steps_back_while_typing():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            idle = _plain(str(pilot.app.query_one("#status").content))
            await pilot.press("h", "i")
            await pilot.pause()
            typing = _plain(str(pilot.app.query_one("#status").content))
            return idle, typing

    idle, typing = asyncio.run(scenario())
    assert idle == "? lists the keys"
    assert typing == ""


def _row1(builder=_builder, prepare=None, size=(120, 40),
          window: int = 0) -> str:
    """The first readout row with the model's context window pinned."""
    base = dict(config.load())

    async def scenario():
        with mock.patch.object(config, 'load',
                               lambda: {**base, 'contextWindow': window}):
            async with PyClawApp(builder=builder).run_test(size=size) as pilot:
                await pilot.pause()
                if prepare is not None:
                    prepare(pilot.app)
                pilot.app._render_readouts()
                return _plain(str(pilot.app.query_one("#hud").content))

    return asyncio.run(scenario())


def _row2(builder=_builder, prepare=None, git: str = '') -> str:
    """The second readout row with git's answer pinned."""

    async def scenario():
        with mock.patch.object(tui, 'git_status', lambda cwd: git):
            async with PyClawApp(builder=builder).run_test(
                    size=(120, 40)) as pilot:
                await pilot.pause()
                if prepare is not None:
                    prepare(pilot.app)
                pilot.app._render_readouts()
                return _plain(str(pilot.app.query_one("#hud2").content))

    return asyncio.run(scenario())


_status_row2 = _row2


def _statusline_rows(builder=_builder, command: str = ''):
    async def scenario():
        async with PyClawApp(builder=builder).run_test(size=(120, 40)) as pilot:
            app = pilot.app
            for _ in range(20):
                await pilot.pause(0.05)
            widget = app.query_one("#statusline")
            return (widget.display, str(widget.content),
                    _plain(str(app.query_one("#status").content)))

    return asyncio.run(scenario())


def test_an_unconfigured_status_line_takes_no_row():
    assert _statusline_rows() == (False, "", "? lists the keys")


def test_a_configured_status_line_paints_every_output_line(monkeypatch):
    monkeypatch.setattr(statusline, 'user_command',
                        lambda: "printf 'first\\n\\nsecond\\n'")
    display, text, hints = _statusline_rows()
    assert display is True
    assert text == "first\nsecond"
    assert hints == ""


def test_a_configured_status_line_gets_its_own_row(monkeypatch):
    monkeypatch.setattr(statusline, 'user_command',
                        lambda: "printf 'ready'")
    team = _GateTeam()

    def prepare(app):
        app._team = team
        app._session.set_permission_mode("plan")

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            prepare(pilot.app)
            for _ in range(20):
                await pilot.pause(0.05)
            return (pilot.app.query_one("#statusline").display,
                    pilot.app.query_one("#hud").display,
                    _plain(str(pilot.app.query_one("#hud2").content)))

    display, first_row, second_row = asyncio.run(scenario())
    assert display is True
    assert first_row is True
    assert "\u23f8 plan mode on" in second_row


def test_the_status_line_reruns_on_a_reply_not_on_an_append(monkeypatch):
    monkeypatch.setattr(statusline, 'user_command', lambda: "echo ready")
    team = _GateTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            before = app._statusline_state()
            team.record("user", [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": "output"}])
            after_append = app._statusline_state()
            team.record("assistant", "a reply")
            return before, after_append, app._statusline_state()

    before, after_append, after_reply = asyncio.run(scenario())
    assert after_append == before
    assert after_reply != before


def test_the_status_line_reruns_when_the_session_state_changes(monkeypatch):
    import sys
    command = ('"{py}" -c \'import json,sys; '
               'print(json.load(sys.stdin)["permission_mode"])\'').format(
        py=sys.executable)
    monkeypatch.setattr(statusline, 'user_command', lambda: command)
    team = _GateTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            for _ in range(20):
                await pilot.pause(0.05)
            first = str(app.query_one("#statusline").content)
            app._session.set_permission_mode("plan")
            app._render_status()
            for _ in range(30):
                await pilot.pause(0.05)
            return first, str(app.query_one("#statusline").content)

    first, second = asyncio.run(scenario())
    assert first == "default"
    assert second == "plan"


def test_configuring_the_status_line_mid_session_adds_the_row(monkeypatch):
    commands = ['']
    monkeypatch.setattr(statusline, 'user_command', lambda: commands[0])
    team = _GateTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            for _ in range(20):
                await pilot.pause(0.05)
            assert app.query_one("#statusline").display is False
            commands[0] = "printf 'live'"
            app._render_status()
            for _ in range(30):
                await pilot.pause(0.05)
            return str(app.query_one("#statusline").content)

    assert asyncio.run(scenario()) == "live"


def test_context_note_is_blank_without_a_context_budget():
    assert _status_right(_builder) == ""


def test_context_note_stays_hidden_while_there_is_room():
    class _RoomyTeam(_meter_team(150_000)):
        compact_threshold = 200_000
        auto_compact = True

    assert _status_right(lambda: _RoomyTeam()) == ""


def test_context_note_counts_the_room_left_down_to_auto_compact():
    class _NearlyFullTeam(_meter_team(180_000)):
        compact_threshold = 200_000
        auto_compact = True

    assert _status_right(lambda: _NearlyFullTeam()) == \
        "[dim]10% left before auto-compact[/]"


def test_context_note_asks_for_a_manual_compact_without_auto_compact():
    class _NoStrategyTeam(_meter_team(190_000)):
        compact_threshold = 200_000

    assert _status_right(lambda: _NoStrategyTeam()) == (
        "[error]Nearly out of context (5% left) "
        "\u00b7 run /compact to carry on[/]")


def _meter_team(used: int, completion: int = 0):
    """A team whose last response reported `used` tokens of window."""
    class _Metered(_FakeTeam):
        def last_usage(self):
            return Usage(prompt=used, completion=completion,
                         total=used + completion)
    return _Metered


def test_the_first_row_leads_with_the_model_and_its_context_meter():
    assert _row1(lambda: _meter_team(68_000)(), window=200_000).startswith(
        "m \u00b7 thinking off \u00b7 │███░░░░░░░│ 34%")


def test_the_note_stays_on_the_footer_while_its_own_row():
    """The compact warning keeps the footer's right edge; the meter moved up to
    the readout row, so the two no longer compete for one slot. Both read the same
    API number, so the percentages they print add up to the whole window."""
    class _TightTeam(_meter_team(190_000)):
        compact_threshold = 200_000

    assert _plain(_status_right(lambda: _TightTeam(),
                                window=200_000)) == (
        "Nearly out of context (5% left) \u00b7 run /compact to carry on")
    assert "│██████████│ 95%" in _row1(lambda: _TightTeam(), window=200_000)


def test_the_context_meter_names_the_window_it_measures_against():
    """The percentage alone is meaningless without the window it is a share of,
    so the meter prints the real configured window next to it."""
    assert _row1(lambda: _meter_team(68_000)(),
                 window=200_000).startswith(
        "m \u00b7 thinking off \u00b7 │███░░░░░░░│ 34% (200k)")
    assert _row1(lambda: _meter_team(340_000)(),
                 window=1_000_000).startswith(
        "m \u00b7 thinking off \u00b7 │███░░░░░░░│ 34% (1m)")
    assert "(0)" not in _row1(lambda: _meter_team(68_000)())


def test_the_first_row_shortens_its_meters_on_a_narrow_terminal():
    assert _row1(lambda: _meter_team(80_000)(), window=200_000,
                 size=(60, 40)).startswith("m \u00b7 thinking off \u00b7 │██░░░│ 40%")


def test_a_resized_terminal_gets_meters_of_the_right_length():
    async def scenario():
        async with PyClawApp(
                builder=lambda: _meter_team(68_000)()
                ).run_test(size=(120, 40)) as pilot:
            base = dict(config.load())
            with mock.patch.object(config, 'load',
                                   lambda: {**base, 'contextWindow': 200_000}):
                await pilot.pause()
                wide = _plain(str(pilot.app.query_one("#hud").content))
                await pilot.resize_terminal(60, 40)
                await pilot.pause()
                pilot.app._render_readouts()
                return wide, _plain(str(pilot.app.query_one("#hud").content))

    wide, narrow = asyncio.run(scenario())
    assert "│███░░░░░░░│ 34%" in wide
    assert "│███░░░░░░░│ 34%" not in narrow
    assert "│██░░░│ 34%" in narrow


def _hud_row(git: str = '', prepare=None, size: tuple = (80, 40)) -> str:
    """The HUD line with git's answer pinned to `git`."""

    async def scenario():
        with mock.patch.object(tui, 'git_status', lambda cwd: git):
            async with PyClawApp(builder=_builder).run_test() as pilot:
                await pilot.pause()
                await pilot.resize_terminal(*size)
                if prepare is not None:
                    prepare(pilot.app)
                pilot.app._render_readouts()
                return _plain(str(pilot.app.query_one("#hud").content))

    return asyncio.run(scenario())


def test_the_first_row_carries_the_usage_bar_and_its_counts():
    """No context meter here: with no window configured there is nothing for
    the bar to measure against."""
    assert _row1() == (
        "m \u00b7 thinking off \u00b7 │▒▒▒▒▒▓▓▓██│ \u00b7 in: 1.9k  out: 500  "
        "cache: 63%  total: 2.4k \u00b7 0 msg \u00b7 \u23f1 0s")


def test_the_first_row_ends_with_the_time_worked():
    def prepare(app):
        app._hud_started = time.monotonic() - 754

    assert _row1(prepare=prepare).endswith("\u00b7 \u23f1 12m 34s")


def test_status_shows_esc_to_interrupt_while_running():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            idle = str(app.query_one("#status").content)
            app._processing = "hello"
            app._render_status()
            busy = str(app.query_one("#status").content)
            return idle, busy

    idle, busy = asyncio.run(scenario())
    assert "? lists the keys" in idle
    assert "esc stops the turn" in busy


def test_model_switch_updates_status_bar(monkeypatch):
    monkeypatch.setattr("pyclaw.load", lambda: {"model": "m"})

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "/model newm"
            await pilot.press("enter")
            for _ in range(4):
                await pilot.pause()
            return (app._session.model,
                    str(app.query_one("#status").content))

    model, status = asyncio.run(scenario())
    assert model == "newm"
    assert "newm" not in status


def test_status_has_no_model_or_thinking_segments():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.pause()
            return str(app.query_one("#status").content)

    status = asyncio.run(scenario())
    assert "thinking" not in status
    assert "p/m" not in status


def test_fmt_compacts():
    """A compact count keeps at most one decimal and drops trailing zeros."""
    from pyclaw.tui.formatting import _format_count as fmt
    assert fmt(900) == "900"
    assert fmt(1000) == "1k"
    assert fmt(1901) == "1.9k"
    assert fmt(48_000) == "48k"
    assert fmt(1_200_000) == "1.2m"


def test_long_block_wraps_not_stretches():
    """A reply longer than the pane folds onto several lines instead of
    widening the conversation."""
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            long_text = "x" * 500
            block = app._append_block(long_text)
            await block
            await pilot.pause()
            rendered = list(app.query_one("#conv").query(Static))[-1]
            assert len(str(rendered.content)) == 500
            assert rendered.region.width < app.screen.size.width
            assert rendered.region.height > 1
    asyncio.run(scenario())


def test_stale_fallback_not_rendered():
    async def scenario():
        async with PyClawApp(builder=lambda: _StaleTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            flat = _flatten(app)
            assert "hi" in flat
            assert "旧回复" not in flat
    asyncio.run(scenario())


async def _submit_and_wait(pilot, text, rounds=4):
    app = pilot.app
    app.query_one(Input).value = text
    await pilot.press("enter")
    for _ in range(rounds):
        await pilot.pause()


def test_busy_submit_serializes_no_duplicate():
    team = _BusyTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            await _submit_and_wait(pilot, "one", 2)
            await _submit_and_wait(pilot, "two", 2)
            for _ in range(6):
                await pilot.pause()
            flat = _flatten(pilot.app)
            assert "reply:one" in flat
            assert "reply:two" in flat
            assert flat.count("reply:one") == 1
            assert flat.count("reply:two") == 1
            assert team.calls == ["one", "two"]
    asyncio.run(scenario())


def test_interrupt_cancels_running_work():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.press("ctrl+c")
            await pilot.pause()
            flat = _flatten(app)
            assert "Stopped" in flat
            assert "tell PyClaw what to do instead" in flat
    asyncio.run(scenario())


def test_subagent_progress_renders_tree_line():
    async def scenario():
        async with PyClawApp(builder=lambda: _SubTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "go"
            await pilot.press("enter")
            for _ in range(6):
                await pilot.pause()
            tasks = str(app._tasks_pane.content)
            assert "Sub-agents" in tasks
            assert "[bold]coder[/]" in tasks
            assert "1 tool call" in tasks
            assert "2k tokens" in tasks
            assert "Done" in tasks
    asyncio.run(scenario())


def _transcript_text(app) -> str:
    view = app.screen.query_one("#transcript")
    return "".join(str(w.content) for w in view.children)


def test_dynamic_text_with_brackets_renders_without_crash():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TEXT, agent="lead",
                                           data={"delta": "[/bold] [x] data"}))
            await pilot.pause()
            assert "[/bold] [x] data" in _flatten(app)
            block = _ToolBlock("Bash", '{"cmd": "[x]"}')
            await app._conv().mount(block)
            block.set_result("[/bold] output [y]")
            await pilot.pause()
            assert "\\[/bold] output \\[y]" in str(block.content)
    asyncio.run(scenario())


def test_diff_block_renders_summary_and_colored_lines():
    out = ("Saved a.txt.\n\n"
           "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c")
    text = _diff_block("Edit", {}, out, ".", 60)
    assert text is not None
    assert "Added 1 line" in text and "removed 1 line" in text
    assert "[on #225C2B]" in text and "[on #7A2936]" in text
    assert "+ B" in text and "- b" in text


def test_diff_block_skips_non_diff_output():
    assert _diff_block("Bash", {}, "ls\n", ".", 60) is None
    err = "Error: old_string not found in a.txt."
    assert _diff_block("Edit", {}, err, ".", 60) is None
    plain = "Saved a.txt."
    assert _diff_block("Edit", {}, plain, ".", 60) is None


def test_diff_block_word_highlights_similar_lines():
    out = ("--- a/x\n+++ b/x\n@@ -1 +1 @@\n"
           "-def foo(bar):\n+def foo(baz):")
    text = _diff_block("Edit", {}, out, ".", 60)
    assert text is not None
    assert "[on #38A660]" in text and "[on #B3596B]" in text


def test_diff_block_pairs_only_adjacent_remove_add():
    out = ("--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n"
           "-old line\n ctx\n+new line")
    text = _diff_block("Edit", {}, out, ".", 60)
    assert text is not None
    assert "#38A660" not in text and "#B3596B" not in text
    assert "[on #225C2B]" in text and "[on #7A2936]" in text


def test_read_card_uses_structured_meta_not_text_parsing():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(
                AGENT_TOOL_CALL, agent="lead",
                data={"tool": "Read", "input": {"file_path": "a.py"},
                      "tool_use_id": "rt1"}))
            await pilot.pause()
            block = app._tools["rt1"]
            block.set_result("a.py:\n10\tfoo\n11\tbar",
                             meta={"num_lines": 42, "path": "a.py"})
            await pilot.pause()
            content = str(block.content)
            assert "Read 42 lines" in content
            assert "Read 2 lines" not in content

    asyncio.run(scenario())


def test_tool_block_renders_edit_diff():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            block = _ToolBlock("Edit", {"file_path": "a.txt"}, cwd=".")
            await app._conv().mount(block)
            block.set_result("Saved a.txt.\n"
                             "\n--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n"
                             " a\n-b\n+B\n c")
            await pilot.pause()
            content = str(block.content)
            assert "Added 1 line" in content
            assert "#225C2B" in content
            assert block.has_class("diff")
    asyncio.run(scenario())


def test_at_token_detects_and_applies():
    from pyclaw.tui.suggest import _apply_at, _at_token
    assert _at_token("hi @src") == "src"
    assert _at_token("@") == ""
    assert _at_token("hi @a/b") == "a/b"
    assert _at_token("email@example") is None
    assert _at_token("hi @src more") is None
    assert _apply_at("hi @src", "src/main.py", False) == "hi @src/main.py "
    assert _apply_at("@", "src/", True) == "@src/"
    assert _apply_at("@src", "src/b.py", False) == "@src/b.py "


def test_file_suggest_lists_workspace_entries(tmp_path):
    from pyclaw.tui.suggest import _file_suggest
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("y")
    (tmp_path / ".hidden").write_text("z")
    items = _file_suggest(str(tmp_path), "")
    names = [i["name"] for i in items]
    assert "a.txt" in names and "src/" in names
    assert all(not n.startswith(".hidden") for n in names)
    assert all(i["icon"] == "+" for i in items)
    deeper = _file_suggest(str(tmp_path), "src/")
    assert deeper[0]["name"] == "src/b.py"
    assert _file_suggest(str(tmp_path), "../") == []


def test_at_typeahead_shows_files_and_tab_applies():

    async def scenario(d):
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            app._cwd = lambda: d
            await pilot.pause()
            inp = app.query_one(Input)
            inp.value = "@"
            await pilot.pause()
            suggest = app.query_one("#suggest", Static)
            assert suggest.display
            assert "+ a.txt" in str(suggest.content)
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            await pilot.pause()
            assert inp.value == "@a.txt "
            assert not suggest.display

    with tempfile.TemporaryDirectory() as d:
        Path(d, "a.txt").write_text("x")
        Path(d, "src").mkdir()
        asyncio.run(scenario(d))


def test_ctrl_r_history_picker_filters_and_executes():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app._history = ["explain this", "git status", "run tests"]
            await pilot.press("ctrl+r")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, HistorySearchScreen)
            hs = app.screen.query_one("#hs-input", Input)
            hs.value = "git"
            await pilot.pause()
            lst = app.screen.query_one("#hs-list", Static)
            assert "git status" in str(lst.content)
            assert "explain this" not in str(lst.content)
            await pilot.press("enter")
            for _ in range(6):
                await pilot.pause()
            assert not isinstance(app.screen, HistorySearchScreen)
            assert "git status" in _flatten(app)

    asyncio.run(scenario())


def test_ctrl_r_history_picker_tab_accepts_without_submit():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app._history = ["run tests", "git status"]
            await pilot.press("ctrl+r")
            await pilot.pause()
            await pilot.pause()
            hs = app.screen.query_one("#hs-input", Input)
            hs.value = "run"
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert not isinstance(app.screen, HistorySearchScreen)
            assert app.query_one("#input", Input).value == "run tests"
            assert "run tests" not in _flatten(app)

    asyncio.run(scenario())


def test_ctrl_s_stash_and_unstash_prompt():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            inp = app.query_one(Input)
            inp.value = "half written"
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert inp.value == ""
            assert app._stashed == "half written"
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert inp.value == "half written"
            assert app._stashed is None
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert inp.value == ""

    asyncio.run(scenario())


def test_ctrl_r_history_picker_escape_cancels():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app._history = ["run tests"]
            inp = app.query_one("#input", Input)
            inp.value = "my draft"
            await pilot.press("ctrl+r")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HistorySearchScreen)
            assert inp.value == "my draft"

    asyncio.run(scenario())


def test_at_typeahead_dir_keeps_navigating():

    async def scenario(d):
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            app._cwd = lambda: d
            await pilot.pause()
            inp = app.query_one(Input)
            inp.value = "@src/"
            await pilot.pause()
            suggest = app.query_one("#suggest", Static)
            assert suggest.display
            assert "+ src/b.py" in str(suggest.content)
            await pilot.press("tab")
            await pilot.pause()
            await pilot.pause()
            assert inp.value == "@src/b.py "

    with tempfile.TemporaryDirectory() as d:
        Path(d, "src").mkdir()
        Path(d, "src", "b.py").write_text("y")
        asyncio.run(scenario(d))


def test_subagent_agent_square_brackets_do_not_crash():

    def builder():
        t = _FakeTeam()
        t.agents["teammate@t"] = type("A", (), {
            "name": "teammate", "agent_id": "teammate@t"})()
        return t

    async def scenario():
        async with PyClawApp(builder=builder).run_test() as pilot:
            app = pilot.app
            await app.action_toggle_tasks()
            await pilot.pause()
            app._subagents["teammate"] = {"type": "Task", "tools": 0,
                                          "tokens": None, "last_tool": None,
                                          "done": False, "recent": [],
                                          "tool_names": {}}
            errors = []

            async def h(ev):
                try:
                    await app._handle(ev)
                    app._render_status()
                    app._render_tasks()
                except Exception as e:
                    errors.append((ev.kind, type(e).__name__, str(e)))

            await h(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
                "tool": "create_agent",
                "input": {"prompt": "build site 1. **x** [/bold]"},
                "tool_use_id": "c1"}))
            await h(RuntimeEvent(AGENT_TEXT, agent="teammate",
                                 data={"delta": "Running [/bold] build"}))
            await h(RuntimeEvent(AGENT_TOOL_CALL, agent="teammate", data={
                "tool": "Bash",
                "input": {"command": "npm create vite"},
                "tool_use_id": "b1"}))
            await h(RuntimeEvent(AGENT_TOOL_RESULT, agent="teammate", data={
                "tool": "Bash", "tool_use_id": "b1", "exit_code": 0}))
            app._sync_tool_states()
            app._tools["c1"].set_result("Report:\n[/bold] done scaffolding")
            await pilot.pause()
            assert not errors
            assert "render error" not in str(app.query_one("#conv"))
            assert "[/bold]" not in str(app.query_one("#conv"))

    asyncio.run(scenario())


def test_slash_menu_shows_and_tab_completes():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.press(*"/mo")
            await pilot.pause()
            suggest = app.query_one("#suggest", Static)
            assert suggest.display
            assert "/model" in str(suggest.content)
            await pilot.press("tab")
            await pilot.pause()
            inp = app.query_one("#input", Input)
            assert inp.value == "/model "
            assert inp.cursor_position == len("/model ")
            assert not suggest.display
    asyncio.run(scenario())


def test_slash_menu_enter_executes_noarg_and_waits_for_args():
    async def scenario():
        async with PyClawApp(builder=lambda: _GateTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.press(*"/plan")
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(4):
                await pilot.pause()
            assert app._session.permission_mode == "plan"
            assert app.query_one("#input", Input).value == ""
            await pilot.press(*"/model")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            inp = app.query_one("#input", Input)
            assert inp.value == "/model "
            assert inp.cursor_position == len("/model ")
            assert not app.query_one("#suggest", Static).display
    asyncio.run(scenario())


def test_slash_menu_navigates_and_hides_for_plain_text():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.press(*"/c")
            await pilot.pause()
            suggest = app.query_one("#suggest", Static)
            assert suggest.display
            first = app._suggest_selected
            await pilot.press("down")
            await pilot.pause()
            assert app._suggest_selected == first + 1
            await pilot.press("x")
            await pilot.pause()
            assert not suggest.display
            await pilot.press("backspace", "backspace", "backspace")
            await pilot.pause()
            assert not suggest.display
            await pilot.press("h", "i")
            await pilot.pause()
            assert not suggest.display
    asyncio.run(scenario())


def test_ctrl_o_opens_transcript_and_q_exits():
    async def scenario():
        async with PyClawApp(builder=lambda: _LongToolTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "go"
            await pilot.press("enter")
            for _ in range(8):
                await pilot.pause()
            assert "y" * 400 not in _flatten(app)
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert type(app.screen).__name__ == "TranscriptScreen"
            assert "y" * 400 in _transcript_text(app)
            assert "x" * 80 in _transcript_text(app)
            await pilot.press("q")
            await pilot.pause()
            assert type(app.screen).__name__ != "TranscriptScreen"
            await pilot.press("ctrl+o")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert type(app.screen).__name__ != "TranscriptScreen"
    asyncio.run(scenario())


def test_transcript_shows_thinking_of_last_assistant():
    async def scenario():
        async with PyClawApp(builder=lambda: _ThinkTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            for _ in range(6):
                await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert "inner monologue" in _transcript_text(app)
    asyncio.run(scenario())


def test_tool_card_expands_full_input_output_on_click():
    async def scenario():
        async with PyClawApp(builder=lambda: _LongToolTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "go"
            await pilot.press("enter")
            for _ in range(8):
                await pilot.pause()
            block = app._tools["t1"]
            assert not block._expanded
            collapsed = str(block.content)
            assert "\u23bf" in collapsed
            assert "(ctrl+o shows the rest)" in collapsed
            block.on_click()
            await pilot.pause()
            expanded = str(block.content)
            assert "(ctrl+o shows the rest)" not in expanded
            assert "y" * 400 in expanded
    asyncio.run(scenario())


class _SplitTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        emit(AGENT_TEXT, agent="lead", delta="part1 ")
        emit(AGENT_TOOL_CALL, agent="lead", tool="k", input={}, tool_use_id="t1")
        emit(AGENT_REASON_START, agent="lead")
        emit(AGENT_TEXT, agent="lead", delta="part2")
        self.record("assistant", "part1 part2")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "part1 part2"


def test_body_and_tools_keep_chronological_order():
    async def scenario():
        async with PyClawApp(builder=lambda: _SplitTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "go"
            await pilot.press("enter")
            for _ in range(6):
                await pilot.pause()
            children = [_block_text(w) for w in app.query_one("#conv").children]
            part1 = [i for i, c in enumerate(children) if "part1" in c]
            part2 = [i for i, c in enumerate(children) if "part2" in c]
            tool = [i for i, c in enumerate(children) if "[bold]k[/]" in c]
            assert len(part1) == 1 and len(part2) == 1
            assert part1 != part2
            assert tool and part1[0] < tool[0] < part2[0]
    asyncio.run(scenario())


class _TimeoutTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        self._running = True
        asyncio.create_task(self._finish_later())
        return "partial"

    async def _finish_later(self):
        await asyncio.sleep(1.0)
        emit(AGENT_TEXT, agent="lead", delta="full answer")
        self.record("assistant", "full answer")
        emit(AGENT_TURN_FINISHED, agent="lead")
        self._running = False


def test_input_stays_busy_until_lead_actually_idle():
    team = _TimeoutTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            for _ in range(3):
                await pilot.pause()
            assert app._processing == "hi"
            assert "esc stops the turn" in str(
                app.query_one("#status").content)
            for _ in range(30):
                await pilot.pause()
                await asyncio.sleep(0.05)
            assert app._processing is None
            assert "full answer" in _flatten(app)
    asyncio.run(scenario())


class _TeammateTeam(_FakeTeam):

    def __init__(self):
        super().__init__()
        owner = self
        self.worker_busy = False

        class _W:
            name = "worker"
            agent_id = "worker@t"
            def abort_work(self):
                pass
            @property
            def busy(self):
                return owner.worker_busy
            async def idle(self):
                return not owner.worker_busy
        self.agents["worker@t"] = _W()

    async def query(self, prompt, timeout=60):
        self.worker_busy = True
        self.record("user", prompt)
        emit(AGENT_TEXT, agent="lead", delta="answer")
        self.record("assistant", "answer")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "answer"


def test_input_free_while_teammate_running():
    team = _TeammateTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            for _ in range(6):
                await pilot.pause()
            for _ in range(20):
                await pilot.pause()
                await asyncio.sleep(0.05)
            assert app._processing is None
            assert "answer" in _flatten(app)
            assert team.worker_busy
    asyncio.run(scenario())


def test_turn_duration_deferred_until_teammates_settle():
    team = _TeammateTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            for _ in range(20):
                await pilot.pause()
                await asyncio.sleep(0.05)
            assert "Handled for" not in _flatten(app)
            team.worker_busy = False
            for _ in range(10):
                await pilot.pause()
                await asyncio.sleep(0.05)
            assert "Handled for" in _flatten(app)
    asyncio.run(scenario())


class _StubBlock:

    _done = False

    def end_progress(self):
        pass


class _Inbox:

    def __init__(self):
        self.written = []
        self.read = []

    def write(self, from_, text, **kw):
        self.written.append((from_, text))

    def unread(self):
        return [m for m in self.written if m not in self.read]

    def mark_all_read(self):
        self.read = list(self.written)


class _SwarmTeam(_FakeTeam):

    def __init__(self):
        super().__init__()
        self.worker_busy = False
        self.submitted = []
        self.aborted = 0
        self.stopped = []
        owner = self

        class _W:
            name = "worker"
            agent_id = "worker@t"
            instruction = "You are the worker."
            is_running = True

            def __init__(self):
                self.messages = [{"role": "user", "content": "do the thing"}]
                self.inbox = _Inbox()

            def abort_work(self):
                owner.aborted += 1

            def submit(self, text):
                owner.submitted.append(text)
                self.messages.append({"role": "user", "content": text})

            @property
            def busy(self):
                return owner.worker_busy

            async def idle(self):
                return not owner.worker_busy

        self.worker = _W()
        self.agents["worker@t"] = self.worker
        self.children = {"lead@t": {"worker@t"}}
        self.parents = {"worker@t": "lead@t"}

    async def stop_agent(self, agent):
        self.stopped.append(agent.name)
        self.agents.pop(agent.agent_id, None)

    async def query(self, prompt, timeout=60):
        self.worker_busy = True
        self.record("user", prompt)
        emit(AGENT_TEXT, agent="lead", delta="lead answer")
        emit(AGENT_TEXT, agent="worker", delta="worker private text")
        emit(AGENT_TOOL_CALL, agent="worker", tool="Read",
             input={"file_path": "a.py"}, tool_use_id="w1")
        self.record("assistant", "lead answer")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "lead answer"


class _GateSwarm(_GateTeam, _SwarmTeam):
    pass


async def _run_turn(pilot, text="go", rounds=8):
    pilot.app.query_one(Input).value = text
    await pilot.press("enter")
    for _ in range(rounds):
        await pilot.pause()
        await asyncio.sleep(0.02)


def _tree(app) -> str:
    return _plain(str(app._agents_pane.content)) if app._agents_pane else ""


class _BlankTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        emit(AGENT_TEXT, agent="lead", delta="\n\n")
        emit(AGENT_TOOL_CALL, agent="lead", tool="k", input={}, tool_use_id="t1")
        emit(AGENT_TEXT, agent="lead", delta="answer")
        self.record("assistant", "answer")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "answer"


def test_blank_deltas_do_not_create_empty_blocks():
    async def scenario():
        async with PyClawApp(builder=lambda: _BlankTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "go"
            await pilot.press("enter")
            for _ in range(6):
                await pilot.pause()
            blocks = [_block_text(w) for w in app.query_one("#conv").children]
            assert "answer" in blocks[-2]
            assert "Handled for" in blocks[-1]
            assert all(b.strip() for b in blocks)
    asyncio.run(scenario())


class _DeltaTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        for ch in "abc":
            emit(AGENT_TEXT, agent="lead", delta=ch)
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "abc"


def test_turn_returns_only_after_all_events_rendered():
    async def scenario():
        async with PyClawApp(builder=lambda: _DeltaTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            rendered = []
            real = app._handle

            async def slow(ev):
                await asyncio.sleep(0.03)
                await real(ev)
                rendered.append(ev.kind)

            app._handle = slow
            await app._converse("hi")
            return rendered

    rendered = asyncio.run(scenario())
    assert rendered.count(AGENT_TEXT) == 3
    assert rendered[-1] == AGENT_TURN_FINISHED


def test_page_keys_scroll_conversation():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            for i in range(40):
                await app._append_block(f"line {i}")
            await pilot.pause()
            conv = app._conv()
            assert conv.max_scroll_y > 0
            conv.scroll_end(animate=False)
            await pilot.pause()
            bottom = conv.scroll_y
            await pilot.press("pageup")
            await pilot.pause()
            assert conv.scroll_y < bottom
            assert app._follow is False
            for _ in range(10):
                await pilot.press("pagedown")
                await pilot.pause()
            assert app._follow is True
            assert app._hint is None
    asyncio.run(scenario())


def _half(view) -> int:
    return max(1, view.scrollable_content_region.height // 2)


def test_page_keys_scroll_half_a_viewport():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            for i in range(40):
                await app._append_block(f"line {i}")
            await pilot.pause()
            conv = app._conv()
            conv.scroll_end(animate=False)
            await pilot.pause()
            step = _half(conv)
            assert step < conv.scrollable_content_region.height
            bottom = conv.scroll_y
            await pilot.press("pageup")
            await pilot.pause()
            assert bottom - conv.scroll_y == step
            await pilot.press("pagedown")
            await pilot.pause()
            assert conv.scroll_y == bottom
    asyncio.run(scenario())


def test_the_transcript_reads_with_pager_keys():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(100, 24)) as pilot:
            app = pilot.app
            await pilot.pause()
            for i in range(40):
                await app._append_block(f"line {i}")
            await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            view = app.screen.query_one("#transcript")
            assert view.max_scroll_y > 0
            await pilot.press("g")
            await pilot.pause()
            assert view.scroll_y == 0
            await pilot.press("j")
            await pilot.pause()
            assert view.scroll_y == 1
            await pilot.press("k")
            await pilot.pause()
            assert view.scroll_y == 0
            await pilot.press("ctrl+d")
            await pilot.pause()
            assert view.scroll_y == _half(view)
            await pilot.press("ctrl+u")
            await pilot.pause()
            assert view.scroll_y == 0
            await pilot.press("space")
            await pilot.pause()
            assert view.scroll_y == view.scrollable_content_region.height
            await pilot.press("b")
            await pilot.pause()
            assert view.scroll_y == 0
            await pilot.press("G")
            await pilot.pause()
            assert view.scroll_y == view.max_scroll_y
            assert type(app.screen).__name__ == "TranscriptScreen"
    asyncio.run(scenario())


def test_the_help_screen_documents_the_pager_keys():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()
            return "".join(str(w.content)
                           for w in pilot.app.screen.query_one(
                               "#help").children)

    body = asyncio.run(scenario())
    assert "Reading the transcript" in body
    for key in ("j", "k", "g", "G", "b", "ctrl+u", "ctrl+d", "space"):
        assert f"  {key}  " in body


def test_the_status_row_is_painted_as_soon_as_it_is_mounted():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            if app._spin_timer is not None:
                app._spin_timer.stop()
            app._turn_verb = "Thinking"
            await app._mount_spinner()
            return str(app._think["widget"].content)

    frame = asyncio.run(scenario())
    assert "Thinking…" in frame


def test_a_leading_blank_delta_keeps_the_status_row():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_REASON_START, agent="lead"))
            await app._handle(RuntimeEvent(AGENT_TEXT, agent="lead",
                                           data={"delta": "\n"}))
            assert app._think is not None
            assert str(app._think["widget"].content).strip()
            await app._handle(RuntimeEvent(AGENT_TEXT, agent="lead",
                                           data={"delta": "answer"}))
            assert app._think is None
            assert "answer" in _flatten(app)
    asyncio.run(scenario())


def test_the_status_row_hands_over_to_the_tool_row():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_REASON_START, agent="lead"))
            alive = []
            real = app._add_tool

            async def spy(ev):
                alive.append(app._think is not None)
                await real(ev)

            app._add_tool = spy
            await app._handle(RuntimeEvent(
                AGENT_TOOL_CALL, agent="lead",
                data={"tool": "Grep", "input": {"pattern": "x"},
                      "tool_use_id": "g1"}))
            rows = [_block_text(w) for w in app._conv().children if w.display]
            assert alive == [True]
            assert all(row.strip() for row in rows)
            assert "Looking for" in rows[-1]
    asyncio.run(scenario())


def test_the_status_row_outlives_the_leader_while_teammates_run():
    team = _TeammateTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            team.worker_busy = True
            await app._handle(RuntimeEvent(AGENT_REASON_START, agent="lead"))
            await app._handle(RuntimeEvent(AGENT_TURN_FINISHED, agent="lead"))
            assert app._think is not None
            assert str(app._think["widget"].content).strip()
            team.worker_busy = False
            for _ in range(6):
                await pilot.pause()
                await asyncio.sleep(0.05)
            assert app._think is None
            assert "Handled for" in _flatten(app)
    asyncio.run(scenario())


class _TwoTurnTeam(_FakeTeam):

    def __init__(self):
        super().__init__()
        self._turn = 0

    async def query(self, prompt, timeout=60):
        self._turn += 1
        text = f"answer{self._turn}"
        self.record("user", prompt)
        emit(AGENT_REASON_START, agent="lead")
        emit(AGENT_TEXT, agent="lead", delta=text)
        self.record("assistant", text, thinking=f"think{self._turn}")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return text


def test_second_turn_reuses_the_single_work_line():
    def work_lines(app):
        return [t for w in app.query_one("#conv").children
                if "Handled for" in (t := _block_text(w))]

    async def scenario():
        async with PyClawApp(builder=lambda: _TwoTurnTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _submit_and_wait(pilot, "one", 6)
            assert len(work_lines(app)) == 1
            await _submit_and_wait(pilot, "two", 6)
            assert len(work_lines(app)) == 1
            assert "answer1" in _flatten(app)
            assert "answer2" in _flatten(app)
    asyncio.run(scenario())


class _LogTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        emit(AGENT_TEXT, agent="lead", delta="logged answer")
        self.record("assistant", "logged answer", thinking="why")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "logged answer"


def test_turn_appends_conversation_log(tmp_path, monkeypatch):
    from conippets import jsonl

    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)

    async def scenario():
        async with PyClawApp(builder=lambda: _LogTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            for _ in range(6):
                await pilot.pause()
    asyncio.run(scenario())

    records = jsonl.read(tmp_path / "t" / "messages.jsonl")
    assistant = [r for r in records if r["role"] == "assistant"]
    assert assistant
    assert assistant[-1]["content"] == "logged answer"
    assert assistant[-1]["reasoning_content"] == "why"


def test_resume_renders_saved_history(tmp_path, monkeypatch):

    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    agents.save_transcript("s1", [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "a.txt"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "file body"}]},
        {"role": "assistant", "content": "old answer", "thinking": "old thought"},
    ])

    async def scenario():
        async with PyClawApp(builder=_builder, session_id="s1",
                             resume=True).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            flat = _flatten(app)
            assert "old question" in flat
            assert "old answer" in flat
            assert "Opened 1 file" in _plain(flat)
            assert "old thought" not in flat
            assert app._tools["t1"]._done is True
    asyncio.run(scenario())


def test_fresh_start_ignores_saved_history(tmp_path, monkeypatch):

    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    agents.save_transcript("s2", [{"role": "assistant", "content": "stale"}])

    async def scenario():
        async with PyClawApp(builder=_builder, session_id="s2",
                             resume=False).run_test() as pilot:
            await pilot.pause()
            return _flatten(pilot.app)

    assert "stale" not in asyncio.run(scenario())


def test_permission_card_shows_why_the_model_wants_to_run_it():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            with_intent = _PermissionPrompt(
                "Bash", {"command": "npm ci",
                         "description": "install pinned dependencies"},
                rememberable=True)
            await app._conv().mount(with_intent)
            await pilot.pause()
            text = str(with_intent.query_one("#perm-body", Static).content)
            assert "install pinned dependencies" in text
            assert "npm ci" in text

            without = _PermissionPrompt("Bash", {"command": "npm ci"},
                                        rememberable=True)
            await app._conv().mount(without)
            await pilot.pause()
            plain = str(without.query_one("#perm-body", Static).content)
            assert "npm ci" in plain
            assert len(text.splitlines()) == len(plain.splitlines()) + 1
    asyncio.run(scenario())


def test_permission_card_drops_remember_option_for_dangerous_command():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            locked = _PermissionPrompt("Bash", {"command": "rm -rf /"},
                                       rememberable=False)
            await app._conv().mount(locked)
            await pilot.pause()
            text = str(locked.query_one("#perm-body", Static).content)
            assert "don't ask again" not in text
            assert "2. No" in text
            assert locked._rememberable is False

            normal = _PermissionPrompt("Edit", "a.txt", rememberable=True,
                                       rule="Edit")
            await app._conv().mount(normal)
            await pilot.pause()
            text = str(normal.query_one("#perm-body", Static).content)
            assert "always allow" in text
            assert "Edit" in text
    asyncio.run(scenario())


def test_the_permission_card_holds_no_rows_beyond_its_own_text():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(90, 30)) as pilot:
            app = pilot.app
            await pilot.pause()
            prompt = _PermissionPrompt("Bash", {"command": "git commit -m x"},
                                       rememberable=True,
                                       rule="Bash(git commit:*)")
            await app._conv().mount(prompt)
            await pilot.pause()
            body = prompt.query_one("#perm-body", Static)
            assert prompt.region.height == len(str(body.content).splitlines())
            prompt.action_opt_next()
            await pilot.pause()
            assert prompt.region.height == len(str(body.content).splitlines())
    asyncio.run(scenario())


def test_focusing_the_remember_option_keeps_the_rule_row_shut():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(90, 30)) as pilot:
            app = pilot.app
            await pilot.pause()
            prompt = _PermissionPrompt("Bash", {"command": "git commit -m x"},
                                       rememberable=True,
                                       rule="Bash(git commit:*)")
            await app._conv().mount(prompt)
            await pilot.pause()
            rule = prompt.query_one("#perm-rule", Input)
            prompt.action_opt_next()
            await pilot.pause()
            assert rule.display is False
            prompt.action_toggle_feedback()
            await pilot.pause()
            assert rule.display is True
            assert rule.value == "Bash(git commit:*)"
            prompt.action_opt_next()
            await pilot.pause()
            assert rule.display is False
    asyncio.run(scenario())


def test_runtime_sink_released_on_unmount():
    from chatchat.hooks import events

    baseline = list(events._runtime_sinks)

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            assert len(events._runtime_sinks) == len(baseline) + 1
    asyncio.run(scenario())
    assert events._runtime_sinks == baseline


def test_follow_pauses_and_jump_to_bottom_resumes():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app._set_follow(False)
            await pilot.pause()
            assert app._follow is False
            await app._append_block("x1")
            await app._append_block("x2")
            await pilot.pause()
            assert app._hint is not None
            assert "2 new" in str(app._hint.content)
            await pilot.press("ctrl+end")
            await pilot.pause()
            assert app._follow is True
            assert app._hint is None

            app._set_follow(False)
            await pilot.pause()
            if app._spin_timer is not None:
                app._spin_timer.stop()
            conv = app._conv()
            calls = []
            conv.scroll_end = lambda *a, **k: calls.append(1)
            await app._add_tool(RuntimeEvent(
                AGENT_TOOL_CALL, agent="lead",
                data={"tool": "k", "input": {"x": 1}, "tool_use_id": "t1"}))
            assert calls == []
            app._set_follow(True)
            await app._add_tool(RuntimeEvent(
                AGENT_TOOL_CALL, agent="lead",
                data={"tool": "k", "input": {"x": 1}, "tool_use_id": "t2"}))
            assert calls == [1]
    asyncio.run(scenario())


def test_spin_tick_is_safe_after_conv_removed():
    from pyclaw.tui.widgets import _Conv

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app._think = {"widget": Static("", markup=True), "agent": "lead"}
            await app._conv().remove()
            await pilot.pause()
            app._tool_spin_tick()
            assert list(app.query(_Conv)) == []
    asyncio.run(scenario())


class _BracketSubTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        self.record("user", prompt)
        emit(AGENT_PROGRESS, agent="sub-1", prompt="x", subagent_type="coder")
        emit(AGENT_PROGRESS, agent="sub-1",
             message={"role": "assistant",
                      "content": [{"type": "tool_use", "id": "t1",
                                   "name": "Grep", "input": {"pattern": "x"}}]})
        emit(AGENT_PROGRESS, agent="sub-1",
             message={"role": "user",
                      "content": [{"type": "tool_result", "tool_use_id": "t1",
                                   "content": "notes.md:96: \u73b0\u8c61\uff1a"
                                              "[/bold] \u7b49\u65b9\u62ec\u53f7"}]})
        self.record("assistant", "done")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "done"


def test_subagent_output_with_brackets_does_not_break_the_tasks_pane():
    async def scenario():
        async with PyClawApp(
                builder=lambda: _BracketSubTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "go"
            await pilot.press("enter")
            for _ in range(8):
                await pilot.pause()
            return str(app._tasks_pane.content), _flatten(app)

    tasks, flat = asyncio.run(scenario())
    assert "render error" not in flat
    assert "Grep" in tasks
    assert "\\[/bold]" in tasks


def _prompts(app) -> list:
    return [w for w in app._conv().children if isinstance(w, _PermissionPrompt)]


async def _asked_prompt(app, pilot, tool="Bash", tool_input=None,
                        tool_use_id="", agent=None):
    """Start a real approval and return its task plus its prompt widget."""
    task = asyncio.ensure_future(
        app._ask_permission(tool, tool_input or {"command": "git commit -m x"},
                            tool_use_id=tool_use_id, agent=agent))
    for _ in range(4):
        await pilot.pause()
    prompts = _prompts(app)
    return task, prompts[0] if prompts else None


def test_bash_rule_row_is_editable_where_it_is_focused():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot)
            assert [o.value for o in prompt._options()] == ["approved",
                                                            "dont_ask",
                                                            "denied"]
            assert [o.feedback for o in prompt._options()] == ["accept",
                                                               "rule",
                                                               "reject"]
            rule_field = prompt.query_one("#perm-rule", Input)
            assert not rule_field.display
            await pilot.press("down")
            await pilot.pause()
            assert prompt._focused == 1
            assert not rule_field.display
            await pilot.press("tab")
            await pilot.pause()
            assert rule_field.display
            assert rule_field.has_focus
            assert rule_field.value == "Bash(git commit:*)"
            assert ([o.label for o in prompt._options()][1]
                    == "Yes, and stop asking about: Bash(git commit:*)")
            rule_field.value = "Bash(git commit --amend:*)"
            await pilot.press("enter")
            await pilot.pause()
            return await task

    choice = asyncio.run(scenario())
    assert choice.value == "dont_ask"
    assert choice.rule == "Bash(git commit --amend:*)"


def test_escape_denies_even_while_a_feedback_field_is_open():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot, tool="Edit",
                                               tool_input={"file_path": "a.txt"})
            await pilot.press("tab")
            await pilot.pause()
            accept = prompt.query_one("#perm-accept", Input)
            assert accept.display and accept.has_focus
            await pilot.press("escape")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "denied"


def test_empty_feedback_submits_the_focused_option():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot, tool="Edit",
                                               tool_input={"file_path": "a.txt"})
            await pilot.press("tab")
            await pilot.pause()
            assert prompt.query_one("#perm-accept", Input).value == ""
            await pilot.press("enter")
            await pilot.pause()
            return await task

    choice = asyncio.run(scenario())
    assert choice.value == "approved"
    assert choice.feedback == ""


def test_arrows_move_the_option_pointer_and_wrap():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot)
            assert prompt._focused == 0
            for expected in (1, 2, 0):
                await pilot.press("down")
                await pilot.pause()
                assert prompt._focused == expected
            await pilot.press("up")
            await pilot.pause()
            assert prompt._focused == 2
            await pilot.press("enter")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "denied"


def test_j_k_and_ctrl_aliases_move_the_pointer():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(
                app, pilot, tool="Edit", tool_input={"file_path": "a.txt"})
            for key, expected in (("j", 1), ("k", 0), ("k", 2), ("j", 0),
                                  ("ctrl+n", 1), ("ctrl+p", 0)):
                await pilot.press(key)
                await pilot.pause()
                assert prompt._focused == expected, key
            await pilot.press("escape")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "denied"


def test_page_keys_jump_across_the_option_list():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(
                app, pilot, tool="Edit", tool_input={"file_path": "a.txt"})
            await pilot.press("pagedown")
            await pilot.pause()
            assert prompt._focused == 2
            await pilot.press("pagedown")
            await pilot.pause()
            assert prompt._focused == 2
            await pilot.press("pageup")
            await pilot.pause()
            assert prompt._focused == 0
            await pilot.press("escape")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "denied"


def test_digits_pick_the_option_at_that_position():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, _prompt = await _asked_prompt(app, pilot)
            await pilot.press("3")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "denied"


def test_a_digit_selects_the_prefilled_rule_row():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, _prompt = await _asked_prompt(app, pilot)
            await pilot.press("2")
            await pilot.pause()
            return await task

    choice = asyncio.run(scenario())
    assert choice.value == "dont_ask"
    assert choice.rule == "Bash(git commit:*)"


def test_a_digit_types_into_an_open_rule_row_instead_of_selecting():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot)
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            rule_field = prompt.query_one("#perm-rule", Input)
            assert rule_field.has_focus
            before = rule_field.value
            await pilot.press("5")
            for _ in range(2):
                await pilot.pause()
            assert rule_field.value == before + "5"
            assert not task.done()
            await pilot.press("escape")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "denied"


def test_y_and_n_are_not_shortcuts():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, _prompt = await _asked_prompt(app, pilot)
            await pilot.press("y")
            await pilot.press("n")
            for _ in range(2):
                await pilot.pause()
            assert not task.done()
            await pilot.press("enter")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "approved"


def test_tab_opens_and_closes_the_feedback_field():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot)
            accept = prompt.query_one("#perm-accept", Input)
            assert not accept.display
            await pilot.press("tab")
            await pilot.pause()
            assert accept.display and accept.has_focus
            await pilot.press("tab")
            await pilot.pause()
            assert not accept.display
            assert app.focused is prompt
            await pilot.press("escape")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "denied"


def test_tab_does_nothing_on_a_row_without_feedback():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(
                app, pilot, tool="Edit", tool_input={"file_path": "a.txt"})
            await pilot.press("down")
            await pilot.pause()
            assert prompt._focused == 1
            await pilot.press("tab")
            await pilot.pause()
            assert not prompt.query_one("#perm-accept", Input).display
            assert not prompt.query_one("#perm-reject", Input).display
            assert app.focused is prompt
            assert not task.done()
            await pilot.press("escape")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "denied"


def test_the_tab_hint_follows_the_focused_row():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot)
            painted = lambda: _plain(_flatten(app))
            assert "tab adds a note" in painted()
            assert "enter to confirm" not in painted()
            await pilot.press("down")
            await pilot.pause()
            assert "tab adds a note" not in painted()
            await pilot.press("down")
            await pilot.pause()
            assert "tab adds a note" in painted()
            await pilot.press("tab")
            await pilot.pause()
            assert "tab adds a note" not in painted()
            await pilot.press("escape")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "denied"


def test_an_empty_rule_row_approves_without_saving():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot)
            await pilot.press("down")
            await pilot.pause()
            prompt.query_one("#perm-rule", Input).value = ""
            await pilot.press("enter")
            await pilot.pause()
            return await task

    choice = asyncio.run(scenario())
    assert choice.value == "approved"
    assert choice.rule is None


def test_accept_feedback_is_carried_on_the_decision():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot)
            await pilot.press("tab")
            await pilot.pause()
            prompt.query_one("#perm-accept", Input).value = "run the tests first"
            await pilot.press("enter")
            await pilot.pause()
            return await task

    choice = asyncio.run(scenario())
    assert choice.value == "approved"
    assert choice.feedback == "run the tests first"


def test_reject_feedback_is_carried_on_the_decision():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot)
            await pilot.press("down")
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            prompt.query_one("#perm-reject", Input).value = "not now"
            await pilot.press("enter")
            await pilot.pause()
            return await task

    choice = asyncio.run(scenario())
    assert choice.value == "denied"
    assert choice.feedback == "not now"


def test_whitespace_only_feedback_is_dropped():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot)
            await pilot.press("tab")
            await pilot.pause()
            prompt.query_one("#perm-accept", Input).value = "   "
            await pilot.press("enter")
            await pilot.pause()
            return await task

    choice = asyncio.run(scenario())
    assert choice.value == "approved"
    assert choice.feedback == ""


def test_chat_keys_are_suppressed_while_an_approval_is_open():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            assert app._session.permission_mode == "default"
            task, prompt = await _asked_prompt(app, pilot)
            assert app.modal_overlay_active
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app._session.permission_mode == "default"
            assert prompt._focused == 0
            assert app.focused is prompt
            await pilot.press("enter")
            await pilot.pause()
            await task
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app._session.permission_mode == "acceptEdits"

    asyncio.run(scenario())


def test_an_open_approval_registers_a_modal_overlay():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            assert app._overlays == set()
            assert not app.modal_overlay_active
            task, _prompt = await _asked_prompt(app, pilot)
            assert "select" in app._overlays
            assert app.modal_overlay_active
            await pilot.press("enter")
            await pilot.pause()
            await task
            assert app._overlays == set()
            assert not app.modal_overlay_active

    asyncio.run(scenario())


def test_the_suggestion_list_is_not_a_modal_overlay():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one("#input", Input).value = "/"
            await pilot.pause()
            assert app._suggest_items
            assert "autocomplete" in app._overlays
            assert not app.modal_overlay_active
            await pilot.press("up")
            await pilot.pause()
            assert app._suggest_selected == len(app._suggest_items) - 1

    asyncio.run(scenario())


def test_approvals_are_asked_one_at_a_time_in_arrival_order():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            first = asyncio.ensure_future(app._ask_permission(
                "Bash", {"command": "ls"}, tool_use_id="a1"))
            second = asyncio.ensure_future(app._ask_permission(
                "Bash", {"command": "pwd"}, tool_use_id="a2"))
            for _ in range(4):
                await pilot.pause()
            assert [a.tool_use_id for a in app._approvals] == ["a1", "a2"]
            assert len(_prompts(app)) == 1
            assert "git" not in _flatten(app)
            assert "ls" in _flatten(app)
            await pilot.press("enter")
            for _ in range(4):
                await pilot.pause()
            assert (await first).value == "approved"
            assert len(_prompts(app)) == 1
            assert "pwd" in _flatten(app)
            await pilot.press("enter")
            for _ in range(4):
                await pilot.pause()
            assert (await second).value == "approved"
            assert app._approvals == []
            assert _prompts(app) == []

    asyncio.run(scenario())


def test_interrupt_clears_every_pending_approval():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            first = asyncio.ensure_future(app._ask_permission(
                "Bash", {"command": "ls"}, tool_use_id="a1"))
            second = asyncio.ensure_future(app._ask_permission(
                "Bash", {"command": "pwd"}, tool_use_id="a2"))
            for _ in range(4):
                await pilot.pause()
            assert len(_prompts(app)) == 1
            await pilot.press("ctrl+c")
            for _ in range(6):
                await pilot.pause()
            assert (await first).value == "denied"
            assert (await second).value == "denied"
            assert app._approvals == []
            assert _prompts(app) == []

    asyncio.run(scenario())


def test_a_teammate_approval_names_the_asking_agent():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, _prompt = await _asked_prompt(app, pilot, agent="watcher")
            painted = _plain(_flatten(app))
            assert "\u00b7 @watcher" in painted
            await pilot.press("enter")
            await pilot.pause()
            await task
            lead, _ = await _asked_prompt(app, pilot, agent="lead")
            assert "@lead" not in _plain(_flatten(app))
            await pilot.press("enter")
            await pilot.pause()
            await lead

    asyncio.run(scenario())


def test_permission_prompt_resolves_through_the_app():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _asked_prompt(app, pilot,
                                               tool_input={"command": "ls"})
            assert prompt is not None
            await pilot.press("enter")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()).value == "approved"


def test_conversation_fills_and_input_sits_at_the_bottom():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            return (app.size.height,
                    app.query_one("#conv").size.height,
                    app.query_one("#footer").size.height,
                    app.query_one(Input).region.y)

    height, conv, footer, input_y = asyncio.run(scenario())
    assert footer == 1
    assert conv == height - 6
    assert input_y == height - 5


def test_bash_collapsible_classification():
    from pyclaw.tui.toolcard import _bash_kinds

    assert _bash_kinds("grep -rn foo .") == {"search"}
    assert _bash_kinds("rg --files") == {"search"}
    assert _bash_kinds("cat a.txt") == {"read"}
    assert _bash_kinds("cat a.txt | wc -l") == {"read"}
    assert _bash_kinds("ls -la") == {"list"}
    assert _bash_kinds("tree") == {"list"}
    assert _bash_kinds("echo hi") == set()
    assert _bash_kinds("ls dir && echo ---") == {"list"}
    assert _bash_kinds("npm test") == {"bash"}
    assert _bash_kinds("grep x a | sort") == {"search", "read"}


def test_tool_collapsible_classification():
    from pyclaw.tui.toolcard import _collapsible_kinds

    assert _collapsible_kinds("Read", {"file_path": "a.py"}) == {"read"}
    assert _collapsible_kinds("Read", {"file_path": "AGENTS.md"}) == {
        "memory_read"}
    assert _collapsible_kinds("Grep", {"pattern": "x"}) == {"search"}
    assert _collapsible_kinds("Glob", {"pattern": "*.py"}) == {"search"}
    assert _collapsible_kinds("LS", {"path": "."}) == {"list"}
    assert _collapsible_kinds("Write", {"file_path": "a.py"}) == set()
    assert _collapsible_kinds("Write", {"file_path": "AGENTS.md"}) == {
        "memory_write"}
    assert _collapsible_kinds("Edit", {"file_path": "a.py"}) == set()
    assert _collapsible_kinds("Bash", {"command": "ls"}) == {"list"}
    assert _collapsible_kinds("create_agent", {"prompt": "x"}) == set()


def test_prompt_has_the_pointer():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            return str(app.query_one("#prompt-pointer").content)

    assert asyncio.run(scenario()) == "\u276f"


def test_up_and_down_walk_the_prompt_history():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            for text in ("first", "second"):
                app.query_one(Input).value = text
                await pilot.press("enter")
                for _ in range(3):
                    await pilot.pause()
            app.query_one(Input).value = "draft"
            await pilot.press("up")
            await pilot.pause()
            newest = app.query_one(Input).value
            await pilot.press("up")
            await pilot.pause()
            older = app.query_one(Input).value
            await pilot.press("down", "down")
            await pilot.pause()
            return newest, older, app.query_one(Input).value

    newest, older, restored = asyncio.run(scenario())
    assert newest == "second"
    assert older == "first"
    assert restored == "draft"


def test_question_mark_opens_the_shortcut_panel():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()
            opened = type(app.screen).__name__
            body = "".join(str(w.content)
                           for w in app.screen.query_one("#help").children)
            assert app.query_one(Input).value == ""
            await pilot.press("escape")
            await pilot.pause()
            return opened, body, type(app.screen).__name__

    opened, body, closed = asyncio.run(scenario())
    assert opened == "HelpScreen"
    for key in ("ctrl+d", "ctrl+c", "ctrl+q", "ctrl+o", "ctrl+t", "escape",
                "shift+tab"):
        assert key in body
    assert "/compact" in body
    assert closed != "HelpScreen"


def test_spinner_verbs_are_clean_words():
    """A busy row reads as work in progress, an idle row as work finished."""

    assert len(SPINNER_VERBS) == len(set(SPINNER_VERBS))
    assert len(SPINNER_VERBS) >= 100
    assert len(PAST_TENSE_VERBS) >= 8
    malformed = [v for v in (*SPINNER_VERBS, *PAST_TENSE_VERBS)
                 if not v[:1].isalpha() or any(ch.isspace() for ch in v)]
    assert malformed == []
    assert [v for v in SPINNER_VERBS if not v.endswith("ing")] == []
    assert [v for v in PAST_TENSE_VERBS if v.endswith("ing")] == []


def test_every_spinner_verb_renders_on_one_line():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            rendered = []
            for verb in SPINNER_VERBS:
                app._turn_verb = verb
                rendered.append(app._spinner_text("\u273b"))
            return rendered

    for verb, text in zip(SPINNER_VERBS, asyncio.run(scenario())):
        assert "\n" not in text
        assert _plain(text) == f"\u273b {verb}\u2026 (0s)"


def test_leading_blank_lines_do_not_orphan_the_bullet():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            block = _TextBlock()
            await app._conv().mount(block)
            block.set_body("\n\nhello there")
            await pilot.pause()
            bullet = str(block.query_one(".text-bullet", Static).content)
            return bullet, block._display(), block._body

    bullet, shown, body = asyncio.run(scenario())
    assert bullet.strip() == "\u23fa"
    assert shown == "hello there"
    assert body == "\n\nhello there"


def test_app_keys_beat_the_input_widget():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            return {key: app.active_bindings[key].binding.action
                    for key in ("ctrl+c", "ctrl+d", "escape", "up", "down")}

    actions = asyncio.run(scenario())
    assert actions["ctrl+c"] == "interrupt"
    assert actions["ctrl+d"] == "quit"
    assert actions["escape"] == "escape"
    assert actions["up"] == "prompt_prev"
    assert actions["down"] == "prompt_next"


def test_escape_closes_modal_screens():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()
            help_open = type(app.screen).__name__
            await pilot.press("escape")
            await pilot.pause()
            help_closed = type(app.screen).__name__
            app.action_toggle_transcript()
            await pilot.pause()
            transcript_open = type(app.screen).__name__
            await pilot.press("escape")
            await pilot.pause()
            return (help_open, help_closed, transcript_open,
                    type(app.screen).__name__)

    help_open, help_closed, transcript_open, transcript_closed = \
        asyncio.run(scenario())
    assert help_open == "HelpScreen" and help_closed != "HelpScreen"
    assert transcript_open == "TranscriptScreen"
    assert transcript_closed != "TranscriptScreen"


def test_permission_screen_keeps_its_own_arrow_keys():
    async def scenario():
        async with PyClawApp(builder=_GateTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "/permissions"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            return (app.screen._selected,
                    app.active_bindings["down"].binding.action)

    selected, action = asyncio.run(scenario())
    assert selected == 0
    assert action == "move_down"


def test_escape_interrupts_a_running_turn():
    team = _TimeoutTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            for _ in range(3):
                await pilot.pause()
            assert app._processing == "hi"
            await pilot.press("escape")
            await pilot.pause()
            return app._processing, _flatten(app)

    processing, flat = asyncio.run(scenario())
    assert processing is None
    assert "Stopped" in flat


def _gradient_cfg(monkeypatch):
    monkeypatch.setattr(
        config, "load",
        lambda: {"banner": {"style": "gradient", "from": "#0084E4",
                            "to": "#F0CC00", "angle": 60.0}})


def test_the_welcome_logo_is_the_painted_wordmark(monkeypatch):
    _gradient_cfg(monkeypatch)

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            return pilot.app.query_one(".logo").render()

    visual = asyncio.run(scenario())
    assert "\u259b" not in visual.plain
    assert visual.plain.splitlines()[0] == banner.WORDMARK[0]
    painted = [span for span in visual.spans if span.style.startswith("rgb(")]
    assert len(painted) == sum(1 for row in banner.WORDMARK
                               for ch in row if ch != " ")
    assert painted[0].style == "rgb(0,132,228)"


def test_the_welcome_logo_stays_until_scrolled_out():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            logo = pilot.app.query_one(".logo")
            await _submit_and_wait(pilot, "hi", 2)
            conv = pilot.app.query_one("#conv")
            assert logo in conv.children
    asyncio.run(scenario())


def test_the_accent_colour_follows_the_launch_palette(monkeypatch):
    _gradient_cfg(monkeypatch)

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            probe = Static("", classes="text-bullet")
            await pilot.app.query_one("#conv").mount(probe)
            await pilot.pause()
            return probe.styles.color.hex, pilot.app.brand

    computed, brand = asyncio.run(scenario())
    assert brand == banner.brand((banner.BLUE, banner.YELLOW, 60.0))
    assert computed == brand


def test_the_user_message_band_follows_the_launch_palette(monkeypatch):
    _gradient_cfg(monkeypatch)

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            block = await pilot.app._append_user("hello")
            await pilot.pause()
            return block.styles.background.hex, pilot.app._triple

    band, triple = asyncio.run(scenario())
    assert band == banner.dimmed(triple, banner.BAND_LIGHTNESS)
    assert band.lower() != "#373737"


def test_the_prompt_frame_is_the_palette_dimmed_to_a_rule(monkeypatch):
    _gradient_cfg(monkeypatch)

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            frame = app.query_one("#prompt")
            return (frame.styles.border_top[1].hex,
                    frame.styles.border_bottom[1].hex, app._triple)

    top, bottom, triple = asyncio.run(scenario())
    assert top == bottom == banner.dimmed(triple, banner.RULE_LIGHTNESS)
    assert max(banner.hex_to_rgb(top)) == banner.RULE_LIGHTNESS


def test_the_prompt_pointer_is_the_accent_and_dims_while_working(monkeypatch):
    _gradient_cfg(monkeypatch)

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            pointer = app.query_one("#prompt-pointer")
            idle = (pointer.styles.color.hex, bool(pointer.styles.text_style))
            app._processing = "hi"
            app._render_status()
            await pilot.pause()
            busy = (pointer.styles.color.hex, bool(pointer.styles.text_style))
            app._processing = None
            app._render_status()
            await pilot.pause()
            return idle, busy, (pointer.styles.color.hex,
                                bool(pointer.styles.text_style))

    idle, busy, back = asyncio.run(scenario())
    brand = banner.brand((banner.BLUE, banner.YELLOW, 60.0))
    assert idle == back == (brand, False)
    assert busy == (brand, True)


def test_the_prompt_frame_takes_the_viewed_teammate_s_colour():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._enter_agent_view(app._agent_by_name("worker"))
            await pilot.pause()
            frame = app.query_one("#prompt")
            pointer = app.query_one("#prompt-pointer")
            border = frame.styles.border_top[1].hex
            colour = pointer.styles.color.hex
            await app._exit_agent_view()
            await pilot.pause()
            return border, colour, app.query_one("#prompt").styles.border_top[1].hex

    border, colour, restored = asyncio.run(scenario())
    assert border == colour
    assert restored != border


def test_the_default_config_picks_a_fresh_palette_per_app():
    first = PyClawApp(builder=_builder)
    second = PyClawApp(builder=_builder)
    assert first.brand != second.brand


def test_agent_output_never_mixes_into_the_leader_transcript():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            flat = _plain(_flatten(app))
            assert "lead answer" in flat
            assert "worker private text" not in flat
            assert "w1" not in app._tools
    asyncio.run(scenario())


def test_ctrl_t_cycles_none_tasks_teammates():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            pane = app.query_one("#tasks")
            assert app._expanded_view == "none"
            assert pane.display is False
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert app._expanded_view == "tasks"
            assert pane.display is True
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert app._expanded_view == "teammates"
            assert pane.display is False
            assert "@worker" in _tree(app)
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert app._expanded_view == "none"
            assert "@worker" not in _tree(app)
    asyncio.run(scenario())


def test_agent_tree_shows_the_leader_and_teammate_stats():
    team = _SwarmTeam()
    team.lead.total_usage = SimpleNamespace(total_tokens=400)
    team.worker.total_usage = SimpleNamespace(total_tokens=2000)

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            await pilot.press("ctrl+t")
            await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            return _tree(app)

    tree = asyncio.run(scenario())
    assert "team-lead" in tree
    assert "400 tokens" in tree
    assert "@worker" in tree
    assert "1 tool call" in tree
    assert "2k tokens" in tree
    assert "Read: a.py" in tree


def test_shift_down_selects_the_row_and_reveals_the_view_hint():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("shift+down")
            await pilot.pause()
            assert app._expanded_view == "teammates"
            assert app._view_selection == "selecting-agent"
            assert app._selected_index == 0
            tree = _tree(app)
            assert "\u276f" in tree
            assert "\u255e\u2550" in tree
            assert "enter opens it" in tree
    asyncio.run(scenario())


def test_enter_opens_the_teammate_view_and_esc_returns():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            team = app._team
            await pilot.pause()
            await _run_turn(pilot)
            team.worker_busy = False
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app._viewing == "worker"
            assert app.query_one("#conv").display is False
            view = app.query_one("#view")
            assert view.display is True
            body = _plain(str(app._view_pane.content))
            assert "Viewing @worker" in body
            assert "esc goes back to the lead" in body
            assert "do the thing" in body
            assert "esc goes back to the lead" in _plain(
                str(app.query_one("#status").content))
            await pilot.press("escape")
            await pilot.pause()
            assert app._viewing is None
            assert app.query_one("#conv").display is True
            assert view.display is False
    asyncio.run(scenario())


def test_input_while_viewing_goes_to_the_viewed_teammate():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            team = app._team
            await pilot.pause()
            await _run_turn(pilot)
            team.worker_busy = False
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app.query_one(Input).value = "please hurry"
            await pilot.press("enter")
            await pilot.pause()
            assert team.submitted == ["please hurry"]
            assert app._processing is None
            assert app.query_one(Input).value == ""
    asyncio.run(scenario())


def test_escape_while_viewing_a_busy_teammate_aborts_only_its_turn():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            team = app._team
            await pilot.pause()
            await _run_turn(pilot)
            assert team.worker_busy is True
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert team.aborted == 1
            assert app._viewing == "worker"
            assert app.query_one("#conv").display is False
    asyncio.run(scenario())


def test_idle_leader_shows_teammates_running_line():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            tree = _tree(app)
            assert "Idle \u00b7 subagents are active" in tree
    asyncio.run(scenario())


def test_k_stops_the_selected_teammate():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            team = app._team
            await pilot.pause()
            await _run_turn(pilot)
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("k")
            await pilot.pause()
            assert team.stopped == ["worker"]
            assert app._selected_index == -1
    asyncio.run(scenario())


class _PlanTeam(_FakeTeam):

    def __init__(self, directory):
        super().__init__()
        self.tasks = TaskList(directory)

    def plan(self, subject, **kw):
        task = self.tasks.create(subject, subject)
        if kw:
            self.tasks.update(task.id, **kw)
        return self.tasks.get(task.id)


def test_the_pane_names_the_next_task_when_there_is_nothing_to_expand(
        tmp_path):
    team = _PlanTeam(tmp_path / 'tasks')
    team.plan('Fix the parser')

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await app._refresh_agents()
            return _tree(app)

    assert asyncio.run(scenario()) == 'Next: Fix the parser'


def test_the_tasks_pane_leads_with_the_plan(tmp_path):
    team = _PlanTeam(tmp_path / 'tasks')
    team.plan('Fix the parser')
    team.plan('Write the tests', status='in_progress', active_form='Testing')

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await app.action_toggle_tasks()
            return str(app._tasks_pane.content)

    text = _plain(asyncio.run(scenario()))
    assert '▪ Write the tests' in text
    assert '▫ Fix the parser' in text
    assert 'Agents' in text


def test_a_claimed_task_s_active_form_is_the_teammate_s_activity(tmp_path):
    team = _SwarmTeam()
    team.tasks = TaskList(tmp_path / 'tasks')
    task = team.tasks.create('Review the diff', 'Review the diff')
    team.tasks.update(task.id, owner='worker', status='in_progress',
                      active_form='Reviewing')

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            team.worker_busy = True
            state = app._state('worker')
            state['last_tool'] = ''
            state['recent'] = []
            return _plain(app._tree_markup(True, False))

    assert 'Reviewing…' in asyncio.run(scenario())


def test_a_teammate_row_counts_the_messages_waiting_on_it():
    team = _SwarmTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            quiet = _plain(app._tree_markup(True, False))
            team.worker.inbox.write("team-lead", "do more")
            team.worker.inbox.write("team-lead", "and more")
            busy = _plain(app._tree_markup(True, False))
            team.worker.inbox.mark_all_read()
            return quiet, busy, _plain(app._tree_markup(True, False))

    quiet, busy, drained = asyncio.run(scenario())
    assert "queued" not in quiet
    assert "2 queued" in busy
    assert "queued" not in drained


def test_the_tree_keeps_track_of_what_a_teammate_has_been_doing():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            return app._state("worker")["recent"]

    assert asyncio.run(scenario()) == [{"read"}]


def test_a_stopped_teammate_reads_stopping_on_its_way_out():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            seen = {}

            async def slow_stop(agent):
                seen["row"] = _plain(app._tree_markup(True, False))
                app._team.stopped.append(agent.name)

            app._team.stop_agent = slow_stop
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("k")
            await pilot.pause()
            return seen

    seen = asyncio.run(scenario())
    assert "Stopping" in seen["row"]


def test_a_teammate_with_a_pending_approval_is_marked_in_the_tree():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            asked = asyncio.create_task(app._ask_permission("Bash", {"command": "ls"},
                                                            agent="worker"))
            await pilot.pause()
            row = _plain(app._tree_markup(True, False))
            asked.cancel()
            return row

    assert "Needs your approval" in asyncio.run(scenario())


def test_the_leader_row_counts_the_lead_not_the_session():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            app._team.lead.total_usage = SimpleNamespace(total_tokens=400)
            await pilot.pause()
            await _run_turn(pilot)
            for line in _plain(app._tree_markup(True, False)).splitlines():
                if "team-lead" in line:
                    return line
            return ""

    line = asyncio.run(scenario())
    assert "400 tokens" in line
    assert "2.4k" not in line


def test_the_tree_stops_viewing_an_agent_that_is_gone():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            team = app._team
            await pilot.pause()
            await _run_turn(pilot)
            await app._enter_agent_view(team.worker)
            assert app._viewing == "worker"
            team.agents.pop("worker@t")
            await app._refresh_agents()
            return app._viewing, app.query_one("#view").display, team.stopped

    viewing, shown, stopped = asyncio.run(scenario())
    assert viewing is None
    assert shown is False
    assert stopped == []


def test_ctrl_shift_o_previews_each_teammate_s_recent_lines():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            team = app._team
            await pilot.pause()
            await _run_turn(pilot)
            team.worker.messages.append(
                {"role": "assistant",
                 "content": [{"type": "text", "text": "reading the tests\nnow the src"}]})
            assert "now the src" not in _tree(app)
            app._expanded_view = 'teammates'
            await app._refresh_agents()
            assert "now the src" not in _tree(app)
            await pilot.press("ctrl+shift+o")
            await pilot.pause()
            return _tree(app)

    tree = asyncio.run(scenario())
    assert "now the src" in tree
    assert "reading the tests" in tree


def test_typing_k_still_reaches_the_input():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.press("k")
            await pilot.pause()
            assert app.query_one(Input).value == "k"
    asyncio.run(scenario())


def test_at_name_sends_a_direct_message_to_the_teammate():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            team = app._team
            await pilot.pause()
            app.query_one(Input).value = "@worker hurry up"
            await pilot.press("enter")
            await pilot.pause()
            assert team.worker.inbox.written == [("lead", "hurry up")]
            assert app._pending_inputs.empty()
            assert "Sent to @worker" in _plain(_flatten(app))
    asyncio.run(scenario())


def test_a_teammate_you_stopped_is_reported_as_stopped_not_done():
    team = _SwarmTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
                "tool": "create_agent",
                "input": {"prompt": "do the thing", "subagent_type": "worker"},
                "tool_use_id": "c1"}))
            await app._handle(RuntimeEvent(
                AGENT_PROGRESS, agent="worker",
                data={"tool_use_id": "c1", "subagent_type": "worker"}))
            app._selected_index = 0
            await app.action_stop_agent()
            await app._handle(RuntimeEvent(AGENT_TURN_FINISHED,
                                           agent="worker"))
            block = app._tools["c1"]
            block.set_result("stopped by user", meta=app._tool_meta.get("c1"))
            return str(block.content)

    content = asyncio.run(scenario())
    assert "Stopped (0 tool calls" in content
    assert "Done (" not in content


def _spawn(app, pilot, uid, **input_):
    data = {'tool': 'create_agent', 'tool_use_id': uid}
    data['input'] = {'prompt': f'job {uid}', 'subagent_type': 'Explore',
                     **input_}
    return app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent='lead', data=data))


def test_two_agents_launched_together_share_one_card():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _spawn(app, pilot, 'a1')
            await _spawn(app, pilot, 'a2')
            await pilot.pause()
            return _plain(app._agent_group.content)

    lines = asyncio.run(scenario()).splitlines()
    assert lines[0].endswith('Running 2 Explore agents… (ctrl+o shows more)')
    assert lines[1:] == ['   \u251c\u2500 Explore(job a1) \u00b7 0 tool calls '
                        '\u00b7 Starting up\u2026',
                        '   \u2514\u2500 Explore(job a2) \u00b7 0 tool calls '
                        '\u00b7 Starting up\u2026']


def test_a_group_row_tracks_its_agent_and_closes_when_it_answers():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _spawn(app, pilot, 'a1')
            await _spawn(app, pilot, 'a2')
            await app._handle(RuntimeEvent(AGENT_PROGRESS, agent='sub-1', data={
                'tool_use_id': 'a1', 'subagent_type': 'Explore'}))
            await app._handle(RuntimeEvent(
                AGENT_PROGRESS, agent='sub-1',
                data={'message': {'role': 'assistant', 'content': [
                    {'type': 'tool_use', 'id': 'g1', 'name': 'Grep',
                     'input': {'pattern': 'p'}}]},
                    'usage': {'prompt_tokens': 900, 'completion_tokens': 100}}))
            await app._handle(RuntimeEvent(
                AGENT_PROGRESS, agent='sub-1',
                data={'message': {'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': 'g1',
                     'content': '3 matches'}]}}))
            await app._handle(RuntimeEvent(
                AGENT_PROGRESS, agent='sub-1',
                data={'message': {'role': 'assistant', 'content': [
                    {'type': 'tool_use', 'id': 'g2', 'name': 'Glob',
                     'input': {'pattern': '*.py'}}]}}))
            await app._handle(RuntimeEvent(
                AGENT_PROGRESS, agent='sub-1',
                data={'message': {'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': 'g2',
                     'content': '4 files'}]}}))
            await pilot.pause()
            live = _plain(app._agent_group.content)
            app._tools['a1'].set_result('the answer')
            app._tools['a2'].set_result('another answer')
            return live, _plain(app._agent_group.content)

    live, done = asyncio.run(scenario())
    assert 'Looking for 2 patterns' in live.splitlines()[1]
    assert '2 tool calls' in live.splitlines()[1]
    assert '1k tokens' in live.splitlines()[1]
    assert done.startswith('\u23fa 2 Explore agents finished')
    assert 'Done' in done.splitlines()[1]


def test_a_teammate_row_stays_live_while_that_teammate_runs():
    """The spawn acknowledgement arrives as a tool_result, but it is not an
    answer: the teammate is still working, so its row must keep reporting
    activity instead of settling on Done."""
    async def scenario():
        async with PyClawApp(builder=_SwarmTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _spawn(app, pilot, 'w1', name='worker')
            await _spawn(app, pilot, 'a2')
            await pilot.pause()
            app._team.record("user", [{"type": "tool_result",
                                       "tool_use_id": "w1",
                                       "content": 'Teammate "worker" spawned '
                                                  'and idle.'}])
            app._tools['a2'].set_result('one-off answer')
            app._sync_tool_states()
            await pilot.pause()
            return _plain(app._agent_group.content)

    lines = asyncio.run(scenario()).splitlines()
    assert 'Running 2' in lines[0]
    assert 'finished' not in lines[0]
    assert 'Done' not in lines[1]
    assert 'Done' in lines[2]


def test_a_teammate_row_closes_once_that_teammate_is_gone():
    async def scenario():
        async with PyClawApp(builder=_SwarmTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _spawn(app, pilot, 'w1', name='worker')
            await _spawn(app, pilot, 'a2')
            await pilot.pause()
            app._team.record("user", [{"type": "tool_result",
                                       "tool_use_id": "w1",
                                       "content": 'Teammate "worker" spawned '
                                                  'and idle.'}])
            app._tools['a2'].set_result('one-off answer')
            app._sync_tool_states()
            await pilot.pause()
            live = _plain(app._agent_group.content)
            app._team.agents.pop("worker@t")
            await app._refresh_agents()
            await pilot.pause()
            return live, _plain(app._agent_group.content)

    live, gone = asyncio.run(scenario())
    assert 'finished' not in live.splitlines()[0]
    assert 'finished' in gone.splitlines()[0]
    assert 'Done' in gone.splitlines()[1]


def test_a_solo_teammate_card_is_not_settled_by_its_spawn_ack():
    async def scenario():
        async with PyClawApp(builder=_SwarmTeam).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _spawn(app, pilot, 'w1', name='worker')
            await pilot.pause()
            app._team.record("user", [{"type": "tool_result",
                                       "tool_use_id": "w1",
                                       "content": 'Teammate "worker" spawned '
                                                  'and idle.'}])
            app._sync_tool_states()
            await pilot.pause()
            card = app._tools['w1']
            live = _plain(card.content)
            app._finish_agent('worker', stopped=True)
            return live, _plain(card.content)

    live, stopped = asyncio.run(scenario())
    assert 'spawned' not in live
    assert 'Done' not in live
    assert 'Stopped' in stopped


def test_a_teammate_spawn_is_labelled_by_its_name():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _spawn(app, pilot, 't1', name='researcher')
            await _spawn(app, pilot, 't2', name='writer')
            await pilot.pause()
            return _plain(app._agent_group.content)

    text = asyncio.run(scenario())
    assert '@researcher(Explore)' in text
    assert '@writer(Explore)' in text
    assert 'Running 2 agents' in text


def test_a_different_card_between_two_spawns_opens_a_second_group():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _spawn(app, pilot, 'a1')
            await _spawn(app, pilot, 'a2')
            first = app._agent_group
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent='lead', data={
                'tool': 'Write', 'input': {'file_path': 'a.py', 'content': 'x'},
                'tool_use_id': 'b1'}))
            await _spawn(app, pilot, 'a3')
            await pilot.pause()
            return first, app._agent_group is not first

    first, reopened = asyncio.run(scenario())
    assert first.active is False
    assert reopened is True
    assert _plain(first.content).startswith(
        '\u23fa Running 2 Explore agents\u2026')


def test_subagent_task_card_reports_done_with_stats():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
                "tool": "create_agent",
                "input": {"prompt": "look around", "subagent_type": "Explore"},
                "tool_use_id": "a1"}))
            await app._handle(RuntimeEvent(AGENT_PROGRESS, agent="sub-1", data={
                "prompt": "look around", "subagent_type": "Explore",
                "tool_use_id": "a1", "started_at": 0.0}))
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="sub-1", data={
                "tool": "Grep", "input": {"pattern": "p"}, "tool_use_id": "g1"}))
            await app._handle(RuntimeEvent(AGENT_PROGRESS, agent="sub-1",
                                           data={"done": True}))
            block = app._tools["a1"]
            block.set_result("the subagent answer",
                             meta=app._tool_meta.get("a1"))
            content = str(block.content)
            assert "[bold]Explore[/]" in content
            assert "Done (1 tool call \u00b7 0 tokens \u00b7" in content
            assert "the subagent answer" not in content
    asyncio.run(scenario())


def test_teammate_spawn_card_has_no_result_line():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
                "tool": "create_agent",
                "input": {"prompt": "watch the build", "name": "watcher"},
                "tool_use_id": "a2"}))
            block = app._tools["a2"]
            block.set_result('Teammate "watcher" spawned and idle.')
            content = str(block.content)
            assert "[bold]Agent[/]" in content
            assert "spawned and idle" not in content
    asyncio.run(scenario())


def test_teammate_message_renders_as_a_byline():
    from pyclaw.tui.formatting import _user_markup
    raw = ('<teammate_message teammate_id="worker">'
           'found the bug</teammate_message>')
    assert _user_markup(raw) == "[bold]@worker[/]\u276f found the bug"
    assert _user_markup(raw, color_for=lambda name: '#FF6B80') == (
        "[#FF6B80][bold]@worker[/][/]\u276f found the bug")
    idle = ('<teammate_message teammate_id="worker">'
            '{"type": "idle_notification", "from": "worker"}'
            '</teammate_message>')
    assert _user_markup(idle) == ""
    assert _user_markup("plain message") == "plain message"


def test_enter_while_selecting_confirms_instead_of_submitting():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _run_turn(pilot)
            await pilot.press("shift+down")
            await pilot.pause()
            app.query_one(Input).value = "not a prompt"
            await pilot.press("enter")
            await pilot.pause()
            assert app._pending_inputs.empty()
            assert app._processing is None
            assert app._view_selection == "selecting-agent"
    asyncio.run(scenario())


def test_escape_leaves_selection_without_cancelling_the_leader():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.press("shift+down")
            await pilot.pause()
            assert app._view_selection == "selecting-agent"
            await pilot.press("escape")
            await pilot.pause()
            assert app._view_selection == "none"
            assert app._selected_index == -1
    asyncio.run(scenario())


def test_queued_messages_are_hidden_while_viewing_a_teammate():
    async def scenario():
        async with PyClawApp(builder=lambda: _SwarmTeam()).run_test() as pilot:
            app = pilot.app
            team = app._team
            await pilot.pause()
            await _run_turn(pilot)
            team.worker_busy = False
            app._peek_queue = lambda: ["queued while busy"]
            await app._render_queued()
            await pilot.pause()
            assert app._queued is not None
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("shift+down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app._viewing == "worker"
            assert app._queued is None
            assert "Press up to edit queued messages" not in str(
                app.query_one(Input).placeholder)
    asyncio.run(scenario())


def test_direct_send_message_cards_stay_off_the_transcript():

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
                "tool": "send_message",
                "input": {"to": "worker", "message": "please continue"},
                "tool_use_id": "s1"}))
            await pilot.pause()
            block = app._tools["s1"]
            assert block.display is False
            block.set_result("Sent to worker's inbox")
            painted = "".join(str(w.content)
                              for w in app.query_one("#conv").children
                              if w.display)
            assert "Message delivered" not in _plain(painted)
    asyncio.run(scenario())


def test_render_failure_is_logged_with_the_event_and_a_traceback(caplog):

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()

            def boom():
                raise ValueError("kaboom")

            app._render_tasks = boom
            with caplog.at_level(logging.ERROR, logger="pyclaw.tui.app"):
                app._queue.put_nowait(RuntimeEvent(
                    AGENT_TEXT, agent="lead", data={"delta": "hi"}))
                for _ in range(10):
                    await pilot.pause()
                    await asyncio.sleep(0.02)
            assert app._render_tasks is boom
    asyncio.run(scenario())
    records = [r for r in caplog.records if r.name == "pyclaw.tui.app"]
    assert any("render failed" in r.getMessage() for r in records)
    failed = [r for r in records if "render failed" in r.getMessage()]
    assert "agent.text" in failed[0].getMessage()
    assert failed[0].exc_info is not None
    assert "kaboom" in caplog.text


def test_every_runtime_event_is_logged_for_postmortem(caplog):

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            with caplog.at_level(logging.DEBUG, logger="pyclaw.tui.app"):
                await app._handle(RuntimeEvent(
                    AGENT_TEXT, agent="worker", data={"delta": "hidden"}))
    asyncio.run(scenario())
    assert any("agent.text" in r.getMessage() and "worker" in r.getMessage()
               for r in caplog.records if r.name == "pyclaw.tui.app")


def test_subagent_spawn_and_finish_are_logged(caplog):

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            with caplog.at_level(logging.INFO, logger="pyclaw.tui.app"):
                await app._handle(RuntimeEvent(AGENT_PROGRESS, agent="sub-1", data={
                    "prompt": "go", "subagent_type": "Explore",
                    "tool_use_id": "a9", "started_at": 0.0}))
                app._tools["a9"] = app._tools.get("a9") or _StubBlock()
                await app._handle(RuntimeEvent(AGENT_PROGRESS, agent="sub-1",
                                               data={"done": True}))
    asyncio.run(scenario())
    messages = [r.getMessage() for r in caplog.records if r.name == "pyclaw.tui.app"]
    assert any("sub-agent sub-1 started" in m for m in messages)
    assert any("sub-agent sub-1 finished" in m for m in messages)


def _logo_text(app) -> str:
    return _plain(str(app.query_one(".logo").content))


def test_the_logo_greets_you_under_the_wordmark(monkeypatch):
    monkeypatch.setattr(welcome, "settings",
                        lambda: {"seen": 4, "lastVersion": "0.0.5"})

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            return _logo_text(pilot.app)

    text = asyncio.run(scenario())
    assert "Good to see you." in text
    assert "PyClaw v" in text
    assert "m \u00b7 p" in text
    assert str(Path.cwd()) in text or "~/" in text


def test_the_logo_feeds_follow_the_welcome_state(monkeypatch):
    monkeypatch.setattr(welcome, "recent_activity", lambda **kwargs: [
        {"text": "fix the PDF outline jump", "timestamp": "2 hours ago"}])
    monkeypatch.setattr(welcome, "settings",
                        lambda: {"seen": 0, "lastVersion": "0.0.5"})
    monkeypatch.setattr(welcome, "onboarding_steps", lambda cwd: [
        {"text": "Ask PyClaw to create a new app", "complete": False,
         "enabled": True}])

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            return _logo_text(pilot.app)

    text = asyncio.run(scenario())
    assert "Getting oriented" in text
    assert "Ask PyClaw to create a new app" in text
    assert "Last sessions" in text
    assert "2 hours ago" in text
    assert "fix the PDF outline jump" in text
    assert "/resume for more" in text


def test_the_logo_drops_the_feeds_once_onboarding_is_settled(monkeypatch):
    monkeypatch.setattr(welcome, "settings",
                        lambda: {"seen": 4, "lastVersion": "0.0.5"})
    monkeypatch.setattr(welcome, "recent_activity", lambda **kwargs: [
        {"text": "should not show", "timestamp": "2 hours ago"}])

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            return _logo_text(pilot.app)

    text = asyncio.run(scenario())
    assert "Good to see you." in text
    assert "Getting oriented" not in text
    assert "Last sessions" not in text
    assert "should not show" not in text


def test_the_greeting_records_the_version_and_the_onboarding_seen_count(
        monkeypatch):
    seen = []
    monkeypatch.setattr(welcome, "feeds_for", lambda **kwargs: ([], True))
    monkeypatch.setattr(welcome, "remember",
                        lambda **kwargs: seen.append(kwargs))

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()

    asyncio.run(scenario())
    assert seen and seen[0]["seen_onboarding"] is True
    assert seen[0]["version"]


async def _mount_agent_card(app, *, tool_use_id="a1", subagent_type="Explore",
                            prompt="look around"):
    await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
        "tool": "create_agent",
        "input": {"prompt": prompt, "subagent_type": subagent_type},
        "tool_use_id": tool_use_id}))
    await app._handle(RuntimeEvent(AGENT_PROGRESS, agent="sub-1", data={
        "prompt": prompt, "subagent_type": subagent_type,
        "tool_use_id": tool_use_id, "started_at": 0.0}))


async def _sub_agent_says(app, content, *, agent="sub-1", usage=None):
    data = {"message": {"role": "assistant", "content": content}}
    if usage is not None:
        data["usage"] = usage
    await app._handle(RuntimeEvent(AGENT_PROGRESS, agent=agent, data=data))


def _card(block) -> str:
    return _plain(str(block.content))


def test_a_running_sub_agent_shows_what_it_is_doing_under_its_card():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _mount_agent_card(app)
            assert "\u23bf  Starting up\u2026" in _card(app._tools["a1"])
            await _sub_agent_says(app, [
                {"type": "text", "text": "Reading the config first"},
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": "pyclaw/tui.py"}},
            ])
            text = _card(app._tools["a1"])
            assert "Reading the config first" in text
            assert "Read(pyclaw/tui.py)" in text
            assert "Starting up" not in text
    asyncio.run(scenario())


def test_the_sub_agent_trail_keeps_the_last_three_messages():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _mount_agent_card(app)
            for index in range(5):
                await _sub_agent_says(app, [
                    {"type": "tool_use", "id": f"t{index}", "name": "Read",
                     "input": {"file_path": f"step-{index}.py"}},
                ])
            text = _card(app._tools["a1"])
            assert "step-4.py" in text
            assert "step-2.py" in text
            assert "step-1.py" not in text
            assert "step-0.py" not in text
            assert "+2 tool calls (ctrl+o shows more)" in text
    asyncio.run(scenario())


def test_the_sub_agent_trail_shows_narration_and_every_tool_call():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _mount_agent_card(app)
            await _sub_agent_says(app, [
                {"type": "text", "text": "Checking the tests\nand the fixtures"},
                {"type": "tool_use", "id": "g1", "name": "Grep",
                 "input": {"pattern": "test_", "path": "tests"}},
                {"type": "tool_use", "id": "b1", "name": "Bash",
                 "input": {"command": "git log --oneline -3"}},
            ])
            text = _card(app._tools["a1"])
            assert "Checking the tests and the fixtures" in text
            assert 'Search(pattern: "test_", path: "tests")' in text
            assert "Bash(git log --oneline -3)" in text
    asyncio.run(scenario())


def test_tool_results_never_enter_the_sub_agent_trail():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _mount_agent_card(app)
            await _sub_agent_says(app, [
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": "pyclaw/tui.py"}},
            ])
            before = _card(app._tools["a1"])
            await app._handle(RuntimeEvent(AGENT_PROGRESS, agent="sub-1", data={
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1",
                     "content": "SHOULD NOT SURFACE"}]}}))
            assert _card(app._tools["a1"]) == before
            assert "SHOULD NOT SURFACE" not in before
    asyncio.run(scenario())


def test_the_done_line_replaces_the_trail_when_the_sub_agent_finishes():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _mount_agent_card(app)
            await _sub_agent_says(app, [
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": "pyclaw/tui.py"}},
            ])
            assert "Read(pyclaw/tui.py)" in _card(app._tools["a1"])
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="sub-1", data={
                "tool": "Read", "input": {"file_path": "pyclaw/tui.py"},
                "tool_use_id": "t1"}))
            await app._handle(RuntimeEvent(AGENT_PROGRESS, agent="sub-1",
                                           data={"done": True}))
            block = app._tools["a1"]
            text = _card(block)
            assert "Read(pyclaw/tui.py)" not in text
            assert "Starting up" not in text
            block.set_result("the subagent answer",
                             meta=app._tool_meta.get("a1"))
            assert "Done (1 tool call \u00b7 0 tokens \u00b7" in _card(block)
    asyncio.run(scenario())


def test_the_transcript_shows_every_trail_row_not_just_the_last_three():
    from pyclaw.tui.screens import TranscriptScreen

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _mount_agent_card(app)
            for index in range(5):
                await _sub_agent_says(app, [
                    {"type": "tool_use", "id": f"t{index}", "name": "Read",
                     "input": {"file_path": f"step-{index}.py"}},
                ])
            entry = _plain(TranscriptScreen._tool_entry(app._tools["a1"]))
            for index in range(5):
                assert f"step-{index}.py" in entry
            assert "more tool calls" not in entry
    asyncio.run(scenario())


def test_the_teammate_spawn_card_never_trails_after_it_resolves():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
                "tool": "create_agent",
                "input": {"prompt": "watch the build", "name": "watcher"},
                "tool_use_id": "a2"}))
            block = app._tools["a2"]
            block.set_result('Teammate "watcher" spawned and idle.')
            text = _card(block)
            assert "spawned and idle" not in text
            assert "Starting up" not in text
    asyncio.run(scenario())


async def _pending_approval(app, pilot, tool_use_id="a1", tool="create_agent",
                            agent=None):
    """Start a real approval request and hand back its task + prompt."""
    task = asyncio.ensure_future(
        app._ask_permission(tool, {"prompt": "go"},
                            tool_use_id=tool_use_id, agent=agent))
    for _ in range(4):
        await pilot.pause()
    prompts = _prompts(app)
    return task, prompts[0] if prompts else None


def test_a_tool_call_waiting_on_approval_says_so():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
                "tool": "create_agent",
                "input": {"prompt": "go", "subagent_type": "general-purpose"},
                "tool_use_id": "a1"}))
            assert "Starting up" in _card(app._tools["a1"])
            task, prompt = await _pending_approval(app, pilot)
            # While an approval is being answered the card says so instead of
            # sitting on the start-up line.
            assert "\u23bf  Needs your approval\u2026" in _card(app._tools["a1"])
            assert "Starting up" not in _card(app._tools["a1"])
            await pilot.press("enter")
            await pilot.pause()
            await task
            assert "Needs your approval" not in _card(app._tools["a1"])
    asyncio.run(scenario())


def test_interrupting_while_an_approval_is_pending_settles_the_call():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
                "tool": "create_agent",
                "input": {"prompt": "go", "subagent_type": "general-purpose"},
                "tool_use_id": "a1"}))
            task, prompt = await _pending_approval(app, pilot)
            assert prompt is not None
            await pilot.press("ctrl+c")
            await pilot.pause()
            for _ in range(6):
                await pilot.pause()
            assert (await task).value == "denied"
            assert app._approvals == []
            assert _prompts(app) == []
            text = _card(app._tools["a1"])
            assert "Agent(go)" in text
            assert "\u23bf  Stopped \u00b7 tell PyClaw what to do instead" in text
            painted = _plain("".join(str(w.content)
                                     for w in app.query_one("#conv").children))
            assert painted.count("tell PyClaw what to do instead") == 1
    asyncio.run(scenario())


def test_an_approval_cannot_outlive_the_turn_it_belongs_to():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TOOL_CALL, agent="lead", data={
                "tool": "create_agent",
                "input": {"prompt": "go", "subagent_type": "general-purpose"},
                "tool_use_id": "a1"}))
            task, _prompt = await _pending_approval(app, pilot)
            await pilot.press("ctrl+c")
            for _ in range(4):
                await pilot.pause()
            assert (await task).value == "denied"
            # Nothing is left to approve, so the next Enter is an ordinary
            # submit instead of a stray approval of a dead turn.
            app.query_one(Input).value = "still here"
            await pilot.press("enter")
            await pilot.pause()
            assert app.query_one(Input).value == ""
            assert app._approvals == []
    asyncio.run(scenario())


def test_escape_still_denies_a_prompt_that_is_being_answered():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task, prompt = await _pending_approval(app, pilot)
            assert prompt.has_focus
            await pilot.press("escape")
            await pilot.pause()
            assert (await task).value == "denied"
            assert _prompts(app) == []
    asyncio.run(scenario())


def test_a_killed_call_is_not_narrated_twice():
    """The card owns the interrupt; _converse must not echo chatchat's
    tool-error-as-answer on top of it."""
    async def run(interrupted):
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            calls = []

            async def chat(text):
                return 'Error: hook blocked tool "create_agent": nope.'

            async def idle():
                return True

            app._session.chat = chat
            app._wait_session_idle = idle
            app._begin_turn()
            app._team.record('user', 'go')
            app._interrupted_call = interrupted
            orig = app._append_block

            async def spy(text):
                calls.append(_plain(str(text)))
                return await orig(text)

            app._append_block = spy
            await app._converse('go')
            return calls

    assert asyncio.run(run(True)) == []
    assert asyncio.run(run(False)) == [
        'Error: hook blocked tool "create_agent": nope.']


def test_a_late_timer_refresh_does_not_paint_into_a_torn_down_dom():
    """The readout interval and the status line debounce can both fire once
    the widgets are already gone, so neither may go looking for them."""
    async def start():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            return pilot.app

    app = asyncio.run(start())
    looked_up = []
    app.query_one = lambda *args, **kwargs: looked_up.append(args)
    app._render_readouts()
    asyncio.run(app._refresh_statusline())
    assert looked_up == []


def test_a_row_drops_its_tail_instead_of_being_cut_through_a_number():
    """Each readout row is one line tall, so what has no room is dropped from
    the right rather than clipped mid-digit."""
    narrow = _row1(size=(60, 40))
    assert len(narrow) <= 58
    assert "msg" not in narrow
    assert narrow.startswith("m \u00b7 thinking off")


def test_the_second_row_holds_the_directory_then_git():
    row = _row2(git="# branch.head main\n1 N... pyclaw/tui.py\n")
    assert row.endswith(' \u00b7 main \u00b11')
    assert len(row.split(' \u00b7 ')) == 2


def test_the_second_row_leaves_the_branch_out_outside_a_repository():
    assert "\u00b1" not in _row2()


def test_the_transcript_keeps_each_grouped_agent_s_own_steps():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _spawn(app, pilot, 'a1')
            await _spawn(app, pilot, 'a2')
            await app._handle(RuntimeEvent(AGENT_PROGRESS, agent='sub-1', data={
                'tool_use_id': 'a1', 'subagent_type': 'Explore'}))
            await app._handle(RuntimeEvent(
                AGENT_PROGRESS, agent='sub-1',
                data={'message': {'role': 'assistant', 'content': [
                    {'type': 'tool_use', 'id': 'g1', 'name': 'Grep',
                     'input': {'pattern': 'bug'}}]}}))
            app.action_toggle_transcript()
            await pilot.pause()
            text = '\n'.join(app.screen._entries())
            app.pop_screen()
            return _plain(text)

    text = asyncio.run(scenario())
    assert 'Explore(job a1)' in text
    assert 'Explore(job a2)' in text
    assert 'Search(pattern: \"bug\")' in text


def test_the_tasks_pane_lists_a_background_shell():
    async def scenario():
        task_id = background.spawn('/tmp', 'sleep 2')
        try:
            async with PyClawApp(builder=_builder).run_test() as pilot:
                app = pilot.app
                await pilot.pause()
                await app.action_toggle_tasks()
                return task_id, _plain(str(app._tasks_pane.content))
        finally:
            background.cleanup_background_tasks()

    task_id, text = asyncio.run(scenario())
    assert 'Background shells 1' in text
    assert task_id in text
    assert 'sleep 2' in text
    assert 'running' in text


def test_a_group_card_points_at_the_transcript_for_the_detail():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _spawn(app, pilot, 'a1')
            await _spawn(app, pilot, 'a2')
            await pilot.pause()
            return _plain(app._agent_group.content)

    assert '(ctrl+o shows more)' in asyncio.run(scenario()).splitlines()[0]


class _RewindHistory:

    def diff_stats(self, mark):
        return {'files': ['a.py'], 'insertions': 1, 'deletions': 2}


class _RewindTeam(_FakeTeam):

    def __init__(self):
        super().__init__()
        self.file_history = _RewindHistory()
        self.rewound = []
        self.record("user", "set up the parser")
        self.record("assistant", "done")
        self.record("user", "fix the tests")
        self.record("assistant", "done again")

    def turns(self):
        return [(0, "set up the parser"), (2, "fix the tests")]

    def rewind(self, mark, *, code=True, conversation=True):
        self.rewound.append((mark, code, conversation))
        if conversation:
            self._messages = self._messages[:mark]
        return {'files': ['a.py'] if code else [],
                'messages': 2 if conversation else 0}


async def _open_rewind(pilot):
    from pyclaw.agents import Session
    with mock.patch.object(Session, 'save_transcript', lambda self: None):
        await _submit_and_wait(pilot, '/rewind')
        return pilot.app.screen


def _rewind_body(screen) -> str:
    return _plain(str(screen.query_one('#rw-body', Static).content))


def test_the_rewind_picker_lists_turns_then_restore_modes():
    async def scenario():
        async with PyClawApp(builder=_RewindTeam, resume=True,
                             session_id='rewind-both').run_test(
                size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_rewind(pilot)
            turns = _rewind_body(screen)
            await pilot.press("enter")
            modes = _rewind_body(pilot.app.screen)
            await pilot.press("enter")
            await pilot.pause()
            conversation = ''.join(str(widget.content)
                                   for widget in app._conv().query(Static)
                                   if hasattr(widget, 'content'))
            return turns, modes, app._team.rewound, app.screen, conversation

    turns, modes, rewound, final, conversation = asyncio.run(scenario())
    assert 'set up the parser' in conversation
    assert '1. set up the parser' in turns
    assert '2. fix the tests' in turns
    assert '1 file' in turns
    assert 'Restore code and conversation' in modes
    assert 'Never mind' in modes
    assert rewound == [(2, True, True)]
    assert type(final).__name__ != 'RewindScreen'
    assert 'fix the tests' not in conversation


def test_the_rewind_picker_can_restore_only_the_files():
    async def scenario():
        async with PyClawApp(builder=_RewindTeam, resume=True,
                             session_id='rewind-code').run_test(
                size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await _open_rewind(pilot)
            await pilot.press("enter")
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            conversation = ''.join(str(widget.content)
                                   for widget in app._conv().query(Static)
                                   if hasattr(widget, 'content'))
            return app._team.rewound, app._team.transcript(), conversation

    rewound, transcript, conversation = asyncio.run(scenario())
    assert rewound == [(2, True, False)]
    assert len(transcript) == 4
    assert 'fix the tests' in conversation


def test_the_rewind_picker_can_be_backed_out_of():
    async def scenario():
        async with PyClawApp(builder=_RewindTeam).run_test(size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await _open_rewind(pilot)
            await pilot.press("enter")
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            return app._team.rewound, app._team.transcript()

    rewound, transcript = asyncio.run(scenario())
    assert rewound == []
    assert len(transcript) == 4


def test_the_rewind_picker_waits_for_a_running_turn():
    async def scenario():
        async with PyClawApp(builder=_RewindTeam).run_test(size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            app._processing = 'set up the parser'
            await _submit_and_wait(pilot, '/rewind')
            await pilot.pause()
            note = ''.join(str(widget.content)
                           for widget in app._conv().query(Static)
                           if hasattr(widget, 'content'))
            return app.screen, app._team.rewound, note

    screen, rewound, note = asyncio.run(scenario())
    assert screen.__class__.__name__ != 'RewindScreen'
    assert rewound == []
    assert 'still working' in _plain(note)


def _fake_shells():
    return mock.patch.object(
        background, 'snapshot',
        lambda: [{'id': 'b1', 'command': 'npm run dev', 'seconds': 12,
                  'exit': None, 'killed': False}])


def _task_body(app) -> str:
    return _plain(str(app.screen.query_one('#tk-body', Static).content))


def test_the_task_panel_lists_live_work_and_stops_the_selected_row():
    async def scenario():
        async with PyClawApp(builder=_SwarmTeam).run_test(size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            with _fake_shells(), mock.patch.object(background, 'stop',
                                                   lambda task_id: None):
                await _submit_and_wait(pilot, '/tasks')
                body = _task_body(app)
                await pilot.press('x')
                await pilot.pause()
                return body, list(app._team.stopped)

    body, stopped = asyncio.run(scenario())
    assert '@worker' in body
    assert 'npm run dev' in body
    assert 'running 12s' in body
    assert stopped == ['worker']


def test_the_task_panel_can_stop_everything_at_once():
    async def scenario():
        async with PyClawApp(builder=_SwarmTeam).run_test(size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            killed = []
            with _fake_shells(), mock.patch.object(
                    background, 'stop', lambda task_id: killed.append(task_id)):
                await _submit_and_wait(pilot, '/bashes')
                await pilot.press('a')
                await pilot.pause()
                return killed, list(app._team.stopped)

    killed, stopped = asyncio.run(scenario())
    assert killed == ['b1']
    assert stopped == ['worker']


def test_the_task_panel_opens_the_view_of_a_selected_teammate():
    async def scenario():
        async with PyClawApp(builder=_SwarmTeam).run_test(size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            with _fake_shells(), mock.patch.object(background, 'stop',
                                                   lambda task_id: None):
                await _submit_and_wait(pilot, '/tasks')
                await pilot.press('enter')
                await pilot.pause()
                return app._viewing, type(app.screen).__name__

    viewing, screen = asyncio.run(scenario())
    assert viewing == 'worker'
    assert screen != 'TasksScreen'


def test_the_task_panel_says_so_when_nothing_is_running():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            with mock.patch.object(background, 'snapshot', lambda: []):
                await _submit_and_wait(pilot, '/tasks')
                await pilot.pause()
                note = ''.join(_plain(str(widget.content))
                               for widget in app._conv().query(Static)
                               if hasattr(widget, 'content'))
                return type(app.screen).__name__, note

    screen, note = asyncio.run(scenario())
    assert screen != 'TasksScreen'
    assert 'No background tasks are running' in note


def test_enter_does_not_run_a_command_the_user_did_not_type():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = '/tsks'
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            texts = [_plain(str(widget.content))
                     for widget in app._conv().query(Static)
                     if hasattr(widget, 'content')]
            return texts[-1] if texts else '', type(app.screen).__name__

    note, screen = asyncio.run(scenario())
    assert 'Unknown command' in note
    assert screen != 'TasksScreen'


def _hook_events_app(**kw):
    return PyClawApp(builder=_builder, **kw)


def test_hook_activity_reaches_the_transcript_only_when_asked():
    from chatchat.hooks import events

    async def scenario():
        async with _hook_events_app(hook_events=True).run_test(
                size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            events.emit_response('h1', 'npm test', 'PreToolUse',
                                 outcome='success')
            for _ in range(3):
                await pilot.pause()
            text = ''.join(_plain(str(widget.content))
                           for widget in app._conv().query(Static)
                           if hasattr(widget, 'content'))
            unreg = app._unreg_hooks
            unreg()
            return text

    text = asyncio.run(scenario())
    assert 'PreToolUse' in text and 'npm test' in text


def test_hook_activity_stays_out_of_the_transcript_by_default():
    from chatchat.hooks import events

    async def scenario():
        async with _hook_events_app().run_test(size=(100, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            registered = app._unreg_hooks
            events.emit_response('h1', 'npm test', 'PreToolUse',
                                 outcome='success')
            for _ in range(3):
                await pilot.pause()
            text = ''.join(_plain(str(widget.content))
                           for widget in app._conv().query(Static)
                           if hasattr(widget, 'content'))
            return registered, text

    registered, text = asyncio.run(scenario())
    assert registered is None
    assert 'npm test' not in text


def _question(multi=False):
    return {'question': 'Which shape?', 'header': 'shape',
            'options': [{'label': 'round', 'description': 'a circle'},
                        {'label': 'square'}],
            'multiSelect': multi}


async def _type(pilot, text):
    for char in text:
        await pilot.press("space" if char == " " else char)
    await pilot.pause()


async def _ask_on_screen(app, pilot, questions):
    from pyclaw.tui.approval import _QuestionPrompt
    task = asyncio.create_task(app._ask_questions('lead', questions))
    for _ in range(3):
        await pilot.pause()
    card = next(widget for widget in app._conv().query(_QuestionPrompt))
    return task, card


def test_the_question_card_asks_and_answers_one_question():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(90, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            task, card = await _ask_on_screen(app, pilot, [_question()])
            text = _plain(str(card.query_one("#q-body", Static).content))
            await pilot.press("down")
            await pilot.press("enter")
            for _ in range(3):
                await pilot.pause()
            return text, await task

    text, answers = asyncio.run(scenario())
    assert 'Which shape?' in text and 'square' in text
    assert answers == ['square']


def test_the_question_card_can_take_several_options():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(90, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            task, card = await _ask_on_screen(app, pilot,
                                              [_question(multi=True)])
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(3):
                await pilot.pause()
            return await task

    assert asyncio.run(scenario()) == ['round, square']


def test_the_question_card_lets_the_answer_be_typed():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(90, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            task, card = await _ask_on_screen(app, pilot, [_question()])
            await pilot.press("tab")
            await pilot.pause()
            await _type(pilot, "a hexagon")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(4):
                await pilot.pause()
            return await task

    assert asyncio.run(scenario()) == ['a hexagon']


def test_backing_out_of_a_question_leaves_it_unanswered():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(90, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            task, card = await _ask_on_screen(app, pilot, [_question()])
            await pilot.press("escape")
            for _ in range(4):
                await pilot.pause()
            return await task

    assert asyncio.run(scenario()) == ['no answer']


def test_the_question_card_keeps_the_prompt_history_out_of_its_way():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test(size=(90, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            app._history.append('an earlier message')
            task, card = await _ask_on_screen(app, pilot, [_question()])
            await pilot.press("up")
            await pilot.pause()
            focused = card._focused
            typed = app.query_one("#input", Input).value
            await pilot.press("enter")
            for _ in range(3):
                await pilot.pause()
            return focused, typed, await task

    focused, typed, answers = asyncio.run(scenario())
    assert focused == 1
    assert typed == ''
    assert answers == ['square']


def test_the_session_can_answer_a_model_question():
    async def scenario():
        from chatchat.core.tools import ask_user
        async with PyClawApp(builder=_builder).run_test(size=(90, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            assert app._session._team.ask_user is not None
            asked = asyncio.create_task(ask_user(
                app._session._team, app._session._team.lead,
                {'questions': [_question()]}))
            for _ in range(4):
                await pilot.pause()
            await pilot.press("enter")
            for _ in range(4):
                await pilot.pause()
            return await asked

    assert 'Which shape?: round' in asyncio.run(scenario())
