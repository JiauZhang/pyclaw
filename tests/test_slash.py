import asyncio

from fakes import Usage
from markup import plain

from pyclaw import config, slash


HELP_KEYWORDS = ("/help", "/clear", "/resume", "/status", "/model", "/cost",
                 "/agents", "/context")


class _Bare:
    pass


def _fake_session(**kwargs):
    class FakeSession:
        name = "s1"
        mode = "agent"
        thinking = False
        provider = "p"
        model = "m"
        available_tools = ["a", "b"]
        context_messages = 0
        lead_instruction = "You solve the task."
        instruction_files = [{"path": "AGENTS.md", "content": "# rules"}]
        schemas = [{"name": "Read", "description": "read a file",
                    "input_schema": {"type": "object"}}]

        def tool_schemas(self):
            return list(self.schemas)

        def transcript(self):
            return list(getattr(self, 'messages', []))
        active_agents = 0
        usage = Usage()

        def __init__(self, **kw):
            self.ended = []
            for k, v in kwargs.items():
                setattr(self, k, v)

        def agent_usage(self):
            return list(getattr(self, 'agents', ()))

        async def end_session(self, reason):
            self.ended.append(reason)

        async def note_config_change(self, source):
            self.noticed = source

        def reset(self):
            self._reset = True

        def set_model(self, model):
            self.model = model

    return FakeSession(**kwargs)


def _strip(text):
    return plain(text)


async def _call(*args, **kw):
    return await slash.handle_slash(*args, **kw)


def test_help_lists_commands():
    out = asyncio.run(_call("/help", _fake_session()))
    assert out is not None
    for kw in HELP_KEYWORDS:
        assert kw in out


def test_unknown_command_returns_help():
    out = asyncio.run(_call("/nope", _fake_session()))
    assert out.startswith("Unknown command")
    assert "/help" in out


def test_non_slash_returns_none():
    assert asyncio.run(_call("hello world", _fake_session())) is None


def test_agents_command_lists_the_types():
    from types import SimpleNamespace
    session = SimpleNamespace(agent_types=[('reviewer', 'Reviews code')])
    reply = asyncio.run(_call('/agents', session))
    assert 'reviewer: Reviews code' in reply


def test_clear_command():
    session = _fake_session()
    out = asyncio.run(_call("/clear", session))
    assert "cleared" in out.lower()
    assert session.ended == ["clear"]


def test_tools_and_thinking_commands_removed():
    assert "/tools" not in slash.HELP and "/thinking" not in slash.HELP
    assert "Unknown command" in asyncio.run(_call("/tools", _fake_session()))
    assert "Unknown command" in asyncio.run(_call("/thinking", _fake_session()))


def test_status_reports_the_session_and_its_environment():
    session = _fake_session()
    out = asyncio.run(_call("/status", session, session_key="k1"))
    assert "k1" in out
    assert "agent" in out
    assert "pyclaw:" in out
    assert "Directory:" in out


def test_init_returns_prompt_tuple_for_model():
    class _S:
        cwd = "/tmp/demo"

    info, prompt = asyncio.run(_call("/init", _S()))
    assert isinstance(info, str) and prompt
    assert "AGENTS.md" in prompt
    assert "PyClaw" not in prompt


def test_init_reports_existing_file(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")

    class _S:
        cwd = str(tmp_path)

    info, prompt = asyncio.run(_call("/init", _S()))
    assert "already exists" in info
    assert "AGENTS.md" in prompt


def test_memory_command_points_at_agents_md(tmp_path):
    class _S:
        cwd = str(tmp_path)

    out = asyncio.run(_call("/memory", _S()))
    assert "AGENTS.md" in out
    assert "PYCLAW.md" not in out


def test_plan_with_description_queues_the_goal():
    class _S:
        permission_mode = "default"

        def set_permission_mode(self, mode):
            self.permission_mode = mode

        def permission_rules(self):
            return []

    s = _S()
    reply = asyncio.run(_call("/plan write a parser", s))
    assert isinstance(reply, tuple)
    _text, goal = reply
    assert goal == "write a parser"
    assert s.permission_mode == "plan"

    assert "No plan file" in asyncio.run(_call("/plan open", s))
    assert "plan" in asyncio.run(_call("/plan", s)).lower()


def test_status_includes_usage_and_cost():
    session = _fake_session(usage=Usage(1200, 300, 1500))
    out = asyncio.run(_call("/status", session, session_key="k1"))
    assert "1200 in" in out
    assert "300 out" in out
    assert "unpriced" in out


def test_model_command_reports_current_model():
    out = asyncio.run(_call("/model", _fake_session()))
    assert out == "Model: m"


def test_model_command_switches_session_and_config(monkeypatch):
    saved = {}
    monkeypatch.setattr(config, "save", lambda c: saved.update(c))
    monkeypatch.setattr("pyclaw.load", lambda: {"model": "m"})

    session = _fake_session()
    out = asyncio.run(_call("/model newmodel", session))
    assert out == "Model: newmodel"
    assert session.model == "newmodel"
    assert saved["model"] == "newmodel"


def test_cost_command_unpriced(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"pricing": {}})
    session = _fake_session(usage=Usage(1200, 300, 1500))
    out = asyncio.run(_call("/cost", session))
    assert "unpriced" in out
    assert "pricing.m" in out
    assert "1200 in" in out


