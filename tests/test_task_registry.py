"""Every kind of background work is one record with one status vocabulary."""
from pyclaw import task_registry as tr


def test_a_finished_task_lingers_then_goes():
    task = tr.Task(id='g1', kind='agent', label='@worker', status=tr.RUNNING,
                   started_at=100.0)
    task.finish(tr.COMPLETED, now=110.0)
    registry = tr.TaskRegistry()
    registry.register(task)

    assert registry.visible(now=115.0) == [task]
    assert registry.visible(now=110.0 + tr.PANEL_GRACE_SECONDS + 1) == []


def test_a_killed_task_is_shown_briefly():
    task = tr.Task(id='g2', kind='agent', label='@other', started_at=0.0)
    task.finish(tr.KILLED, now=5.0)
    registry = tr.TaskRegistry()
    registry.register(task)

    assert registry.visible(now=6.0) == [task]
    assert registry.visible(now=5.0 + tr.STOPPED_DISPLAY_SECONDS + 1) == []


def test_a_completed_agent_lingers_longer_than_a_stopped_one():
    done = tr.Task(id='a1', kind='agent', label='@worker', started_at=0.0)
    done.finish(tr.COMPLETED, now=1.0)
    killed = tr.Task(id='a2', kind='agent', label='@other', started_at=0.0)
    killed.finish(tr.KILLED, now=1.0)

    assert done.lingers_until() > killed.lingers_until()


def test_every_kind_of_finished_work_ends_up_gone():
    shell = tr.Task(id='b6', kind='shell', label='ls', started_at=0.0)
    shell.finish(tr.COMPLETED, now=1.0)

    assert shell.lingers_until() < float('inf')


def test_the_registry_sweeps_what_was_shown_long_enough():
    registry = tr.TaskRegistry()
    finished = registry.register(tr.Task(id='a3', kind='agent', label='@x'))
    finished.finish(tr.KILLED, now=100.0)
    running = registry.register(tr.Task(id='a4', kind='agent', label='@y'))

    gone = registry.sweep(now=100.0 + tr.STOPPED_DISPLAY_SECONDS + 1)

    assert gone == [finished]
    assert registry.all() == [running]


def test_seconds_counts_up_while_running_and_freezes_when_finished():
    task = tr.Task(id='b5', kind='shell', label='x', started_at=100.0)
    assert task.seconds(112.0) == 12
    task.finish(tr.COMPLETED, now=115.0)
    assert task.seconds(200.0) == 15


def test_only_terminal_statuses_are_terminal():
    assert not any(tr.is_terminal(status) for status in (tr.PENDING, tr.RUNNING))
    assert all(tr.is_terminal(status)
               for status in (tr.COMPLETED, tr.FAILED, tr.KILLED))


def test_ids_say_what_kind_of_work_they_belong_to():
    assert tr.new_id('shell').startswith('b')
    assert tr.new_id('teammate').startswith('t')
    assert len(tr.new_id('agent')) == 9


def test_tasks_of_one_kind_come_back_in_the_order_they_started():
    registry = tr.TaskRegistry()
    later = registry.register(tr.Task(id='b7', kind='shell', label='b',
                                      started_at=20.0))
    earlier = registry.register(tr.Task(id='a1', kind='agent', label='a',
                                        started_at=10.0))

    assert registry.of_kind('shell') == [later]
    assert registry.all() == [earlier, later]
