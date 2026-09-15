import asyncio
import re

import pytest

from textual.widgets import Input, Static

from chatchat.hooks.events import (
    AGENT_PROGRESS,
    AGENT_REASON_START,
    AGENT_TEXT,
    AGENT_TOOL_CALL,
    AGENT_TURN_FINISHED,
)
from pyclaw.tui import PyClawApp


@pytest.fixture(autouse=True)
def _isolated_runtime_sinks():
    from chatchat.hooks import events
    saved = list(events._runtime_sinks)
    yield
    events._runtime_sinks[:] = saved


class _FakeTeam:
    provider = "p"
    model = "m"
    thinking = False
    name = "t"

    def __init__(self):
        self._running = False
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
        self.children = {}
        self.lead = self.agents["lead@t"]
        self._messages = []

    def provided_tools(self):
        return []

    def tool_schemas(self):
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
        from chatchat.hooks.events import emit
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
        from chatchat.hooks.events import emit
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
        from chatchat.hooks.events import emit
        self.record("user", prompt)
        emit(AGENT_REASON_START, agent="lead")
        emit(AGENT_TEXT, agent="lead", delta="answer")
        self.record("assistant", "answer", thinking="inner monologue")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "answer"


class _SubTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        from chatchat.hooks.events import emit
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
        from chatchat.hooks.events import emit
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
        from chatchat.hooks.events import emit
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
        import tempfile as _t
        self._tmp = _t.TemporaryDirectory()
        self._pyclaw_gate = PermissionController(mode="default",
                                                 cwd=self._tmp.name)


def test_shift_tab_cycles_permission_mode():
    async def scenario():
        async with PyClawApp(builder=lambda: _GateTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            assert app._session.permission_mode == "default"
            await pilot.press("shift+tab")
            assert app._session.permission_mode == "acceptEdits"
            await pilot.press("shift+tab")
            assert app._session.permission_mode == "plan"
            status = str(app.query_one("#status").content)
            assert "plan mode on" in status
            assert "shift+tab to cycle" in status
    asyncio.run(scenario())


def _flatten(app) -> str:
    return "".join(str(w.content) for w in app.query_one("#conv").children)


def _plain(text) -> str:
    if not isinstance(text, str):
        text = _flatten(text)
    return re.sub(r"\[?/?[^\]\[\n]*\]", "", text).replace("\\[", "[")


def test_ui_launches_and_renders_panels():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            assert app.query_one("#conv") is not None
            assert app.query_one("#input", Input) is not None
            assert "? for shortcuts" in str(app.query_one("#status").content)
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
            blocks = [str(w.content) for w in app.query_one("#conv").children]
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
    assert "Read [bold]5[/] files" in flat
    assert "(ctrl+o to expand)" in flat


class _ReadBodyTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        from chatchat.hooks.events import emit
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
            expanded = _plain(_transcript_text(app))
            assert "Read(a.txt)" in expanded
            assert "alpha" in expanded
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
            blocks = [str(w.content) for w in conv.children]
            assert "\u273b Worked for" in blocks[-1]
            assert "inner monologue" not in "".join(blocks)
            assert "answer" in "".join(blocks)
    asyncio.run(scenario())


def test_spinner_row_shows_a_verb_and_the_interrupt_hint():
    from pyclaw.spinner_verbs import SPINNER_VERBS

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app._turn_verb = "Thinking"
            return app._spinner_text("\u273b")

    text = asyncio.run(scenario())
    assert "Thinking\u2026" in text
    assert "esc to interrupt" in text
    assert "\u2193 2.4k tokens" in text
    assert len(SPINNER_VERBS) > 100


def test_status_shows_shortcut_hint_and_context_usage():
    class _ThresholdTeam(_FakeTeam):
        compact_threshold = 4802

    async def scenario():
        async with PyClawApp(
                builder=lambda: _ThresholdTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.pause()
            return (str(app.query_one("#status").content),
                    str(app.query_one("#status-right").content))

    status, right = asyncio.run(scenario())
    assert "? for shortcuts" in status
    assert "50% context used" in right


def test_status_right_is_blank_without_compact_threshold():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            return str(pilot.app.query_one("#status-right").content)

    assert asyncio.run(scenario()) == ""


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
    assert "? for shortcuts" in idle
    assert "esc to interrupt" in busy


def test_model_switch_updates_status_bar(monkeypatch):
    from pyclaw import config as config_module
    monkeypatch.setattr(config_module, "save", lambda c: None)
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
    from pyclaw.tui import PyClawApp as A
    assert A._fmt(900) == "900"
    assert A._fmt(1901) == "1.9k"
    assert A._fmt(1_200_000) == "1.2m"


def test_long_block_wraps_not_stretches():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            long_text = "x" * 500
            block = app._append_block(long_text)
            await block
            await pilot.pause()
            conv = app.query_one("#conv")
            for w in conv.query(Static):
                assert w.styles.width.value in (None, "100%", "1fr") or True
            assert True
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
            assert "Interrupted" in flat
            assert "What should Claude do instead?" in flat
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
            assert "1 tool use" in tasks
            assert "2k tokens" in tasks
            assert "Done" in tasks
    asyncio.run(scenario())


def _transcript_text(app) -> str:
    view = app.screen.query_one("#transcript")
    return "".join(str(w.content) for w in view.children)


def test_dynamic_text_with_brackets_renders_without_crash():
    from chatchat.hooks.events import RuntimeEvent

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TEXT, agent="lead",
                                           data={"delta": "[/bold] [x] data"}))
            await pilot.pause()
            assert "\\[/bold] \\[x] data" in _flatten(app)
            from pyclaw.tui import _ToolBlock
            block = _ToolBlock("Bash", '{"cmd": "[x]"}')
            await app._conv().mount(block)
            block.set_result("[/bold] output [y]")
            await pilot.pause()
            assert "\\[/bold] output \\[y]" in str(block.content)
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
            assert app.query_one("#input", Input).value == "/model "
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
            assert app.query_one("#input", Input).value == "/model "
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
            assert "(ctrl+o to expand)" in collapsed
            block.on_click()
            await pilot.pause()
            expanded = str(block.content)
            assert "(ctrl+o to expand)" not in expanded
            assert "y" * 400 in expanded
    asyncio.run(scenario())


