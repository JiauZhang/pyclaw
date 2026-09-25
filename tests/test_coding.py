import asyncio
import re
import tempfile
import time
from pathlib import Path

from conippets import json

from chatchat.core.agents import AgentDefinition
from chatchat.core.filehistory import FileHistory
from chatchat.tool import ToolContext, ToolResult

from pyclaw import agents as agents_mod
from pyclaw.agents import Session
from pyclaw.team_builder import build_team
from pyclaw.tools.coding import (CODING_TOOLS, PermissionController,
                                 background, next_mode, parse_mode,
                                 permission as perm, shell)
from pyclaw.tools.coding.permission import (REJECT_MESSAGE,
                                            REJECT_MESSAGE_WITH_REASON_PREFIX,
                                            SUBAGENT_REJECT_MESSAGE,
                                            SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX,
                                            PermissionChoice)
from pyclaw.tools.coding.shell_rules import (bash_rule_matches,
                                             is_dangerous_removal, is_read_only,
                                             parse_bash_rule, suggested_rule)


def _tools(d, files=None):
    ctx = ToolContext(cwd=Path(d).resolve(), files=files)

    def bound(tool):
        def call(**kwargs):
            return asyncio.run(tool(ctx, **kwargs))
        call.tool = tool
        return call

    return {t.name: bound(t) for t in CODING_TOOLS}


def test_coding_tools_are_shared_singletons():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        Path(a, 'same.txt').write_text('in a\n', encoding='utf-8')
        Path(b, 'same.txt').write_text('in b\n', encoding='utf-8')
        first, second = _tools(a)['Read'], _tools(b)['Read']
        assert first.tool is second.tool
        assert 'in a' in _text(first(file_path='same.txt'))
        assert 'in b' in _text(second(file_path='same.txt'))


def _all_properties(schema: dict) -> dict:
    found = dict(schema.get('properties', {}))
    for spec in schema.get('properties', {}).values():
        if spec.get('type') == 'array':
            found.update(_all_properties(spec.get('items', {})))
    return found


def test_tool_text_respects_the_budget_and_documents_every_parameter():
    ctx = ToolContext(cwd=Path("/w"))
    for tool in CODING_TOOLS:
        text = tool.describe(ctx)
        assert len(text) <= 350, (tool.name, len(text))
        assert "claude" not in text.lower()
        for name, spec in _all_properties(tool.parameters).items():
            assert spec.get("description"), (tool.name, name)


def test_read_warns_about_the_line_prefix_that_edit_must_not_copy():
    text = dict((t.name, t) for t in CODING_TOOLS)["Read"].describe(
        ToolContext(cwd=Path("/w")))
    assert "line number" in text and "tab" in text


def test_grep_states_the_regex_dialect_and_its_line_scope():
    text = dict((t.name, t) for t in CODING_TOOLS)["Grep"].describe(
        ToolContext(cwd=Path("/w")))
    assert "Python re" in text and "newline" in text


def test_read_offset_counts_the_lines_the_output_shows():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "ten.txt").write_text(
            "\n".join(f"l{i}" for i in range(1, 11)), encoding="utf-8")
        out = _text(_tools(d)["Read"](file_path="ten.txt", offset=3, limit=2))
        assert "3\tl3" in out and "4\tl4" in out
        assert "l5" not in out and "2\tl2" not in out


def test_read_caps_long_files_at_the_line_count_it_promises():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "long.txt").write_text(
            "\n".join(f"l{i}" for i in range(2100)), encoding="utf-8")
        out = _text(_tools(d)["Read"](file_path="long.txt"))
        assert "showing 1-2000" in out and "2100 lines" in out
        assert "2000\tl1999" in out and "2001\tl2000" not in out
        assert "2000" in dict((t.name, t) for t in CODING_TOOLS)[
            "Read"].describe(ToolContext(cwd=Path("/w")))


def _text(result):
    return result.text if isinstance(result, ToolResult) else result


def _read(p):
    return p.read_text(encoding="utf-8")


def test_read_and_find():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("alpha\nbeta\n")
        t = _tools(d)
        out = _text(t["Read"](file_path="a.txt"))
        assert "alpha" in out and "a.txt" in out
        g = _text(t["Glob"](pattern="*.txt"))
        assert "a.txt" in g


def test_grep_matches_and_limits():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("x1\n---\nx2\n")
        t = _tools(d)
        out = _text(t["Grep"](pattern="x[0-9]"))
        assert "a.txt:1: x1" in out and "a.txt:3: x2" in out


def test_grep_never_searches_vendored_or_state_dirs():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for rel in ("node_modules/p/x.txt", ".git/x.txt",
                    ".pyclaw/teams/x.txt"):
            (root / rel).parent.mkdir(parents=True)
            (root / rel).write_text("hidden\n")
        (root / "src.txt").write_text("visible\n")
        out = _text(_tools(d)["Grep"](pattern="hidden|visible"))
        assert "src.txt:1: visible" in out
        assert "hidden" not in out


def test_glob_bare_pattern_matches_one_level():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "pkg").mkdir()
        (root / "top.py").write_text("")
        (root / "pkg" / "deep.py").write_text("")
        t = _tools(d)
        assert _text(t["Glob"](pattern="*.py")).split() == ["top.py"]
        assert sorted(_text(t["Glob"](pattern="**/*.py")).split()) == [
            "pkg/deep.py", "top.py"]


def test_path_escape_guard():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        for tool, kw in (("Read", {"file_path": "../secret"}),
                         ("Write", {"file_path": "../x", "content": "y"})):
            assert t[tool](**kw).startswith("Error")


def test_edit_unique_and_ambiguous():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("one\ntwo\none\n")
        t = _tools(d)
        ambiguous = _text(t["Edit"](file_path="a.txt", old_string="one",
                                      new_string="X"))
        assert "not unique" in ambiguous
        ok = _text(t["Edit"](file_path="a.txt", old_string="two", new_string="TWO"))
        assert "Saved" in ok
        assert "-two" in ok and "+TWO" in ok
        assert "TWO" in _read(root / "a.txt")
        missing = _text(t["Edit"](file_path="a.txt", old_string="zzz",
                                    new_string="y"))
        assert "not found" in missing


def test_write_and_edit():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        t = _tools(d)
        wrote = _text(t["Write"](file_path="b.txt", content="hi\n"))
        assert "Created b.txt." in wrote
        out = _text(t["Edit"](file_path="b.txt", old_string="hi",
                              new_string="hello"))
        assert "Saved" in out
        assert "-hi" in out and "+hello" in out
        assert "hello" in _read(root / "b.txt")


def test_mode_parse_and_cycle():
    for m in ("default", "acceptEdits", "plan", "bypassPermissions"):
        assert parse_mode(m).value == m
    assert next_mode("default").value == "acceptEdits"
    assert next_mode("acceptEdits").value == "plan"
    assert next_mode("plan").value == "default"
    assert next_mode("plan", bypass_available=True).value == "bypassPermissions"
    assert next_mode("bypassPermissions").value == "default"


def test_decide_matrix():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, tools=CODING_TOOLS)
        assert g.decide("Read", {"file_path": "a.txt"}) == "allow"
        assert g.decide("Edit", {"file_path": "a.txt"}) == "ask"
        assert g.decide("Write", {"file_path": "../x"}) == "ask"
        assert g.decide("Read", {"file_path": "../x"}) == "ask"
        assert g.decide("datetime", {}) == "ask"

        plan = PermissionController(mode="plan", cwd=d, tools=CODING_TOOLS)
        assert plan.decide("Edit", {"file_path": "a.txt"}) == "deny"
        assert plan.decide("Read", {"file_path": "../x"}) == "allow"

        ae = PermissionController(mode="acceptEdits", cwd=d,
                                 tools=CODING_TOOLS)
        assert ae.decide("Edit", {"file_path": "a.txt"}) == "allow"


