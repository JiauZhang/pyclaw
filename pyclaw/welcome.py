"""The greeting drawn under the wordmark.

The text half of the landing screen: a welcome line, the product/version, the
model facts and the working directory, then up to two feeds — "Tips for
getting started" while project onboarding is still pending, and "Recent
activity" built from saved sessions.

Everything here is pure: the data half reads the filesystem/config, the render
half returns Rich markup lines. `tui.py` owns the widget.
"""
from __future__ import annotations

import os
import time
import unicodedata
from pathlib import Path

from rich.markup import escape

MEMORY_FILE_NAME = 'AGENTS.md'
ONBOARDING_SEEN_LIMIT = 4
ACTIVITY_LIMIT = 3
MAX_FEED_WIDTH = 78
MAX_USERNAME_LENGTH = 20
DIVIDER_CHAR = '\u2500'
COMPLETE_TICK = '\u2714'
STAMP_GAP = '  '

_RELATIVE_UNITS = (('year', 31_536_000), ('month', 2_592_000),
                   ('week', 604_800), ('day', 86_400), ('hour', 3_600),
                   ('minute', 60), ('second', 1))


def home_path(value) -> str:
    text = str(value or '')
    if not text:
        return ''
    home = str(Path.home())
    if text == home:
        return '~'
    if text.startswith(home + os.sep):
        return '~' + text[len(home):]
    return text


