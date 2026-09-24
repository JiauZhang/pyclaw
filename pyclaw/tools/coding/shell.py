from __future__ import annotations

import os
import secrets
import signal
import subprocess
import tempfile
from pathlib import Path

from chatchat.tool import ToolResult, tool

from pyclaw.tui.formatting import _clip_lines
from pyclaw.tui.theme import MAX_COMMAND_CHARS, MAX_COMMAND_LINES
from pyclaw.tui.toolui import build_tool_ui, register

from .shell_rules import base_command, split_commands

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
    'grep': (1, 'Nothing matched'),
    'rg': (1, 'Nothing matched'),
    'find': (1, 'Parts of the tree could not be read'),
    'diff': (1, 'The files are not identical'),
    'test': (1, 'The condition did not hold'),
    '[': (1, 'The condition did not hold'),
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
    note = f'{rest.count(chr(10)) + 1} more lines left out'
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
            return (f'{limit}ms passed with the command still running, so it moved '
                    f'to the background as {task_id}. Read its output with '
                    'TaskOutput.')
        _kill(process)
        process.wait()
        return _join(f'Error: the command ran past {limit}ms',
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
    return ToolResult(text=_join(f'Command exited with {code}', body),
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
    max_result_chars=MAX_OUTPUT_UPPER_LIMIT + 1_000,
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
                'description': 'What the command is for, shown to the human '
                               'who approves it.',
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
        return (f'Started in the background as {task_id}; output goes to '
                f'{_output_path(task_id)}. Read it with TaskOutput.')
    return run_command(context.cwd, command, timeout)


BASH_SEARCH_COMMANDS = frozenset({'find', 'grep', 'rg', 'ag', 'ack', 'locate',
                                  'which', 'whereis'})
BASH_READ_COMMANDS = frozenset({'cat', 'head', 'tail', 'less', 'more', 'wc',
                                'stat', 'file', 'strings', 'jq', 'awk', 'cut',
                                'sort', 'uniq', 'tr'})
BASH_LIST_COMMANDS = frozenset({'ls', 'tree', 'du'})
BASH_NEUTRAL_COMMANDS = frozenset({'echo', 'printf', 'true', 'false', ':'})


def bash_kinds(command) -> set:
    try:
        parts = [p for p in split_commands(str(command or ''))
                 if base_command(p) not in BASH_NEUTRAL_COMMANDS]
    except Exception:
        return set()
    if not parts:
        return set()
    kinds = set()
    for part in parts:
        base = base_command(part)
        if base in BASH_SEARCH_COMMANDS:
            kinds.add('search')
        elif base in BASH_READ_COMMANDS:
            kinds.add('read')
        elif base in BASH_LIST_COMMANDS:
            kinds.add('list')
        else:
            return {'bash'}
    return kinds


def _bash_args(name, tool_input, cwd):
    data = tool_input if isinstance(tool_input, dict) else {}
    return _clip_lines(data.get("command", ""), MAX_COMMAND_LINES,
                       MAX_COMMAND_CHARS)


def _bash_kinds(name, tool_input):
    data = tool_input if isinstance(tool_input, dict) else {}
    return bash_kinds(data.get('command'))


register('Bash', build_tool_ui(args=_bash_args, kinds=_bash_kinds))