def test_decide_mode_override_per_call():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, tools=CODING_TOOLS)
        assert g.decide("Edit", {"file_path": "a.txt"}) == "ask"
        assert g.decide("Edit", {"file_path": "a.txt"},
                        mode="acceptEdits") == "allow"
        assert g.decide("Edit", {"file_path": "a.txt"},
                        mode="plan") == "deny"
        assert g.decide("Edit", {"file_path": "a.txt"}) == "ask"
        assert g.mode.value == "default"
        assert g.decide("Bash", {"command": "echo hi"},
                        mode="acceptEdits") == "allow"


def test_coding_tools_declare_their_permissions_capabilities():
    by_name = {t.name: t for t in CODING_TOOLS}
    assert {n for n, t in by_name.items() if t.read_only} == {
        "Read", "Glob", "Grep"}
    assert by_name["Edit"].get_path({"file_path": "a.py"}) == "a.py"
    assert by_name["Grep"].get_path({"pattern": "a|b", "path": "src"}) == "src"
    assert by_name["Grep"].get_path({"pattern": "a|b"}) is None
    assert by_name["Bash"].get_path is None
    assert by_name["Bash"].read_only is False


def test_the_gate_asks_the_tool_what_it_addresses():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "src").mkdir()
        g = PermissionController(mode="default", cwd=d, tools=CODING_TOOLS)
        assert g.decide("Grep", {"pattern": "../secret"}) == "allow"
        assert g.decide("Grep", {"pattern": "x", "path": "../secret"}) == "ask"
        assert g.suggested_rule("Grep", {"pattern": "a|b"}) is None
        assert g.suggested_rule("Grep", {"pattern": "a|b",
                                         "path": "src"}) == "Grep(./src)"
        assert (g.suggested_rule("Bash", {"command": "npm test"}) or ""
                ).startswith("Bash(npm")


async def _gate_call(team, tool_name, tool_input, agent_type):
    matched = team.hooks.get_matching_hooks(
        "PreToolUse", tool_name,
        {"hook_event_name": "PreToolUse", "tool_name": tool_name})
    fn = matched[0].config.fn
    return await fn({"hook_event_name": "PreToolUse",
                     "tool_name": tool_name, "tool_input": tool_input,
                     "agent_type": agent_type})


def _decision(payload):
    specific = payload["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    return specific


def test_permission_gate_resolves_subagent_mode():

    async def main():
        with tempfile.TemporaryDirectory() as d:
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            team.register_agent_definition(AgentDefinition(
                "reader", system_prompt="read only",
                permission_mode="plan"))
            file = {"file_path": str(Path(d) / "a.txt")}
            planned = await _gate_call(team, "Write", file, "reader")
            subagent = await _gate_call(team, "Write", file, "subagent")
            lead = await _gate_call(team, "Write", file, "")
            return planned, subagent, lead

    planned, subagent, lead = asyncio.run(main())
    assert _decision(planned)["permissionDecision"] == "deny"
    assert _decision(planned)["permissionDecisionReason"].startswith(
        "Write is not allowed in plan mode")
    assert subagent is True
    assert "needs approval" in _decision(lead)["permissionDecisionReason"]


def test_permission_gate_parent_mode_takes_precedence():

    async def main():
        with tempfile.TemporaryDirectory() as d:
            def accept_edits_team():
                team = build_team("agnes", "agnes-2.5-flash", cwd=d,
                                  permission_mode="acceptEdits")
                team.register_agent_definition(AgentDefinition(
                    "reader", system_prompt="read only",
                    permission_mode="plan"))
                return team

            file = {"file_path": str(Path(d) / "a.txt")}
            team = accept_edits_team()
            edit_in_accept_edits = await _gate_call(team, "Edit", file,
                                                    "reader")
            bash_in_accept_edits = await _gate_call(
                team, "Bash", {"command": "echo hi"}, "reader")

            bypass = build_team("agnes", "agnes-2.5-flash", cwd=d,
                                permission_mode="bypassPermissions")
            bypass.register_agent_definition(AgentDefinition(
                "reader", system_prompt="read only", permission_mode="plan"))
            edit_in_bypass = await _gate_call(bypass, "Edit", file, "reader")
            datetime_in_bypass = await _gate_call(bypass, "datetime", {},
                                                  "reader")
            return (edit_in_accept_edits, bash_in_accept_edits,
                    edit_in_bypass, datetime_in_bypass)

    edit_ae, bash_ae, edit_bp, dt_bp = asyncio.run(main())
    assert edit_ae is True
    assert bash_ae is True
    assert edit_bp is True
    assert dt_bp is True


def test_bypass_permissions_allows_unless_a_rule_says_otherwise():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="bypassPermissions", cwd=d)
        assert g.decide("Edit", {"file_path": "../outside"}) == "allow"
        assert g.decide("Bash", {"command": "python3 x.py"}) == "allow"
        assert g.decide("datetime", {}) == "allow"

        ruled = PermissionController(mode="bypassPermissions", cwd=d,
                                     deny=["Bash(rm:*)"],
                                     ask=["Bash(git push:*)"])
        assert ruled.decide("Bash", {"command": "rm -rf x"}) == "deny"
        assert ruled.decide("Bash", {"command": "git push origin"}) == "ask"


def test_deny_removes_tool_and_precedence():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, deny=["Edit"])
        assert g.allowed_tool("Edit") is False
        assert g.allowed_tool("Read") is True
        g2 = PermissionController(mode="default", cwd=d,
                                  deny=["Edit"], allow=["Edit"])
        assert g2.decide("Edit", {"file_path": "a.txt"}) == "deny"


def test_non_bash_tool_rules_match_their_path_argument():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, tools=CODING_TOOLS,
                                 deny=["Read(./secret.txt)"])
        assert g.decide("Read", {"file_path": "secret.txt"}) == "deny"
        assert g.decide("Read", {"file_path": "notes.txt"}) == "allow"

        scoped = PermissionController(mode="default", cwd=d,
                                      tools=CODING_TOOLS,
                                      allow=["Edit(./src/**)"])
        assert scoped.decide("Edit", {"file_path": "src/a.py"}) == "allow"
        assert scoped.decide("Edit", {"file_path": "other/a.py"}) == "ask"