def relative_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    for name, size in _RELATIVE_UNITS:
        if seconds >= size or name == 'second':
            value = int(seconds // size)
            return f"{value} {name if value == 1 else name + 's'} ago"
    return '0 seconds ago'


def welcome_message(username=None) -> str:
    if not username or len(str(username)) > MAX_USERNAME_LENGTH:
        return 'Welcome back!'
    return f'Welcome back {username}!'


def onboarding_steps(cwd) -> list[dict]:
    root = Path(cwd or '.')
    has_memory = (root / MEMORY_FILE_NAME).exists()
    try:
        empty = not any(root.iterdir())
    except OSError:
        empty = False
    return [
        {'text': 'Ask PyClaw to create a new app or clone a repository',
         'complete': False, 'enabled': empty},
        {'text': f'Run /init to create an {MEMORY_FILE_NAME} file with '
                 f'instructions for PyClaw',
         'complete': has_memory, 'enabled': not empty},
    ]


def onboarding_pending(steps: list[dict]) -> bool:
    active = [step for step in steps if step.get('enabled')]
    return bool(active) and not all(step.get('complete') for step in active)


def home_warning(cwd) -> str | None:
    try:
        if Path(cwd or '.').resolve() != Path.home().resolve():
            return None
    except OSError:
        return None
    return ('Note: You have launched PyClaw in your home directory. For the '
            'best experience, launch it in a project directory instead.')


def _plain_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(block.get('text', '') for block in content
                       if isinstance(block, dict)
                       and block.get('type') == 'text')
    return ''


def first_prompt(session_id) -> str:
    from pyclaw.agents import load_entries
    for entry in load_entries(session_id):
        if not isinstance(entry, dict) or entry.get('role') != 'user':
            continue
        text = ' '.join(_plain_text(entry.get('content')).split())
        if text:
            return text
    return ''


def recent_activity(limit: int = ACTIVITY_LIMIT, *, exclude=None,
                    now: float | None = None) -> list[dict]:
    from pyclaw.agents import list_sessions
    stamp = time.time() if now is None else now
    found = []
    for session in list_sessions():
        if exclude and session.get('id') == exclude:
            continue
        text = first_prompt(session['id'])
        if not text:
            continue
        found.append({'text': text,
                      'timestamp': relative_time(stamp - session['modified'])})
        if len(found) >= limit:
            break
    return found


def tips_feed(steps: list[dict], cwd) -> dict | None:
    ordered = sorted((step for step in steps if step.get('enabled')),
                     key=lambda step: bool(step.get('complete')))
    lines = [{'text': (COMPLETE_TICK + ' ' if step.get('complete') else '')
                      + step['text']}
             for step in ordered]
    note = home_warning(cwd)
    if note and lines:
        lines.append({'text': note})
    if not lines:
        return None
    return {'title': 'Tips for getting started', 'lines': lines}


def activity_feed(entries: list[dict]) -> dict:
    return {
        'title': 'Recent activity',
        'lines': [{'text': entry['text'],
                   'timestamp': entry.get('timestamp', '')}
                  for entry in entries],
        'footer': '/resume for more' if entries else None,
        'empty': 'No recent activity',
    }


def _width(text) -> int:
    total = 0
    for char in str(text or ''):
        total += 2 if unicodedata.east_asian_width(char) in 'WF' else 1
    return total


def _truncate(text, width: int) -> str:
    text = str(text or '')
    if width <= 0:
        return ''
    if _width(text) <= width:
        return text
    out = ''
    used = 0
    for char in text:
        span = 2 if _width(char) == 2 else 1
        if used + span > max(0, width - 1):
            break
        out += char
        used += span
    return out + '\u2026'


def feed_width(feed: dict) -> int:
    width = _width(feed.get('title'))
    lines = feed.get('lines') or []
    stamp = max([_width(line.get('timestamp')) for line in lines] or [0])
    for line in lines:
        extra = stamp + len(STAMP_GAP) if stamp else 0
        width = max(width, _width(line['text']) + extra)
    if not lines and feed.get('empty'):
        width = max(width, _width(feed['empty']))
    if feed.get('footer'):
        width = max(width, _width(feed['footer']))
    return width


def _render_feed(feed: dict, width: int, brand: str) -> list[str]:
    title = escape(str(feed.get('title', '')))
    lines = [f"[{brand}][bold]{title}[/][/]"]
    rows = feed.get('lines') or []
    if rows:
        stamp = max([_width(row.get('timestamp')) for row in rows] or [0])
        for row in rows:
            text_width = max(10, width - (stamp + len(STAMP_GAP) if stamp
                                          else 0))
            body = escape(_truncate(row['text'], text_width))
            if stamp:
                mark = escape(str(row.get('timestamp') or '').ljust(stamp))
                lines.append(f"[dim]{mark}[/]{STAMP_GAP}{body}")
            else:
                lines.append(body)
    elif feed.get('empty'):
        lines.append(f"[dim]{escape(_truncate(feed['empty'], width))}[/]")
    if feed.get('footer'):
        lines.append(f"[dim][i]{escape(_truncate(feed['footer'], width))}[/][/]")
    return lines


def render_feeds(feeds: list, brand: str) -> list[str]:
    feeds = [feed for feed in feeds if feed]
    if not feeds:
        return []
    width = min(max(feed_width(feed) for feed in feeds), MAX_FEED_WIDTH)
    out = []
    for index, feed in enumerate(feeds):
        if index:
            out.append(f"[{brand}]{DIVIDER_CHAR * width}[/]")
        out.extend(_render_feed(feed, width, brand))
    return out


def block(*, version, model, provider, cwd, feeds, brand,
          username=None) -> str:
    lines = [f"[bold]{escape(welcome_message(username))}[/]",
             f"[bold]PyClaw[/] [dim]v{escape(str(version))}[/]"]
    facts = ' \u00b7 '.join(escape(str(part)) for part in (model, provider)
                            if part)
    if facts:
        lines.append(f"[dim]{facts}[/]")
    path = home_path(cwd)
    if path:
        lines.append(f"[dim]{escape(path)}[/]")
    rendered = render_feeds(feeds, brand)
    if rendered:
        lines.append('')
        lines.extend(rendered)
    return '\n'.join(lines)


def settings() -> dict:
    from pyclaw import config
    block_config = config.load().get('welcome') or {}
    return {'seen': int(block_config.get('seen', 0) or 0),
            'lastVersion': str(block_config.get('lastVersion', '') or '')}


def remember(*, seen_onboarding: bool, version: str) -> None:
    from pyclaw import config
    current = config.load()
    section = dict(current.get('welcome') or {})
    seen = int(section.get('seen', 0) or 0)
    if seen_onboarding:
        seen += 1
    if (seen == int(section.get('seen', 0) or 0)
            and section.get('lastVersion') == str(version)):
        return
    section['seen'] = seen
    section['lastVersion'] = str(version)
    current['welcome'] = section
    config.save(current)


def feeds_for(*, cwd, version, session_id=None) -> tuple[list, bool]:
    current = settings()
    steps = onboarding_steps(cwd)
    shown = (onboarding_pending(steps)
             and current['seen'] < ONBOARDING_SEEN_LIMIT)
    fresh_version = current['lastVersion'] != str(version)
    if not (shown or fresh_version):
        return [], False
    feeds = []
    if shown:
        feeds.append(tips_feed(steps, cwd))
    feeds.append(activity_feed(recent_activity(exclude=session_id)))
    return [feed for feed in feeds if feed], shown
