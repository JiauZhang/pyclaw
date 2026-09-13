from __future__ import annotations

import atexit
import secrets
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from chatchat.tool import tool

from .shell import _kill

TASK_OUTPUT_TAIL_CHARS = 30_000
TASK_BLOCK_POLL_S = 0.1
TASK_MAX_TIMEOUT_MS = 600_000
_TASK_ID_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'

_tasks: dict = {}
_notifier = None


def set_notifier(cb) -> None:
    """注册任务完成回调（pyclaw 用它把 <task-notification> 排进 lead 附件）。"""
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


def _output_path(task_id: str) -> Path:
    directory = Path(tempfile.gettempdir()) / 'pyclaw-tasks'
    directory.mkdir(exist_ok=True)
    return directory / f'{task_id}.output'


def spawn(cwd: str, command: str) -> str:
    """claude Shell.ts 的对齐：任务 id 先生成，stdout/stderr 指向同一个
    追加写文件（O_APPEND 原子交错），父进程随即释放句柄。"""
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
    """claude gracefulShutdown 语义：进程优雅退出时全部后台 shell SIGKILL。"""
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


def make_task_output(cwd: str):
    @tool(
        name='TaskOutput',
        description='Read output of a background task started with Bash '
                    'run_in_background. Returns the combined stdout/stderr '
                    'tail so far and the exit code once the task has exited.',
        parameters={
            'type': 'object',
            'properties': {
                'task_id': {'type': 'string',
                            'description': 'Background task ID (starts with b).'},
                'block': {'type': 'boolean',
                          'description': 'Wait for the task to finish before '
                                         'returning (default true).'},
                'timeout': {'type': 'integer',
                            'description': 'Max milliseconds to wait when '
                                           f'block is true (default 30000, '
                                           f'max {TASK_MAX_TIMEOUT_MS}).'},
            },
            'required': ['task_id'],
        },
    )
    def task_output(task_id: str, block: bool = True,
                    timeout: int = 30000) -> str:
        task = _tasks.get(task_id)
        if task is None:
            return f'Error: no such background task: {task_id}'
        limit = max(1, min(int(timeout or 30000), TASK_MAX_TIMEOUT_MS)) / 1000
        deadline = time.monotonic() + (limit if block else 0)
        while True:
            if task['process'].poll() is not None:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(TASK_BLOCK_POLL_S)
        body = _tail(task['output']).rstrip('\n')
        lines = [body if body.strip() else '(no output yet)']
        code = task['process'].poll()
        if code is None:
            lines.append('(still running)')
        elif task['killed']:
            lines.append(f'<exit_code>{code}</exit_code> (killed)')
        else:
            lines.append(f'<exit_code>{code}</exit_code>')
        return '\n'.join(lines)
    return task_output


def make_task_stop(cwd: str):
    @tool(
        name='TaskStop',
        description='Stop a background task started with Bash '
                    'run_in_background (kills the whole process tree).',
        parameters={
            'type': 'object',
            'properties': {
                'task_id': {'type': 'string',
                            'description': 'Background task ID (starts with b).'},
            },
            'required': ['task_id'],
        },
    )
    def task_stop(task_id: str) -> str:
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
        return f'Successfully stopped task: {task_id} ({task["command"]})'
    return task_stop
