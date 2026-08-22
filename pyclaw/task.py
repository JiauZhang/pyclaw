import asyncio
import inspect
import itertools
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

OUTPUT_LIMIT = 500
_counter = itertools.count()
_jobs: dict[str, dict] = {}


def _parse_delay(when: str, now: datetime | None = None) -> float:
    s = when.strip().lower()
    if s.endswith('s'):
        return float(s[:-1])
    if s.endswith('m'):
        return float(s[:-1]) * 60
    if s.endswith('h'):
        return float(s[:-1]) * 3600
    try:
        return float(s)
    except ValueError:
        pass
    now = now or datetime.now()
    parts = [int(x) for x in s.split(':')]
    if len(parts) < 2:
        raise ValueError(f'invalid time: {when}')
    target = now.replace(hour=parts[0], minute=parts[1], second=parts[2] if len(parts) > 2 else 0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _execute(command: str):
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return out.decode(errors='ignore'), err.decode(errors='ignore'), proc.returncode


async def _invoke(job: dict):
    if job['fn'] is not None:
        result = job['fn']()
        if inspect.isawaitable(result):
            await result
        job['result'] = 0
    else:
        out, err, code = await _execute(job['command'])
        job['result'] = code
        job['stdout'] = out[:OUTPUT_LIMIT]
        job['stderr'] = err[:OUTPUT_LIMIT]


async def _run_job(job: dict):
    try:
        while True:
            await asyncio.sleep(job['delay'])
            logger.info("scheduled task %s firing: %s", job['id'], job.get('command') or job.get('name', ''))
            try:
                await _invoke(job)
            except Exception as exc:
                job['result'] = -1
                job['stderr'] = str(exc)
            job['last'] = datetime.now()
            job['next'] = job['last'] + timedelta(seconds=job['delay'])
            if not job['repeat']:
                break
    except asyncio.CancelledError:
        pass
    finally:
        _jobs.pop(job['id'], None)


def schedule(command: str | None = None, when: str = '1s', repeat: bool = False, fn=None, name: str = '') -> dict:
    job = {
        'id': f'task-{next(_counter)}',
        'command': command,
        'name': name,
        'delay': _parse_delay(when),
        'repeat': repeat,
        'fn': fn,
        'next': None,
        'last': None,
        'result': None,
        'stdout': '',
        'stderr': '',
    }
    job['next'] = datetime.now() + timedelta(seconds=job['delay'])
    loop = asyncio.get_running_loop()
    job['task'] = loop.create_task(_run_job(job))
    _jobs[job['id']] = job
    return job


def schedule_delivery(text: str, when: str, deliver):
    return schedule(fn=lambda text=text: deliver(text), when=when, name=f'deliver: {text[:40]}')


def cancel(task_id: str) -> bool:
    job = _jobs.pop(task_id, None)
    if job is None:
        return False
    job['task'].cancel()
    return True


def list_tasks() -> list[dict]:
    return [
        {'id': j['id'], 'command': j['command'], 'next': j['next'], 'last': j['last'], 'repeat': j['repeat']}
        for j in _jobs.values()
    ]