import asyncio
from datetime import datetime

import pytest

from pyclaw import task


def _job(**kw):
    async def main():
        job = task.schedule(when='0.01s', repeat=False, **kw)
        await asyncio.sleep(0.05)
        return job

    return asyncio.run(main())


def test_parse_delay_counts_seconds_from_a_suffix_or_none():
    for text, seconds in (('30s', 30.0), ('2m', 120.0), ('1h', 3600.0),
                          ('  45S ', 45.0), ('45', 45.0)):
        assert task._parse_delay(text) == seconds


def test_parse_delay_reads_a_clock_time_as_a_wait_for_today():
    now = datetime(2026, 1, 1, 10, 0, 0)
    assert task._parse_delay('11:30', now) == 5400.0
    assert task._parse_delay('09:00', now) == 82800.0
    assert task._parse_delay('10:00:30', now) == 30.0


def test_parse_delay_invalid_raises():
    with pytest.raises(ValueError):
        task._parse_delay('abc')


def test_schedule_sync_fn_executes_and_records_result():
    state = {}
    job = _job(fn=lambda: state.__setitem__('ran', True))
    assert state.get('ran') is True
    assert job['result'] == 0
    assert job['last'] is not None


def test_schedule_async_fn_executes():
    state = {}

    async def runner():
        state['ran'] = True

    job = _job(fn=runner)
    assert state.get('ran') is True
    assert job['result'] == 0


def test_schedule_fn_error_records_failure():
    def boom():
        raise RuntimeError('boom')

    job = _job(fn=boom)
    assert job['result'] == -1
    assert 'boom' in job['stderr']


def test_schedule_command_captures_output():
    job = _job(command='echo hi')
    assert job['result'] == 0
    assert 'hi' in job['stdout']


def test_cancel_removes_job():
    async def main():
        job = task.schedule(command='echo hi', when='3600s', repeat=False)
        cancelled = task.cancel(job['id'])
        await asyncio.sleep(0.02)
        return cancelled, job['id']

    cancelled, job_id = asyncio.run(main())
    assert cancelled is True
    assert all(j['id'] != job_id for j in task.list_tasks())


def test_cancel_unknown_returns_false():
    assert task.cancel('task-does-not-exist') is False


def test_list_tasks_shape():
    async def main():
        task.schedule(command='echo hi', when='3600s', repeat=False)
        await asyncio.sleep(0.01)
        return task.list_tasks()

    entries = asyncio.run(main())
    assert any(e['command'] == 'echo hi' and 'id' in e and 'next' in e
               for e in entries)
