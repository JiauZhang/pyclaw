"""Every tool owns its card rendering through a ToolUI registered next to the
tool; the registry fills defaults for anything unregistered and a crashing UI
degrades to the default instead of taking the transcript down."""
import pytest

import pyclaw.tools
import pyclaw.tools.display
from pyclaw.tui import toolui
from pyclaw.tui.toolui import (build_tool_ui, collapse_kinds, hidden_card,
                               read_key, result_summary, tool_args,
                               tool_label)


def test_an_unregistered_tool_gets_the_plain_name():
    assert tool_label('Weather', {}) == 'Weather'


def test_display_names_come_from_the_theme_table():
    assert tool_label('Edit', {}) == 'Update'
    assert tool_label('Grep', {}) == 'Search'
    assert tool_label('Glob', {}) == 'Search'


def test_a_spawn_is_labelled_by_its_agent_type():
    assert tool_label('Agent', {'subagent_type': 'worker'}) == 'Agent'
    assert tool_label('Agent',
                      {'subagent_type': 'reviewer'}) == 'reviewer'
    assert tool_label('Agent', {}) == 'Agent'
    assert tool_label('Agent',
                      {'subagent_type': 'general-purpose'}) == 'Agent'


def test_path_tools_show_the_path_and_search_tools_the_pattern():
    assert tool_args('Read', {'file_path': '/tmp/w/a.py'}, '/tmp/w') == 'a.py'
    assert tool_args('Grep', {'pattern': 'x'}, '/tmp/w') == 'pattern: "x"'
    assert (tool_args('Glob', {'pattern': '*.py', 'path': 'src'}, '/tmp/w')
            == 'pattern: "*.py", path: "src"')


def test_bash_shows_the_command_clipped():
    assert tool_args('Bash', {'command': 'echo hi'}, '/tmp/w') == 'echo hi'
    long = '\n'.join(f'line{i}' for i in range(20))
    clipped = tool_args('Bash', {'command': long}, '/tmp/w')
    assert clipped.count('\n') == 1 and clipped.endswith('…')


def test_unknown_inputs_fall_back_to_the_pair_summary():
    assert (tool_args('Weather', {'city': 'Oslo', 'days': 2}, '/tmp/w')
            == 'city: Oslo, days: 2')
    assert tool_args('Weather', 'raw text', '/tmp/w') == 'raw text'


def test_a_spawn_summarises_its_prompt_and_a_message_its_target():
    assert tool_args('Agent', {'prompt': 'do ' + 'x' * 100},
                     '/tmp/w').endswith('…')
    assert (tool_args('SendMessage', {'to': 'bot', 'message': 'hi'},
                      '/tmp/w') == 'bot: hi')


def test_result_summaries_count_what_each_tool_produced():
    assert result_summary('Read', 'a.py:\nrow1\nrow2\nrow3', 80) == \
        'Read 3 lines'
    assert result_summary('Grep', 'a.py:1:x\nb.py:2:y', 80) == 'Found 2 lines'
    assert result_summary('Glob', 'a.py\nb.py', 80) == 'Found 2 files'
    assert result_summary('Agent', 'spawned worker', 80) == 'Done'


def test_generic_results_preview_and_errors_keep_their_first_line():
    text = result_summary('Weather', 'sunny\nwarm\n', 80)
    assert 'sunny' in text
    assert result_summary('Bash', 'Error: boom\nmore', 80) == 'Error: boom'
    assert result_summary('Bash', '', 80) == 'Done'


def test_collapse_kinds_come_from_each_tool():
    assert collapse_kinds('Read', {'file_path': 'a.py'}) == {'read'}
    assert collapse_kinds('Read', {'file_path': 'AGENTS.md'}) == \
        {'memory_read'}
    assert collapse_kinds('Grep', {'pattern': 'x'}) == {'search'}
    assert collapse_kinds('Glob', {'pattern': '*.py'}) == {'search'}
    assert collapse_kinds('Write', {'file_path': 'a.py'}) == set()
    assert collapse_kinds('Write', {'file_path': 'AGENTS.md'}) == \
        {'memory_write'}
    assert collapse_kinds('Edit', {'file_path': 'a.py'}) == set()
    assert collapse_kinds('Bash', {'command': 'ls'}) == {'list'}
    assert collapse_kinds('Bash', {'command': 'ls dir && echo ---'}) == \
        {'list'}
    assert collapse_kinds('Bash', {'command': 'grep x a | sort'}) == \
        {'search'}
    assert collapse_kinds('Bash', {'command': 'ls tests | head -3'}) == \
        {'list'}
    assert collapse_kinds('Bash', {'command': 'cat a.py'}) == {'read'}
    assert collapse_kinds('Bash', {'command': 'rg x'}) == {'search'}
    assert collapse_kinds('Bash', {'command': 'rg x; cat a.py'}) == \
        {'search'}
    assert collapse_kinds('Bash', {'command': 'rm -rf /'}) == {'bash'}
    assert collapse_kinds('Bash', {'command': 'echo hi'}) == set()
    assert collapse_kinds('Agent', {'prompt': 'x'}) == set()


def test_the_read_key_groups_by_path_when_there_is_one():
    assert read_key('Read', {'file_path': 'a.py'}) == 'a.py'
    assert read_key('Grep', {'pattern': 'x'}) == 'Grep'
    assert read_key('Bash', {}) == 'Bash'


def test_only_message_sends_hide_their_card():
    assert hidden_card('SendMessage', {'message': 'hi'}) is True
    assert hidden_card('SendMessage', {'to': 'bot'}) is False
    assert hidden_card('Bash', {'command': 'ls'}) is False


def test_a_crashing_ui_degrades_to_the_default(monkeypatch):
    def boom(name, tool_input):
        raise RuntimeError('no')

    monkeypatch.setitem(toolui.TOOL_UIS, 'Boom', build_tool_ui(label=boom,
                                                               args=boom))
    assert tool_label('Boom', {}) == 'Boom'
    assert tool_args('Boom', {'a': 1}, '/tmp/w') == 'a: 1'


def test_build_tool_ui_fills_every_default_and_overrides_win():
    ui = build_tool_ui()
    for method in ('label', 'args', 'summary', 'kinds', 'key', 'hidden'):
        assert callable(getattr(ui, method))
    assert build_tool_ui(
        label=lambda name, tool_input: 'X').label('Read', {}) == 'X'


def test_a_tool_declares_its_name_once():
    """The schema and the display card read the same constant, so renaming a
    tool cannot leave one of the two behind."""
    from pyclaw.tools import BUILTIN_TOOLS, names
    from pyclaw.tui.toolui import TOOL_UIS

    built_in = {names.READ, names.GLOB, names.GREP, names.WRITE, names.EDIT,
                names.BASH, names.TASK_OUTPUT, names.TASK_STOP}
    assert built_in == {tool.name for tool in BUILTIN_TOOLS}
    assert {names.READ, names.GLOB, names.GREP, names.WRITE, names.EDIT,
            names.BASH} <= set(TOOL_UIS)
    assert names.AGENT in TOOL_UIS and names.SEND_MESSAGE in TOOL_UIS