def test_cost_command_with_pricing(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {
        "pricing": {"m": {"input": 1, "output": 2}}})
    session = _fake_session(usage=Usage(1000, 500, 1500))
    out = asyncio.run(_call("/cost", session))
    assert "$0.0020" in out


def test_cost_breaks_the_spend_down_by_agent(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {
        "pricing": {"m": {"input": 1, "output": 2}}})
    session = _fake_session(usage=Usage(1500, 600, 2100), agents=[
        ("team-lead", "m", Usage(1000, 500, 1500)),
        ("researcher", "cheap-m", Usage(500, 100, 600)),
    ])
    out = asyncio.run(_call("/cost", session))

    assert "By agent:" in out
    assert "@researcher \u00b7 cheap-m \u00b7 500 in / 100 out" in out
    assert "@team-lead \u00b7 m \u00b7 1000 in / 500 out" in out


def test_cost_stays_a_single_block_without_a_team(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"pricing": {}})
    out = asyncio.run(_call("/cost", _fake_session(usage=Usage(1200, 300, 1500))))
    assert "By agent:" not in out


def test_mode_switch_commands_removed():
    out = asyncio.run(_call("/agent", _fake_session()))
    assert "Unknown command" in out
    out = asyncio.run(_call("/team", _fake_session()))
    assert "Unknown command" in out
    names = {entry['name'] for entry in slash.COMMANDS}
    assert 'agent' not in names and 'team' not in names


def test_statusline_command_queues_the_setup_agent():
    info, prompt = asyncio.run(_call("/statusline", _Bare()))
    assert isinstance(info, str) and info
    assert "statusline-setup" in prompt
    assert "Set up my status line from my shell PS1 configuration" in prompt
    assert "/statusline" in slash.HELP


def test_statusline_command_forwards_the_user_instructions():
    _, prompt = asyncio.run(_call("/statusline show the model in green", _Bare()))
    assert "show the model in green" in prompt
    assert "statusline-setup" in prompt
    assert "Configure my statusLine" not in prompt


def test_compact_calls_session_compactor():
    class _S:
        compacted = False

        async def compact(self):
            self.compacted = True
            return 'Compacted: 10 -> 4 messages'

    s = _S()
    out = asyncio.run(_call("/compact", s))
    assert s.compacted is True
    assert "Compacted" in out


def test_permissions_lists_mode_and_rules_with_sources():
    class _S:
        permission_mode = "default"

        def permission_rules(self):
            return [("allow", "Bash(git commit:*)", "local"),
                    ("deny", "Bash(curl:*)", "user")]

    out = asyncio.run(_call("/permissions", _S()))
    assert "default" in out
    assert "allow" in out and "Bash(git commit:*)" in out and "local" in out
    assert "deny" in out and "Bash(curl:*)" in out and "user" in out


def test_permissions_remove_rule():
    class _S:
        removed = None
        noted = None

        async def note_config_change(self, source):
            self.noted = source

        def permission_mode(self):
            return "default"

        permission_mode = "default"

        def permission_rules(self):
            return [("allow", "Bash(a:*)", "local")]

        def remove_rule(self, rule):
            self.removed = rule
            return rule == "Bash(a:*)"

    s = _S()
    out = asyncio.run(_call("/permissions remove Bash(a:*)", s))
    assert s.removed == "Bash(a:*)"
    assert s.noted == "permissions"
    assert "Removed" in out
    out = asyncio.run(_call("/permissions remove Nope", _S()))
    assert "not found" in out


