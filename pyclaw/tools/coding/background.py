from __future__ import annotations

import atexit
import secrets
import string
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from chatchat.tool import ToolResult, tool

from .shell import _kill

TASK_OUTPUT_TAIL_CHARS = 30_000
TASK_BLOCK_POLL_S = 0.1
TASK_DEFAULT_TIMEOUT_MS = 30_000
TASK_MAX_TIMEOUT_MS = 600_000
_TASK_ID_ALPHABET = string.ascii_lowercase + string.digits

_tasks: dict = {}
_notifier = None


def set_notifier(cb) -> None:
    global _notifier
    _notifier = cb


def _watch(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        return
    code = task['process'].wait()
    cb = _notifier
    if cb is not None:
        try:
            cb(task_id, task['command'], code, task['killed'])
        except Exception:
            pass


def _new_task_id() -> str:
    return 'b' + ''.join(secrets.choice(_TASK_ID_ALPHABET) for _ in range(8))


def _tasks_dir() -> Path:
    directory = Path(tempfile.gettempdir()) / 'pyclaw-tasks'
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def scratch_path() -> Path:
    return _tasks_dir() / f'{secrets.token_hex(8)}.output'


def _output_path(task_id: str) -> Path:
    return _tasks_dir() / f'{task_id}.output'


def adopt(command: str, process, output: Path) -> str:
    task_id = _new_task_id()
    _tasks[task_id] = {'command': command, 'process': process,
                       'output': output, 'killed': False}
    threading.Thread(target=_watch, args=(task_id,), daemon=True).start()
    return task_id


def spawn(cwd: str, command: str) -> str:
    task_id = _new_task_id()
    path = _output_path(task_id)
    handle = open(path, 'ab')
    try:
        process = subprocess.Popen(
            command, shell=True, cwd=cwd, stdout=handle, stderr=handle,
            start_new_session=True)
    finally:
        handle.close()
    _tasks[task_id] = {'command': command, 'process': process,
                       'output': path, 'killed': False}
    threading.Thread(target=_watch, args=(task_id,), daemon=True).start()
    return task_id


def _tail(path: Path, limit: int = TASK_OUTPUT_TAIL_CHARS) -> str:
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''
    if len(text) <= limit:
        return text
    return (f'[... {len(text) - limit} chars of earlier output omitted ...]\n'
            + text[-limit:])


def cleanup_background_tasks():
    for task in _tasks.values():
        process = task['process']
        if process.poll() is None:
            _kill(process)
            task['killed'] = True
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    _tasks.clear()


atexit.register(cleanup_background_tasks)


@tool(
    name='TaskOutput',
    description='Reads a background task, returning a status header, the tail '
                'of its combined stdout and stderr, and the exit code once it '
                'has exited. Waits for the task by default, so a timeout '
                'report means it is still running rather than that it failed.',
    parameters={
        'type': 'object',
        'properties': {
            'task_id': {'type': 'string',
                        'description': 'Background task ID (starts with b).'},
            'block': {'type': 'boolean',
                      'description': 'Wait for the task to finish before '
                                     'returning.',
                      'default': True},
            'timeout': {'type': 'integer',
                        'description': 'Milliseconds to wait when block is '
                                       f'true (max {TASK_MAX_TIMEOUT_MS}).',
                        'default': TASK_DEFAULT_TIMEOUT_MS},
        },
        'required': ['task_id'],
    },
)
def TaskOutput(context, task_id: str, block: bool = True,
               timeout: int = TASK_DEFAULT_TIMEOUT_MS) -> str:
    task = _tasks.get(task_id)
    if task is None:
        return f'Error: no such background task: {task_id}'
    ms = TASK_DEFAULT_TIMEOUT_MS if timeout is None else timeout
    limit = max(1, min(int(ms), TASK_MAX_TIMEOUT_MS)) / 1000
    deadline = time.monotonic() + (limit if block else 0)
    while True:
        if task['process'].poll() is not None:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(TASK_BLOCK_POLL_S)
    code = task['process'].poll()
    if code is not None:
        retrieval, status = 'success', 'completed'
    elif block:
        retrieval, status = 'timeout', 'running'
    else:
        retrieval, status = 'not_ready', 'running'
    lines = [f'<fetch_result>{retrieval}</fetch_result>',
             f'<task_ref>{task_id}</task_ref>',
             '<task_kind>shell</task_kind>',
             f'<run_state>{status}</run_state>']
    if code is not None:
        lines.append(f'<return_code>{code}</return_code>')
    body = _tail(task['output']).rstrip('\n')
    lines.append(body if body.strip() else '(no output yet)')
    meta = {'status': status}
    if code is not None:
        meta['exit_code'] = code
    return ToolResult(text='\n'.join(lines), meta=meta)


@tool(
    name='TaskStop',
    description='Stops a background task, killing its whole process tree, and '
                'reports what was stopped. The output collected so far stays '
                'readable with TaskOutput afterwards.',
    parameters={
        'type': 'object',
        'properties': {
            'task_id': {'type': 'string',
                        'description': 'Background task ID (starts with b).'},
        },
        'required': ['task_id'],
    },
)
def TaskStop(context, task_id: str) -> str:
    task = _tasks.get(task_id)
    if task is None:
        return f'Error: no such background task: {task_id}'
    process = task['process']
    if process.poll() is None:
        _kill(process)
        task['killed'] = True
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return ToolResult(
        text=f'Stopped {task_id} ({task["command"]})',
        meta={'stopped': True})
