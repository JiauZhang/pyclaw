from __future__ import annotations

import os
import secrets
import signal
import subprocess
import tempfile
from pathlib import Path

from chatchat.tool import ToolResult, tool

from .shell_rules import split_commands

DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000
MAX_OUTPUT_DEFAULT = 30_000
MAX_OUTPUT_UPPER_LIMIT = 150_000


def _env_int(name: str, fallback: int, upper: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw:
        try:
            parsed = int(str(raw).strip())
        except ValueError:
            parsed = 0
        if parsed > 0:
            return min(parsed, upper) if upper else parsed
    return fallback


def get_default_timeout_ms() -> int:
    return _env_int('BASH_DEFAULT_TIMEOUT_MS', DEFAULT_TIMEOUT_MS)


def get_max_timeout_ms() -> int:
    return max(_env_int('BASH_MAX_TIMEOUT_MS', MAX_TIMEOUT_MS),
               get_default_timeout_ms())


def get_max_output_chars() -> int:
    return _env_int('BASH_MAX_OUTPUT_LENGTH', MAX_OUTPUT_DEFAULT,
                    MAX_OUTPUT_UPPER_LIMIT)

EXIT_CODE_MESSAGES = {
    'grep': (1, 'No matches found'),
    'rg': (1, 'No matches found'),
    'find': (1, 'Some directories were inaccessible'),
    'diff': (1, 'Files differ'),
    'test': (1, 'Condition is false'),
    '[': (1, 'Condition is false'),
}


def _base_name(command: str) -> str:
    parts = split_commands(str(command))
    text = parts[-1] if parts else str(command)
    tokens = text.strip().split()
    return os.path.basename(tokens[0]) if tokens else ''


def _exit_message(command: str, code: int) -> str | None:
    special = EXIT_CODE_MESSAGES.get(_base_name(command))
    if special is not None and code == special[0]:
        return special[1]
    return None


def _persist_output(text: str) -> str | None:
    try:
        directory = Path(tempfile.gettempdir()) / 'pyclaw-bash-output'
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{secrets.token_hex(8)}.txt'
        path.write_text(text, encoding='utf-8')
        return str(path)
    except OSError:
        return None


def _truncate(text: str) -> str:
    limit = get_max_output_chars()
    if len(text) <= limit:
        return text
    rest = text[limit:]
    note = f'{rest.count(chr(10)) + 1} lines truncated'
    path = _persist_output(text)
    if path:
        note += f', full output: {path}'
    return f'{text[:limit]}\n\n... [{note}] ...'


def _clean(text: str) -> str:
    lines = str(text).split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return _truncate('\n'.join(lines))


def _join(head: str, body: str) -> str:
    return f'{head}\n{body}' if body else head


def _kill(process) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except OSError:
        process.kill()


DISALLOWED_AUTO_BACKGROUND = frozenset({'sleep'})


def _first_command(command: str) -> str:
    parts = split_commands(str(command))
    text = parts[0] if parts else str(command)
    tokens = text.strip().split()
    return os.path.basename(tokens[0]) if tokens else ''


def autobackground_allowed(command: str) -> bool:
    return _first_command(command) not in DISALLOWED_AUTO_BACKGROUND


def _read_output(path) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def run_command(cwd: str, command: str, timeout_ms: int | None = None) -> str:
    text = str(command).strip()
    if not text:
        return 'Error: empty command.'
    limit = get_default_timeout_ms() if not timeout_ms else int(timeout_ms)
    limit = max(1, min(limit, get_max_timeout_ms()))
    from .background import adopt, scratch_path
    output_path = scratch_path()
    try:
        handle = open(output_path, 'wb')
    except OSError as e:
        return f'Error: cannot run command: {e}'
    try:
        process = subprocess.Popen(
            text, shell=True, cwd=cwd, stdout=handle,
            stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        return f'Error: cannot run command: {e}'
    finally:
        handle.close()
    try:
        process.wait(timeout=limit / 1000)
    except subprocess.TimeoutExpired:
        if autobackground_allowed(text):
            task_id = adopt(text, process, output_path)
            return (f'Command timed out after {limit}ms and was moved to the '
                    f'background with ID: {task_id}. Read its output with '
                    f'TaskOutput.')
        _kill(process)
        process.wait()
        return _join(f'Error: command timed out after {limit}ms',
                     _clean(_read_output(output_path)))
    body = _clean(_read_output(output_path))
    code = process.returncode
    if code == 0:
        return ToolResult(text=body or '(no output)',
                          meta={'exit_code': 0})
    message = _exit_message(text, code)
    if message is not None:
        return ToolResult(text=_join(message, body),
                          meta={'exit_code': code})
    return ToolResult(text=_join(f'Exit code {code}', body),
                      meta={'exit_code': code})


@tool(
    name='Bash',
    description='Runs a shell command in the workspace and returns stdout '
                'and stderr combined. Prefer Read, Glob, Grep, Edit '
                'and Write over cat, find, grep, sed and shell redirection. '
                'A command that outlives its timeout usually moves to the '
                'background and reports its task ID instead of failing. Ask '
                'before destructive git commands like push --force or reset '
                '--hard.',
    parameters={
        'type': 'object',
        'properties': {
            'command': {'type': 'string',
                        'description': 'The command to execute. Chain '
                                       'dependent steps with && rather than '
                                       'newlines.'},
            'timeout': {
                'type': 'integer',
                'description': 'Milliseconds to wait before the command is '
                               f'moved to the background (default '
                               f'{get_default_timeout_ms()}, max '
                               f'{get_max_timeout_ms()}).',
            },
            'description': {
                'type': 'string',
                'description': 'Short description of what the command '
                               'does.',
            },
            'run_in_background': {
                'type': 'boolean',
                'description': 'Run in the background and return a task ID '
                               'immediately; read what it produced with '
                               'TaskOutput. Never append "&".',
            },
        },
        'required': ['command'],
    },
)
def Bash(context, command: str, timeout: int | None = None,
         description: str | None = None,
         run_in_background: bool = False) -> str:
    if run_in_background:
        from .background import _output_path, spawn
        task_id = spawn(context.cwd, command)
        return (f'Command running in background with ID: {task_id}. '
                f'Output is being written to: {_output_path(task_id)}. '
                'Read the output with TaskOutput.')
    return run_command(context.cwd, command, timeout)