def test_suggest_matches_alias_and_description():
    names = [c["name"] for c in slash.suggest("/h")]
    assert "help" in names
    names = [c["name"] for c in slash.suggest("/tok")]
    assert "cost" in names


def test_suggest_empty_query_lists_all_and_args_hide_menu():
    assert len(slash.suggest("/")) == len(slash.COMMANDS)
    assert slash.suggest("/model x") == []
    assert slash.suggest("/model ") == []
    assert slash.suggest("hello") == []


def test_suggest_case_insensitive():
    names = [c["name"] for c in slash.suggest("/MO")]
    assert names[0] == "model"


def test_suggest_fuzzy_matches_typos():
    names = [c["name"] for c in slash.suggest("/staus")]
    assert names[0] == "status"


def test_suggest_prefix_name_beats_fuzzy():
    names = [c["name"] for c in slash.suggest("/he")]
    assert names[0] == "help"
    prefix_first = [c["name"] for c in slash.suggest("/per")]
    assert prefix_first[0] == "permissions"


def test_suggest_recently_used_commands_sort_first():
    slash._USAGE.clear()
    try:
        slash._USAGE["cost"] = 3
        slash._USAGE["model"] = 1
        names = [c["name"] for c in slash.suggest("/")]
        assert names.index("cost") < names.index("model")
        assert names.index("model") < names.index("help")
    finally:
        slash._USAGE.clear()


def test_handle_slash_tracks_usage():
    slash._USAGE.clear()
    try:
        asyncio.run(_call("/cost", _fake_session()))
        asyncio.run(_call("/model m", _fake_session()))
        asyncio.run(_call("/cost", _fake_session()))
        assert slash._USAGE["cost"] == 2
        assert slash._USAGE["model"] == 1
        unknown = asyncio.run(_call("/unknown-zzz", _fake_session()))
        assert unknown.startswith("Unknown command")
        assert "unknown-zzz" not in slash._USAGE
    finally:
        slash._USAGE.clear()


CONTEXT_SESSION = dict(
    model="m", used_context=40_000, context_window=200_000,
    compact_threshold=160_000, context_messages=12,
    lead_instruction="You solve the task.",
    instruction_files=[{"path": "AGENTS.md", "content": "# project rules\n"}],
    schemas=[{"name": "Read", "description": "d", "input_schema": {}},
             {"name": "Bash", "description": "e" * 400, "input_schema": {}}],
    agent_types=[("reviewer", "reads diffs")], messages=[
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": "on it"},
    ])


def test_context_reports_what_the_model_is_shown_in_characters(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {})
    out = asyncio.run(_call("/context", _fake_session(**CONTEXT_SESSION)))
    plain = _strip(out)

    assert "System prompt" in plain
    assert "Memory files" in plain and "AGENTS.md" in plain
    assert "Tools:" in plain and "Bash" in plain
    assert "Agents:" in plain and "reviewer" in plain
    assert "Conversation:   12 messages" in plain


def test_context_shows_only_the_measured_token_numbers(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {})
    out = _strip(asyncio.run(_call("/context", _fake_session(**CONTEXT_SESSION))))

    assert "Measured: 40,000 of 200,000 tokens (20%) \u00b7 160,000 free" in out
    assert "Auto-compact at 160,000 tokens" in out
    assert "in characters" in out
    assert "token" not in out.split('in characters')[1]


def test_rewind_lists_the_turns_that_can_be_given_back():
    session = _fake_session(
        turns=lambda: [(0, 'set up the parser'), (2, 'fix the tests')])
    out = _strip(asyncio.run(_call('/rewind', session)))
    assert '1. set up the parser' in out
    assert '2. fix the tests' in out


def test_rewind_puts_a_turn_back_and_reports_what_moved():
    seen = {}

    def rewind(mark, code, conversation):
        seen.update(mark=mark, code=code, conversation=conversation)
        return {'files': ['a.py'], 'messages': 3}

    session = _fake_session(turns=lambda: [(0, 'a'), (2, 'b')], rewind=rewind)
    out = _strip(asyncio.run(_call('/rewind 1', session)))
    assert seen == {'mark': 0, 'code': True, 'conversation': True}
    assert '1 file' in out
    assert '3 messages' in out


