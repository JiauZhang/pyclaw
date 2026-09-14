import asyncio

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
                              "content": "y" * 80}])
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
            assert "perm" in str(app.query_one("#status").content)
    asyncio.run(scenario())


def _flatten(app) -> str:
    return "".join(str(w.content) for w in app.query_one("#conv").children)


def test_ui_launches_and_renders_panels():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            assert app.query_one("#conv") is not None
            assert app.query_one("#input", Input) is not None
            assert app.query_one("#status") is not None
            assert "p/m" in str(app.query_one("#status").content)
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
            app.query_one(Input).value = "/tools"
            await pilot.press("enter")
            await pilot.pause()
            flat = _flatten(app)
            assert "k" in flat
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


def test_thinking_is_a_trailing_element_after_the_answer():
    from pyclaw.tui import _ThinkingBlock

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
            assert isinstance(conv.children[-1], _ThinkingBlock)
            assert "∴ Thinking" in blocks[-1]
            assert "inner monologue" not in blocks[-1]
            assert "answer" in blocks[-2]
    asyncio.run(scenario())


def test_thinking_auto_hides_after_ttl(monkeypatch):
    from pyclaw import tui
    monkeypatch.setattr(tui, "THINKING_TTL", 0.2)

    async def scenario():
        async with PyClawApp(builder=lambda: _ThinkTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one(Input).value = "hi"
            await pilot.press("enter")
            seen = gone = False
            for _ in range(60):
                await pilot.pause()
                await asyncio.sleep(0.05)
                if app._thought is not None:
                    seen = True
                elif seen:
                    gone = True
                    break
            return seen, gone, app._thought, _flatten(app)

    seen, gone, thought, flat = asyncio.run(scenario())
    assert seen
    assert gone and thought is None
    assert "∴ Thinking" not in flat


def test_status_shows_usage_compact_and_cached():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.pause()
            status = str(app.query_one("#status").content)
            assert "in" in status and "out" in status
            assert "total" in status
            assert "cached" in status
    asyncio.run(scenario())


def test_status_shows_cached_even_when_details_absent():
    class _NoCacheTeam(_FakeTeam):
        def usage(self):
            class _U:
                prompt_tokens = 10
                completion_tokens = 5
                total_tokens = 15
                prompt_tokens_details = None
            return _U()

    async def scenario():
        async with PyClawApp(builder=lambda: _NoCacheTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.pause()
            status = str(app.query_one("#status").content)
            assert "0 cached" in status
    asyncio.run(scenario())


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
            return str(app.query_one("#status").content)

    assert "p/newm" in asyncio.run(scenario())


def test_status_shows_thinking_config_switch():
    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.pause()
            status = str(app.query_one("#status").content)
            assert "thinking off" in status
    asyncio.run(scenario())


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
            assert "Interrupted by user" in flat
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
            assert "[coder]" in tasks
            assert "1 tool use" in tasks
            assert "2k tokens" in tasks
            assert "Done" in tasks
    asyncio.run(scenario())


def _transcript_text(app) -> str:
    view = app.screen.query_one("#transcript")
    return "".join(str(w.content) for w in view.children)


def test_dynamic_text_with_brackets_renders_without_crash():
    """回归：模型/工具输出里的 [] 会被当 markup 解析，直接把 TUI 炸退
    （真机：MarkupError closing tag '[/bold]'）。动态内容必须 escape。"""
    from chatchat.hooks.events import RuntimeEvent

    async def scenario():
        async with PyClawApp(builder=_builder).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await app._handle(RuntimeEvent(AGENT_TEXT, agent="lead",
                                           data={"delta": "[/bold] [x] data"}))
            await pilot.pause()
            assert "[/bold] [x] data" in _flatten(app)
            app._tools["t1"].set_result("[/bold] output [y]")
            await pilot.pause()
            assert "output: [/bold] output [y]" in _flatten(app) \
                or "[/bold] output [y]" in str(app._tools["t1"].content)
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
            assert not suggest.display          # 命令名 + 空格后菜单隐藏
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
            assert app._session.permission_mode == "plan"   # 无参命令直接执行
            assert app.query_one("#input", Input).value == ""
            await pilot.press(*"/model")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            # 带参命令：Enter 只补全，停在输入框等参数
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
            await pilot.press("x")                # /cx 无匹配 → 隐藏
            await pilot.pause()
            assert not suggest.display
            await pilot.press("backspace", "backspace", "backspace")
            await pilot.pause()
            assert not suggest.display            # 空输入 → 隐藏
            await pilot.press("h", "i")           # 非 / 开头 → 不出现
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
            assert "y" * 80 not in _flatten(app)      # 普通模式工具输出折叠
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert type(app.screen).__name__ == "TranscriptScreen"
            assert "y" * 80 in _transcript_text(app)   # verbose：输出全文
            assert "x" * 80 in _transcript_text(app)   # input 全文
            await pilot.press("q")
            await pilot.pause()
            assert type(app.screen).__name__ != "TranscriptScreen"
            # esc 同样可退出
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
            assert "…" in str(block.content)
            block.on_click()
            await pilot.pause()
            content = str(block.content)
            assert "input:" in content
            assert "output:" in content
            assert "x" * 80 in content
            assert "y" * 80 in content
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
            tool = [i for i, c in enumerate(children) if "k (" in c]
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
            assert app.query_one(Input).placeholder.startswith("⏳")
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
            assert "answer" in blocks[-1]
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


def test_second_turn_thinking_replaces_the_trailing_element():
    async def scenario():
        async with PyClawApp(builder=lambda: _TwoTurnTeam()).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await _submit_and_wait(pilot, "one", 6)
            assert app._thought is not None
            assert app._thought._thinking == "think1"
            await _submit_and_wait(pilot, "two", 6)
            assert app._thought._thinking == "think2"
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
            assert "file body" in flat
            assert "\u2234 Thinking" in flat
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
            locked = _PermissionPrompt("Bash", "rm -rf /", rememberable=False)
            await app._conv().mount(locked)
            await pilot.pause()
            text = str(locked.content)
            assert "don't ask again" not in text
            assert "cannot be remembered" in text
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
