"""The task pane is five independent sections; each one is built from its own
data and joins the others with a single blank line."""
from pyclaw.tui.task_panel import (agent_tree, panel_text, shell_rows,
                                   subagent_rows, tool_rows)


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
                       {'worker': worker._state})
    assert 'team-lead' in lines[1]
    assert 'worker' in lines[2]
    assert 'working…' in lines[2]
    assert lines[0] == '[bold]Agents[/bold]'


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
