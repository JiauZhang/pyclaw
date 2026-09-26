"""The task pane is five independent sections; each one is built from its own
data and joins the others with a single blank line."""
from pyclaw.tui.task_panel import (agent_tree, panel_text, shell_rows,
                                   subagent_rows, task_detail_lines,
                                   tool_rows)

from markup import plain


class _Agent:

    def __init__(self, name, tools=0, think=False, busy=False):
        self.name = name
        self.agent_id = f'{name}@t'
        self._state = {'tools': tools, 'think': think, 'busy': busy}


class _Team:

    def __init__(self, lead, *teammates):
        self.lead = lead
        self.agents = {agent.agent_id: agent
                       for agent in (lead, *teammates)}
        self.children = {lead.agent_id: [agent.agent_id
                                         for agent in teammates]}


def _sub(**kw):
    state = {'type': 'Explore', 'done': False, 'tools': 0, 'tokens': 0,
             'last_tool': '', 'recent': []}
    state.update(kw)
    return state


def test_the_tree_shows_the_lead_then_each_child_with_its_state():
    lead = _Agent('team-lead')
    worker = _Agent('worker', tools=3, think=True)
    lines = agent_tree(_Team(lead, worker),
                       {'worker': worker._state}, {})
    assert 'team-lead' in lines[1]
    assert 'worker' in lines[2]
    assert 'working…' in lines[2]
    assert lines[0] == '[bold]Agents[/bold]'


def test_the_tree_paints_an_agent_with_the_colour_it_was_given():
    lead = _Agent('team-lead')
    worker = _Agent('worker')
    lines = agent_tree(_Team(lead, worker), {'worker': worker._state},
                       {'worker': '#FF6B80'})
    assert '[#FF6B80][bold]worker[/][/]' in lines[2]
    assert '└─ worker ·' in plain(lines[2])


def test_the_tree_leaves_an_agent_without_a_colour_plain():
    lead = _Agent('team-lead')
    lines = agent_tree(_Team(lead), {}, {})
    assert lines[1] == '└─ team-lead · 0 tool calls (idle)'


def test_a_finished_subagent_row_says_done_and_a_running_one_its_tool():
    rows = subagent_rows({'a@t': _sub(done=True),
                          'b@t': _sub(last_tool='Read', tools=2,
                                      tokens=1234)})
    assert 'Done' in rows[2]
    assert 'Read' in rows[4]
    assert '1.2k tokens' in rows[3]
    assert subagent_rows({}) == []


def test_a_running_shell_shows_seconds_and_an_exited_one_the_code():
    rows = shell_rows([{'id': 'b1', 'command': 'npm run dev', 'seconds': 12,
                        'exit': None},
                       {'id': 'b2', 'command': 'pytest', 'seconds': 3,
                        'exit': 1}])
    assert 'npm run dev' in rows[1] and 'running' in rows[1]
    assert 'exited 1' in rows[2]
    assert shell_rows([]) == []


def test_the_tool_section_lists_what_the_model_can_call():
    rows = tool_rows([{'name': f'tool{i}'} for i in range(50)])
    assert rows[0] == '[bold]Tools[/bold] 50'
    assert len(rows) == 41


def test_empty_sections_are_dropped_and_the_rest_keep_one_gap():
    text = panel_text([[], ['plan'], ['Agents'], [], ['shells']])
    assert text.splitlines() == ['plan', '', 'Agents', '', 'shells']


def test_a_blank_line_only_separates_sections_that_exist():
    assert panel_text([[], ['only']]) == 'only'


def test_output_full_of_brackets_still_leaves_the_whole_panel_readable():
    """Tool output quotes things as ["ssh -i '/tmp/k'"], and Textual opens a
    tag at any unescaped bracket: one such row must not take the panel down."""
    from textual.content import Content
    output = ("Git(ssh_key) -> [\"ssh -i '/tmp/k' -o IdentitiesOnly=yes\"] "
              "and [/bold] too")
    text = panel_text([
        subagent_rows({'a@t': _sub(last_tool=output, tools=1)}),
        agent_tree(_Team(_Agent('lead')), {}, {'lead': '#FF6B80'}),
        shell_rows([{'id': 'b1', 'command': output, 'seconds': 1,
                     'exit': None}]),
        tool_rows([{'name': 'Read'}]),
    ])
    rendered = str(Content.from_markup(text))
    assert output in rendered
    assert 'Agents' in rendered and 'Tools 1' in rendered


def test_a_shell_detail_shows_status_runtime_command_and_output():
    lines = task_detail_lines(
        {'kind': 'shell', 'id': 'b1', 'command': 'npm run dev',
         'seconds': 12, 'exit': None}, output='ready on :3000\n')
    body = plain('\n'.join(lines))
    assert 'Background shell b1' in body
    assert 'Status:   running' in body
    assert 'Runtime:  12s' in body
    assert 'Command:  npm run dev' in body
    assert 'ready on :3000' in body


def test_a_shell_detail_says_so_while_it_has_produced_nothing():
    lines = task_detail_lines(
        {'kind': 'shell', 'id': 'b2', 'command': 'sleep 30', 'seconds': 1,
         'exit': None}, output='')
    assert lines[-1] == '[dim]nothing yet[/]'


def test_an_exited_shell_reports_its_code_and_that_it_was_stopped():
    lines = task_detail_lines(
        {'kind': 'shell', 'id': 'b3', 'command': 'pytest', 'seconds': 40,
         'exit': 1, 'killed': True}, output='1 failed\n')
    assert 'Status:   exited 1 (stopped)' in plain('\n'.join(lines))


def test_a_sub_agent_detail_names_its_type_tools_and_activity():
    lines = task_detail_lines(
        {'kind': 'sub-agent', 'label': '@worker', 'detail': 'in the background'},
        state={'tools': 2, 'last_tool': 'Read: src/a.py', 'started_at': 100.0},
        subagent={'type': 'Explore', 'tokens': 1200, 'done': False},
        now=145.0)
    body = plain('\n'.join(lines))
    assert 'Sub-agent @worker' in body
    assert 'Type:     Explore' in body
    assert 'Runtime:  45s' in body
    assert '2 tool calls' in body
    assert '1.2k' in body
    assert 'Read: src/a.py' in body
