from __future__ import annotations

import asyncio
import json
import os

STATUS_LINE_TIMEOUT_SECONDS = 5.0
STATUS_LINE_DEBOUNCE_SECONDS = 0.3


_CONFIG_MTIME: float | None = None


def _sync_config() -> None:
    global _CONFIG_MTIME
    from pyclaw import config
    try:
        mtime = config.__config_file__.stat().st_mtime
    except OSError:
        mtime = None
    if mtime is not None and mtime == _CONFIG_MTIME:
        return
    _CONFIG_MTIME = mtime
    config.reload()


def user_command() -> str:
    from pyclaw.config import load
    _sync_config()
    entry = load().get('statusLine')
    if not entry or entry.get('type') != 'command':
        return ''
    return str(entry.get('command') or '')


def context_percentages(used: int, size: int) -> tuple[int, int]:
    if not size:
        return 0, 100
    percentage = min(100, max(0, round(used / size * 100)))
    return percentage, 100 - percentage


def build_payload(session) -> dict:
    from pyclaw.agents import transcript_path
    from pyclaw.version import __version__
    usage = session.usage
    window = session.context_window
    last = session.last_usage
    details = usage.prompt_tokens_details or {}
    if last is None:
        current_usage = None
        used_percentage = remaining_percentage = None
    else:
        last_details = last.prompt_tokens_details or {}
        current_usage = {
            'input_tokens': last.prompt_tokens,
            'output_tokens': last.completion_tokens,
            'cached_tokens': int(last_details.get('cached_tokens', 0) or 0),
        }
        used_percentage, remaining_percentage = context_percentages(
            last.prompt_tokens, window)
    return {
        'session_id': session.conv_session_id,
        'transcript_path': str(transcript_path(session.conv_session_id)),
        'cwd': session.cwd,
        'permission_mode': session.permission_mode,
        'model': {'id': session.model, 'display_name': session.model},
        'workspace': {
            'current_dir': os.getcwd(),
            'project_dir': session.cwd,
            'added_dirs': [],
        },
        'version': str(__version__),
        'usage': {
            'prompt_tokens': usage.prompt_tokens,
            'completion_tokens': usage.completion_tokens,
            'total_tokens': usage.total_tokens,
            'cached_tokens': int(details.get('cached_tokens', 0) or 0),
        },
        'context_window': {
            'total_input_tokens': usage.prompt_tokens,
            'total_output_tokens': usage.completion_tokens,
            'context_window_size': window,
            'current_usage': current_usage,
            'used_percentage': used_percentage,
            'remaining_percentage': remaining_percentage,
        },
    }


def clean_output(stdout: str) -> str:
    lines = [line.strip() for line in stdout.strip().split('\n')]
    return '\n'.join(line for line in lines if line)


async def run(session, command: str) -> str:
    if not command:
        return ''
    payload = json.dumps(build_payload(session))
    process = await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=session.cwd,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(payload.encode()),
            STATUS_LINE_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        _kill(process)
        await process.wait()
        raise
    except Exception:
        _kill(process)
        await process.wait()
        return ''
    if process.returncode != 0:
        return ''
    return clean_output(stdout.decode(errors='replace'))


def _kill(process) -> None:
    try:
        process.kill()
    except ProcessLookupError:
        return