def test_rewind_can_be_limited_to_the_files():
    seen = {}

    def rewind(mark, code, conversation):
        seen.update(code=code, conversation=conversation)
        return {'files': ['a.py', 'b.py'], 'messages': 0}

    session = _fake_session(turns=lambda: [(0, 'a')], rewind=rewind)
    out = _strip(asyncio.run(_call('/rewind 1 code', session)))
    assert seen == {'code': True, 'conversation': False}
    assert '2 files' in out
    assert 'messages' not in out


def test_rewind_can_be_limited_to_the_conversation():
    def rewind(mark, code, conversation):
        return {'files': [], 'messages': 5}

    session = _fake_session(turns=lambda: [(0, 'a')], rewind=rewind)
    out = _strip(asyncio.run(_call('/rewind 1 conversation', session)))
    assert '5 messages' in out
    assert 'files' not in out


def test_rewind_refuses_a_turn_it_has_no_record_of():
    called = []
    session = _fake_session(turns=lambda: [(0, 'a')],
                            rewind=lambda **kw: called.append(kw))
    out = _strip(asyncio.run(_call('/rewind 9', session)))
    assert 'no turn 9' in out
    assert called == []


def test_rewind_says_so_when_nothing_is_recorded():
    session = _fake_session(turns=lambda: [])
    out = _strip(asyncio.run(_call('/rewind', session)))
    assert 'file history' in out


def test_tasks_lists_what_is_running_in_the_background():
    session = _fake_session(task_rows=lambda: [
        {'kind': 'teammate', 'id': 'worker', 'label': '@worker',
         'detail': 'working', 'stoppable': True},
        {'kind': 'shell', 'id': 'b1', 'label': 'npm run dev',
         'detail': 'running 12s', 'stoppable': True}])
    out = _strip(asyncio.run(_call('/tasks', session)))
    assert '@worker · working' in out
    assert 'b1 npm run dev · running 12s' in out
    assert 'panel' in out


def test_tasks_says_so_when_nothing_runs():
    session = _fake_session(task_rows=lambda: [])
    assert _strip(asyncio.run(_call('/tasks', session))) == (
        'No background tasks are running.')


def test_skills_lists_what_was_found_and_where_it_came_from():
    session = _fake_session(skill_problems=lambda: [], skill_rows=lambda: [
        {'name': 'notes', 'description': 'turn a log into bullets',
         'source': 'project', 'allowed_tools': ('Read',)},
        {'name': 'pdf', 'description': 'work with pdfs', 'source': 'user',
         'allowed_tools': ()}])
    out = _strip(asyncio.run(_call('/skills', session)))
    assert 'notes' in out and 'project' in out
    assert 'allows Read' in out
    assert 'pdf' in out


def test_skills_reports_the_directories_it_could_not_use():
    session = _fake_session(skill_rows=lambda: [],
                            skill_problems=lambda: ['vague: no description'])
    out = _strip(asyncio.run(_call('/skills', session)))
    assert 'vague: no description' in out


def test_skills_says_so_when_none_are_installed():
    session = _fake_session(skill_rows=lambda: [], skill_problems=lambda: [])
    assert 'No skills are installed' in _strip(asyncio.run(_call('/skills', session)))


def test_hooks_lists_what_will_run_and_where_it_came_from():
    session = _fake_session(hook_rows=lambda: [
        {'event': 'PreToolUse', 'matcher': 'Bash', 'type': 'command',
         'source': 'projectSettings', 'detail': 'npm test'},
        {'event': 'Stop', 'matcher': '*', 'type': 'prompt',
         'source': 'sessionHook', 'detail': 'check the tests'}])
    out = _strip(asyncio.run(_call('/hooks', session)))
    assert 'PreToolUse' in out and 'projectSettings' in out
    assert 'npm test' in out
    assert 'Stop' in out


def test_hooks_says_so_when_none_are_configured():
    session = _fake_session(hook_rows=lambda: [])
    assert 'No hooks' in _strip(asyncio.run(_call('/hooks', session)))


def test_status_names_the_active_team():
    session = _fake_session(team_context={'name': 'parser',
                                          'description': 'rework'})
    out = _strip(asyncio.run(_call('/status', session)))
    assert 'Team: parser' in out


def test_status_omits_the_team_line_without_one():
    out = _strip(asyncio.run(_call('/status', _fake_session())))
    assert 'Team:' not in out