def test_ask_flow_authorize():
    with tempfile.TemporaryDirectory() as d:

        async def approve(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("approved")

        g = PermissionController(mode="default", cwd=d, request=approve)
        assert asyncio.run(g.authorize("Edit", {"file_path": "a.txt"})) is True

        async def denied(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("denied")

        g2 = PermissionController(mode="default", cwd=d, request=denied)
        res = asyncio.run(g2.authorize("Edit", {"file_path": "a.txt"}))
        assert _decision(res)["permissionDecision"] == "deny"

        g3 = PermissionController(mode="default", cwd=d)
        res3 = asyncio.run(g3.authorize("Edit", {"file_path": "a.txt"}))
        assert _decision(res3)["permissionDecision"] == "deny"


def test_the_asking_agent_reaches_the_prompt():
    with tempfile.TemporaryDirectory() as d:
        seen = {}

        async def ask(name, inp, *, tool_use_id='', agent=''):
            seen["agent"] = agent
            return PermissionChoice("approved")

        g = PermissionController(mode="default", cwd=d, request=ask)
        asyncio.run(g.authorize("Edit", {"file_path": "a.txt"}, agent="watcher"))
        assert seen["agent"] == "watcher"


def test_a_permission_request_hook_can_answer_before_the_user():
    with tempfile.TemporaryDirectory() as d:
        asked = []

        async def ask(name, inp, *, tool_use_id='', agent=''):
            asked.append(name)
            await asyncio.sleep(5)
            return PermissionChoice("approved")

        seen = {}

        async def hooks(tool_name, tool_input, tool_use_id, agent):
            seen.update(tool_name=tool_name, tool_use_id=tool_use_id,
                        agent=agent)
            return {"behavior": "allow",
                    "updated_input": {"file_path": "b.txt"}, "message": ""}

        g = PermissionController(mode="default", cwd=d, request=ask)
        g.permission_hooks = hooks
        res = asyncio.run(g.authorize("Edit", {"file_path": "a.txt"},
                                      tool_use_id="u1", agent="watcher"))
        assert seen == {"tool_name": "Edit", "tool_use_id": "u1",
                        "agent": "watcher"}
        assert _decision(res)["permissionDecision"] == "allow"
        assert _decision(res)["updatedInput"] == {"file_path": "b.txt"}
        assert asked == ["Edit"]


def test_a_permission_request_hook_denial_carries_its_message():
    with tempfile.TemporaryDirectory() as d:
        asked = []

        async def ask(name, inp, *, tool_use_id='', agent=''):
            asked.append(name)
            await asyncio.sleep(5)
            return PermissionChoice("approved")

        async def hooks(tool_name, tool_input, tool_use_id, agent):
            return {"behavior": "deny", "updated_input": None,
                    "message": "this path is off limits"}

        g = PermissionController(mode="default", cwd=d, request=ask)
        g.permission_hooks = hooks
        res = asyncio.run(g.authorize("Edit", {"file_path": "a.txt"}))
        assert _decision(res)["permissionDecision"] == "deny"
        assert (_decision(res)["permissionDecisionReason"]
                == "this path is off limits")
        assert asked == ["Edit"]


def test_a_permission_request_hook_without_a_decision_leaves_the_user_in_charge():
    with tempfile.TemporaryDirectory() as d:

        async def ask(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("denied", feedback="user said no")

        async def hooks(tool_name, tool_input, tool_use_id, agent):
            return None

        g = PermissionController(mode="default", cwd=d, request=ask)
        g.permission_hooks = hooks
        res = asyncio.run(g.authorize("Edit", {"file_path": "a.txt"}))
        assert _decision(res)["permissionDecision"] == "deny"
        assert ("user said no" in _decision(res)["permissionDecisionReason"])


def test_the_user_answer_wins_against_a_slow_permission_request_hook():
    with tempfile.TemporaryDirectory() as d:
        cancelled = []

        async def ask(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("approved")

        async def hooks(tool_name, tool_input, tool_use_id, agent):
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return {"behavior": "deny", "updated_input": None,
                    "message": "too late"}

        g = PermissionController(mode="default", cwd=d, request=ask)
        g.permission_hooks = hooks
        assert asyncio.run(g.authorize("Edit", {"file_path": "a.txt"})) is True
        assert cancelled == [True]


def test_a_denial_reports_the_refusal_to_the_agent_that_asked():
    with tempfile.TemporaryDirectory() as d:

        def gate(feedback):
            async def denied(name, inp, *, tool_use_id='', agent=''):
                return PermissionChoice("denied", feedback=feedback)
            return PermissionController(mode="default", cwd=d, request=denied)

        cases = (
            ("", "", REJECT_MESSAGE),
            ("watcher", "", SUBAGENT_REJECT_MESSAGE),
            ("", "not now", REJECT_MESSAGE_WITH_REASON_PREFIX + "not now"),
            ("watcher", "not now",
             SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX + "not now"))
        for agent, feedback, expected in cases:
            res = asyncio.run(gate(feedback).authorize(
                "Edit", {"file_path": "a.txt"}, agent=agent))
            assert _decision(res)["permissionDecisionReason"] == expected


def test_the_denial_copy_says_nothing_ran_and_who_should_act():
    for message in (REJECT_MESSAGE, SUBAGENT_REJECT_MESSAGE,
                    REJECT_MESSAGE_WITH_REASON_PREFIX,
                    SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX):
        assert "refused" in message
        assert "nothing ran" in message
        assert "claude" not in message.lower()
    assert REJECT_MESSAGE_WITH_REASON_PREFIX.endswith("\n")
    assert SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX.endswith("\n")
    assert "wait for them" in REJECT_MESSAGE
    assert "Route around it" in SUBAGENT_REJECT_MESSAGE


def test_approval_feedback_travels_as_additional_context():
    with tempfile.TemporaryDirectory() as d:

        async def approve(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("approved", feedback="run the tests first")

        g = PermissionController(mode="default", cwd=d, request=approve)
        res = asyncio.run(g.authorize("Edit", {"file_path": "a.txt"}))
        assert _decision(res) == {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'allow',
            'additionalContext': 'run the tests first'}


def test_bash_tool_runs_in_workspace():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "marker.txt").write_text("x\n")
        t = _tools(d)
        assert "Bash" in t
        assert "marker.txt" in _text(t["Bash"](command="ls"))
        out = _text(t["Bash"](command="python3 -c \"import os;"
                                        "print(os.path.realpath(os.getcwd()))\""))
        assert str(root.resolve()) in out


def test_bash_merges_stdout_and_stderr():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = _text(t["Bash"](command="echo out; echo err 1>&2"))
        assert "out" in out and "err" in out


def test_bash_reports_nonzero_exit_with_output():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = _text(t["Bash"](command="echo boom 1>&2; exit 3"))
        assert out.startswith("Command exited with 3")
        assert "boom" in out


def test_bash_grep_exit_one_is_not_an_error():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("alpha\n")
        t = _tools(d)
        out = _text(t["Bash"](command="grep zzz a.txt"))
        assert "Nothing matched" in out
        assert "exited with" not in out


def test_bash_test_exit_one_is_condition_false():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = _text(t["Bash"](command="test 1 = 2"))
        assert "The condition did not hold" in out
        assert "exited with" not in out


def test_bash_timeout_kills_command():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = _text(t["Bash"](command="sleep 5", timeout=200))
        assert "ran past" in out


def test_bash_timeout_moves_running_command_to_background():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = _text(t["Bash"](command="echo start; sleep 2; echo done", timeout=300))
        assert "background" in out
        task_id = re.search(r"as (b[0-9a-z]{8})", out).group(1)
        final = _text(t["TaskOutput"](task_id=task_id, timeout=8000))
        assert "done" in final


def test_bash_truncates_large_output():
    with tempfile.TemporaryDirectory() as d:
        t = _tools(d)
        out = _text(t["Bash"](command="python3 -c \"print('y' * 40000)\""))
        assert "left out" in out
        assert len(out) < 40000


def test_bash_exit_code_semantics_use_the_last_subcommand():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("alpha\n")
        t = _tools(d)
        out = _text(t["Bash"](command="cd . && grep zzz a.txt"))
        assert "Nothing matched" in out
        assert "exited with" not in out


def test_bash_timeout_and_output_env_overrides(monkeypatch):
    monkeypatch.setenv("BASH_DEFAULT_TIMEOUT_MS", "5000")
    monkeypatch.setenv("BASH_MAX_TIMEOUT_MS", "1000")
    assert shell.get_default_timeout_ms() == 5000
    assert shell.get_max_timeout_ms() == 5000
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "999999999")
    assert shell.get_max_output_chars() == shell.MAX_OUTPUT_UPPER_LIMIT


def test_bash_persists_truncated_output_for_readback(monkeypatch, tmp_path):
    monkeypatch.setattr(shell.tempfile, "gettempdir",
                        lambda: str(tmp_path / "scratch"))
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "200")
    t = _tools(str(tmp_path))
    out = _text(t["Bash"](command="python3 -c \"print('y' * 500)\""))
    assert "left out" in out and "full output:" in out
    path = out.split("full output: ")[1].split("]")[0]
    assert len(Path(path).read_text(encoding="utf-8")) == 500


def test_bash_budget_keeps_its_own_output_pointer_readable(monkeypatch, tmp_path):
    from chatchat.tool import DEFAULT_MAX_RESULT_CHARS
    t = _tools(str(tmp_path))
    assert t["Bash"].tool.max_result_chars > DEFAULT_MAX_RESULT_CHARS

    limit = shell.MAX_OUTPUT_UPPER_LIMIT
    monkeypatch.setattr(shell.tempfile, "gettempdir",
                        lambda: str(tmp_path / "scratch"))
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", str(limit))
    out = _text(t["Bash"](command=f"python3 -c \"print('y' * {limit + 5})\""))
    assert "full output:" in out
    assert "was truncated" not in out


def test_bash_rule_parse_and_match():
    assert parse_bash_rule("Bash(npm run test:*)") == ("prefix", "npm run test")
    assert parse_bash_rule("Bash(ls)") == ("exact", "ls")
    assert parse_bash_rule("Bash(git * logs)")[0] == "wildcard"

    assert bash_rule_matches("Bash(npm run test:*)", "npm run test")
    assert bash_rule_matches("Bash(npm run test:*)", "npm run test -- unit")
    assert not bash_rule_matches("Bash(npm run test:*)", "npm run test:unit")
    assert not bash_rule_matches("Bash(npm run test:*)", "npm runner")
    assert bash_rule_matches("Bash(git *)", "git add .")
    assert bash_rule_matches("Bash(git *)", "git")
    assert bash_rule_matches("Bash(ls)", "ls")
    assert not bash_rule_matches("Bash(ls)", "ls -la")
    assert bash_rule_matches("Bash(grep:*)", "xargs grep pattern")
    assert not bash_rule_matches("Bash(grep:*)", "xargs -n1 grep pattern")


def test_bash_prefix_rule_rejects_compound_command():
    assert not bash_rule_matches("Bash(cd:*)", "cd /tmp && rm -rf x")
    assert not bash_rule_matches("Bash(git:*)" , "git status; curl evil")


def test_bash_dangerous_removal_detection():
    assert is_dangerous_removal("rm -rf /")
    assert is_dangerous_removal("rm -rf /*")
    assert is_dangerous_removal("rm -rf ~")
    assert is_dangerous_removal("rmdir /usr")
    assert not is_dangerous_removal("rm -rf build")
    assert not is_dangerous_removal("rm a.txt")


def test_bash_read_only_detection():
    assert is_read_only("ls -la")
    assert is_read_only("cat a.txt | head -3")
    assert is_read_only("git status")
    assert is_read_only("git diff --stat")
    assert not is_read_only("echo $HOME")
    assert not is_read_only("ls > out.txt")
    assert not is_read_only("find . -delete")
    assert not is_read_only("cd /tmp && git status")
    assert not is_read_only("python3 -c 'x'")
    assert not is_read_only("rm a.txt")
    assert is_read_only("git branch")
    assert is_read_only("git tag")
    assert is_read_only("git reflog")
    assert not is_read_only("git branch -D main")
    assert not is_read_only("git tag -d v1")
    assert not is_read_only("git remote add origin url")
    assert not is_read_only("git remote set-url origin url")
    assert not is_read_only("git reflog expire --all")


def test_bash_decision_order_deny_ask_allow_readonly():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, deny=["Bash(rm:*)"],
                                 allow=["Bash(npm ci)"], ask=["Bash(git push:*)"])
        assert g.decide("Bash", {"command": "rm -rf x"}) == "deny"
        assert g.decide("Bash", {"command": "git push origin main"}) == "ask"
        assert g.decide("Bash", {"command": "npm ci"}) == "allow"
        assert g.decide("Bash", {"command": "ls -la"}) == "allow"
        assert g.decide("Bash", {"command": "make build"}) == "ask"

        both = PermissionController(mode="default", cwd=d,
                                    deny=["Bash(rm:*)"], allow=["Bash(rm:*)"])
        assert both.decide("Bash", {"command": "rm -rf x"}) == "deny"


def test_bash_env_and_wrapper_stripping():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, allow=["Bash(npm ci)"],
                                 deny=["Bash(curl:*)"])
        assert g.decide("Bash", {"command": "NODE_ENV=prod npm ci"}) == "allow"
        assert g.decide("Bash", {"command": "timeout 5 curl http://x"}) == "deny"
        assert g.decide("Bash", {"command": "FOO=bar curl http://x"}) == "deny"
        assert g.decide("Bash", {"command": "FOO=bar npm ci"}) == "ask"


