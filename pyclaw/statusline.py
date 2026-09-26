from __future__ import annotations

import asyncio
import json
import logging
import os
from pyclaw import config, cost
from pyclaw.config import load
from pyclaw.session.store import transcript_path
from pyclaw.version import __version__

logger = logging.getLogger(__name__)

STATUS_LINE_TIMEOUT_SECONDS = 5.0
STATUS_LINE_DEBOUNCE_SECONDS = 0.3


_CONFIG_MTIME: float | None = None


def _sync_config() -> None:
    global _CONFIG_MTIME
    try:
        mtime = config.__config_file__.stat().st_mtime
    except OSError:
        mtime = None
    if mtime is not None and mtime == _CONFIG_MTIME:
        return
    _CONFIG_MTIME = mtime
    config.reload()


def _pricing() -> dict:
    return load().get('pricing') or {}


def user_padding() -> int:
    _sync_config()
    entry = load().get('statusLine') or {}
    try:
        return max(0, int(entry.get('padding') or 0))
    except (TypeError, ValueError):
        return 0


def user_command() -> str:
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
    usage = session.usage
    metrics = session.metrics()
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
    payload = {
        'session_id': session.conv_session_id,
        'transcript_path': str(transcript_path(session.conv_session_id)),
        'cwd': session.cwd,
        'permission_mode': session.permission_mode,
        'model': {'id': session.model, 'display_name': session.model},
        'workspace': {
            'current_dir': os.getcwd(),
            'project_dir': session.cwd,
            'added_dirs': [str(path) for path in session.working_dirs[1:]],
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
        'exceeds_200k_tokens': bool(window and window > 200_000),
        'cost': {
            'total_cost_usd': cost.usage_cost(session.model, usage,
                                              _pricing()),
            'total_duration_ms': session.elapsed_seconds * 1000,
            'total_api_duration_ms': int(metrics.get('api_ms', 0)),
            'total_lines_added': int(metrics.get('lines_added', 0)),
            'total_lines_removed': int(metrics.get('lines_removed', 0)),
        },
        **({'session_name': session.title} if session.title else {}),
    }
    worktree = session.worktree
    if worktree:
        payload['worktree'] = {'name': str(worktree.get('name') or ''),
                               'path': str(worktree.get('path') or ''),
                               'branch': str(worktree.get('branch') or ''),
                               'original_cwd': str(worktree.get('origin') or ''),
                               'original_branch': str(
                                   worktree.get('origin_branch') or '')}
    return payload


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
        stdout, stderr = await asyncio.wait_for(
            process.communicate(payload.encode()),
            STATUS_LINE_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        _kill(process)
        await process.wait()
        raise
    except Exception:
        _kill(process)
        await process.wait()
        logger.exception('Status line command could not be started')
        return ''
    if process.returncode != 0:
        logger.warning('Status line command exited %d: %s',
                       process.returncode,
                       stderr.decode(errors='replace').strip()[:200])
        return ''
    text = clean_output(stdout.decode(errors='replace'))
    logger.debug('Status line: %s', text or 'nothing usable')
    return text


def _kill(process) -> None:
    try:
        process.kill()
    except ProcessLookupError:
        return