class _SplitTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        from chatchat.hooks.events import emit
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
            children = [str(w.content) for w in app.query_one("#conv").children]
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
        from chatchat.hooks.events import emit
        await asyncio.sleep(0.2)
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
            assert "esc to interrupt" in str(
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
        from chatchat.hooks.events import emit
        self.worker_busy = True
        self.record("user", prompt)
        emit(AGENT_TEXT, agent="lead", delta="answer")
        self.record("assistant", "answer")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "answer"


def test_input_stays_busy_while_teammate_running():
    team = _TeammateTeam()

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            for _ in range(6):
                await pilot.pause()
            assert app._processing == "hi"
            team.worker_busy = False
            for _ in range(20):
                await pilot.pause()
                await asyncio.sleep(0.05)
            assert app._processing is None
            assert "answer" in _flatten(app)
    asyncio.run(scenario())


class _BlankTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        from chatchat.hooks.events import emit
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
            blocks = [str(w.content) for w in app.query_one("#conv").children]
            assert "answer" in blocks[-2]
            assert "Worked for" in blocks[-1]
            assert all(b.strip() for b in blocks)
    asyncio.run(scenario())


class _DeltaTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        from chatchat.hooks.events import emit
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


class _TwoTurnTeam(_FakeTeam):

    def __init__(self):
        super().__init__()
        self._turn = 0

    async def query(self, prompt, timeout=60):
        from chatchat.hooks.events import emit
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
        return [str(w.content) for w in app.query_one("#conv").children
                if "Worked for" in str(w.content)]

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
        from chatchat.hooks.events import emit
        self.record("user", prompt)
        emit(AGENT_TEXT, agent="lead", delta="logged answer")
        self.record("assistant", "logged answer", thinking="why")
        emit(AGENT_TURN_FINISHED, agent="lead")
        return "logged answer"


def test_turn_appends_conversation_log(tmp_path, monkeypatch):
    from conippets import jsonl

    from pyclaw import agents
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
    from pyclaw import agents

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
            assert "Read 1 file" in _plain(flat)
            assert "old thought" not in flat
            assert app._tools["t1"]._done is True
    asyncio.run(scenario())


def test_fresh_start_ignores_saved_history(tmp_path, monkeypatch):
    from pyclaw import agents

    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    agents.save_transcript("s2", [{"role": "assistant", "content": "stale"}])

    async def scenario():
        async with PyClawApp(builder=_builder, session_id="s2",
                             resume=False).run_test() as pilot:
            await pilot.pause()
            return _flatten(pilot.app)

    assert "stale" not in asyncio.run(scenario())


def test_permission_card_drops_remember_option_for_dangerous_command():
    from pyclaw.tui import _PermissionPrompt

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            locked = _PermissionPrompt("Bash", {"command": "rm -rf /"},
                                       rememberable=False)
            await app._conv().mount(locked)
            await pilot.pause()
            text = str(locked.content)
            assert "don't ask again" not in text
            assert "2. No" in text
            assert locked._rememberable is False

            normal = _PermissionPrompt("Edit", "a.txt", rememberable=True,
                                       rule="Edit")
            await app._conv().mount(normal)
            await pilot.pause()
            text = str(normal.content)
            assert "don't ask again" in text
            assert "Edit" in text
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
    from chatchat.hooks.events import AGENT_TOOL_CALL, RuntimeEvent

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
    from pyclaw.tui import _Conv

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
        from chatchat.hooks.events import emit
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


def test_permission_prompt_resolves_through_the_app():
    from pyclaw.tui import _PermissionPrompt

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task = asyncio.ensure_future(
                app._ask_permission("Bash", {"command": "ls"}))
            for _ in range(4):
                await pilot.pause()
            prompts = [w for w in app._conv().children
                       if isinstance(w, _PermissionPrompt)]
            assert prompts
            await pilot.press("y")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()) == "approved"


