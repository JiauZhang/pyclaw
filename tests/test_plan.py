from chatchat.core.tasks import TaskList

from pyclaw.team_builder import agent_instruction, team_instruction

from pyclaw.tui.plan import (next_pending, next_task_line, plan_lines,
                             recent_completions)
from markup import plain


def _tasks(tmp_path):
    return TaskList(tmp_path / 'tasks')


def _add(tasks, subject, *, status='pending', owner='', blocked_by=()):
    task = tasks.create(subject, subject)
    if owner or blocked_by:
        tasks.update(task.id, owner=owner, blocked_by=list(blocked_by))
    if status != 'pending':
        tasks.update(task.id, status=status)
    return tasks.get(task.id)


def _lines(tasks, **kw):
    kwargs = dict(columns=100, rows=40, brand='#4782C8')
    kwargs.update(kw)
    return plan_lines(tasks.all(), **kwargs)


def test_the_collapsed_line_names_the_first_task_that_can_actually_run(tmp_path):
    tasks = _tasks(tmp_path)
    blocker = _add(tasks, 'Fix the parser')
    _add(tasks, 'Write the docs')
    _add(tasks, 'Ship it', blocked_by=[blocker.id])

    assert next_pending(tasks.all()).subject == 'Fix the parser'
    assert plain(next_task_line(tasks.all())) == 'Next: Fix the parser'
    tasks.update(blocker.id, status='completed')
    assert plain(next_task_line(tasks.all())) == 'Next: Write the docs'


def test_a_task_in_progress_lets_the_next_pending_one_be_named(tmp_path):
    tasks = _tasks(tmp_path)
    _add(tasks, 'Running now', status='in_progress')
    assert plain(next_task_line(tasks.all())) == ''


def test_no_tasks_no_line(tmp_path):
    assert next_task_line([]) == ''
    assert _lines(_tasks(tmp_path)) == []


def test_a_pending_task_is_a_blank_square_and_a_done_one_is_struck(tmp_path):
    tasks = _tasks(tmp_path)
    _add(tasks, 'Open item')
    _add(tasks, 'Closed item', status='completed')
    lines = _lines(tasks)

    assert lines[0] == '[white]\u25ab[/] Open item'
    assert lines[1] == '[#4EBA65]\u2713[/] [strike dim]Closed item[/]'


def test_a_blocked_task_names_what_holds_it_back(tmp_path):
    tasks = _tasks(tmp_path)
    blocker = _add(tasks, 'First')
    _add(tasks, 'Second', blocked_by=[blocker.id])
    lines = _lines(tasks)

    assert plain(lines[1]) == '▫ Second › blocked by #1'
    assert '[dim]' in lines[1]


def test_a_running_task_shows_what_its_owner_is_doing_right_now(tmp_path):
    tasks = _tasks(tmp_path)
    _add(tasks, 'Investigate the crash', owner='researcher')
    tasks.update('1', status='in_progress')
    lines = _lines(tasks, activity={'researcher': 'Looking for 3 patterns'},
                   alive={'researcher'})

    assert plain(lines[0]) == '▪ Looking for 3 patterns (@researcher)'
    assert '[bold]' in lines[0]


def test_an_absent_owner_and_a_finished_task_drop_their_labels(tmp_path):
    tasks = _tasks(tmp_path)
    _add(tasks, 'Investigate', owner='ghost')
    _add(tasks, 'Already delivered', owner='ghost', status='completed')
    lines = _lines(tasks)

    assert plain(lines[0]) == '▫ Investigate'
    assert plain(lines[1]) == '✓ Already delivered'


def test_an_owner_gets_its_row_colour(tmp_path):
    tasks = _tasks(tmp_path)
    _add(tasks, 'Investigate', owner='researcher')
    tasks.update('1', status='in_progress')
    lines = _lines(tasks, alive={'researcher'},
                   colors={'researcher': '#FF6B80'})

    assert '[#FF6B80]@researcher[/]' in lines[0]


def test_a_narrow_terminal_hides_the_owner(tmp_path):
    tasks = _tasks(tmp_path)
    _add(tasks, 'Investigate', owner='researcher')
    tasks.update('1', status='in_progress')
    lines = _lines(tasks, columns=50, alive={'researcher'})

    assert '@researcher' not in plain(lines[0])


def test_a_short_screen_keeps_the_work_in_view_and_counts_the_rest(tmp_path):
    tasks = _tasks(tmp_path)
    for index in range(6):
        _add(tasks, f'Item {index}',
             status='completed' if index < 3 else 'pending')
    full = _lines(tasks, rows=40)
    cut = _lines(tasks, rows=16)

    assert len(full) == 6
    assert len(cut) == 4
    assert plain(cut[-1]) == ' \u2026 +3 completed'


def test_a_tiny_screen_lists_everything_without_a_summary(tmp_path):
    tasks = _tasks(tmp_path)
    for index in range(6):
        _add(tasks, f'Item {index}',
             status='completed' if index < 3 else 'pending')
    lines = _lines(tasks, rows=9)

    assert len(lines) == 6
    assert not any('completed' in plain(line) and line.startswith('[dim]')
                   for line in lines)


def test_a_task_that_just_closed_is_still_counted_as_recent(tmp_path):
    tasks = _tasks(tmp_path)
    closed = _add(tasks, 'Just finished', status='completed')
    seen = {}

    assert recent_completions(tasks.all(), seen, 100.0) == {closed.id}
    assert recent_completions(tasks.all(), seen, 129.0) == {closed.id}
    assert recent_completions(tasks.all(), seen, 131.0) == set()


def test_reopening_a_task_clears_its_completion_memory(tmp_path):
    tasks = _tasks(tmp_path)
    closed = _add(tasks, 'Reopened', status='completed')
    seen = {}
    recent_completions(tasks.all(), seen, 100.0)

    tasks.update(closed.id, status='pending')
    assert recent_completions(tasks.all(), seen, 101.0) == set()
    assert seen == {}


def test_a_recently_closed_task_outranks_an_old_one(tmp_path):
    tasks = _tasks(tmp_path)
    old = _add(tasks, 'Old work', status='completed')
    _add(tasks, 'Live work', status='pending')
    fresh = _add(tasks, 'Fresh work', status='completed')

    order = [plain(line) for line in
             _lines(tasks, rows=16, recent={fresh.id})]
    assert order[0].endswith('Fresh work')
    assert order[-1].endswith('Old work') or 'completed' in order[-1]


def test_both_instructions_point_at_the_task_list():
    for instruction in (team_instruction(['Read']), agent_instruction(['Read'])):
        assert 'task_create' in instruction
        assert 'task_update' in instruction
