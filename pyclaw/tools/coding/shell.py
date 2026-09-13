from __future__ import annotations

import os
import signal
import subprocess

from chatchat.tool import tool

BASH_DEFAULT_TIMEOUT_MS = 120_000
BASH_MAX_TIMEOUT_MS = 600_000
BASH_MAX_OUTPUT_CHARS = 30_000

EXIT_CODE_MESSAGES = {
    'grep': (1, 'No matches found'),
    'rg': (1, 'No matches found'),
    'find': (1, 'Some directories were inaccessible'),
    'diff': (1, 'Files differ'),
    'test': (1, 'Condition is false'),
    '[': (1, 'Condition is false'),
}


def _base_name(command: str) -> str:
    tokens = str(command).strip().split()
    return os.path.basename(tokens[0]) if tokens else ''


def _exit_message(command: str, code: int) -> str | None:
    special = EXIT_CODE_MESSAGES.get(_base_name(command))
    if special is not None and code == special[0]:
        return special[1]
    return None


def _truncate(text: str) -> str:
    if len(text) <= BASH_MAX_OUTPUT_CHARS:
        return text
    head = text[:BASH_MAX_OUTPUT_CHARS]
    rest = text[BASH_MAX_OUTPUT_CHARS:]
    return f'{head}\n\n... [{rest.count(chr(10)) + 1} lines truncated] ...'


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


def run_command(cwd: str, command: str, timeout_ms: int | None = None) -> str:
    text = str(command).strip()
    if not text:
        return 'Error: empty command.'
    limit = BASH_DEFAULT_TIMEOUT_MS if not timeout_ms else int(timeout_ms)
    limit = max(1, min(limit, BASH_MAX_TIMEOUT_MS))
    try:
        process = subprocess.Popen(
            text, shell=True, cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    except OSError as e:
        return f'Error: cannot run command: {e}'
    try:
        output, _ = process.communicate(timeout=limit / 1000)
    except subprocess.TimeoutExpired:
        _kill(process)
        output, _ = process.communicate()
        return _join(f'Error: command timed out after {limit}ms', _clean(output))
    body = _clean(output)
    code = process.returncode
    if code == 0:
        return body or '(no output)'
    message = _exit_message(text, code)
    if message is not None:
        return _join(message, body)
    return _join(f'Exit code {code}', body)


def make_bash(cwd: str):
    @tool(
        name='Bash',
        description='Run a shell command in the workspace and return its '
                    'combined stdout and stderr. Prefer Read, Glob, Grep, '
                    'Edit and Write over cat, find, grep, sed and shell '
                    'redirection. Chain dependent commands with && instead of '
                    'newlines. Never run destructive git commands such as '
                    'push --force or reset --hard without explicit approval.',
        parameters={
            'type': 'object',
            'properties': {
                'command': {'type': 'string',
                            'description': 'The command to execute.'},
                'timeout': {
                    'type': 'integer',
                    'description': 'Optional timeout in milliseconds '
                                   f'(max {BASH_MAX_TIMEOUT_MS}).',
                },
                'description': {
                    'type': 'string',
                    'description': 'Short description of what the command '
                                   'does.',
                },
                'run_in_background': {
                    'type': 'boolean',
                    'description': 'Set to true to run this command in the '
                                   'background and get a task ID back '
                                   'immediately. Read its output later with '
                                   'TaskOutput. Do not use a trailing "&".',
                },
            },
            'required': ['command'],
        },
    )
    def bash(command: str, timeout: int | None = None,
             description: str | None = None,
             run_in_background: bool = False) -> str:
        if run_in_background:
            from .background import _output_path, spawn
            task_id = spawn(cwd, command)
            return (f'Command running in background with ID: {task_id}. '
                    f'Output is being written to: {_output_path(task_id)}. '
                    'Read the output with TaskOutput.')
        return run_command(cwd, command, timeout)
    return bash
