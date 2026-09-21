from __future__ import annotations

import json
import re
from pathlib import Path
from rich.markup import escape

from pyclaw.tui.theme import MAX_RESULT_LINES, POINTER, RESULT_PREFIX


TEAMMATE_MESSAGE_RE = re.compile(
    r'<teammate_message\s+teammate_id="([^"]*)"[^>]*>\s*(.*?)\s*'
    r'</teammate_message>', re.S)
HIDDEN_TEAMMATE_TYPES = frozenset({'idle_notification', 'shutdown_approved',
                                   'teammate_terminated'})
DIRECT_MESSAGE_RE = re.compile(r'^@([\w-]+)\s+(.+)$', re.S)


def _summarize(value, limit: int = 60) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _clip_lines(value, max_lines: int, max_chars: int) -> str:
    lines = str(value if value is not None else "").strip("\n").split("\n")
    clipped = "\n".join(lines[:max_lines]).strip()
    if len(lines) > max_lines or len(clipped) > max_chars:
        clipped = clipped[:max_chars].rstrip() + "…"
    return clipped


def _display_path(cwd, value) -> str:
    raw = str(value if value is not None else "")
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = Path(cwd or ".") / path
    try:
        path = path.resolve()
        return str(path.relative_to(Path(cwd or ".").resolve()))
    except (ValueError, OSError):
        pass
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _preview(text, width: int, limit: int = MAX_RESULT_LINES) -> str:
    rows: list[str] = []
    for raw in str(text if text is not None else "").rstrip("\n").split("\n"):
        if not raw:
            rows.append("")
            continue
        for start in range(0, len(raw), max(20, width)):
            rows.append(raw[start:start + max(20, width)])
    if len(rows) <= limit + 1:
        return "\n".join(rows)
    return "\n".join(rows[:limit]
                     + [f"\u2026 +{len(rows) - limit} lines "
                        f"(ctrl+o shows the rest)"])


def _edit_summary(added: int, removed: int) -> str:
    if added and removed:
        return (f"Added {_plural(added, 'line')}, "
                f"removed {_plural(removed, 'line')}")
    if added:
        return f"Added {_plural(added, 'line')}"
    return f"Removed {_plural(removed, 'line')}"


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _visible_len(markup: str) -> int:
    return len(re.sub(r'\[[^\]]*\]', '', markup))


def _format_count(n: int) -> str:
    for unit, scale in (('m', 1_000_000), ('k', 1000)):
        if n >= scale:
            return f'{n / scale:.1f}'.rstrip('0').rstrip('.') + unit
    return str(n)


def _token_rate(tokens: int, seconds: int) -> str:
    rate = tokens / seconds
    return f'{rate:.1f} tok/s' if rate < 10 else f'{round(rate)} tok/s'


def _fit(parts: tuple, room: int) -> str:
    kept = [part for part in parts if part]
    while len(kept) > 1 and _visible_len(' \u00b7 '.join(kept)) > room:
        kept.pop()
    return ' \u00b7 '.join(kept)


def _display_cwd(path: str) -> str:
    home = str(Path.home())
    if path == home:
        return '~'
    if path.startswith(home + '/'):
        return '~/' + path[len(home) + 1:]
    return path


def _single_line(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    if len(text) <= limit:
        return text
    return text[:max(1, limit - 1)].rstrip() + "\u2026"


def _teammate_blocks(text) -> list | None:
    matches = TEAMMATE_MESSAGE_RE.findall(str(text or ''))
    if not matches:
        return None
    blocks = []
    for sender, payload in matches:
        payload = payload.strip()
        obj = None
        try:
            obj = json.loads(payload)
        except (TypeError, ValueError):
            obj = None
        kind = obj.get('type') if isinstance(obj, dict) else None
        if kind in HIDDEN_TEAMMATE_TYPES:
            continue
        if kind == 'task_completed':
            subject = obj.get('task_subject') or obj.get('subject') or ''
            tail = f" ({subject})" if subject else ''
            blocks.append((sender,
                           "\n" + RESULT_PREFIX + "\u2713 Completed task #"
                           f"{obj.get('task_id', '')}{tail}"))
            continue
        line = payload.splitlines()[0].strip() if payload else ''
        blocks.append((sender, line))
    return blocks


def _direct_message(text: str):
    match = DIRECT_MESSAGE_RE.match(str(text or ''))
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def _user_markup(text: str) -> str:
    blocks = _teammate_blocks(text)
    if blocks is None:
        return escape(str(text))
    return "\n".join(f"[bold]{escape(str(sender))}[/]{POINTER} "
                     f"{escape(body)}" for sender, body in blocks)


def _hang(prefix: str, body: str) -> str:
    return prefix + body.replace("\n", "\n" + " " * len(prefix))


def duration(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds}s'
    return f'{seconds // 60}m {seconds % 60}s'


def thinking_map(messages) -> dict:
    pairs = {}
    for message in messages:
        if message.get('role') != 'assistant' or not message.get('thinking'):
            continue
        text = _content_text(message.get('content'))
        if text:
            pairs[text] = message['thinking']
    return pairs