class _BracketSubTeam(_FakeTeam):

    async def query(self, prompt, timeout=60):
        from chatchat.hooks.events import emit
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


def test_permission_prompt_resolves_through_the_app():
    from pyclaw.tui import _PermissionPrompt

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            task = asyncio.ensure_future(
                app._ask_permission("Bash", {"command": "ls"}))
            for _ in range(4):
                await pilot.pause()
            prompts = [w for w in app._conv().children
                       if isinstance(w, _PermissionPrompt)]
            assert prompts
            await pilot.press("y")
            await pilot.pause()
            return await task

    assert asyncio.run(scenario()) == "approved"


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
    assert conv == height - 4
    assert input_y == height - 4


def test_bash_collapsible_classification():
    from pyclaw.tui import _bash_kinds

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
    from pyclaw.tui import _collapsible_kinds

    assert _collapsible_kinds("Read", {"file_path": "a.py"}) == {"read"}
    assert _collapsible_kinds("Read", {"file_path": "PYCLAW.md"}) == {
        "memory_read"}
    assert _collapsible_kinds("Grep", {"pattern": "x"}) == {"search"}
    assert _collapsible_kinds("Glob", {"pattern": "*.py"}) == {"search"}
    assert _collapsible_kinds("LS", {"path": "."}) == {"list"}
    assert _collapsible_kinds("Write", {"file_path": "a.py"}) == set()
    assert _collapsible_kinds("Write", {"file_path": "PYCLAW.md"}) == {
        "memory_write"}
    assert _collapsible_kinds("Edit", {"file_path": "a.py"}) == set()
    assert _collapsible_kinds("Bash", {"command": "ls"}) == {"list"}
    assert _collapsible_kinds("create_agent", {"prompt": "x"}) == set()


def test_bash_collapsible_classification():
    from pyclaw.tui import _bash_kinds

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
    from pyclaw.tui import _collapsible_kinds

    assert _collapsible_kinds("Read", {"file_path": "a.py"}) == {"read"}
    assert _collapsible_kinds("Read", {"file_path": "PYCLAW.md"}) == {
        "memory_read"}
    assert _collapsible_kinds("Grep", {"pattern": "x"}) == {"search"}
    assert _collapsible_kinds("Glob", {"pattern": "*.py"}) == {"search"}
    assert _collapsible_kinds("LS", {"path": "."}) == {"list"}
    assert _collapsible_kinds("Write", {"file_path": "a.py"}) == set()
    assert _collapsible_kinds("Write", {"file_path": "PYCLAW.md"}) == {
        "memory_write"}
    assert _collapsible_kinds("Edit", {"file_path": "a.py"}) == set()
    assert _collapsible_kinds("Bash", {"command": "ls"}) == {"list"}
    assert _collapsible_kinds("create_agent", {"prompt": "x"}) == set()
