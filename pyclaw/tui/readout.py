from __future__ import annotations

import subprocess
import time

from pyclaw import banner

from pyclaw.tui.formatting import _format_count, duration
from pyclaw.tui.theme import MODE_COLORS, MODE_SYMBOLS, MODE_TITLES


CONTEXT_WARNING_BUFFER_TOKENS = 20_000
CONTEXT_ERROR_BUFFER_TOKENS = 20_000


CONTEXT_METER_CELLS = 10
NARROW_CONTEXT_METER_CELLS = 5
NARROW_TERMINAL_COLUMNS = 80
METER_FILL = "\u2588"
METER_EMPTY = "\u2591"
METER_EDGE = "\u2502"
METER_CACHED = "\u2592"
METER_FRESH = "\u2593"
METER_TRACK_LIGHTNESS = 45
ELAPSED_ICON = "\u23f1"


GIT_STATUS_TIMEOUT_SECONDS = 2.0
HUD_TICK_SECONDS = 1.0


def _meter_edges() -> str:
    return f'[dim]{METER_EDGE}[/]'


def _split(parts: tuple, cells: int) -> list:
    total = sum(parts)
    shares = [p / total * cells for p in parts]
    counts = [int(s) for s in shares]
    for i in sorted(range(len(parts)), key=lambda i: shares[i] - counts[i],
                    reverse=True):
        if sum(counts) >= cells:
            break
        counts[i] += 1
    for i, part in enumerate(parts):
        if part and not counts[i]:
            widest = counts.index(max(counts))
            counts[widest] -= 1
            counts[i] = 1
    return counts


def context_meter(triple: tuple, used: int, window: int,
                  cells: int = CONTEXT_METER_CELLS) -> str:
    if window <= 0 or cells <= 0:
        return ''
    fraction = min(1.0, max(0.0, used / window))
    colour = banner.rgb_to_hex(banner.ramp(triple[0], triple[1], fraction))
    track = banner.dimmed(triple, METER_TRACK_LIGHTNESS)
    filled = round(fraction * cells)
    return (f'{_meter_edges()}[{colour}]{METER_FILL * filled}[/]'
            f'[on {track}]{METER_EMPTY * (cells - filled)}[/]{_meter_edges()} '
            f'[{banner.rgb_to_hex(banner.ramp(triple[0], triple[1], 0.5))}]'
            f'{round(fraction * 100)}%[/]'
            f'[dim] ({_format_count(window)})[/]')


def usage_meter(triple: tuple, usage,
                cells: int = CONTEXT_METER_CELLS) -> str:
    prompt = int(getattr(usage, 'prompt_tokens', 0) or 0)
    completion = int(getattr(usage, 'completion_tokens', 0) or 0)
    total = int(getattr(usage, 'total_tokens', 0) or 0)
    if cells <= 0:
        return ''
    if not total:
        track = banner.dimmed(triple, METER_TRACK_LIGHTNESS)
        return (f'{_meter_edges()}[on {track}]'
                f'{METER_EMPTY * cells}[/]{_meter_edges()}')
    details = getattr(usage, 'prompt_tokens_details', None) or {}
    cached = min(prompt, int(details.get('cached_tokens', 0) or 0))
    parts = (cached, prompt - cached, completion)
    spans = [banner.rgb_to_hex(banner.ramp(triple[0], triple[1], stop))
             for stop in (0.0, 0.5, 1.0)]
    body = ''.join(f'[{colour}]{glyph * count}[/]'
                   for colour, glyph, count in zip(
                       spans, (METER_CACHED, METER_FRESH, METER_FILL),
                       _split(parts, cells)))
    return f'{_meter_edges()}{body}{_meter_edges()}'


def usage_hud(usage) -> str:
    prompt = int(getattr(usage, 'prompt_tokens', 0) or 0)
    completion = int(getattr(usage, 'completion_tokens', 0) or 0)
    total = int(getattr(usage, 'total_tokens', 0) or 0)
    details = getattr(usage, 'prompt_tokens_details', None) or {}
    cached = int(details.get('cached_tokens', 0) or 0)
    return (f'in: {_format_count(prompt)}  out: {_format_count(completion)}  '
            f'cache: {round(cached / prompt * 100) if prompt else 0}%  '
            f'total: {_format_count(total)}')


def git_label(status: str) -> str:
    branch = ''
    dirty = 0
    for line in status.splitlines():
        if line.startswith('# branch.head '):
            branch = line[len('# branch.head '):].strip()
        elif line and not line.startswith('#'):
            dirty += 1
    if not branch or branch == '(unknown)':
        return ''
    return branch if not dirty else f'{branch} \u00b1{dirty}'


def git_status(cwd: str) -> str:
    try:
        done = subprocess.run(
            ['git', '-C', cwd, 'status', '--porcelain=v2', '--branch'],
            capture_output=True, text=True, timeout=GIT_STATUS_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return ''
    return done.stdout if done.returncode == 0 else ''


def _agent_tokens(agent) -> int:
    usage = getattr(agent, 'total_usage', None)
    return int(getattr(usage, 'total_tokens', 0) or 0)


def context_note(session) -> str:
    if session is None:
        return ""
    limit = session.compact_threshold
    if limit <= 0:
        return ""
    used = session.used_context
    if used < limit - CONTEXT_WARNING_BUFFER_TOKENS:
        return ""
    percent_left = max(0, round((limit - used) / limit * 100))
    if session.auto_compact:
        return f"[dim]{percent_left}% left before auto-compact[/]"
    severity = ("error" if used >= limit - CONTEXT_ERROR_BUFFER_TOKENS
                else "warning")
    return (f"[{severity}]Nearly out of context ({percent_left}% left) "
            f"\u00b7 run /compact to carry on[/]")


def thinking_label(session) -> str:
    return f"[dim]thinking {'on' if session.thinking else 'off'}[/]"


def mode_pill(session, *, background: bool) -> str:
    perm = session.permission_mode
    if perm not in MODE_SYMBOLS:
        return ''
    pill = (f"[{MODE_COLORS.get(perm, '#9A9A9A')}]{MODE_SYMBOLS[perm]} "
            f"{MODE_TITLES[perm]} on[/]")
    if not background:
        pill += " [dim](shift+tab to cycle)[/]"
    return pill


def message_count(session) -> str:
    return f'{len(session.transcript())} msg'


def elapsed_row(since: float) -> str:
    return f'{ELAPSED_ICON} {duration(max(0, int(time.monotonic() - since)))}'


def meter_cells(width: int) -> int:
    return (CONTEXT_METER_CELLS if width >= NARROW_TERMINAL_COLUMNS
            else NARROW_CONTEXT_METER_CELLS)