def test_bash_dangerous_removal_asks_even_when_allowed():
    with tempfile.TemporaryDirectory() as d:
        g = PermissionController(mode="default", cwd=d, allow=["Bash(rm:*)"])
        assert g.decide("Bash", {"command": "rm -rf build"}) == "allow"
        assert g.decide("Bash", {"command": "rm -rf /"}) == "ask"


def test_bash_dangerous_removal_is_not_remembered():
    with tempfile.TemporaryDirectory() as d:

        async def once(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("dont_ask")

        g = PermissionController(mode="default", cwd=d, request=once)
        assert asyncio.run(g.authorize("Bash", {"command": "rm -rf /"})) is True
        assert g._allow == []
        assert asyncio.run(
            g.authorize("Bash", {"command": "npm ci"})) is True
        assert g._allow == ["Bash(npm ci:*)"]


def test_bash_accept_edits_mode_allows_workspace_file_commands():
    with tempfile.TemporaryDirectory() as d:
        ae = PermissionController(mode="acceptEdits", cwd=d)
        assert ae.decide("Bash", {"command": "mkdir out"}) == "allow"
        assert ae.decide("Bash", {"command": "mv a b"}) == "allow"
        assert ae.decide("Bash", {"command": "rm ../outside"}) == "ask"
        assert ae.decide("Bash", {"command": "python3 x.py"}) == "ask"


def test_bash_bare_deny_removes_tool_but_rule_does_not():
    with tempfile.TemporaryDirectory() as d:
        bare = PermissionController(mode="default", cwd=d, deny=["Bash"])
        assert bare.allowed_tool("Bash") is False
        rule = PermissionController(mode="default", cwd=d, deny=["Bash(rm:*)"])
        assert rule.allowed_tool("Bash") is True


def test_build_team_wires_bash_through_permission_hook():
    async def main():
        with tempfile.TemporaryDirectory() as d:
            team = build_team("agnes", "agnes-2.5-flash", cwd=d,
                              disallowed_tools=["Bash(curl:*)"])
            names = [t["name"] for t in team.tool_schemas(team.tool_context)]
            blocked = await team.execute_tool(
                "Bash", {"command": "curl http://example.com"}, team.lead)
            allowed = await team.execute_tool(
                "Bash", {"command": "echo hi"}, team.lead)
            return names, blocked, allowed

    names, blocked, allowed = asyncio.run(main())
    assert "Bash" in names
    assert "hook blocked" in blocked.text
    assert "hi" in allowed.text


def test_approval_feedback_lands_beside_the_tool_result():
    with tempfile.TemporaryDirectory() as d:

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)

            async def approve(name, inp, *, tool_use_id='', agent=''):
                return PermissionChoice("approved",
                                        feedback="run the tests first")

            team._pyclaw_gate.request = approve
            return await team.execute_tool("Bash",
                                           {"command": "touch marker.txt"},
                                           team.lead, "t1")

        outcome = asyncio.run(main())
        assert Path(d, "marker.txt").exists()
        assert outcome.additional_context == "run the tests first"


def test_a_permission_request_hook_answers_before_the_user():
    with tempfile.TemporaryDirectory() as d:
        asked = []

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)

            async def ask(name, inp, *, tool_use_id='', agent=''):
                asked.append(name)
                await asyncio.sleep(5)
                return PermissionChoice("denied")

            team._pyclaw_gate.request = ask
            team.hooks.register('PermissionRequest', fn=lambda inp: {
                'hookSpecificOutput': {
                    'hookEventName': 'PermissionRequest',
                    'decision': {'behavior': 'allow',
                                 'updatedInput': {'command': 'touch after'}}}})
            return await team.execute_tool("Bash", {"command": "touch before"},
                                           team.lead, "t1")

        outcome = asyncio.run(main())
        assert asked == ['Bash']
        assert Path(d, "after").exists()
        assert not Path(d, "before").exists()


def test_a_permission_request_hook_denial_reaches_the_model():
    with tempfile.TemporaryDirectory() as d:
        asked = []

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)

            async def ask(name, inp, *, tool_use_id='', agent=''):
                asked.append(name)
                await asyncio.sleep(5)
                return PermissionChoice("approved")

            team._pyclaw_gate.request = ask
            team.hooks.register('PermissionRequest', fn=lambda inp: {
                'hookSpecificOutput': {
                    'hookEventName': 'PermissionRequest',
                    'decision': {'behavior': 'deny',
                                 'message': 'deploys are frozen'}}})
            return await team.execute_tool("Bash", {"command": "touch never"},
                                           team.lead, "t1")

        outcome = asyncio.run(main())
        assert asked == ['Bash']
        assert "deploys are frozen" in outcome.text
        assert not Path(d, "never").exists()


