import asyncio

from fakes import Usage
from markup import plain

from pyclaw import config, slash


HELP_KEYWORDS = ("/help", "/clear", "/resume", "/status", "/model", "/cost",
                 "/agents", "/context")


class _Bare:
    pass


def _thinking():
    from chatchat.core.thinking import Thinking

    return Thinking()


def _fake_session(**kwargs):
    class FakeSession:
        name = "s1"
        mode = "agent"
        thinking = _thinking()
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
            self.set = []
            for k, v in kwargs.items():
                setattr(self, k, v)

        def set_thinking(self, thinking):
            self.thinking = thinking
            self.set.append(thinking)

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


def test_the_tools_command_stays_gone_but_reasoning_is_settable():
    assert "/tools" not in slash.HELP
    assert "Unknown command" in asyncio.run(_call("/tools", _fake_session()))
    assert "/thinking" in slash.HELP and "/effort" in slash.HELP


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


def test_status_names_the_worktree_the_session_moved_into():
    session = _fake_session(worktree={'name': 'side', 'branch': 'wt/side',
                                      'path': '/tmp/repo/.pyclaw/worktrees/side'})
    out = _strip(asyncio.run(_call('/status', session)))
    assert 'Worktree: wt/side' in out


def test_thinking_can_be_switched_and_persists_for_next_time(monkeypatch,
                                                              tmp_path):
    written = {}
    monkeypatch.setattr(config, 'load', lambda: {'thinking': {}})
    monkeypatch.setattr(config, 'save', written.update)
    session = _fake_session()

    out = asyncio.run(_call('/thinking adaptive', session))

    assert session.set[-1].mode == 'adaptive'
    assert 'adaptive' in out
    assert written['thinking']['mode'] == 'adaptive'


def test_a_budget_is_read_from_the_argument(monkeypatch):
    monkeypatch.setattr(config, 'load', lambda: {'thinking': {'mode': 'on'}})
    monkeypatch.setattr(config, 'save', lambda cfg: None)
    session = _fake_session()

    asyncio.run(_call('/thinking 8000', session))

    assert (session.set[-1].mode, session.set[-1].budget) == ('on', 8000)


def test_an_unknown_reasoning_setting_changes_nothing(monkeypatch):
    monkeypatch.setattr(config, 'load', lambda: {'thinking': {'mode': 'on'}})
    monkeypatch.setattr(config, 'save',
                        lambda cfg: (_ for _ in ()).throw(AssertionError()))
    session = _fake_session()

    out = asyncio.run(_call('/thinking sometimes', session))

    assert 'adaptive' in out and session.set == []


def test_effort_is_a_level_and_auto_gives_it_back(monkeypatch):
    monkeypatch.setattr(config, 'load', lambda: {'thinking': {}})
    monkeypatch.setattr(config, 'save', lambda cfg: None)
    session = _fake_session()

    asyncio.run(_call('/effort high', session))
    assert session.set[-1].effort == 'high'
    asyncio.run(_call('/effort auto', session))
    assert session.set[-1].effort == ''
    unknown = asyncio.run(_call('/effort extreme', session))
    assert 'low, medium, high' in unknown


def test_the_status_line_shows_the_reasoning_setting(monkeypatch):
    from chatchat.core.thinking import Thinking

    monkeypatch.setattr(config, 'load', lambda: {'thinking': {}})
    session = _fake_session()
    session.thinking = Thinking('adaptive', effort='medium')

    out = asyncio.run(_call('/status', session, session_key='k1'))

    assert 'thinking adaptive' in out and 'effort medium' in out


def _history(monkeypatch, tmp_path, rows):
    import json
    from datetime import date, timedelta

    from pyclaw import usage_history

    home = tmp_path / 'usage'
    home.mkdir(exist_ok=True)
    today = date.today()
    monkeypatch.setattr(usage_history, '_directory', lambda: home)
    for row in rows:
        day = row['day']
        path = home / f'{day}.jsonl'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row) + '\n')
    return today


def _row(day, **kw):
    from chatchat.core.metrics import Metrics

    base = {'at': f'{day}T09:00:00', 'day': day, 'session': 's1',
            'provider': 'agnes', 'model': 'm', 'input': 1000, 'output': 200,
            'cached': 400, 'turns': 1,
            'metrics': Metrics().as_dict()}
    if 'metrics' in kw:
        base['metrics'] = {**base['metrics'], **kw.pop('metrics')}
    row = {**base, **kw}
    row['total'] = row['input'] + row['output']
    return row


def test_usage_reports_the_measured_numbers_for_one_day(monkeypatch, tmp_path):
    from datetime import date

    today = date.today().isoformat()
    _history(monkeypatch, tmp_path,
             [_row(today, metrics={'tool_calls': 3}),
              _row(today, input=500, output=50,
                   metrics={'tool_calls': 2, 'api_ms': 1500,
                            'lines_added': 7, 'denials': 1})])

    out = plain(asyncio.run(_call('/usage', _fake_session())))

    assert '2 turns' in out
    assert '1.8k' in out
    assert '5 tool calls' in out
    assert '1.5s' in out
    assert '7 lines added' in out
    assert '1 refused' in out