def test_the_gate_reports_the_session_permission_mode_to_the_hooks():
    with tempfile.TemporaryDirectory() as d:
        seen = []

        async def main():
            from pyclaw.agents import Session
            from pyclaw.team_builder import build_team
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            team.hooks.on('PreToolUse', fn=lambda inp: seen.append(
                inp.get('permission_mode', 'missing')) or True)
            await team.execute_tool("Read", {"file_path": __file__},
                                    team.lead, "t1")
            Session(team).set_permission_mode("plan")
            await team.execute_tool("Read", {"file_path": __file__},
                                    team.lead, "t2")

        asyncio.run(main())
        assert seen == ["default", "plan"]


def test_build_team_removes_bash_when_denied_by_bare_name():
    async def main():
        with tempfile.TemporaryDirectory() as d:
            team = build_team("agnes", "agnes-2.5-flash", cwd=d,
                              disallowed_tools=["Bash"])
            return [t["name"] for t in team.tool_schemas(team.tool_context)]

    assert "Bash" not in asyncio.run(main())


def test_build_team_mode_gating():

    async def main():
        with tempfile.TemporaryDirectory() as d:
            single = build_team("agnes", "agnes-2.5-flash", cwd=d)
            teams = build_team("agnes", "agnes-2.5-flash", cwd=d, use_team=True)
            s_single = agents_mod.Session(single, session_id="ga")
            s_team = agents_mod.Session(teams, session_id="gt")
            return (single._pyclaw_mode, s_single.mode,
                    teams._pyclaw_mode, s_team.mode,
                    single.lead.instruction, teams.lead.instruction)

    (mode_a, session_a, mode_t, session_t, inst_a, inst_t) = asyncio.run(main())
    assert mode_a == "agent" and session_a == "agent"
    assert mode_t == "team" and session_t == "team"
    assert "capable AI assistant" in inst_a
    assert "leader of a task-executing team" in inst_t
    assert "Agent" in inst_t and "Agent" not in inst_a


def test_build_team_tools_differ_by_mode():
    async def names(**kw):
        with tempfile.TemporaryDirectory() as d:
            team = build_team("agnes", "agnes-2.5-flash", cwd=d, **kw)
            return {t["name"] for t in team.tool_schemas(team.tool_context)}

    single = asyncio.run(names())
    multi = asyncio.run(names(use_team=True))
    assert "Agent" in single and "Agent" in multi
    assert "TaskStop" in single and "TaskStop" in multi
    assert "SendMessage" not in single
    assert {"SendMessage", "TeamCreate", "TeamDelete"} <= multi