def test_usage_can_widen_to_a_week(monkeypatch, tmp_path):
    from datetime import date, timedelta

    today = date.today()
    old = (today - timedelta(days=5)).isoformat()
    _history(monkeypatch, tmp_path, [_row(today.isoformat()), _row(old)])

    day = plain(asyncio.run(_call('/usage today', _fake_session())))
    week = plain(asyncio.run(_call('/usage week', _fake_session())))

    assert 'input: 1000' in day
    assert 'input: 2000' in week


def test_usage_says_so_when_nothing_has_been_recorded(monkeypatch, tmp_path):
    _history(monkeypatch, tmp_path, [])

    out = plain(asyncio.run(_call('/usage', _fake_session())))

    assert 'Nothing recorded yet' in out


def test_stats_lists_the_days_newest_first(monkeypatch, tmp_path):
    from datetime import date, timedelta

    today = date.today()
    _history(monkeypatch, tmp_path,
             [_row((today - timedelta(days=1)).isoformat()),
              _row(today.isoformat(), output=900)])

    out = plain(asyncio.run(_call('/stats', _fake_session())))

    lines = out.splitlines()
    assert lines[1].startswith(today.isoformat())
    assert today.isoformat() in out and (today - timedelta(days=1)).isoformat() in out
    assert out.index('900') < out.index('200')


def test_stats_counts_the_errors_recorded_in_the_same_window(monkeypatch,
                                                            tmp_path):
    from datetime import date, datetime

    from pyclaw import events

    today = date.today()
    _history(monkeypatch, tmp_path, [_row(today.isoformat())])
    home = tmp_path / 'events'
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(events, '_directory', lambda: home)
    sink = events.open_stream(at=datetime(today.year, today.month, today.day,
                                          9, 0), session='s1')
    sink.note_error('the provider went away')
    sink.close()

    out = plain(asyncio.run(_call('/stats', _fake_session())))

    assert '1 error recorded' in out
    assert 'provider went away' in out


def _clipboard(sequence: str) -> str:
    import base64

    payload = sequence.split(';c;')[1].rstrip('\x07')
    return base64.b64decode(payload).decode()


def _turn_session():
    session = _fake_session(messages=[
        {'role': 'user', 'content': 'fix the parser please'},
        {'role': 'assistant', 'content': 'First reply\n\n```python\ndef f():\n    return 1\n```'},
        {'role': 'user', 'content': [{'type': 'tool_result',
                                      'tool_use_id': 't1', 'content': 'ok'}]},
        {'role': 'assistant', 'content': 'Second reply with 中文'}])
    return session


def test_export_writes_the_conversation_as_markdown(tmp_path):
    import os

    session = _turn_session()
    os.chdir(tmp_path)
    out = asyncio.run(_call('/export', session))

    written = sorted(tmp_path.glob('*.md'))
    assert len(written) == 1 and out.endswith(written[0].name)
    body = written[0].read_text(encoding='utf-8')
    assert written[0].name.endswith('.md')
    assert 'fix the parser please' in body
    assert 'Second reply with 中文' in body
    assert body.index('fix the parser') < body.index('First reply')


def test_export_takes_a_name_when_given_one(tmp_path):
    import os

    session = _turn_session()
    os.chdir(tmp_path)
    asyncio.run(_call('/export notes.md', session))

    assert (tmp_path / 'notes.md').exists()


def test_copy_reports_the_answer_and_the_fallback_file(tmp_path, monkeypatch):
    session = _turn_session()
    sent = []
    monkeypatch.setattr('pyclaw.notify.set_title', lambda write, title: None)
    out = plain(asyncio.run(_call('/copy', session, terminal=sent.append)))

    assert 'Copied' in out and 'Second reply' not in out
    assert any('\033]52;' in row for row in sent)


def test_copy_can_reach_an_older_answer_and_a_code_block(tmp_path, monkeypatch):
    session = _turn_session()
    sent = []
    plain(asyncio.run(_call('/copy 2', session, terminal=sent.append)))
    assert 'First reply' in _clipboard(sent[-1])
    sent.clear()
    out = plain(asyncio.run(_call('/copy 2:1', session, terminal=sent.append)))
    assert 'code block' in out or 'python' in out
    assert 'def f():' in _clipboard(sent[-1])
    sent.clear()
    missing = plain(asyncio.run(_call('/copy 1:3', session,
                                      terminal=sent.append)))
    assert 'No code block' in missing and sent == []


def test_copy_says_so_when_there_is_nothing_to_copy(monkeypatch):
    out = plain(asyncio.run(_call('/copy', _fake_session(),
                                  terminal=lambda s: None)))
    assert 'Nothing to copy' in out


def test_the_copy_targets_are_the_reply_and_its_code_blocks():
    from pyclaw.export import copy_targets

    session = _turn_session()
    targets = copy_targets(session.transcript())
    assert [target[0] for target in targets] == ['whole reply']
    assert targets[0][1] == 'Second reply with 中文'
    older = copy_targets(session.transcript(), which=2)
    assert [label for label, _ in older] == ['whole reply', 'python']
    assert older[1][1] == 'def f():\n    return 1'


def test_the_export_names_itself_after_the_first_prompt(tmp_path):
    from datetime import datetime

    from pyclaw.export import export_text, filename_for

    session = _turn_session()
    body = export_text(session.transcript(), session_id='abc123')
    assert '# PyClaw session abc123' in body
    assert '**you**' in body and '**pyclaw**' in body
    name = filename_for(session.transcript(),
                        when=datetime(2026, 5, 4, 9, 30, 12))
    assert name == '2026-05-04-093012-fix-the-parser-please.md'