def test_dont_ask_persists_allow():
    with tempfile.TemporaryDirectory() as d:

        async def once(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("dont_ask")

        g = PermissionController(mode="default", cwd=d, request=once,
                                 tools=CODING_TOOLS)
        assert asyncio.run(g.authorize("Edit", {"file_path": "a.txt"})) is True
        assert "Edit(./a.txt)" in g._allow
        assert g.decide("Edit", {"file_path": "a.txt"}) == "allow"
        data = json.read(Path(d) / ".pyclaw" / "settings.local.json")
        assert data["permissions"]["allow"] == ["Edit(./a.txt)"]


def test_tools_return_structured_meta():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.py").write_text("x\ny\n")
        t = _tools(d)
        r = t["Read"](file_path="a.py")
        assert isinstance(r, ToolResult)
        assert r.meta["num_lines"] == 2
        assert r.meta["path"] == "a.py"
        assert r.text.startswith("a.py")

        gr = _tools(d)["Grep"](pattern="x")
        assert isinstance(gr, ToolResult)
        assert gr.meta["num_files"] == 1
        assert gr.meta["num_lines"] == 1

        w = t["Write"](file_path="b.py", content="hi\n")
        assert isinstance(w, ToolResult)
        assert w.meta["mode"] == "wrote"

        e = t["Edit"](file_path="a.py", old_string="x", new_string="X")
        assert isinstance(e, ToolResult)
        assert e.meta["num_added"] == 1 and e.meta["num_removed"] == 1

        b = _text(t["Bash"](command="exit 3"))
        assert b.startswith("Command exited with 3")
        br = t["Bash"](command="exit 3")
        assert br.meta["exit_code"] == 3


def test_suggested_rule_uses_the_addressed_path():
    g = PermissionController(mode="default", cwd="/w", tools=CODING_TOOLS)
    assert g.suggested_rule("Edit", {"file_path": "a.txt"}) == "Edit(./a.txt)"
    assert g.suggested_rule("Read", {"file_path": "./docs/x.md"}
                            ) == "Read(./docs/x.md)"
    assert g.suggested_rule("Glob", {"pattern": "*.py", "path": ".pyclaw"}
                            ) == "Glob(./.pyclaw)"
    assert g.suggested_rule("Edit", {}) is None
    assert g.suggested_rule("Edit", {"file_path": "."}) is None


def test_authorize_accepts_amended_rule():
    with tempfile.TemporaryDirectory() as d:

        async def amend(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice(
                "dont_ask", rule="Bash(git commit --amend:*)")

        g = PermissionController(mode="default", cwd=d, request=amend)
        ok = asyncio.run(g.authorize(
            "Bash", {"command": "git commit --amend -m x"}))
        assert ok is True
        assert "Bash(git commit --amend:*)" in g._allow
        assert g.decide("Bash",
                        {"command": "git commit --amend -m x"}) == "allow"


def test_suggested_rule_prefers_two_word_prefix():
    assert suggested_rule('git commit -m "fix"') == 'Bash(git commit:*)'
    assert suggested_rule('npm run test') == 'Bash(npm run:*)'
    assert suggested_rule('timeout 10 git push') == 'Bash(git push:*)'
    assert bash_rule_matches('Bash(git commit:*)', 'git commit -m "fix"')


def test_suggested_rule_exact_fallback():
    assert suggested_rule('ls -la') == 'Bash(ls -la)'
    assert suggested_rule('python3 x.py') == 'Bash(python3 x.py)'
    assert suggested_rule('mkdir a && mkdir b') == 'Bash(mkdir a && mkdir b)'
    assert bash_rule_matches('Bash(ls -la)', 'ls -la')
    assert bash_rule_matches('Bash(mkdir a && mkdir b)', 'mkdir a && mkdir b')


def test_suggested_rule_refuses_risky_commands():
    assert suggested_rule('rm -rf /') is None
    assert suggested_rule('FOO=bar npm test') is None
    assert suggested_rule('echo `whoami`') is None
    assert suggested_rule('sudo rm x') == 'Bash(sudo rm x)'


def test_dont_ask_saves_rule_to_local_settings():
    with tempfile.TemporaryDirectory() as d:

        async def always(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("dont_ask")

        g = PermissionController(mode="default", cwd=d, request=always)
        assert asyncio.run(
            g.authorize("Bash", {"command": "git commit -m x"})) is True
        data = json.read(Path(d) / ".pyclaw" / "settings.local.json")
        assert data["permissions"]["allow"] == ["Bash(git commit:*)"]
        g2 = PermissionController(mode="default", cwd=d)
        assert g2.decide("Bash", {"command": "git commit -m x"}) == "allow"


def test_rules_load_from_user_and_local_settings(tmp_path, monkeypatch):
    user_file = tmp_path / "user-settings.json"
    json.write(user_file, {"permissions": {
        "allow": ["Bash(npm run:*)"], "deny": ["Bash(curl:*)"]}})
    monkeypatch.setattr(perm, "_user_settings_file", lambda: user_file)

    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / ".pyclaw" / "settings.local.json"
        local.parent.mkdir(parents=True)
        json.write(local, {"permissions": {"deny": ["Bash(npm run:*)"]}})
        g = perm.PermissionController(mode="default", cwd=d)
        assert g.decide("Bash", {"command": "npm run test"}) == "deny"
        assert g.decide("Bash", {"command": "curl http://x"}) == "deny"
        assert g.decide("Bash", {"command": "node server.js"}) == "ask"


def test_rule_listing_reports_sources(tmp_path, monkeypatch):
    user_file = tmp_path / "user-settings.json"
    json.write(user_file, {"permissions": {"allow": ["Bash(npm run:*)"]}})
    monkeypatch.setattr(perm, "_user_settings_file", lambda: user_file)

    with tempfile.TemporaryDirectory() as d:
        g = perm.PermissionController(mode="default", cwd=d,
                                      deny=["Bash(curl:*)"])
        rules = g.rule_listing()
        assert ('allow', 'Bash(npm run:*)', 'user') in rules
        assert ('deny', 'Bash(curl:*)', 'cli') in rules


def test_rules_load_project_shared_layer(tmp_path, monkeypatch):
    user_file = tmp_path / "user-settings.json"
    json.write(user_file, {"permissions": {"allow": ["Bash(npm run:*)"]}})
    monkeypatch.setattr(perm, "_user_settings_file", lambda: user_file)

    with tempfile.TemporaryDirectory() as d:
        shared = Path(d) / ".pyclaw" / "settings.json"
        shared.parent.mkdir(parents=True)
        json.write(shared, {"permissions": {"deny": ["Bash(npm run:*)"]}})
        g = perm.PermissionController(mode="default", cwd=d)
        assert g.decide("Bash", {"command": "npm run test"}) == "deny"
        assert ('deny', 'Bash(npm run:*)', 'project') in g.rule_listing()


def test_remove_rule_deletes_from_saved_layer(tmp_path):
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / ".pyclaw" / "settings.local.json"
        local.parent.mkdir(parents=True)
        json.write(local, {"permissions": {"allow": ["Bash(a:*)", "Bash(b:*)"]}})
        g = perm.PermissionController(mode="default", cwd=d,
                                      deny=["Bash(curl:*)"])
        assert g.remove_rule("Bash(a:*)") is True
        assert json.read(local)["permissions"]["allow"] == ["Bash(b:*)"]
        assert ('allow', 'Bash(a:*)', 'local') not in g.rule_listing()
        assert g.decide("Bash", {"command": "a x"}) == "ask"
        assert g.remove_rule("Bash(curl:*)") is False
        assert g.remove_rule("Bash(zzz:*)") is False


def test_session_remove_rule(tmp_path):

    async def main():
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / ".pyclaw" / "settings.local.json"
            local.parent.mkdir(parents=True)
            json.write(local, {"permissions": {"allow": ["Bash(git push:*)"]}})
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            session = agents_mod.Session(team, session_id="s")
            return session.remove_rule("Bash(git push:*)")

    assert asyncio.run(main()) is True


def test_bash_run_in_background_returns_immediately():
    bash = _tools("/tmp")["Bash"]
    started = time.monotonic()
    out = bash(command="echo bg-done-42", run_in_background=True)
    elapsed = time.monotonic() - started
    assert elapsed < 5
    match = re.search(r"as (b[0-9a-z]{8})", out)
    assert match, out
    task_id = match.group(1)
    assert "output goes to" in out
    assert task_id in background._tasks
    task = background._tasks[task_id]
    for _ in range(50):
        if task["process"].poll() is not None:
            break
        time.sleep(0.1)
    assert task["process"].poll() == 0
    assert "bg-done-42" in task["output"].read_text(encoding="utf-8")


def test_task_output_blocks_until_completion():
    bash = _tools("/tmp")["Bash"]
    out = bash(command="sleep 0.4 && echo finished-data", run_in_background=True)
    task_id = re.search(r"as (b[0-9a-z]{8})", out).group(1)
    text = _text(_tools("/tmp")["TaskOutput"](task_id=task_id, block=True, timeout=5000))
    assert "finished-data" in text
    assert "<return_code>0</return_code>" in text
    assert "<fetch_result>success</fetch_result>" in text
    assert "<run_state>completed</run_state>" in text


def test_task_output_timeout_reports_still_running():
    bash = _tools("/tmp")["Bash"]
    out = bash(command="sleep 5", run_in_background=True)
    task_id = re.search(r"as (b[0-9a-z]{8})", out).group(1)
    text = _text(_tools("/tmp")["TaskOutput"](task_id=task_id, block=True, timeout=300))
    assert "<fetch_result>timeout</fetch_result>" in text
    assert "<run_state>running</run_state>" in text
    assert "exit_code" not in text
    background.cleanup_background_tasks()


def test_task_output_non_blocking_reports_not_ready():
    bash = _tools("/tmp")["Bash"]
    out = bash(command="sleep 5", run_in_background=True)
    task_id = re.search(r"as (b[0-9a-z]{8})", out).group(1)
    text = _text(_tools("/tmp")["TaskOutput"](task_id=task_id, block=False))
    assert "<fetch_result>not_ready</fetch_result>" in text
    background.cleanup_background_tasks()


def test_task_stop_kills_process_group():
    bash = _tools("/tmp")["Bash"]
    out = bash(command="sleep 30", run_in_background=True)
    task_id = re.search(r"as (b[0-9a-z]{8})", out).group(1)
    task = background._tasks[task_id]
    text = _text(_tools("/tmp")["TaskStop"](task_id=task_id))
    assert f"Stopped {task_id}" in text
    assert "sleep 30" in text
    for _ in range(30):
        if task["process"].poll() is not None:
            break
        time.sleep(0.1)
    assert task["process"].poll() is not None
    assert task["killed"] is True


def test_a_stopped_task_stays_readable():
    t = _tools("/tmp")
    out = t["Bash"](command="echo kept-output; sleep 30",
                   run_in_background=True)
    task_id = re.search(r"as (b[0-9a-z]{8})", out).group(1)
    def read():
        return _text(t["TaskOutput"](task_id=task_id, block=False))
    deadline = time.monotonic() + 5
    while "kept-output" not in read() and time.monotonic() < deadline:
        time.sleep(0.1)
    t["TaskStop"](task_id=task_id)
    assert "kept-output" in read()


def test_background_tasks_cleanup_kills_all():
    bash = _tools("/tmp")["Bash"]
    bash(command="sleep 30", run_in_background=True)
    bash(command="sleep 30", run_in_background=True)
    tasks = list(background._tasks.values())
    background.cleanup_background_tasks()
    for task in tasks:
        assert task["process"].poll() is not None
    assert background._tasks == {}


def test_task_output_unknown_task():
    text = _text(_tools("/tmp")["TaskOutput"](task_id="bdeadbeef"))
    assert "no such background task" in text
    text = _text(_tools("/tmp")["TaskStop"](task_id="bdeadbeef"))
    assert "no such background task" in text


def test_background_tools_registered_as_coding_tools():
    names = {t.name for t in CODING_TOOLS}
    assert {"TaskOutput", "TaskStop"} <= names


def test_background_completion_fires_notifier():
    events = []
    background.set_notifier(
        lambda tid, cmd, code, killed: events.append((tid, cmd, code, killed)))
    try:
        bash = _tools("/tmp")["Bash"]
        out = bash(command="exit 0", run_in_background=True)
        task_id = re.search(r"as (b[0-9a-z]{8})", out).group(1)
        for _ in range(30):
            if events:
                break
            time.sleep(0.1)
        assert events and events[0][0] == task_id
        assert events[0][2] == 0 and events[0][3] is False
    finally:
        background.set_notifier(None)
        background.cleanup_background_tasks()


def test_write_overwrite_returns_a_diff():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("one\ntwo\nthree\n")
        t = _tools(d)
        out = _text(t["Write"](file_path="a.txt", content="one\nTWO\nthree\n"))
        assert "Saved" in out
        assert "--- a/a.txt" in out and "+++ b/a.txt" in out
        assert "-two" in out and "+TWO" in out
        assert _read(root / "a.txt") == "one\nTWO\nthree\n"


def test_edit_result_is_a_unified_diff():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("a\nb\nc\n")
        t = _tools(d)
        out = _text(t["Edit"](file_path="a.txt", old_string="b", new_string="B"))
        assert "Saved" in out
        assert "@@ -1,3 +1,3 @@" in out
        assert "-b" in out and "+B" in out


def test_edit_replaces_every_occurrence_when_asked():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_text("x\ny\nx\n")
        t = _tools(d)
        refused = _text(t["Edit"](file_path="a.txt", old_string="x",
                                  new_string="X"))
        assert "not unique" in refused
        out = _text(t["Edit"](file_path="a.txt", old_string="x",
                              new_string="X", replace_all=True))
        assert "Saved" in out
        assert "-x" in out and "+X" in out
        assert _read(root / "a.txt") == "X\ny\nX\n"


def test_build_team_wires_task_notifications_to_lead():

    async def main():
        with tempfile.TemporaryDirectory() as d:
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            background.spawn(d, "exit 3")
            for _ in range(50):
                msgs = [m for m in team.lead.messages
                        if isinstance(m, dict)
                        and "<background_done>" in str(m.get("content"))]
                if msgs:
                    return msgs
                await asyncio.sleep(0.1)
            return []

    texts = asyncio.run(main())
    assert texts and "<result>failed</result>" in texts[0]["content"]
    assert "<task_ref>b" in texts[0]["content"]

def test_team_tools_never_ask_the_human():
    """Agent/SendMessage/TaskStop orchestrate agents this one owns.

    They are absent from the coding tool registry, so decide() used to fall
    through to its "unknown tool" branch and prompt on every single call --
    which in a fresh cwd meant the human had to approve each delegation.
    Team tools are always allowed.
    """
    from pyclaw.tools.coding.permission import AUTO_TOOLS

    with tempfile.TemporaryDirectory() as d:
        gate = PermissionController(mode="default", cwd=d)
        for name in sorted(AUTO_TOOLS):
            assert gate.decide(name, {"prompt": "x"}) == "allow", name
        assert "Bash" not in AUTO_TOOLS


def test_an_explicit_rule_still_gates_a_team_tool():
    with tempfile.TemporaryDirectory() as d:
        asking = PermissionController(mode="default", cwd=d,
                                      ask=["Agent"])
        assert asking.decide("Agent", {"prompt": "x"}) == "ask"
        denying = PermissionController(mode="default", cwd=d,
                                       deny=["Agent"])
        assert denying.decide("Agent", {"prompt": "x"}) == "deny"


def test_auto_tools_do_not_leak_into_other_tool_decisions():
    with tempfile.TemporaryDirectory() as d:
        gate = PermissionController(mode="default", cwd=d,
                                    tools=CODING_TOOLS)
        assert gate.decide("Edit", {"file_path": "a.txt"}) == "ask"
        assert gate.decide("Bash", {"command": "rm -rf /"}) == "ask"
        assert gate.decide("Read", {"file_path": "a.txt"}) == "allow"


def test_a_background_shell_is_listed_with_its_state():
    task_id = background.spawn('/tmp', 'sleep 3')
    try:
        rows = {row['id']: row for row in background.snapshot()}
        assert rows[task_id]['command'] == 'sleep 3'
        assert rows[task_id]['exit'] is None
        assert rows[task_id]['seconds'] >= 0
        background._tasks[task_id]['process'].wait()
        done = {row['id']: row for row in background.snapshot()}
        assert done[task_id]['exit'] == 0
    finally:
        background.cleanup_background_tasks()
    assert background.snapshot() == []


def test_the_editing_tools_back_up_a_file_before_changing_it():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d).resolve()
        (root / 'a.py').write_text('v0\n', encoding='utf-8')
        history = FileHistory(directory=root / 'history', cwd=root)
        tools = _tools(d, files=history)
        history.snapshot(0)
        _text(tools['Write'](file_path='a.py', content='v1\n'))
        _text(tools['Edit'](file_path='a.py', old_string='v1',
                            new_string='v2'))

        assert history.rewind(0) == ['a.py']
        assert (root / 'a.py').read_text(encoding='utf-8') == 'v0\n'


def test_a_file_created_by_the_tools_can_be_taken_away_again():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d).resolve()
        history = FileHistory(directory=root / 'history', cwd=root)
        tools = _tools(d, files=history)
        history.snapshot(0)
        _text(tools['Write'](file_path='new.py', content='created\n'))

        assert history.rewind(0) == ['new.py']
        assert not (root / 'new.py').exists()


def test_a_team_keeps_file_history_under_the_pyclaw_home():
    async def main():
        with tempfile.TemporaryDirectory() as d:
            return build_team("agnes", "agnes-2.5-flash", cwd=d), d

    team, d = asyncio.run(main())
    assert team.file_history is not None
    assert team.file_history.directory.parent.name == 'file-history'
    assert team.file_history.cwd == Path(d).resolve()
    assert team.tool_context.files is team.file_history


def test_file_history_can_be_switched_off():
    from unittest import mock

    from pyclaw import config

    async def main():
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(config, 'load',
                                   lambda: {'checkpoints': False}):
                return build_team("agnes", "agnes-2.5-flash", cwd=d)

    team = asyncio.run(main())
    assert team.file_history is None
    assert team.tool_context.files is None


def test_a_background_shell_can_be_stopped_by_id():
    task_id = background.spawn('/tmp', 'sleep 30')
    try:
        row = background.stop(task_id)
        assert row['id'] == task_id
        assert row['killed'] is True
        assert row['exit'] is not None
        assert background.stop('b0deadbeef') is None
    finally:
        background.cleanup_background_tasks()


def test_a_project_skill_is_offered_and_loads_on_demand():
    with tempfile.TemporaryDirectory() as d:
        directory = Path(d) / '.pyclaw' / 'skills' / 'notes'
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(
            '---\nname: notes\ndescription: turn a log into bullets\n'
            '---\n\nRewrite the log as bullets.\n', encoding='utf-8')

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            schemas = team.tool_schemas(team.tool_context)
            schema = next(schema for schema in schemas
                          if schema['name'] == 'Skill')
            outcome = await team.execute_tool('Skill',
                                              {'skill': 'notes'}, team.lead)
            return schema['description'], outcome.text

        description, body = asyncio.run(main())
        assert '- notes: turn a log into bullets' in description
        assert body.startswith('Rewrite the log as bullets.')


def test_no_skill_tool_is_offered_when_nothing_is_installed():
    with tempfile.TemporaryDirectory() as d:
        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d, skills=[])
            return [schema['name'] for schema in
                    team.tool_schemas(team.tool_context)]

        assert 'Skill' not in asyncio.run(main())


def test_a_denied_call_is_reported_to_hooks():
    with tempfile.TemporaryDirectory() as d:
        seen = []

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            team.hooks.register('PermissionDenied', '*',
                                fn=lambda inp: seen.append(inp) or True)
            team._pyclaw_gate.request = None
            return await _gate_call(team, "Edit", {"file_path": "a.txt"}, "")

        outcome = asyncio.run(main())
        assert _decision(outcome)["permissionDecision"] == "deny"
        assert seen and seen[0]['tool_name'] == 'Edit'


def test_a_remembered_rule_is_reported_as_a_config_change():
    with tempfile.TemporaryDirectory() as d:
        seen = []

        async def approve(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("dont_ask")

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            team.hooks.register(
                'ConfigChange', '*',
                fn=lambda inp: seen.append(inp['source']) or True)
            team._pyclaw_gate.request = approve
            return await _gate_call(team, "Edit", {"file_path": "a.txt"}, "")

        asyncio.run(main())
        assert seen == ['permissions']


def test_an_ordinary_approval_changes_no_configuration():
    with tempfile.TemporaryDirectory() as d:
        seen = []

        async def approve(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("approved")

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            team.hooks.register(
                'ConfigChange', '*',
                fn=lambda inp: seen.append(inp['source']) or True)
            team._pyclaw_gate.request = approve
            return await _gate_call(team, "Edit", {"file_path": "a.txt"}, "")

        asyncio.run(main())
        assert seen == []


def test_changing_a_setting_reports_the_config_change():
    with tempfile.TemporaryDirectory() as d:
        seen = []

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            team.hooks.register(
                'ConfigChange', '*',
                fn=lambda inp: seen.append(inp['source']) or True)
            session = agents_mod.Session(team, session_id='cfg')
            await session.note_config_change('config')
            return session.task_rows()

        asyncio.run(main())
        assert seen == ['config']


def test_a_denied_rule_only_change_is_reported_once():
    with tempfile.TemporaryDirectory() as d:
        seen = []

        async def refuse(name, inp, *, tool_use_id='', agent=''):
            return PermissionChoice("denied")

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d)
            team.hooks.register(
                'ConfigChange', '*',
                fn=lambda inp: seen.append(inp['source']) or True)
            team._pyclaw_gate.request = refuse
            return await _gate_call(team, "Edit", {"file_path": "a.txt"}, "")

        asyncio.run(main())
        assert seen == []


def test_the_team_has_a_store_the_lead_can_name(tmp_path):
    async def main():
        with tempfile.TemporaryDirectory() as d:
            return build_team("agnes", "agnes-2.5-flash", cwd=d)

    team = asyncio.run(main())
    assert team.team_store is not None
    assert team.team_store.directory.name == 'teams'
    assert team.team_context is None


def test_the_session_reports_the_team_it_joined(tmp_path):
    async def main():
        with tempfile.TemporaryDirectory() as d:
            team = build_team("agnes", "agnes-2.5-flash", cwd=d,
                              use_team=True)
            session = agents_mod.Session(team, session_id='teamctx')
            before = session.team_context
            await session._team.execute_tool(
                'TeamCreate', {'team_name': 'parser',
                                'description': 'rework'}, team.lead)
            return before, session.team_context

    before, after = asyncio.run(main())
    assert before is None
    assert after['name'] == 'parser'


def test_a_worktree_moves_the_whole_session_with_it(tmp_path):
    import subprocess

    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', 'config', 'user.email', 't@example.com'],
                   cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.name', 't'], cwd=tmp_path,
                   check=True)
    (tmp_path / 'a.txt').write_text('x\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'a.txt'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'commit', '-qm', 'first'], cwd=tmp_path, check=True)

    async def main():
        team = build_team("agnes", "agnes-2.5-flash", cwd=str(tmp_path))
        session = agents_mod.Session(team, session_id='wtree')
        before = Path(team._pyclaw_gate.cwd)
        await team.enter_worktree('side')
        return team, session, before

    team, session, before = asyncio.run(main())
    assert team.tool_context.cwd != before
    assert team._pyclaw_gate.cwd == team.tool_context.cwd
    assert session.worktree['name'] == 'side'
    assert team._pyclaw_gate.decide("Edit", {"file_path": "../a.txt"}) == "ask"

    async def leave():
        await team.exit_worktree()
        return team._pyclaw_gate.cwd, session.worktree

    back, worktree = asyncio.run(leave())
    assert back == before
    assert worktree is None


def test_a_teammate_approval_request_names_that_teammate():
    with tempfile.TemporaryDirectory() as d:
        asked = []

        async def ask(name, inp, *, tool_use_id='', agent=''):
            asked.append((name, agent))
            return PermissionChoice("approved")

        async def main():
            team = build_team("agnes", "agnes-2.5-flash", cwd=d,
                              use_team=True)
            team._pyclaw_gate.request = ask
            worker = team.create_agent("worker", instruction="do work")
            return await team.execute_tool("Write", {"file_path": "a.txt",
                                                     "content": "x"}, worker)

        asyncio.run(main())
        assert asked == [("Write", "worker")]


def test_a_built_team_gives_every_agent_type_a_place_for_notes(tmp_path):
    from pyclaw import pyclaw_home

    async def main():
        team = build_team("agnes", "agnes-2.5-flash", cwd=str(tmp_path))
        return team.agent_memory

    memory = asyncio.run(main())
    assert memory is not None
    assert (memory.directory('reviewer', 'user').parent
            == pyclaw_home() / 'agent-memory')
    assert memory.directory('reviewer', 'project').parent == (
        tmp_path / '.pyclaw' / 'agent-memory')


def test_an_agent_writing_its_own_notes_needs_no_approval(tmp_path):
    async def main():
        team = build_team("agnes", "agnes-2.5-flash", cwd=str(tmp_path))
        held = (team.agent_memory.directory('reviewer', 'user')
                / 'MEMORY.md')
        return team._pyclaw_gate.decide("Write", {"file_path": str(held)})

    assert asyncio.run(main()) == 'allow'


def test_an_explicit_rule_still_asks_about_a_notes_file(tmp_path):
    async def main():
        team = build_team("agnes", "agnes-2.5-flash", cwd=str(tmp_path),
                          ask=["Write(*)"])
        held = (team.agent_memory.directory('reviewer', 'user')
                / 'MEMORY.md')
        return team._pyclaw_gate.decide("Write", {"file_path": str(held)})

    assert asyncio.run(main()) == 'ask'


def test_loading_the_defs_seeds_an_agents_notes_from_the_project(tmp_path):
    snapshot = (tmp_path / '.pyclaw' / 'agent-memory-snapshots' / 'reviewer')
    snapshot.mkdir(parents=True)
    (snapshot / 'MEMORY.md').write_text('- seeded note', encoding='utf-8')
    (snapshot / 'snapshot.json').write_text(
        '{"updatedAt": "2026-01-01T00:00:00Z"}', encoding='utf-8')
    agents = tmp_path / '.pyclaw' / 'agents'
    agents.mkdir(parents=True)
    (agents / 'reviewer.md').write_text(
        '---\nname: reviewer\ndescription: reads diffs for a living\n'
        'memory: project\n---\n\n' + 'be harsh about the diff ' * 5,
        encoding='utf-8')

    async def main():
        team = build_team("agnes", "agnes-2.5-flash", cwd=str(tmp_path))
        held = (team.agent_memory.directory('reviewer', 'project')
                / 'MEMORY.md')
        return held.read_text(encoding='utf-8') if held.exists() else ''

    assert 'seeded note' in asyncio.run(main())


def _git(directory, *args):
    import subprocess

    return subprocess.run(('git', *args), cwd=str(directory),
                          capture_output=True, text=True)


def test_the_diff_shows_the_working_tree_of_a_repository(tmp_path):
    async def main():
        team = build_team("agnes", "agnes-2.5-flash", cwd=str(tmp_path))
        session = Session(team, session_id='diff1')
        _git(tmp_path, 'init', '-q')
        _git(tmp_path, 'config', 'user.email', 't@example.com')
        _git(tmp_path, 'config', 'user.name', 't')
        (tmp_path / 'a.py').write_text('one\n', encoding='utf-8')
        _git(tmp_path, 'add', 'a.py')
        _git(tmp_path, 'commit', '-qm', 'start')
        (tmp_path / 'a.py').write_text('one\ntwo\n', encoding='utf-8')
        view = session.diff()
        await team.end_session('done')
        return view

    view = asyncio.run(main())
    assert view['kind'] == 'git'
    assert '+two' in view['text']
    assert view['files'] == ['a.py']


def test_outside_a_repository_the_diff_lists_what_this_session_touched(
        tmp_path):
    async def main():
        team = build_team("agnes", "agnes-2.5-flash", cwd=str(tmp_path),
                          permission_mode="acceptEdits")
        session = Session(team, session_id='diff2')
        (tmp_path / 'notes.md').write_text('first\nsecond\n', encoding='utf-8')
        team.file_history.snapshot(1)
        await team.execute_tool(
            'Edit', {'file_path': 'notes.md', 'old_string': 'second',
                     'new_string': 'second\nthird'}, team.lead)
        view = session.diff()
        await team.end_session('done')
        return view

    view = asyncio.run(main())
    assert view['kind'] == 'session'
    assert view['files'] == ['notes.md']
    assert '+1 -0' in view['text']
