from __future__ import annotations

import asyncio
import dataclasses
import difflib
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

from rich.markup import escape
from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.theme import BUILTIN_THEMES
from textual.widgets import Input, Markdown, Static


from chatchat.core.agents import AgentDefinition
from chatchat.hooks.events import (
    AGENT_PROGRESS,
    AGENT_REASON_START,
    AGENT_STATE,
    AGENT_TEXT,
    AGENT_TOOL_CALL,
    AGENT_TOOL_RESULT,
    AGENT_TURN_FINISHED,
    AGENT_WARN,
    register_runtime_handler,
)

from pyclaw import __version__, agent_defs, banner, config, statusline, welcome
from pyclaw.agents import Session, append_conv
from pyclaw.spinner_verbs import PAST_TENSE_VERBS, SPINNER_VERBS
from pyclaw.slash import suggest as slash_suggest
from pyclaw.tools.coding import next_mode
from pyclaw.tools.coding.permission import BASH_TOOL, PermissionChoice


logger = logging.getLogger(__name__)

MAX_LOG_PAYLOAD = 1000


def _log_data(data) -> str:
    try:
        text = repr(data)
    except Exception:
        return "<unrepresentable>"
    return text if len(text) <= MAX_LOG_PAYLOAD else text[:MAX_LOG_PAYLOAD] + "\u2026"


BULLET = "\u23fa" if sys.platform == "darwin" else "\u25cf"
POINTER = "\u276f"
RESULT_GLYPH = "\u23bf"
ASTERISK = "\u273b"
BULLET_PREFIX = f"{BULLET} "
RESULT_PREFIX = f"  {RESULT_GLYPH}  "
RESULT_HANG = " " * len(RESULT_PREFIX)

TREE_LEAD = ("\u250c\u2500", "\u2552\u2550")
TREE_BRANCH = ("\u251c\u2500", "\u255e\u2550")
TREE_LAST = ("\u2514\u2500", "\u2558\u2550")
TREE_INDENT = "   "
TREE_POINTER = POINTER
SELECT_HINT = "shift+\u2191/\u2193 picks a row"
VIEW_HINT = "enter opens it"
COLLAPSE_HINT = "enter closes it"
IDLE_TEXT = "Idle"
AGENT_TEAMMATES_HINT = "subagents are active"
TEAMMATE_VIEW_HINT = "esc goes back to the lead"

AGENT_TRAIL_LIMIT = 3
INITIALIZING_TEXT = "Starting up\u2026"
EXPAND_HINT = "ctrl+o shows more"

WAITING_PERMISSION_TEXT = "Needs your approval\u2026"
INTERRUPTED_TEXT = "Stopped \u00b7 tell PyClaw what to do instead"

OPTION_PAGE_SIZE = 5
ACCEPT_FEEDBACK_HINT = "and tell PyClaw what to do next"
REJECT_FEEDBACK_HINT = "and tell PyClaw what to do differently"
RULE_FEEDBACK_HINT = "a command prefix, like npm run:*"

NON_MODAL_OVERLAYS = frozenset({'autocomplete'})

OVERLAY_GATED_ACTIONS = frozenset({
    'suggest_tab', 'prompt_next', 'prompt_prev', 'agent_next', 'agent_prev',
    'stop_agent', 'cycle_permission', 'focus_next', 'focus_previous'})

AGENT_COLORS = ("#FF6B80", "#4782C8", "#4EBA65", "#FFC107",
                "#AF87FF", "#D77757", "#FD5DB1", "#48968C")

TEAMMATE_MESSAGE_RE = re.compile(
    r'<teammate_message\s+teammate_id="([^"]*)"[^>]*>\s*(.*?)\s*'
    r'</teammate_message>', re.S)
HIDDEN_TEAMMATE_TYPES = frozenset({'idle_notification', 'shutdown_approved',
                                   'teammate_terminated'})
DIRECT_MESSAGE_RE = re.compile(r'^@([\w-]+)\s+(.+)$', re.S)

MODE_SYMBOLS = {"acceptEdits": "\u23f5\u23f5",
                "bypassPermissions": "\u23f5\u23f5", "plan": "\u23f8"}
MODE_TITLES = {"acceptEdits": "accept edits", "plan": "plan mode",
               "bypassPermissions": "skip permission prompts"}
MODE_COLORS = {"acceptEdits": "#AF87FF", "plan": "#48968C",
               "bypassPermissions": "#FF6B80"}

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

SPINNER_CHARS = ["\u00b7", "\u2722", "\u2733", "\u2736", "\u273b", "\u273d"]
SPINNER_FRAMES = SPINNER_CHARS + list(reversed(SPINNER_CHARS))
SPINNER_INTERVAL = 0.05

MAX_COMMAND_LINES = 2
MAX_COMMAND_CHARS = 160
MAX_RESULT_LINES = 3
MAX_USE_ARG_CHARS = 80

DISPLAY_NAMES = {"Edit": "Update", "MultiEdit": "Update", "Grep": "Search",
                 "Glob": "Search", "LS": "List"}
PATH_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "LS")
SEARCH_TOOLS = ("Grep", "Glob")

BASH_SEARCH_COMMANDS = frozenset({'find', 'grep', 'rg', 'ag', 'ack', 'locate',
                                  'which', 'whereis'})
BASH_READ_COMMANDS = frozenset({'cat', 'head', 'tail', 'less', 'more', 'wc',
                                'stat', 'file', 'strings', 'jq', 'awk', 'cut',
                                'sort', 'uniq', 'tr'})
BASH_LIST_COMMANDS = frozenset({'ls', 'tree', 'du'})
BASH_NEUTRAL_COMMANDS = frozenset({'echo', 'printf', 'true', 'false', ':'})
MEMORY_FILE_NAME = 'AGENTS.md'

GROUP_PARTS = (
    ('search', 'Looking for', 'Looked for', 'pattern', 'patterns'),
    ('read', 'Opening', 'Opened', 'file', 'files'),
    ('list', 'Walking', 'Walked', 'folder', 'folders'),
    ('bash', 'Executing', 'Executed', 'shell command', 'shell commands'),
    ('memory_read', 'Remembering', 'Remembered', 'memory', 'memories'),
    ('memory_write', 'Saving', 'Saved', 'memory', 'memories'),
)


def _is_memory_path(value) -> bool:
    return Path(str(value or '')).name == MEMORY_FILE_NAME


def _bash_kinds(command) -> set:
    from pyclaw.tools.coding.shell_rules import base_command, split_commands
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


def _collapsible_kinds(name, tool_input) -> set:
    data = tool_input if isinstance(tool_input, dict) else {}
    raw_path = data.get('file_path') or data.get('path') or ''
    if name == 'Read':
        return {'memory_read' if _is_memory_path(raw_path) else 'read'}
    if name in ('Grep', 'Glob'):
        return {'search'}
    if name == 'LS':
        return {'list'}
    if name in ('Write', 'Edit', 'MultiEdit'):
        return {'memory_write'} if _is_memory_path(raw_path) else set()
    if name == 'Bash':
        return _bash_kinds(data.get('command'))
    return set()


def _read_key(name, tool_input) -> str:
    data = tool_input if isinstance(tool_input, dict) else {}
    return str(data.get('file_path') or data.get('path') or name)


def _summarize(value, limit: int = 60) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _display_name(name: str) -> str:
    return DISPLAY_NAMES.get(name, name)


def _agent_tool_name(tool_input) -> str:
    data = tool_input if isinstance(tool_input, dict) else {}
    subagent = str(data.get('subagent_type') or '')
    if subagent and subagent != 'general-purpose':
        return 'Agent' if subagent == 'worker' else subagent
    return 'Agent'


def _tool_label(name: str, tool_input) -> str:
    if name == 'create_agent':
        return _agent_tool_name(tool_input)
    return _display_name(name)


def _hidden_card(name: str, tool_input) -> bool:
    if name != 'send_message':
        return False
    data = tool_input if isinstance(tool_input, dict) else {}
    return isinstance(data.get('message'), str)


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


_DIFF_HUNK_RE = re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')
_DIFF_TOOLS = ('Edit', 'MultiEdit', 'Write')
_DIFF_ADD = '#225C2B'
_DIFF_RM = '#7A2936'
_DIFF_ADD_WORD = '#38A660'
_DIFF_RM_WORD = '#B3596B'
_DIFF_WORD_RATIO = 0.4
_WORD_SPLIT_RE = re.compile(r'\w+|[^\w]+')


def _diff_rows(text: str) -> list:
    rows = []
    old = new = None
    for line in text.split('\n'):
        m = _DIFF_HUNK_RE.match(line)
        if m:
            old, new = int(m.group(1)), int(m.group(2))
            continue
        if old is None or not line or line[0] not in ' +-':
            continue
        kind = 'ctx' if line[0] == ' ' else ('rm' if line[0] == '-' else 'add')
        content = line[1:]
        if kind == 'ctx':
            rows.append((kind, old, new, content))
            old += 1
            new += 1
        elif kind == 'rm':
            rows.append((kind, old, None, content))
            old += 1
        else:
            rows.append((kind, None, new, content))
            new += 1
    return rows


def _word_parts(old: str, new: str):
    sm = difflib.SequenceMatcher(a=_WORD_SPLIT_RE.findall(old),
                                 b=_WORD_SPLIT_RE.findall(new),
                                 autojunk=False)
    rm_parts = []
    add_parts = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            text = ''.join(sm.a[i1:i2])
            rm_parts.append((False, text))
            add_parts.append((False, text))
        elif tag == 'replace':
            rm_parts.append((True, ''.join(sm.a[i1:i2])))
            add_parts.append((True, ''.join(sm.b[j1:j2])))
        elif tag == 'delete':
            rm_parts.append((True, ''.join(sm.a[i1:i2])))
        else:
            add_parts.append((True, ''.join(sm.b[j1:j2])))
    return rm_parts, add_parts


def _word_pairs(rows: list) -> dict:
    pairs = {}
    i = 0
    n = len(rows)
    while i < n:
        if rows[i][0] != 'rm':
            i += 1
            continue
        rms = []
        while i < n and rows[i][0] == 'rm':
            rms.append(i)
            i += 1
        adds = []
        while i < n and rows[i][0] == 'add':
            adds.append(i)
            i += 1
        for k in range(min(len(rms), len(adds))):
            pairs[rms[k]] = adds[k]
            pairs[adds[k]] = rms[k]
    return pairs


def _chunks(text: str, width: int) -> list:
    if not text:
        return ['']
    return [text[i:i + width] for i in range(0, len(text), width)]


def _diff_block(name, tool_input, output, cwd, width: int) -> str | None:
    if name not in _DIFF_TOOLS:
        return None
    rows = _diff_rows(str(output or ''))
    if not rows:
        return None
    added = sum(1 for k, *_ in rows if k == 'add')
    removed = sum(1 for k, *_ in rows if k == 'rm')
    nums = [n for _, o, nw, _ in rows for n in (o, nw) if n is not None]
    gutter = max((len(str(n)) for n in nums), default=1)
    content_w = max(20, width - gutter - 3)
    pairs = _word_pairs(rows)
    out = [_edit_summary(added, removed)]
    for idx, (kind, old, new, content) in enumerate(rows):
        num = old if kind != 'add' else new
        num_s = f'{num:>{gutter}} ' if num is not None else ' ' * (gutter + 1)
        sigil = '+' if kind == 'add' else ('-' if kind == 'rm' else ' ')
        prefix = f'{num_s}{sigil} '
        if kind == 'ctx':
            chunks = _chunks(content, content_w)
            last = len(chunks) - 1
            for ci, chunk in enumerate(chunks):
                lead = prefix if ci == 0 else ' ' * len(prefix)
                pad = ' ' * (content_w - len(chunk)) if ci == last else ''
                out.append(f'[dim]{escape(lead + chunk + pad)}[/]')
            continue
        bg = _DIFF_ADD if kind == 'add' else _DIFF_RM
        word_bg = _DIFF_ADD_WORD if kind == 'add' else _DIFF_RM_WORD
        parts = None
        if idx in pairs:
            other = rows[pairs[idx]]
            old_line = content if kind == 'rm' else other[3]
            new_line = content if kind == 'add' else other[3]
            rm_parts, add_parts = _word_parts(old_line, new_line)
            parts = rm_parts if kind == 'rm' else add_parts
            changed = sum(len(v) for ch, v in rm_parts if ch)                 + sum(len(v) for ch, v in add_parts if ch)
            if changed / max(1, len(old_line) + len(new_line)) > _DIFF_WORD_RATIO:
                parts = None
        if parts is not None:
            spans = [escape(prefix)]
            for changed, value in parts:
                if value:
                    spans.append(f'[on {word_bg if changed else bg}]'
                                 f'{escape(value)}[/]')
            pad = max(0, content_w - len(content))
            if pad:
                spans.append(f'[on {bg}]{" " * pad}[/]')
            out.append(''.join(spans))
            continue
        chunks = _chunks(content, content_w)
        last = len(chunks) - 1
        for ci, chunk in enumerate(chunks):
            lead = prefix if ci == 0 else ' ' * len(prefix)
            pad = ' ' * (content_w - len(chunk)) if ci == last else ''
            out.append(f'[on {bg}]{escape(lead + chunk + pad)}[/]')
    return '\n'.join(out)


def _tool_use_args(name: str, tool_input, cwd) -> str:
    if not isinstance(tool_input, dict):
        return _clip_lines(tool_input, MAX_COMMAND_LINES, MAX_COMMAND_CHARS)
    data = tool_input
    if name == "Bash":
        return _clip_lines(data.get("command", ""), MAX_COMMAND_LINES,
                           MAX_COMMAND_CHARS)
    if name in PATH_TOOLS:
        return _display_path(cwd, data.get("file_path") or data.get("path"))
    if name in SEARCH_TOOLS:
        parts = [f'pattern: "{data.get("pattern", "")}"']
        target = data.get("path")
        if target:
            parts.append(f'path: "{_display_path(cwd, target)}"')
        return ", ".join(parts)
    if name == "create_agent":
        return _summarize(data.get("prompt") or data.get("name") or "",
                          MAX_USE_ARG_CHARS)
    if name == "send_message":
        return _summarize(f'{data.get("to", "")}: {data.get("message", "")}',
                          MAX_USE_ARG_CHARS)
    pairs = ", ".join(f"{k}: {v}" for k, v in data.items())
    return _clip_lines(pairs, MAX_COMMAND_LINES, MAX_COMMAND_CHARS)


def _last_assistant_key(messages) -> tuple:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get('role') == 'assistant':
            return (i, len(str(messages[i].get('content') or '')))
    return (0, 0)


def _result_summary(name: str, output, width: int) -> str:
    text = str(output if output is not None else "")
    if not text:
        return "Done"
    if text.startswith("Error"):
        return text.split("\n")[0]
    if name == "Read":
        rows = text.split("\n")
        body = rows[1:] if rows and rows[0].endswith(":") else rows
        return f"Read {_plural(len([r for r in body if r.strip()]), 'line')}"
    if name in SEARCH_TOOLS:
        hits = [r for r in text.split("\n") if r.strip()]
        if name == "Glob" or hits and ":" not in hits[0]:
            return f"Found {_plural(len(hits), 'file')}"
        return f"Found {_plural(len(hits), 'line')}"
    if name == "LS":
        rows = [r for r in text.split("\n")[1:] if r.strip()]
        noun = "entry" if len(rows) == 1 else "entries"
        return f"Listed {len(rows)} {noun}"
    if name == "Bash":
        return _preview(text, width)
    if name == "create_agent":
        return "Done"
    return _preview(text, width)


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _at_token(value: str) -> str | None:
    m = re.search(r'(^|\s)@([^\s]*)$', value)
    return m.group(2) if m else None


def _apply_at(value: str, name: str, is_dir: bool) -> str:
    value = re.sub(r'(^|\s)@[^\s]*$',
                   lambda m: m.group(1) + '@' + name, value)
    return value if is_dir else value + ' '


def _file_suggest(cwd: str, token: str, limit: int = 6) -> list[dict]:
    head, _, base = token.rpartition('/')
    root = Path(cwd or '.').resolve()
    base_dir = (root / head).resolve() if head else root
    try:
        base_dir.relative_to(root)
    except ValueError:
        return []
    try:
        entries = list(os.scandir(base_dir))
    except OSError:
        return []
    entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
    items = []
    for e in entries:
        if len(items) >= limit:
            break
        name = e.name
        if name.startswith('.') and not base.startswith('.'):
            continue
        if base and not name.lower().startswith(base.lower()):
            continue
        is_dir = e.is_dir()
        rel = f'{head}/{name}' if head else name
        if is_dir:
            rel += '/'
        items.append({'name': rel, 'icon': '+', 'desc': '', 'dir': is_dir})
    return items


def _suggest_label(item: dict) -> str:
    icon = item.get('icon')
    if icon:
        return f"{icon} {item['name']}"
    return f"/{item['name']}"


def _agent_alive(agent) -> bool:
    flag = getattr(agent, 'is_running', None)
    if flag is None:
        return True
    return bool(flag)


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


def _tool_uses(count: int) -> str:
    return f"{count} tool call" if count == 1 else f"{count} tool calls"


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
            f'{round(fraction * 100)}%[/]')


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


def _more_tool_uses(count: int) -> str:
    return f"+{_tool_uses(count)} ({EXPAND_HINT})"


def _single_line(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    if len(text) <= limit:
        return text
    return text[:max(1, limit - 1)].rstrip() + "\u2026"


def _agent_progress_rows(message, cwd, width: int) -> tuple[list[str], int]:
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        return [], 0
    content = message.get('content')
    if isinstance(content, str):
        text = _single_line(content, width)
        return ([escape(text)] if text else []), 0
    if not isinstance(content, list):
        return [], 0
    rows: list[str] = []
    uses = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get('type')
        if kind == 'text':
            text = _single_line(block.get('text', ''), width)
            if text:
                rows.append(escape(text))
        elif kind == 'tool_use':
            uses += 1
            target = str(block.get('name') or 'tool')
            label = escape(_tool_label(target, block.get('input')))
            args = _single_line(_tool_use_args(target, block.get('input'), cwd),
                                max(width * 2, 80))
            rows.append(f"{label}({escape(args)})" if args else label)
    return rows, uses


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


class _Conv(VerticalScroll):

    def watch_scroll_y(self, value):
        try:
            self.app._set_follow(bool(self.is_vertical_scroll_end))
        except Exception:
            pass


def _half_page(view) -> int:
    return max(1, view.scrollable_content_region.height // 2)


def _full_page(view) -> int:
    return max(1, view.scrollable_content_region.height)


class _PagerScroll(VerticalScroll):

    BINDINGS = [
        Binding("up", "line_up", "Scroll up", show=False),
        Binding("k", "line_up", "Scroll up", show=False),
        Binding("down", "line_down", "Scroll down", show=False),
        Binding("j", "line_down", "Scroll down", show=False),
        Binding("pageup", "half_page_up", "Half page up", show=False),
        Binding("ctrl+u", "half_page_up", "Half page up", show=False),
        Binding("pagedown", "half_page_down", "Half page down", show=False),
        Binding("ctrl+d", "half_page_down", "Half page down", show=False),
        Binding("ctrl+b", "full_page_up", "Page up", show=False),
        Binding("b", "full_page_up", "Page up", show=False),
        Binding("ctrl+f", "full_page_down", "Page down", show=False),
        Binding("space", "full_page_down", "Page down", show=False),
        Binding("home", "scroll_home", "Top", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("end", "scroll_end", "Bottom", show=False),
        Binding("G", "scroll_end", "Bottom", show=False),
    ]

    def _jump(self, rows: int):
        self.scroll_relative(y=rows, animate=False)

    def action_line_up(self):
        self._jump(-1)

    def action_line_down(self):
        self._jump(1)

    def action_half_page_up(self):
        self._jump(-_half_page(self))

    def action_half_page_down(self):
        self._jump(_half_page(self))

    def action_full_page_up(self):
        self._jump(-_full_page(self))

    def action_full_page_down(self):
        self._jump(_full_page(self))


class _JumpToBottom(Static):

    def __init__(self, text: str = "", **kw):
        super().__init__(text, markup=True, **kw)

    async def on_click(self):
        await self.app.action_jump_to_bottom()


def _hang(prefix: str, body: str) -> str:
    return prefix + body.replace("\n", "\n" + " " * len(prefix))


class _TextBlock(Vertical):

    def __init__(self, bullet: str = BULLET_PREFIX, **kw):
        super().__init__(classes="text-block", **kw)
        self._body = ""
        self._bullet = bullet
        self._md: Markdown | None = None
        self._flush_scheduled = False

    def _display(self) -> str:
        return self._body.strip("\n")

    def set_body(self, text: str):
        self._body = text
        if self._md is None:
            if self.is_mounted:
                self._mount_children()
            else:
                return
        if not self._flush_scheduled:
            self._flush_scheduled = True
            self.call_later(self._flush)

    async def _flush(self):
        self._flush_scheduled = False
        if self._md is not None:
            self._md.update(self._display())

    def on_mount(self):
        if self._md is None:
            self._mount_children()

    def _mount_children(self):
        bullet = Static(self._bullet, markup=True, classes="text-bullet")
        self._md = Markdown(self._display(), classes="text-body")
        self.mount(Horizontal(bullet, self._md, classes="text-row"))


class _GroupBlock(Static):

    def __init__(self, **kw):
        super().__init__(markup=True, **kw)
        self.counts = {kind: 0 for kind, *_ in GROUP_PARTS}
        self.entries = []
        self.active = True
        self._frame = BULLET
        self._read_keys = set()
        self._draw()

    def add(self, kinds, key, uid):
        self.entries.append((kinds, key, uid))
        for kind in kinds:
            if kind == 'read':
                if key in self._read_keys:
                    continue
                self._read_keys.add(key)
            self.counts[kind] = self.counts.get(kind, 0) + 1
        self._draw()

    def tick(self, char: str):
        self._frame = char
        if self.active:
            self._draw()

    def finish(self):
        if not self.active:
            return
        self.active = False
        self._draw()

    def _parts(self) -> str:
        chunks = []
        for kind, active_verb, done_verb, noun, plural in GROUP_PARTS:
            count = self.counts.get(kind, 0)
            if not count:
                continue
            verb = active_verb if self.active else done_verb
            verb = verb[0].upper() + verb[1:] if not chunks else \
                verb[0].lower() + verb[1:]
            word = noun if count == 1 else plural
            chunks.append(f"{verb} [bold]{count}[/] {word}")
        return ", ".join(chunks)

    def _draw(self):
        body = self._parts()
        if not body:
            self.update("")
            return
        color = self.app.brand if self.active else '#4EBA65'
        marker = self._frame if self.active else BULLET
        hint = "" if self.active else " [dim](ctrl+o for the list)[/]"
        self.update(f"[{color}]{marker}[/] {body}{hint}")


class _LogoBlock(Static):

    def __init__(self, triple: tuple, greeting: str = "", **kw):
        lines = [banner.directional(*triple)]
        if greeting:
            lines.append("\n" + greeting)
        super().__init__("\n".join(lines), markup=True, classes="logo", **kw)


class _PromptInput(Input):

    def check_consume_key(self, key: str, character: str | None) -> bool:
        app = self.app
        if key == 'k' and getattr(app, '_view_selection', '') \
                == 'selecting-agent':
            return False
        return super().check_consume_key(key, character)


class _AgentPane(Static):

    def __init__(self, **kw):
        super().__init__("", markup=True, classes="agents", **kw)


class _UserBlock(Static):

    def __init__(self, text: str, **kw):
        super().__init__(_user_markup(text), markup=True, classes="user", **kw)


class _ToolBlock(Static):

    def __init__(self, name: str, tool_input, cwd: str = ".", **kw):
        super().__init__(markup=True, **kw)
        self._name = name
        self._input = tool_input
        self._cwd = cwd
        self._output = None
        self._done = False
        self._expanded = False
        self._failed = False
        self._frame = BULLET
        self._meta: dict | None = None
        self._progress: list[tuple[list[str], int]] = []
        self._progress_done = False
        self._waiting_permission = False
        self._rejected: str | None = None
        self._draw()

    def tick(self, char: str):
        if not self._done:
            self._frame = char
            self._draw()

    def set_waiting_permission(self, waiting: bool):
        if self._waiting_permission == waiting:
            return
        self._waiting_permission = waiting
        self._draw()

    def reject(self, text: str = INTERRUPTED_TEXT):
        self._done = True
        self._progress = []
        self._progress_done = True
        self._waiting_permission = False
        self._rejected = text
        self._failed = True
        self._draw()

    def add_progress(self, rows: list[str], tool_uses: int):
        if self._output is not None:
            return
        self._progress.append((list(rows), int(tool_uses)))
        self._draw()

    def end_progress(self):
        if not self._progress and self._progress_done:
            return
        self._progress = []
        self._progress_done = True
        self._draw()

    def _trail_rows(self, full: bool = False) -> list[str]:
        if self._name != 'create_agent':
            return []
        if full:
            shown, hidden = self._progress, 0
        else:
            shown = self._progress[-AGENT_TRAIL_LIMIT:]
            hidden = sum(uses for _rows, uses
                         in self._progress[:-AGENT_TRAIL_LIMIT])
        rows = [row for entry, _uses in shown for row in entry]
        if hidden:
            rows.append(_more_tool_uses(hidden))
        return rows

    def _trail_markup(self, full: bool = False) -> str | None:
        if self._output is not None or self._progress_done:
            return None
        if self._waiting_permission:
            return f"[dim]{RESULT_PREFIX}{WAITING_PERMISSION_TEXT}[/]"
        rows = self._trail_rows(full) or [INITIALIZING_TEXT]
        body = ("\n" + RESULT_HANG).join(rows)
        return f"[dim]{RESULT_PREFIX}{body}[/]"

    def set_result(self, output, meta=None):
        self._done = True
        self._progress = []
        self._progress_done = True
        self._output = output
        self._meta = meta
        self._failed = str(output or "").startswith("Error")
        self._draw()

    def _meta_summary(self) -> str | None:
        m = self._meta or {}
        if m.get('num_lines') and self._name == 'Read':
            path = m.get('path', '')
            return (f"Read {_plural(m['num_lines'], 'line')}"
                    + (f" {escape(path)}" if path else ""))
        if m.get('num_files') is not None or m.get('num_lines') is not None:
            if self._name == 'Grep':
                hits = m.get('num_lines', 0)
                files = m.get('num_files', 0)
                return (f"Found {_plural(hits, 'line')} in "
                        f"{_plural(files, 'file')}")
            if m.get('num_files') is not None:
                return f"Found {_plural(m['num_files'], 'file')}"
        if m.get('num_entries') is not None:
            return f"Listed {_plural(m['num_entries'], 'entry')}"
        if m.get('num_added', 0) or m.get('num_removed', 0):
            return _edit_summary(m.get('num_added', 0),
                                 m.get('num_removed', 0))
        if self._name == 'Write' and m.get('mode'):
            verb = 'Wrote' if m['mode'] == 'wrote' else 'Updated'
            if m.get('path'):
                return f"{verb} {escape(m['path'])}"
        return None

    def on_click(self):
        self._expanded = not self._expanded
        self._draw()

    def on_resize(self):
        self._draw()

    def _width(self) -> int:
        width = self.size.width or 0
        if width <= 0:
            try:
                width = self.app.size.width
            except Exception:
                width = 0
        return max(20, (width or 80) - len(RESULT_PREFIX) - 2)

    def _is_teammate_spawn(self) -> bool:
        return (self._name == 'create_agent'
                and isinstance(self._input, dict)
                and bool(self._input.get('name')))

    def _agent_summary(self) -> str | None:
        return (self._meta or {}).get('agent_summary')

    def _head(self) -> str:
        name = escape(_tool_label(self._name, self._input))
        args = escape(_tool_use_args(self._name, self._input, self._cwd))
        color = "#FF6B80" if self._failed else (
            "#4EBA65" if self._done else self.app.brand)
        marker = BULLET if (self._done or self._failed) else self._frame
        return f"[{color}]{marker}[/] [bold]{name}[/]({args})"

    def _draw(self):
        head = self._head()
        if self._rejected is not None:
            self.update(f"{head}\n[dim]{RESULT_PREFIX}"
                        f"{escape(self._rejected)}[/]")
            return
        if self._is_teammate_spawn():
            trail = self._trail_markup()
            self.update(f"{head}\n{trail}" if trail else head)
            return
        summary = self._agent_summary()
        if summary is not None and self._output is not None:
            rows = escape(summary).replace("\n", "\n" + RESULT_HANG)
            self.update(f"{head}\n[dim]{RESULT_PREFIX}{rows}[/]")
            return
        if self._output is None:
            trail = self._trail_markup()
            self.update(f"{head}\n{trail}" if trail else head)
            return
        if self._expanded:
            body = str(self._output)
            if not body.strip():
                self.update(f"{head}\n[dim]{RESULT_PREFIX}(no output)[/]")
                return
            rows = escape(body).replace("\n", "\n" + RESULT_HANG)
            self.update(f"{head}\n{RESULT_PREFIX}{rows}")
            return
        summary = self._meta_summary()
        if summary is None:
            summary = _result_summary(self._name, self._output,
                                      self._width())
        if self._meta_summary() is not None:
            self.remove_class("diff")
        else:
            diff = _diff_block(self._name, self._input, self._output,
                               self._cwd, self._width())
            if diff is not None:
                self.add_class("diff")
                self.update(f"{head}\n{diff}")
                return
            self.remove_class("diff")
        rows = escape(summary).replace("\n", "\n" + RESULT_HANG)
        self.update(f"{head}\n[dim]{RESULT_PREFIX}{rows}[/]")


class TranscriptScreen(Screen):

    BINDINGS = [("escape", "exit_transcript", "Back"),
                ("q", "exit_transcript", "Back"),
                ("ctrl+o", "exit_transcript", "Back"),
                ("ctrl+c", "exit_transcript", "Back")]

    def __init__(self, owner, **kw):
        super().__init__(**kw)
        self._owner = owner

    def compose(self) -> ComposeResult:
        with _PagerScroll(id="transcript"):
            for entry in self._entries():
                yield Static(entry, markup=True)

    def on_mount(self):
        self.query_one("#transcript", _PagerScroll).focus()

    @staticmethod
    def _tool_entry(block) -> str:
        head = block._head()
        if block._output is None:
            trail = block._trail_markup(full=True)
            return f"{head}\n{trail}" if trail else head
        diff = _diff_block(block._name, block._input, block._output,
                           block._cwd, block._width())
        if diff is not None:
            return head + "\n" + diff
        body = escape(str(block._output)) or "(no output)"
        return (head + "\n" + RESULT_PREFIX
                + body.replace("\n", "\n" + RESULT_HANG))

    @staticmethod
    def _thinking_entry(text: str) -> str:
        return (f"[#9A9A9A]{ASTERISK} Thinking\u2026[/]\n"
                + _hang(RESULT_HANG, escape(text)))

    def _entries(self) -> list[str]:
        app = self._owner
        entries: list[str] = []
        thinking_map = app._thinking_map()
        for widget in app._conv().children:
            if isinstance(widget, (_PermissionPrompt, _JumpToBottom, _LogoBlock)):
                continue
            if isinstance(widget, _TextBlock):
                body = widget._body or ""
                shown = body.strip("\n")
                thought = thinking_map.get(body) or thinking_map.get(shown)
                if thought:
                    entries.append(self._thinking_entry(thought))
                entries.append(_hang(BULLET_PREFIX, escape(shown)))
            elif isinstance(widget, _UserBlock):
                entries.append(escape(str(widget.content)))
            elif isinstance(widget, _GroupBlock):
                for _kinds, _key, uid in widget.entries:
                    block = app._tools.get(uid)
                    if block is not None:
                        entries.append(self._tool_entry(block))
            elif isinstance(widget, _ToolBlock):
                if widget.display:
                    entries.append(self._tool_entry(widget))
            else:
                entries.append(str(widget.content))
        return entries

    def action_exit_transcript(self):
        self.app.pop_screen()


class HelpScreen(Screen):

    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close"),
                ("ctrl+o", "close", "Close"), ("?", "close", "Close")]

    def _body(self) -> str:
        from pyclaw.slash import COMMANDS
        lines = ["[bold]Shortcuts[/bold]"]
        seen = set()
        for entry in PyClawApp.BINDINGS:
            if isinstance(entry, Binding):
                key, action, description = (entry.key, entry.action,
                                            entry.description)
            else:
                key, action, description = (entry[0], entry[1],
                                            entry[2] if len(entry) > 2 else '')
            if key in seen or not description:
                continue
            seen.add(key)
            lines.append(f"  {escape(key)}  "
                         f"[dim]{escape(description)}[/]")
        lines.append("")
        lines.append("[bold]Reading the transcript (ctrl+o)[/bold]")
        pager_seen = set()
        for entry in _PagerScroll.BINDINGS:
            if entry.key in pager_seen or not entry.description:
                continue
            pager_seen.add(entry.key)
            lines.append(f"  {escape(entry.key)}  "
                         f"[dim]{escape(entry.description)}[/]")
        lines.append("")
        lines.append("[bold]Slash commands[/bold]")
        for item in COMMANDS:
            lines.append(f"  /{escape(item['name'])}  "
                         f"[dim]{escape(item['desc'])}[/]")
        lines.append("")
        lines.append("[dim]esc to close[/]")
        return "\n".join(lines)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help"):
            yield Static(self._body(), markup=True)

    def action_close(self):
        self.app.pop_screen()


class HistorySearchScreen(Screen):

    BINDINGS = [Binding("up", "prev", "Previous", priority=True),
                Binding("down", "next", "Next", priority=True),
                ("escape", "close", "Close"),
                ("ctrl+c", "close", "Close"),
                ("tab", "accept", "Accept")]

    def __init__(self, owner, **kw):
        super().__init__(**kw)
        self._owner = owner
        self._selected = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="history"):
            yield Input(placeholder="Search history\u2026", id="hs-input")
            yield Static("", id="hs-list", markup=True)

    def on_mount(self):
        self.query_one("#hs-input", Input).focus()
        self._refresh()

    def _matches(self) -> list[str]:
        query = self.query_one("#hs-input", Input).value
        return [t for t in reversed(self._owner._history)
                if not query or query in t]

    def _refresh(self):
        try:
            widget = self.query_one("#hs-list", Static)
        except Exception:
            return
        matches = self._matches()
        if not matches:
            widget.update("[dim]no matching history[/]")
            return
        self._selected = min(self._selected, len(matches) - 1)
        start = max(0, min(self._selected - 3, len(matches) - 7))
        window = matches[start:start + 7]
        lines = []
        for i, text in enumerate(window):
            index = start + i
            row = escape(text)
            lines.append(f"[#B1B9F9]{row}[/]"
                         if index == self._selected else f"[dim]{row}[/]")
        widget.update("\n".join(lines))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._selected = 0
        self._refresh()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        await self._finish(True)

    def action_accept(self):
        asyncio.get_running_loop().create_task(self._finish(False))

    def action_close(self):
        self.app.pop_screen()

    def action_prev(self):
        matches = self._matches()
        if matches:
            self._selected = (self._selected - 1) % len(matches)
            self._refresh()

    def action_next(self):
        matches = self._matches()
        if matches:
            self._selected = (self._selected + 1) % len(matches)
            self._refresh()

    async def _finish(self, execute: bool):
        matches = self._matches()
        if not matches:
            self.app.pop_screen()
            return
        text = matches[self._selected]
        inp = self._owner.query_one("#input", Input)
        inp.value = text
        inp.cursor_position = len(text)
        self.app.pop_screen()
        if execute:
            await inp.action_submit()


class _RuleList(VerticalScroll):

    BINDINGS = [("up", "move_up", "Up"), ("down", "move_down", "Down"),
                ("d", "remove_rule", "Remove rule"),
                ("escape", "close", "Close"), ("q", "close", "Close")]

    def __init__(self, screen, **kw):
        super().__init__(**kw)
        self._screen = screen

    def action_move_up(self):
        self._screen.action_move_up()

    def action_move_down(self):
        self._screen.action_move_down()

    def action_remove_rule(self):
        self._screen.action_remove_rule()

    def action_close(self):
        self._screen.action_close()


class PermissionsScreen(Screen):

    BINDINGS = [("escape", "close", "Close"),
                ("q", "close", "Close"),
                ("d", "remove_rule", "Remove rule"),
                ("up", "move_up", "Up"),
                ("down", "move_down", "Down")]

    def __init__(self, session, **kw):
        super().__init__(**kw)
        self._session = session
        self._selected = 0

    def compose(self) -> ComposeResult:
        with _RuleList(self, id="permissions"):
            yield Static('...', id="permissions-body")

    def on_mount(self):
        self.query_one("#permissions", _RuleList).focus()
        self._refresh_body()

    def _rules(self) -> list:
        getter = getattr(self._session, 'permission_rules', None)
        return list(getter()) if getter else []

    def _refresh_body(self):
        rules = self._rules()
        mode = getattr(self._session, 'permission_mode', 'default')
        bypass = bool(getattr(self._session, 'bypass_available', False))
        lines = [f'[bold]Permission mode[/bold] [{self.app.brand}]{mode}[/]',
                 f'[#9A9A9A]bypass available: {bypass} · '
                 f'shift+tab cycles · /permissions <mode> switches[/]', '']
        if not rules:
            lines.append('[#9A9A9A]No permission rules.[/]')
        else:
            lines.append('[bold]Rules[/bold] '
                         '[#9A9A9A](up/down to move, d to remove)[/]')
            for index, (behavior, rule, source) in enumerate(rules):
                marker = '\u203a' if index == self._selected else ' '
                body = escape(f'  {marker} [{behavior}] {rule}  ({source})')
                lines.append(f'[reverse]{body}[/]' if index == self._selected
                             else body)
        self.query_one('#permissions-body', Static).update('\n'.join(lines))

    def action_close(self):
        self.app.pop_screen()

    def action_move_up(self):
        self._selected = max(0, self._selected - 1)
        self._refresh_body()

    def action_move_down(self):
        rules = self._rules()
        self._selected = min(max(0, len(rules) - 1), self._selected + 1)
        self._refresh_body()

    def action_remove_rule(self):
        rules = self._rules()
        if not rules:
            return
        index = min(self._selected, len(rules) - 1)
        _behavior, rule, _source = rules[index]
        remover = getattr(self._session, 'remove_rule', None)
        if remover is None or not remover(rule):
            return
        self._selected = max(0, self._selected - 1)
        self._refresh_body()


_POINTER = '\u203a'
_CHECKED = '\u2612'
_UNCHECKED = '\u2610'
_WARN = '\u26a0'

AGENT_STEPS = ('name', 'location', 'prompt', 'description', 'tools', 'model',
               'confirm')
AGENT_TITLES = {
    'name': 'Name (identifier)',
    'location': 'Where to save',
    'prompt': 'System prompt',
    'description': 'Description (when PyClaw should delegate)',
    'tools': 'Pick tools',
    'model': 'Pick a model',
    'confirm': 'Review and save',
}
AGENT_QUESTIONS = {
    'name': 'What should this agent be called?',
    'prompt': 'What should it do? This text becomes its system prompt.',
    'description': 'When does PyClaw hand work to this agent?',
    'model': 'A model sets how it reasons and how fast it runs.',
}
AGENT_PLACEHOLDERS = {
    'name': 'like test-runner or tech-lead',
    'prompt': 'You review diffs and point out...',
    'description': 'like: once a piece of code is written',
    'model': 'empty keeps the session model',
}
AGENT_TEXT_STEPS = ('name', 'prompt', 'description', 'model')
AGENT_LOCATIONS = (('project', '.pyclaw/agents/'),
                   ('user', '~/.pyclaw/agents/'))
AGENT_NAV = ('\u2191\u2193 move \u00b7 enter picks \u00b7 esc goes back')
AGENT_TEXT_NAV = ('type it in \u00b7 enter continues \u00b7 esc goes back')


class _AgentKeys(VerticalScroll):

    can_focus = True

    BINDINGS = [("up", "move_up", "Up"), ("down", "move_down", "Down"),
                ("enter", "choose", "Select"), ("escape", "back", "Back"),
                ("s", "save", "Save")]

    def __init__(self, screen, **kw):
        super().__init__(**kw)
        self._screen = screen

    def action_move_up(self):
        self._screen.action_move_up()

    def action_move_down(self):
        self._screen.action_move_down()

    def action_choose(self):
        self._screen.action_choose()

    def action_back(self):
        self._screen.action_back()

    def action_save(self):
        self._screen.action_save()


class _AgentInput(Input):

    BINDINGS = [("escape", "agents_back", "Back")]

    def __init__(self, screen, **kw):
        super().__init__(**kw)
        self._screen = screen

    def action_agents_back(self):
        self._screen.action_back()


class AgentsScreen(Screen):

    def __init__(self, session, **kw):
        super().__init__(**kw)
        self._session = session
        self._team = session._team
        self._all_tools = list(getattr(session._team, 'provided_tools', []))
        self._names = [t.name for t in self._all_tools]
        self._cwd = session.cwd
        self._mode = 'list'
        self._entries = []
        self._selectable = []
        self._pos = 0
        self._menu_pos = 0
        self._delete_pos = 0
        self._tools_pos = 0
        self._tools_individual = False
        self._selected_tools: set = set()
        self._step = 0
        self._target = None
        self._edit_field: str | None = None
        self._draft = {'name': '', 'scope': 'project', 'prompt': '',
                       'description': '', 'model': '', 'tools': None}
        self._changes: list = []
        self._error = ''

    def compose(self) -> ComposeResult:
        with _AgentKeys(self, id='agents'):
            yield Static('', id='agents-body')
        text_input = _AgentInput(self, id='agents-input')
        text_input.display = False
        yield text_input

    def on_mount(self):
        self._reload()
        self._refresh()

    def _reload(self):
        self._entries = agent_defs.list_order(
            agent_defs.discover(self._cwd, self._all_tools))
        self._selectable = [e for e in self._entries
                            if e.scope != agent_defs.BUILT_IN]
        self._pos = min(self._pos, len(self._selectable))

    def _selected(self):
        return None if self._pos == 0 else self._selectable[self._pos - 1]

    @property
    def _step_name(self) -> str:
        return AGENT_STEPS[self._step]


    def _refresh(self):
        lines = {
            'list': self._render_list,
            'menu': self._render_menu,
            'view': self._render_view,
            'delete': self._render_delete,
            'edit-menu': self._render_edit_menu,
            'edit-tools': self._render_edit_tools,
            'edit-model': self._render_edit_model,
            'create': self._render_create,
        }[self._mode]()
        self.query_one('#agents-body', Static).update('\n'.join(lines))
        text_step = self._is_text_step()
        inp = self.query_one('#agents-input', _AgentInput)
        inp.display = text_step
        if text_step:
            field = 'model' if self._mode == 'edit-model' else self._step_name
            inp.placeholder = AGENT_PLACEHOLDERS[field]
            if inp.value != self._draft[field]:
                inp.value = self._draft[field]
                inp.cursor_position = len(inp.value)
            inp.focus()
        else:
            self.query_one('#agents', _AgentKeys).focus()

    def _is_text_step(self) -> bool:
        if self._mode == 'edit-model':
            return True
        return self._mode == 'create' and self._step_name in AGENT_TEXT_STEPS

    def _header(self, subtitle: str, note=None) -> list:
        lines = ['[bold]Agents[/bold]']
        if self._mode == 'create':
            lines = ['[bold]New agent[/bold]']
        lines.append(f'[#9A9A9A]{escape(subtitle)}[/#9A9A9A]')
        if note:
            lines.append(f'[#9A9A9A]{escape(str(note))}[/#9A9A9A]')
        return lines + ['']

    def _options(self, labels, pos, indent=2) -> list:
        lines = []
        for index, label in enumerate(labels):
            marker = f'{_POINTER} ' if index == pos else ' ' * indent
            text = escape(f'{marker}{label}')
            lines.append(f'[{self.app.brand}]{text}[/]' if index == pos
                         else text)
        return lines

    def _footer(self, hint: str) -> list:
        return ['', f'[#9A9A9A]{escape(hint)}[/#9A9A9A]']

    def _problem(self) -> list:
        if not self._error:
            return []
        return [f'[#FF6B80]{escape(self._error)}[/#FF6B80]', '']

    def _render_list(self) -> list:
        if not self._selectable:
            lines = self._header('Nothing defined yet')
            lines += self._options(['New agent'], self._pos, indent=0)
            lines += ['',
                      '[#9A9A9A]No subagents yet. A subagent is a role PyClaw '
                      'can hand a job to.[/]',
                      '[#9A9A9A]Each one brings its own context, prompt and '
                      'tool set.[/]',
                      '[#9A9A9A]Ideas: code reviewer, simplifier, security '
                      'reviewer, tech lead.[/]']
        else:
            count = agent_defs.agent_count(self._entries)
            lines = self._header(f'{count} agents',
                                 self._changes[-1] if self._changes else None)
            lines += self._options(['New agent'], self._pos, indent=0)
            lines.append('')
            position = 0
            for scope in (agent_defs.USER, agent_defs.PROJECT):
                group = [e for e in self._entries if e.scope == scope]
                if not group:
                    continue
                directory = agent_defs.agents_dir(scope, self._cwd)
                lines.append('[bold][#9A9A9A]'
                             f'{escape(agent_defs.SCOPE_LABELS[scope])} '
                             f'({escape(str(directory))})[/#9A9A9A][/bold]')
                for entry in group:
                    position += 1
                    lines.append(self._row(entry, position))
        built_ins = [e for e in self._entries
                     if e.scope == agent_defs.BUILT_IN]
        if built_ins:
            lines += ['', '[bold][#9A9A9A]Bundled with PyClaw[/#9A9A9A][/bold]']
            lines += [self._row(e, -1) for e in built_ins]
        return lines + self._footer(AGENT_NAV)

    def _row(self, entry, position) -> str:
        is_built_in = entry.scope == agent_defs.BUILT_IN
        chosen = not is_built_in and self._pos == position
        marker = '' if is_built_in else (f'{_POINTER} ' if chosen else '  ')
        model = agent_defs.model_display(entry.defn, self._session.model)
        text = f'{marker}{entry.agent_type} \u00b7 {model}'
        if entry.shadowed_by:
            text += f' {_WARN} hidden behind {entry.shadowed_by}'
        text = escape(text)
        if chosen:
            return f'[{self.app.brand}]{text}[/]'
        if is_built_in or entry.shadowed_by:
            return f'[dim]{text}[/dim]'
        return text

    def _render_menu(self) -> list:
        lines = self._header(self._target.agent_type)
        lines += [f'[#9A9A9A]Source: '
                  f'{escape(self._target.scope)}[/#9A9A9A]', '']
        return lines + self._options(
            [label for label, _ in self._menu_options()],
            self._menu_pos) + self._footer(AGENT_NAV)

    def _render_view(self) -> list:
        defn = self._target.defn
        tools = [t.name for t in defn.tools]
        lines = [f'[#9A9A9A]{escape(agent_defs.relative_path(self._target))}'
                 f'[/#9A9A9A]', '']
        lines += ['[bold]Description[/bold] (tells PyClaw when to use this '
                  'agent):',
                  f'  {escape(defn.description or "No description.")}']
        lines.append('[bold]Tools[/bold]: '
                     + (escape(', '.join(tools)) if tools
                        else escape('All tools')))
        lines.append(f'[bold]Model[/bold]: '
                     f'{escape(defn.model or self._session.model)}')
        if defn.permission_mode:
            lines.append('[bold]Permission mode[/bold]: '
                         f'{escape(defn.permission_mode)}')
        lines += ['', '[bold]System prompt[/bold]',
                  escape(defn.system_prompt or '')]
        return lines + self._footer('enter or esc goes back')

    def _render_delete(self) -> list:
        lines = self._header('Delete an agent')
        lines += [escape(f'Remove {self._target.agent_type} for good?'),
                  f'[#9A9A9A]Source: {escape(self._target.scope)}[/#9A9A9A]',
                  '']
        return lines + self._options(['Delete it', 'Keep it'],
                                     self._delete_pos) + self._footer(AGENT_NAV)

    def _render_edit_menu(self) -> list:
        lines = self._header(self._target.agent_type)
        lines += [f'[#9A9A9A]Source: '
                  f'{escape(self._target.scope)}[/#9A9A9A]', '']
        return lines + self._options(['Change tools', 'Change model'],
                                     self._menu_pos) + self._footer(AGENT_NAV)

    def _render_create(self) -> list:
        name = self._step_name
        if name == 'location':
            lines = self._header(AGENT_TITLES[name])
            return lines + self._options(
                [f'{agent_defs.SCOPE_LABELS[s]} ({hint})'
                 for s, hint in AGENT_LOCATIONS], self._pos) \
                + self._footer(AGENT_NAV)
        if name == 'tools':
            return self._tools_lines(AGENT_TITLES['tools'])
        if name == 'confirm':
            return self._render_confirm()
        lines = self._header(AGENT_TITLES[name])
        lines += self._problem()
        return lines + [escape(AGENT_QUESTIONS[name])] + self._footer(
            AGENT_TEXT_NAV)

    def _render_edit_tools(self) -> list:
        return self._tools_lines(
            self._target.agent_type, title='Change tools')

    def _tools_lines(self, subtitle: str, title=None) -> list:
        lines = [f'[bold]{escape(title or "New agent")}[/bold]',
                 f'[#9A9A9A]{escape(subtitle)}[/#9A9A9A]', '']
        labels = []
        for kind, label, payload in self._tool_items():
            if kind in ('continue', 'toggle'):
                labels.append(f'[ {escape(label)} ]')
            else:
                names = self._names if kind == 'all' else list(payload)
                mark = (_CHECKED
                        if all(n in self._selected_tools for n in names)
                        else _UNCHECKED)
                labels.append(f'{mark} {label}')
        lines += self._options(labels, self._tools_pos)
        chosen = len([n for n in self._names if n in self._selected_tools])
        selected = ('Every tool picked' if chosen == len(self._names)
                    else f'{chosen} of {len(self._names)} picked')
        lines += ['', f'[#9A9A9A]{escape(selected)}[/#9A9A9A]']
        return lines + self._footer(
            'enter toggles \u00b7 \u2191\u2193 move \u00b7 esc goes back')

    def _render_edit_model(self) -> list:
        lines = self._header(AGENT_TITLES['model'], self._error or None)
        return lines + [escape(AGENT_QUESTIONS['model'])] \
            + self._footer(AGENT_TEXT_NAV)

    def _render_confirm(self) -> list:
        errors, warnings = agent_defs.validate(
            self._draft_definition(), self._names, self._taken())
        rows = [('Name', self._draft['name']),
                ('Location', agent_defs.SCOPE_LABELS[self._draft['scope']]),
                ('Tools', self._tools_display()),
                ('Model', self._draft['model'] or self._session.model),
                ('Description', self._draft['description']),
                ('System prompt', self._draft['prompt'])]
        lines = self._header(AGENT_TITLES['confirm'], self._error or None)
        lines += [f'[bold]{label}[/bold]: {escape(str(value))}'
                  for label, value in rows]
        if warnings:
            lines += ['', '[bold][#9A9A9A]Warnings:[/#9A9A9A][/bold]']
            lines += [f'[#9A9A9A] \u2022 {escape(w)}[/#9A9A9A]'
                      for w in warnings]
        if errors:
            lines += ['', '[bold][#FF6B80]Errors:[/#FF6B80][/bold]']
            lines += [f'[#FF6B80] \u2022 {escape(e)}[/#FF6B80]'
                      for e in errors]
        return lines + self._footer('s or enter saves \u00b7 esc goes back')

    def _tools_display(self) -> str:
        chosen = self._draft['tools']
        return 'All tools' if chosen is None else ', '.join(chosen)

    def _tool_items(self) -> list:
        items = [('continue', 'Continue', ()), ('all', 'All tools', ())]
        for label, members in agent_defs.tool_buckets(self._names):
            items.append(('bucket', label, tuple(members)))
        items.append(('toggle',
                      'Hide the tool list' if self._tools_individual
                      else 'Show the tool list', ()))
        if self._tools_individual:
            items += [('tool', name, (name,)) for name in self._names]
        return items

    def _menu_options(self) -> list:
        options = [('Open', 'view')]
        if self._target.scope != agent_defs.BUILT_IN:
            options += [('Change', 'edit'), ('Delete', 'delete')]
        return options + [('Back', 'back')]

    def _menu_values(self) -> list:
        if self._mode == 'edit-menu':
            return ['tools', 'model']
        return [value for _, value in self._menu_options()]

    def _taken(self) -> list:
        return [(e.agent_type, e.scope) for e in self._entries
                if e.scope != agent_defs.BUILT_IN
                and e.agent_type != self._draft['name']]

    def _draft_definition(self):
        chosen = self._draft['tools']
        by_name = {t.name: t for t in self._all_tools}
        tools = (list(self._all_tools) if chosen is None
                 else [by_name[n] for n in chosen if n in by_name])
        return AgentDefinition(
            self._draft['name'], system_prompt=self._draft['prompt'],
            tools=tools, model=self._draft['model'] or None,
            description=self._draft['description'])


    def action_move_up(self):
        self._move(-1)

    def action_move_down(self):
        self._move(1)

    def _move(self, delta: int):
        if self._mode == 'list':
            self._pos = (self._pos + delta) % (len(self._selectable) + 1)
        elif self._mode == 'delete':
            self._delete_pos = min(max(0, self._delete_pos + delta), 1)
        elif self._mode == 'create' and self._step_name == 'location':
            self._pos = 1 - self._pos
        elif ((self._mode == 'create' and self._step_name == 'tools')
                or self._mode == 'edit-tools'):
            self._move_tools(delta)
        elif self._mode in ('menu', 'edit-menu'):
            self._menu_pos = min(max(0, self._menu_pos + delta),
                                 len(self._menu_values()) - 1)
        self._refresh()

    def _move_tools(self, delta: int):
        items = self._tool_items()
        self._tools_pos = min(max(0, self._tools_pos + delta), len(items) - 1)

    def action_choose(self):
        if self._mode == 'list':
            if self._pos == 0:
                self._start_create()
            else:
                self._open(self._selected())
        elif self._mode in ('menu', 'edit-menu'):
            self._choose_menu(self._menu_values()[self._menu_pos])
        elif self._mode == 'delete':
            if self._delete_pos == 0:
                self._delete_target()
            else:
                self._mode = 'menu'
        elif self._mode == 'create' and self._step_name == 'location':
            self._draft['scope'] = AGENT_LOCATIONS[self._pos][0]
            self._advance()
        elif self._mode in ('create', 'edit-tools'):
            self._choose_tool_item()
        elif self._mode == 'view':
            self._mode = 'menu'
        self._refresh()

    def _choose_menu(self, value: str):
        if value == 'view':
            self._mode = 'view'
        elif value == 'edit':
            self._mode = 'edit-menu'
            self._menu_pos = 0
        elif value == 'delete':
            self._mode = 'delete'
            self._delete_pos = 0
        elif value == 'tools':
            self._start_edit_tools()
        elif value == 'model':
            self._start_edit_model()
        else:
            self._mode = 'list'

    def _choose_tool_item(self):
        if self._mode == 'create' and self._step_name == 'confirm':
            self._save_draft()
            return
        kind, _label, payload = self._tool_items()[self._tools_pos]
        if kind == 'continue':
            chosen = [n for n in self._names if n in self._selected_tools]
            picked = (None if len(chosen) == len(self._names) else chosen)
            if self._mode == 'edit-tools':
                self._draft['tools'] = picked
                self._save_edit()
            else:
                self._draft['tools'] = picked
                self._advance()
            return
        if kind == 'toggle':
            self._tools_individual = not self._tools_individual
            if not self._tools_individual:
                self._tools_pos = min(self._tools_pos,
                                      len(self._tool_items()) - 1)
            return
        names = self._names if kind == 'all' else list(payload)
        select = not all(n in self._selected_tools for n in names)
        for name in names:
            if select:
                self._selected_tools.add(name)
            else:
                self._selected_tools.discard(name)

    def action_save(self):
        if self._mode == 'create' and self._step_name == 'confirm':
            self._save_draft()
            self._refresh()

    def action_back(self):
        if self._mode == 'list':
            self._exit()
        elif self._mode == 'create':
            self._back_step()
        elif self._mode == 'edit-model':
            self._mode = 'edit-menu'
            self._refresh()
        elif self._mode in ('view', 'delete'):
            self._mode = 'menu'
            self._refresh()
        elif self._mode == 'edit-tools':
            self._mode = 'edit-menu'
            self._refresh()
        else:
            self._mode = 'list'
            self._refresh()


    def _start_create(self):
        self._mode = 'create'
        self._step = 0
        self._pos = 0
        self._error = ''
        self._draft = {'name': '', 'scope': 'project', 'prompt': '',
                       'description': '', 'model': '', 'tools': None}
        self._selected_tools = set(self._names)
        self._tools_individual = False
        self._tools_pos = 0

    def _advance(self):
        self._step += 1
        self._error = ''
        self._pos = 0
        self._tools_pos = 0

    def _back_step(self):
        if self._step == 0:
            self._mode = 'list'
        else:
            self._step -= 1
            self._error = ''
            self._pos = 0
        self._refresh()

    def on_input_submitted(self, event: Input.Submitted):
        value = event.value.strip()
        if self._mode == 'edit-model':
            self._draft['model'] = value
            self._save_edit()
            self._refresh()
            return
        field = self._step_name
        if field == 'name':
            self._error = agent_defs.validate_type(value) or ''
            if self._error:
                self._refresh()
                return
        elif field in ('prompt', 'description'):
            if not value:
                self._error = (f'{AGENT_TITLES[field].split(" (")[0]} is '
                               'required')
                self._refresh()
                return
        self._draft[field] = value
        self._advance()
        self._refresh()


    def _open(self, entry):
        self._target = entry
        self._mode = 'menu'
        self._menu_pos = 0
        self._error = ''
        self._refresh()

    def _start_edit_tools(self):
        self._selected_tools = {t.name for t in self._target.defn.tools}
        self._tools_individual = False
        self._tools_pos = 0
        self._edit_field = 'tools'
        self._mode = 'edit-tools'

    def _start_edit_model(self):
        self._draft['model'] = self._target.defn.model or ''
        self._edit_field = 'model'
        self._mode = 'edit-model'

    def _edited_definition(self):
        defn = self._target.defn
        if self._edit_field == 'model':
            return dataclasses.replace(defn,
                                       model=self._draft['model'] or None)
        chosen = self._draft['tools']
        if chosen is None:
            return dataclasses.replace(defn, tools=list(self._all_tools))
        return dataclasses.replace(
            defn, tools=[t for t in self._all_tools
                         if t.name in set(chosen)])

    def _save_edit(self):
        defn = self._edited_definition()
        try:
            agent_defs.write_agent(defn, self._target.scope, self._cwd,
                                   self._all_tools, overwrite=True)
        except OSError as e:
            self._error = str(e)
            return
        self._changes.append(f'Saved changes to {defn.agent_type}')
        self._error = ''
        self._reload()
        self._sync()
        self._mode = 'list'

    def _delete_target(self):
        entry = self._target
        try:
            agent_defs.remove_agent(entry)
        except ValueError as e:
            self._error = str(e)
            self._mode = 'menu'
            return
        self._changes.append(f'Removed {entry.agent_type}')
        self._reload()
        self._sync(gone=(entry.agent_type,))
        self._mode = 'list'

    def _save_draft(self):
        defn = self._draft_definition()
        try:
            agent_defs.write_agent(defn, self._draft['scope'], self._cwd,
                                   self._all_tools)
        except (FileExistsError, OSError) as e:
            self._error = str(e)
            return
        self._changes.append(f'Added {defn.agent_type}')
        self._error = ''
        self._reload()
        self._sync()
        self._mode = 'list'

    def _sync(self, gone: tuple = ()):
        live = set()
        for entry in self._entries:
            if entry.shadowed_by is None:
                self._team.register_agent_definition(entry.defn)
                live.add(entry.agent_type)
        for agent_type in gone:
            if agent_type not in live:
                self._team.remove_agent_definition(agent_type)

    def _exit(self):
        message = ('What changed:\n' + '\n'.join(self._changes)
                   if self._changes else 'Closed the agents list')
        self.app.pop_screen()
        self.app.call_later(self._show, message)

    async def _show(self, message: str):
        await self.app._append_block(escape(message))


@dataclasses.dataclass
class _PermOption:
    value: str
    label: str
    feedback: str = ''


@dataclasses.dataclass
class _Approval:
    tool_use_id: str
    tool_name: str
    prompt: '_PermissionPrompt'
    block: object
    future: asyncio.Future


class _PermissionPrompt(Vertical):

    can_focus = True

    BINDINGS = [
        ("up", "opt_prev", "Previous option"),
        ("down", "opt_next", "Next option"),
        ("k", "opt_prev", "Previous option"),
        ("j", "opt_next", "Next option"),
        ("ctrl+p", "opt_prev", "Previous option"),
        ("ctrl+n", "opt_next", "Next option"),
        ("pageup", "opt_prev_page", "Page up"),
        ("pagedown", "opt_next_page", "Page down"),
        ("enter", "accept_option", "Confirm"),
        ("escape", "cancel", "Cancel"),
        ("tab", "toggle_feedback", "Amend"),
    ] + [Binding(str(index), f"pick_option({index})", "Pick option")
         for index in range(1, 10)]

    def __init__(self, tool_name: str, tool_input, cwd: str = ".",
                 rememberable: bool = True, rule: str = "", agent: str = "",
                 **kw):
        super().__init__(classes="permission", **kw)
        self._tool = tool_name
        self._input = tool_input
        self._cwd = cwd
        self._rememberable = rememberable
        self._rule = rule
        self._agent = agent
        self._focused = 0
        self._open_accept = False
        self._open_reject = False
        self.on_choice = None

    def compose(self) -> ComposeResult:
        yield Static("", id="perm-body", markup=True)
        yield Input("", placeholder=ACCEPT_FEEDBACK_HINT, id="perm-accept",
                    select_on_focus=False)
        yield Input("", placeholder=REJECT_FEEDBACK_HINT, id="perm-reject",
                    select_on_focus=False)
        yield Input(self._rule, placeholder=RULE_FEEDBACK_HINT, id="perm-rule",
                    select_on_focus=False)

    def on_mount(self):
        self.app.register_overlay("select")
        self._draw()
        self._sync_row()

    def on_unmount(self):
        self.app.unregister_overlay("select")

    def _options(self) -> list[_PermOption]:
        options = [_PermOption("approved", "Yes", "accept")]
        if self._rememberable:
            if self._tool == BASH_TOOL and self._rule:
                options.append(_PermOption(
                    "dont_ask",
                    f"Yes, and stop asking about: {self._rule}", "rule"))
            else:
                options.append(_PermOption(
                    "dont_ask",
                    f"Yes, always allow {self._tool} in {self._cwd}", ""))
        options.append(_PermOption("denied", "No", "reject"))
        return options

    def _row_field(self, option: _PermOption) -> Input | None:
        if option.feedback == "rule":
            return self.query_one("#perm-rule", Input)
        if option.feedback == "accept" and self._open_accept:
            return self.query_one("#perm-accept", Input)
        if option.feedback == "reject" and self._open_reject:
            return self.query_one("#perm-reject", Input)
        return None

    def _fields(self) -> list[Input]:
        return [self.query_one(f"#perm-{name}", Input)
                for name in ("accept", "reject", "rule")]

    def _sync_row(self):
        active = self._row_field(self._options()[self._focused])
        for field in self._fields():
            field.display = field is active
        if active is not None:
            active.cursor_position = len(active.value)
            active.focus()
        else:
            self.focus()
        self._draw()

    def _draw(self):
        options = self._options()
        focused = options[self._focused]
        title = "[bold]Tool use[/bold]"
        if self._agent:
            title += f" [dim]\u00b7 @{escape(self._agent)}[/]"
        args = _tool_use_args(self._tool, self._input, self._cwd)
        lines = [f"[#B1B9F9]{BULLET}[/] {title}",
                 f"  {escape(_display_name(self._tool))}({escape(args)})"]
        intent = ''
        if isinstance(self._input, dict):
            intent = str(self._input.get('description') or '').strip()
        if intent:
            lines.append(f"  [dim]{escape(intent)}[/]")
        lines.append("  Allow this call?")
        for index, option in enumerate(options):
            marker = POINTER if index == self._focused else ' '
            row = escape(f"  {marker} {index + 1}. {option.label}")
            lines.append(f"[#B1B9F9]{row}[/]" if index == self._focused
                         else f"[dim]{row}[/]")
        hint = ((focused.feedback == "accept" and not self._open_accept)
                or (focused.feedback == "reject" and not self._open_reject))
        lines.append("[dim]  esc cancels"
                     + (" \u00b7 tab adds a note" if hint else "") + "[/]")
        self.query_one("#perm-body", Static).update("\n".join(lines))

    def _move(self, delta: int, wrap: bool):
        total = len(self._options())
        self._focused = ((self._focused + delta) % total if wrap
                         else min(max(0, self._focused + delta), total - 1))
        option = self._options()[self._focused].feedback
        if option != "accept" and self._open_accept:
            self._open_accept = bool(
                self.query_one("#perm-accept", Input).value.strip())
        if option != "reject" and self._open_reject:
            self._open_reject = bool(
                self.query_one("#perm-reject", Input).value.strip())
        self._sync_row()

    def action_opt_next(self):
        self._move(1, wrap=True)

    def action_opt_prev(self):
        self._move(-1, wrap=True)

    def action_opt_next_page(self):
        self._move(OPTION_PAGE_SIZE, wrap=False)

    def action_opt_prev_page(self):
        self._move(-OPTION_PAGE_SIZE, wrap=False)

    def action_toggle_feedback(self):
        option = self._options()[self._focused]
        if option.feedback == "accept":
            self._open_accept = not self._open_accept
        elif option.feedback == "reject":
            self._open_reject = not self._open_reject
        else:
            return
        self._sync_row()

    async def action_accept_option(self):
        await self._submit(self._options()[self._focused])

    async def action_pick_option(self, index: int):
        options = self._options()
        if index > len(options):
            return
        await self._submit(options[index - 1])

    async def action_cancel(self):
        await self._finish(PermissionChoice("denied"))

    def _choice(self, option: _PermOption, text: str) -> PermissionChoice:
        text = text.strip()
        if option.feedback == "rule":
            return (PermissionChoice("approved") if not text
                    else PermissionChoice("dont_ask", rule=text))
        if self._row_field(option) is not None:
            return PermissionChoice(option.value, feedback=text)
        return PermissionChoice(option.value)

    async def _submit(self, option: _PermOption):
        field = self._row_field(option)
        await self._finish(self._choice(option, field.value if field else ""))

    async def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        await self._submit(self._options()[self._focused])

    async def _finish(self, choice: PermissionChoice):
        if self.on_choice is not None:
            self.on_choice(choice)


class PyClawApp(App[None]):
    TITLE = "PyClaw"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    $background: #101010;
    $text: #FFFFFF;
    $inactive: #999999;
    $subtle: #505050;
    $success: #4EBA65;
    $error: #FF6B80;
    $warning: #FFC107;
    $suggestion: #B1B9F9;
    $permission: #B1B9F9;
    $plan-mode: #48968C;
    $auto-accept: #AF87FF;
    $bash-border: #FD5DB1;
    $ide: #4782C8;
    $diff-added: #225C2B;
    $diff-removed: #7A2936;
    $selection: #264F78;

    Screen { layout: vertical; background: $background; }
    #conv { width: 1fr; height: 1fr; background: $background; overflow-y: auto;
            scrollbar-gutter: stable; padding: 0 1; }
    #conv > Static { width: 100%; margin-bottom: 1; }
    .user { background: $user-message; }
    .diff { border-top: dashed $subtle; border-bottom: dashed $subtle;
            border-left: none; border-right: none; padding: 0 1; }
    .permission { width: 100%; margin-bottom: 1; }
    .permission Input { display: none; width: 100%; height: 1; margin-top: 1;
                        border: round $permission; background: $background;
                        color: $text; padding: 0 1; }
    .logo { width: auto; margin-bottom: 1; }
    .text-block { width: 100%; height: auto; margin-bottom: 1; }
    .text-row { width: 100%; height: auto; }
    .text-bullet { width: 2; height: 1; color: $brand; }
    .text-body { width: 1fr; height: auto; color: $text; background: $background; }
    #transcript { width: 1fr; height: 1fr; background: $background; padding: 0 1; }
    #view { display: none; width: 1fr; height: 1fr; background: $background;
            overflow-y: auto; scrollbar-gutter: stable; padding: 0 1; }
    #view > Static { width: 100%; height: auto; }
    .agents { display: none; width: 100%; height: auto; background: $background;
              margin: 0 1; padding: 0 1; }
    #help { width: 1fr; height: 1fr; background: $background; padding: 0 1; }
    #help > Static { width: 100%; margin-bottom: 1; }
    #transcript > Static { width: 100%; margin-bottom: 1; }
    #history { width: 1fr; height: 1fr; background: $background;
               padding: 0 1; }
    #hs-input { height: 1; margin-bottom: 1; border: none;
                background: $background; color: $text; }
    #hs-list { width: 100%; height: auto; margin-bottom: 1; }
    #suggest { display: none; height: auto; max-height: 8; background: $background;
               margin: 0 1; padding: 0 1; }
    #tasks { display: none; height: auto; max-height: 12; background: $background;
             border-top: round $permission; margin: 0 1; padding: 0 1; }
    #prompt { height: 3; border-top: round $prompt-border;
              border-bottom: round $prompt-border; }
    #prompt-pointer { width: 2; height: 1; color: $brand; }
    #input { height: 1; width: 1fr; border: none; padding: 0;
             background: $background; color: $text; }
    #footer { height: 1; }
    #statusline { height: auto; width: 1fr; color: $inactive; padding: 0 1;
                  display: none; }
    #hud { height: 1; width: 1fr; color: $subtle; padding: 0 1; }
    #hud2 { height: 1; width: 1fr; color: $subtle; padding: 0 1; }
    #status { height: 1; width: auto; background: $background;
              color: $inactive; padding: 0 1; }
    #status-right { height: 1; width: 1fr; text-align: right;
                    background: $background; color: $subtle; padding: 0 1; }
    """
    BINDINGS = [Binding("ctrl+d", "quit", "Exit", priority=True),
                Binding("ctrl+c", "interrupt", "Stop current work",
                        priority=True),
                Binding("escape", "escape", "Cancel / dismiss"),
                ("ctrl+q", "quit", "Exit (fallback)"),
                ("ctrl+t", "toggle_tasks", "Show/hide tasks"),
                ("ctrl+l", "redraw", "Redraw"),
                ("ctrl+o", "toggle_transcript", "Transcript"),
                ("ctrl+r", "history_search", "Search history"),
                ("ctrl+s", "stash", "Stash prompt"),
                ("pageup", "conv_page_up", "Scroll up"),
                ("pagedown", "conv_page_down", "Scroll down"),
                Binding("ctrl+home", "conv_scroll_top", "Scroll to top"),
                Binding("ctrl+end", "jump_to_bottom", "Scroll to latest"),
                Binding("shift+up", "agent_prev", "Previous agent",
                        priority=True),
                Binding("shift+down", "agent_next", "Next agent",
                        priority=True),
                Binding("k", "stop_agent", "Stop selected agent",
                        priority=True),
                Binding("shift+tab", "cycle_permission", "Cycle permission mode",
                        priority=True),
                Binding("down", "prompt_next", "Next", priority=True),
                Binding("up", "prompt_prev", "Previous", priority=True),
                Binding("tab", "suggest_tab", "Complete suggestion",
                        priority=True)]

    def check_action(self, action: str, parameters) -> bool:
        if action in OVERLAY_GATED_ACTIONS and self.modal_overlay_active:
            return False
        if action in ('suggest_tab', 'suggest_dismiss'):
            return bool(self._suggest_items)
        if action in ('prompt_next', 'prompt_prev'):
            return len(self.screen_stack) <= 1
        if action == 'stop_agent':
            return (self._view_selection == 'selecting-agent'
                    and 0 <= self._selected_index < len(self._teammates()))
        if action in ('agent_next', 'agent_prev'):
            return bool(self._teammates())
        if action == 'quit':
            return not isinstance(self.focused, _PagerScroll)
        return True

    def register_overlay(self, name: str):
        self._overlays.add(name)

    def unregister_overlay(self, name: str):
        self._overlays.discard(name)

    @property
    def modal_overlay_active(self) -> bool:
        return bool(self._overlays - NON_MODAL_OVERLAYS)

    def __init__(self, *, builder, session_id=None, resume=False,
                 resume_from=None):
        super().__init__()
        self._triple = banner.palette(config.load()["banner"])
        self.brand = banner.brand(self._triple)
        self.register_theme(dataclasses.replace(
            BUILTIN_THEMES["textual-dark"], name="pyclaw",
            variables={"brand": self.brand,
                       "user-message": banner.dimmed(self._triple,
                                                     banner.BAND_LIGHTNESS),
                       "prompt-border": banner.dimmed(self._triple,
                                                      banner.RULE_LIGHTNESS)}))
        self.theme = "pyclaw"
        self._builder = builder
        self._session_id = session_id
        self._resume = resume
        self._resume_from = resume_from
        self._team = None
        self._session: Session | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._pending_inputs: asyncio.Queue = asyncio.Queue()
        self._processing: str | None = None
        self._driving = False
        self._follow = True
        self._unreg = None
        self._queued: Static | None = None
        self._hint: _JumpToBottom | None = None
        self._new_messages = 0
        self._wrote_body = False
        self._live: _TextBlock | None = None
        self._turn_start = 0
        self._live_text = ""
        self._response_chars = 0
        self._work_block: Static | None = None
        self._turn_started_at = 0.0
        self._hud_started = time.monotonic()
        self._git = ''
        self._statusline_cmd = ""
        self._statusline_text = ""
        self._statusline_seen: tuple | None = None
        self._statusline_timer = None
        self._statusline_task = None
        self._turn_verb = SPINNER_VERBS[0]
        self._turn_past = self._completion_verb()
        self._tools: dict[str, _ToolBlock] = {}
        self._spin_timer = None
        self._spin_i = 0
        self._think: dict | None = None
        self._overlays: set[str] = set()
        self._approvals: list[_Approval] = []
        self._interrupted_call = False
        self._subagents: dict[str, dict] = {}
        self._agent_state: dict[str, dict] = {}
        self._expanded_view = 'none'
        self._view_selection = 'none'
        self._selected_index = -1
        self._viewing: str | None = None
        self._agents_pane: _AgentPane | None = None
        self._view_pane: Static | None = None
        self._spawns: dict[str, str] = {}
        self._agent_colors: dict[str, str] = {}
        self._suggest_items: list[dict] = []
        self._suggest_selected = 0
        self._suggest_dismissed: str | None = None
        self._typeahead: str | None = None
        self._tool_meta: dict[str, dict] = {}
        self._stashed: str | None = None
        self._group: _GroupBlock | None = None
        self._last_interrupt = 0.0
        self._history: list[str] = []
        self._history_index: int | None = None
        self._draft = ''

    def compose(self) -> ComposeResult:
        yield _Conv(id="conv")
        yield VerticalScroll(id="view")
        yield Static('', id='suggest')
        yield VerticalScroll(id="tasks")
        with Horizontal(id="prompt"):
            yield Static(POINTER, id="prompt-pointer")
            yield _PromptInput(placeholder="Message PyClaw\u2026", id="input")
        yield Static('', id='statusline')
        yield Static('', id='hud')
        yield Static('', id='hud2')
        with Horizontal(id="footer"):
            yield Static(id="status")
            yield Static(id="status-right")

    async def on_mount(self):
        self._team = self._builder()
        self._session = Session(self._team, session_id=self._session_id,
                                resume_from=self._resume_from)
        self._session.attach_approval(self._ask_permission)
        self._unreg = register_runtime_handler(self._on_event)
        self._tasks_pane = Static("", markup=True)
        await self.query_one("#tasks", VerticalScroll).mount(self._tasks_pane)
        asyncio.create_task(self._pump())
        asyncio.create_task(self._drive())
        self._spin_timer = self.set_interval(SPINNER_INTERVAL, self._tool_spin_tick)
        self.set_interval(HUD_TICK_SECONDS, self._render_readouts)
        greeting, shown_onboarding = self._greeting()
        logo = _LogoBlock(self._triple, greeting=greeting)
        await self._conv().mount(logo)
        welcome.remember(seen_onboarding=shown_onboarding,
                         version=str(__version__))
        if self._resume:
            self._session.restore_transcript()
            await self._render_history()
        self.query_one(Input).focus()
        self._statusline_cmd = statusline.user_command()
        await self._refresh_git()
        self._render_status()
        self._render_tasks()
        await self._refresh_agents()

    async def on_unmount(self):
        if self._spin_timer is not None:
            self._spin_timer.stop()
        if self._statusline_timer is not None:
            self._statusline_timer.stop()
        task = self._statusline_task
        if task is not None and not task.done():
            task.cancel()
        if self._unreg is not None:
            self._unreg()
            self._unreg = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _conv(self) -> _Conv:
        return self.query_one("#conv", _Conv)

    def _cwd(self) -> str:
        if self._session is None:
            return "."
        value = str(getattr(self._session, "cwd", "") or "")
        if value and value != ".":
            return value
        context = getattr(self._team, "tool_context", None)
        return str(getattr(context, "cwd", "") or value or ".")

    def _greeting(self) -> tuple[str, bool]:
        session = self._session
        version = str(__version__)
        cwd = self._cwd()
        feeds, shown = welcome.feeds_for(
            cwd=cwd, version=version,
            session_id=getattr(session, 'conv_session_id', None))
        text = welcome.block(
            version=version,
            model=str(getattr(session, 'model', '') or ''),
            provider=str(getattr(session, 'provider', '') or ''),
            cwd=cwd, feeds=feeds, brand=self.brand)
        return text, shown

    def _teammates(self) -> list:
        team = self._team
        if team is None:
            return []
        lead = getattr(team, 'lead', None)
        found = []
        for agent in getattr(team, 'agents', {}).values():
            if agent is lead or getattr(agent, '_internal', False):
                continue
            if _agent_alive(agent):
                found.append(agent)
        found.sort(key=lambda a: str(getattr(a, 'name', '')))
        return found

    def _agent_by_name(self, name):
        for agent in getattr(self._team, 'agents', {}).values():
            if getattr(agent, 'name', '') == name:
                return agent
        return None

    def _completion_verb(self) -> str:
        return random.choice(PAST_TENSE_VERBS)

    def _state(self, name: str) -> dict:
        state = self._agent_state.get(name)
        if state is None:
            state = {'tools': 0, 'think': False, 'busy': False,
                     'last_tool': '', 'error': '',
                     'verb': random.choice(SPINNER_VERBS),
                     'past': self._completion_verb(),
                     'started_at': time.monotonic(), 'idle_since': None}
            self._agent_state[name] = state
        return state

    def _agent_color(self, name: str) -> str:
        color = self._agent_colors.get(name)
        if color is None:
            index = len(self._agent_colors) % len(AGENT_COLORS)
            color = AGENT_COLORS[index]
            self._agent_colors[name] = color
        return color

    def _agent_running(self, agent) -> bool:
        if agent is None:
            return False
        return bool(getattr(agent, 'busy', False))

    def _status_text(self, agent, all_idle: bool, highlighted: bool) -> str:
        state = self._state(str(getattr(agent, 'name', '')))
        if state.get('error'):
            return _summarize(state['error'], 60)
        now = time.monotonic()
        if self._agent_running(agent):
            if highlighted:
                return ''
            if state['last_tool']:
                return f"{state['last_tool']}\u2026"
            return f"{state['verb']}\u2026"
        if all_idle:
            return (f"{state['past']} for "
                    f"{self._duration(int(now - state['started_at']))}")
        if state.get('idle_since') is None:
            state['idle_since'] = now
        return (f"{IDLE_TEXT} for "
                f"{self._duration(int(now - state['idle_since']))}")

    def _leader_row(self, selected) -> str:
        foreground = self._viewing is None
        highlighted = foreground or selected == -1
        glyph = TREE_LEAD[1] if selected == -1 else TREE_LEAD[0]
        pointer = TREE_POINTER if selected == -1 else ' '
        label = f"[#48968C]team-lead[/]"
        if not foreground:
            if self._processing is not None:
                label += f": {escape(self._turn_verb)}\u2026"
            else:
                label += f": {IDLE_TEXT}"
        tokens = 0
        if self._session is not None:
            tokens = int(getattr(self._session.usage, 'total_tokens', 0) or 0)
        stats = f" \u00b7 {_format_count(tokens)} tokens" if tokens else ''
        hint = f" \u00b7 {SELECT_HINT}" if highlighted else ''
        view = f" \u00b7 {VIEW_HINT}" if selected == -1 and not foreground else ''
        return (f"{TREE_INDENT}{pointer} {glyph} {label}{stats}{hint}{view}")

    def _teammate_row(self, agent, index: int, last: bool,
                      selected, all_idle: bool) -> str:
        name = str(getattr(agent, 'name', ''))
        state = self._state(name)
        chosen = selected == index
        glyph = (TREE_LAST if last else TREE_BRANCH)[1 if chosen else 0]
        pointer = TREE_POINTER if chosen else ' '
        color = '#B1B9F9' if chosen else self._agent_color(name)
        status = self._status_text(agent, all_idle, chosen)
        label = f"[{color}][bold]@{escape(name)}[/][/]"
        body = f"{label}: {escape(status)}" if status else label
        stats = (f" \u00b7 {_tool_uses(state['tools'])}"
                 f" \u00b7 {_format_count(_agent_tokens(agent))} tokens")
        hint = f" \u00b7 {SELECT_HINT}" if chosen else ''
        view = f" \u00b7 {VIEW_HINT}" if chosen else ''
        return f"{TREE_INDENT}{pointer} {glyph} {body}{stats}{hint}{view}"

    def _hide_row(self, chosen: bool) -> str:
        glyph = TREE_LAST[1] if chosen else TREE_LAST[0]
        pointer = TREE_POINTER if chosen else ' '
        hint = f" \u00b7 {COLLAPSE_HINT}" if chosen else ''
        return f"{TREE_INDENT}{pointer} {glyph} hide{hint}"

    def _tree_markup(self, rows: bool, idle_line: bool) -> str:
        teammates = self._teammates()
        if not teammates:
            return ''
        selecting = self._view_selection == 'selecting-agent'
        selected = self._selected_index if selecting else None
        all_idle = all(not self._agent_running(a) for a in teammates)
        lines = []
        if idle_line:
            suffix = '' if all_idle else f" \u00b7 {AGENT_TEAMMATES_HINT}"
            lines.append(f"[dim]{ASTERISK} {IDLE_TEXT}{suffix}[/]")
        if rows:
            lines.append(self._leader_row(selected))
            for index, agent in enumerate(teammates):
                lines.append(self._teammate_row(
                    agent, index, index == len(teammates) - 1,
                    selected, all_idle))
            if selecting:
                lines.append(self._hide_row(selected == len(teammates)))
        return '\n'.join(lines)

    async def _refresh_agents(self):
        known = {str(getattr(a, 'name', ''))
                 for a in getattr(self._team, 'agents', {}).values()}
        for name in list(self._agent_state):
            if name not in known:
                self._agent_state.pop(name, None)
        for name in list(self._spawns):
            if name not in known:
                self._spawns.pop(name, None)
        teammates = self._teammates()
        rows = bool(teammates) and self._expanded_view == 'teammates'
        idle_line = bool(teammates) and self._processing is None
        text = self._tree_markup(rows, idle_line)
        if not text:
            if self._agents_pane is not None:
                self._agents_pane.remove()
                self._agents_pane = None
            return
        if self._agents_pane is None or self._agents_pane.parent is None:
            self._agents_pane = _AgentPane()
            await self.screen.mount(self._agents_pane,
                                    before=self.query_one("#prompt"))
        self._agents_pane.display = True
        self._agents_pane.update(text)

    def _step_selection(self, delta: int):
        teammates = self._teammates()
        if not teammates:
            return
        if self._expanded_view != 'teammates':
            self._selected_index = -1
        else:
            last = len(teammates)
            current = self._selected_index
            if delta == 1:
                self._selected_index = -1 if current >= last else current + 1
            else:
                self._selected_index = last if current <= -1 else current - 1
        self._view_selection = 'selecting-agent'
        self._expanded_view = 'teammates'

    async def action_agent_next(self):
        self._step_selection(1)

    async def action_agent_prev(self):
        self._step_selection(-1)

    async def _confirm_selection(self):
        teammates = self._teammates()
        index = self._selected_index
        if index == -1:
            await self._exit_agent_view()
        elif index >= len(teammates):
            self._expanded_view = 'none'
            self._view_selection = 'none'
            self._selected_index = -1
        else:
            await self._enter_agent_view(teammates[index])
        self._render_status()
        await self._refresh_agents()

    async def _enter_agent_view(self, agent):
        self._viewing = str(getattr(agent, 'name', ''))
        self._view_selection = 'viewing-agent'
        self.query_one("#conv").display = False
        view = self.query_one("#view", VerticalScroll)
        view.display = True
        await self._render_agent_view()
        view.scroll_end(animate=False)
        await self._render_queued()
        self._render_status()

    async def _exit_agent_view(self):
        if self._viewing is None:
            return
        self._viewing = None
        self._view_selection = 'none'
        self._selected_index = -1
        self.query_one("#view", VerticalScroll).display = False
        self.query_one("#conv").display = True
        self._follow_scroll()
        await self._render_queued()
        self._render_status()

    def _agent_prompt(self, agent) -> str:
        for message in getattr(agent, 'messages', []) or []:
            if not isinstance(message, dict):
                continue
            if message.get('role') == 'user' \
                    and isinstance(message.get('content'), str):
                return message['content']
        return str(getattr(agent, 'instruction', '') or '')

    def _tool_line(self, block: dict) -> str:
        name = str(block.get('name', 'tool'))
        tool_input = block.get('input', '')
        args = _tool_use_args(name, tool_input, self._cwd())
        label = escape(_tool_label(name, tool_input))
        return f"{BULLET} [bold]{label}[/]({escape(args)})"

    def _agent_entries(self, agent) -> list:
        entries = []
        for message in getattr(agent, 'messages', []) or []:
            if not isinstance(message, dict):
                continue
            role = message.get('role')
            content = message.get('content')
            if role == 'user':
                if isinstance(content, str):
                    blocks = _teammate_blocks(content)
                    if blocks is not None:
                        for sender, body in blocks:
                            color = self._agent_color(str(sender))
                            entries.append(f"[{color}]"
                                           f"@{escape(str(sender))}[/]"
                                           f"{POINTER} {escape(body)}")
                        continue
                    if content.strip():
                        entries.append(_user_markup(content))
                    continue
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) \
                                and block.get('type') == 'tool_result':
                            body = str(block.get('content', '')).strip()
                            head = body.splitlines()[0] if body else ''
                            entries.append(
                                f"[dim]{RESULT_PREFIX}"
                                f"{escape(_summarize(head, 100))}[/]")
                continue
            if role != 'assistant':
                continue
            if isinstance(content, str):
                text = content.strip('\n')
                if text:
                    entries.append(f"{BULLET_PREFIX}{escape(text)}")
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) \
                            and block.get('type') == 'tool_use':
                        entries.append(self._tool_line(block))
        return entries

    def _agent_view_markup(self, agent) -> str:
        if agent is None:
            return "[dim]this agent is gone[/]"
        name = str(getattr(agent, 'name', ''))
        color = self._agent_color(name)
        lines = [f"[bold]Viewing [/][{color}][bold]@{escape(name)}[/][/]"
                 f"[dim] \u00b7 {TEAMMATE_VIEW_HINT}[/]"]
        prompt = self._agent_prompt(agent)
        if prompt:
            lines.append(f"[dim]{escape(_summarize(prompt, 200))}[/]")
        lines.append('')
        lines += self._agent_entries(agent)
        return '\n'.join(lines)

    async def _render_agent_view(self):
        if self._viewing is None:
            return
        markup = self._agent_view_markup(self._agent_by_name(self._viewing))
        view = self.query_one("#view", VerticalScroll)
        if self._view_pane is None or self._view_pane.parent is None:
            self._view_pane = Static(markup, markup=True)
            await view.mount(self._view_pane)
        else:
            self._view_pane.update(markup)

    async def action_stop_agent(self):
        teammates = self._teammates()
        index = self._selected_index
        if index < 0 or index >= len(teammates):
            return
        agent = teammates[index]
        logger.info("stopping teammate %s by user request",
                    getattr(agent, 'name', ''))
        if self._viewing == str(getattr(agent, 'name', '')):
            await self._exit_agent_view()
        stop = getattr(self._team, 'stop_agent', None)
        if stop is not None:
            await stop(agent)
        self._selected_index = -1
        await self._refresh_agents()

    async def _send_direct(self, agent, message: str):
        lead = getattr(self._team, 'lead', None)
        sender = str(getattr(lead, 'name', 'team-lead'))
        agent.inbox.write(sender, message)
        logger.info("direct message %s -> @%s", sender,
                    getattr(agent, 'name', ''))
        await self._append_block(
            f"[#B1B9F9]Sent to @{escape(str(agent.name))}[/]")

    async def _append_widget(self, widget):
        await self._conv().mount(widget)
        await self._after_mount()
        return widget

    async def _append_user(self, text: str):
        return await self._append_widget(_UserBlock(text))

    async def _append_error(self, text: str):
        return await self._append_block(
            f"[#FF6B80]{BULLET}[/] [#FF6B80]{escape(str(text))}[/]")

    def _follow_scroll(self):
        if self._follow:
            self._conv().scroll_end(animate=False)

    def _set_follow(self, follow: bool):
        if follow == self._follow:
            return
        self._follow = follow
        if follow:
            self._new_messages = 0
        self.call_later(self._refresh_follow_hint)

    async def _refresh_follow_hint(self):
        inp = self.query_one("#input", Input)
        if self._follow or not self._new_messages or self._viewing is not None:
            if self._hint is not None:
                self._hint.remove()
                self._hint = None
            return
        text = f"[#B1B9F9]\u2193 Scroll to latest \u00b7 {self._new_messages} new[/]"
        if self._hint is None:
            self._hint = _JumpToBottom(text)
            await self.screen.mount(self._hint,
                                    before=self.query_one("#prompt"))
        else:
            self._hint.update(text)

    async def _after_mount(self):
        if self._follow:
            self._follow_scroll()
        else:
            self._new_messages += 1
        await self._refresh_follow_hint()

    async def action_jump_to_bottom(self):
        self._follow = True
        self._new_messages = 0
        self._conv().scroll_end(animate=False)
        await self._refresh_follow_hint()

    async def action_conv_page_up(self):
        conv = self._conv()
        conv.scroll_relative(y=-_half_page(conv), animate=False)

    async def action_conv_page_down(self):
        conv = self._conv()
        conv.scroll_relative(y=_half_page(conv), animate=False)

    async def action_conv_scroll_top(self):
        self._conv().scroll_home(animate=False)

    async def _append_block(self, text: str):
        block = Static(text, markup=True)
        conv = self._conv()
        await conv.mount(block)
        await self._after_mount()
        return block

    async def _frozen(self):
        self._live = None
        self._live_text = ""

    async def _start_live(self):
        self._live = _TextBlock()
        await self._conv().mount(self._live)
        self._live_text = ""
        await self._after_mount()

    def _update_live(self):
        if self._live is not None:
            self._live.set_body(self._live_text)
            self._follow_scroll()

    async def _pump(self):
        while True:
            ev = await self._queue.get()
            try:
                await self._handle(ev)
                self._render_status()
                self._render_tasks()
                if self._viewing is not None:
                    await self._render_agent_view()
                await self._refresh_agents()
            except Exception as exc:
                logger.exception(
                    "render failed for %s agent=%r data=%s",
                    ev.kind, ev.agent, _log_data(ev.data))
                try:
                    await self._append_error(
                        f"render error: {exc}")
                except Exception:
                    logger.exception("failed to report a render error")
            finally:
                self._queue.task_done()

    def _on_event(self, ev):
        if ev.agent and ev.agent not in self._member_names():
            if ev.kind != AGENT_PROGRESS:
                return
        self._queue.put_nowait(ev)

    def _member_names(self) -> set:
        return {a.name for a in self._team.agents.values()}

    async def _handle(self, ev):
        logger.debug("event %s agent=%r data=%s", ev.kind, ev.agent,
                     _log_data(ev.data))
        name = ev.agent or ""
        lead = str(getattr(self._team.lead, 'name', ''))
        mine = not name or name == lead
        if ev.kind == AGENT_REASON_START:
            self._note(name, think=True)
            if mine:
                await self._add_think(ev)
        elif ev.kind == AGENT_TEXT:
            self._note(name, think=False, busy=True)
            delta = ev.data.get("delta", "")
            if not delta:
                return
            if not mine:
                return
            self._response_chars += len(delta)
            if self._live is None:
                if not delta.strip():
                    return
                await self._start_live()
                self._discard_think()
            self._live_text += delta
            self._update_live()
            self._wrote_body = True
            self._reset_tool_group()
        elif ev.kind == AGENT_TOOL_CALL:
            self._note(name, tools=1, think=False)
            if not mine:
                self._note_tool(name, ev.data)
                return
            await self._frozen()
            await self._add_tool(ev)
        elif ev.kind == AGENT_PROGRESS:
            self._note_progress(ev)
        elif ev.kind == AGENT_WARN:
            if not mine:
                self._state(name)['error'] = str(ev.data.get('text', ''))
                return
            await self._frozen()
            await self._append_error(ev.data.get('text', ''))
        elif ev.kind == AGENT_TOOL_RESULT:
            uid = ev.data.get('tool_use_id') or ev.data.get('tool', '')
            self._tool_meta[uid] = dict(ev.data)
        elif ev.kind == AGENT_TURN_FINISHED:
            self._note(name, think=False, busy=False)
            self._finish_agent(name)
            if not mine:
                return
            if not self._teammates_running():
                self._discard_think()
            self._reset_tool_group()
            await self._frozen()
            self._sync_tool_states()
            self._log_turn()
            asyncio.create_task(self._finish_work_when_settled())
        elif ev.kind == AGENT_STATE:
            self._note(name, busy=bool(ev.data.get("busy", False)))

    def _note_tool(self, name: str, data: dict):
        state = self._state(name)
        tool = str(data.get('tool', 'tool'))
        args = _tool_use_args(tool, data.get('input', ''), self._cwd())
        state['last_tool'] = f"{_tool_label(tool, data.get('input'))}: {args}"

    def _spawn_block(self, name: str):
        uid = self._spawns.get(name)
        if not uid:
            return None
        block = self._tools.get(uid)
        return block if isinstance(block, _ToolBlock) else None

    def _finish_agent(self, name: str):
        state = self._state(name)
        state['busy'] = False
        state['think'] = False
        state['idle_since'] = None
        uid = self._spawns.get(name)
        if not uid:
            return
        block = self._tools.get(uid)
        if block is None or block._done:
            return
        meta = self._tool_meta.setdefault(uid, {})
        if 'agent_summary' not in meta:
            started = state['started_at']
            elapsed = max(0, int(time.monotonic() - started))
            agent = self._agent_by_name(name)
            tokens = _agent_tokens(agent) if agent is not None else 0
            meta['agent_summary'] = (
                f"Done ({_tool_uses(state['tools'])} \u00b7 "
                f"{_format_count(tokens)} tokens \u00b7 "
                f"{self._duration(elapsed)})")
            logger.info("sub-agent %s finished: %s", name,
                        meta['agent_summary'])
        block.end_progress()

    _SPIN = "".join(SPINNER_FRAMES)

    def _spin_char(self) -> str:
        return self._SPIN[self._spin_i % len(self._SPIN)]

    async def _add_tool(self, ev):
        await self._mount_tool(ev.data.get("tool", "tool"),
                               ev.data.get("input", ""),
                               ev.data.get("tool_use_id", ""))

    def _reset_tool_group(self):
        if self._group is not None:
            self._group.finish()
            self._group = None

    async def _mount_tool(self, name, raw_input, tool_use_id):
        uid = tool_use_id or name
        block = _ToolBlock(name, raw_input, cwd=self._cwd())
        self._tools[uid] = block
        if _hidden_card(name, raw_input):
            self._reset_tool_group()
            block.display = False
            await self._conv().mount(block)
            return
        kinds = _collapsible_kinds(name, raw_input)
        if kinds:
            if self._group is None:
                self._group = _GroupBlock()
                self._group._frame = self._spin_char()
                await self._conv().mount(self._group)
            self._group.add(kinds, _read_key(name, raw_input), uid)
            self._discard_think()
            await self._after_mount()
            return
        self._reset_tool_group()
        await self._conv().mount(block)
        self._discard_think()
        await self._after_mount()

    async def _render_history(self):
        for message in self._session.transcript():
            role = message.get("role")
            if role == "user":
                text = _content_text(message.get("content"))
                if text:
                    await self._append_user(text)
                continue
            if role != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) \
                            and block.get("type") == "tool_use":
                        await self._mount_tool(block.get("name", "tool"),
                                               block.get("input", ""),
                                               block.get("id", ""))
            text = _content_text(content)
            if text:
                text_block = _TextBlock()
                await self._conv().mount(text_block)
                text_block.set_body(text)
                await self._after_mount()

        self._reset_tool_group()
        self._sync_tool_states()

    async def _add_think(self, ev):
        await self._mount_spinner()

    async def _mount_spinner(self):
        widget = Static(self._spinner_text(self._spin_char()), markup=True)
        await self._conv().mount(widget)
        self._discard_think()
        self._think = {"widget": widget, "agent": ""}
        await self._after_mount()

    def _discard_think(self):
        if self._think is None:
            return
        widget = self._think["widget"]
        if widget in self._conv().children:
            widget.remove()
        self._think = None

    def _thinking_map(self) -> dict:
        pairs = {}
        for message in self._team.transcript():
            if message.get('role') != 'assistant' or not message.get('thinking'):
                continue
            text = _content_text(message.get('content'))
            if text:
                pairs[text] = message['thinking']
        return pairs

    def _spinner_text(self, char: str) -> str:
        viewed = self._viewing
        if viewed is not None:
            if self._agent_running(self._agent_by_name(viewed)):
                verb = self._state(viewed)['verb']
                return (f"[{self.brand}]{char}[/] {escape(verb)}\u2026 "
                        f"[dim](esc stops the turn [/]"
                        f"[{self._agent_color(viewed)}]@{escape(viewed)}[/]"
                        f"[dim])[/]")
            return self._idle_row(self._state(viewed))
        elif self._processing is None and self._teammates_running():
            return self._idle_row()
        parts = []
        running = self._teammates_running()
        elapsed = self._elapsed_seconds()
        parts.append(self._duration(elapsed))
        tokens = self._turn_tokens(running=running)
        if tokens:
            arrow = '' if running else '\u2193 '
            parts.append(f"{arrow}{_format_count(tokens)} tokens")
            if elapsed:
                parts.append(_token_rate(tokens, elapsed))
        if self._leader_thinking():
            parts.append('thinking')
        head = f"[{self.brand}]{char}[/] {self._turn_verb}\u2026"
        if not parts:
            return head
        return (f"{head} [dim]([/]"
                + "[dim] \u00b7 [/]".join(parts) + "[dim])[/]")

    def _idle_row(self, state: dict | None = None) -> str:
        if state is None:
            return (f"[dim]{ASTERISK} {IDLE_TEXT} \u00b7 "
                    f"{AGENT_TEAMMATES_HINT}[/]")
        teammates = self._teammates()
        if teammates and all(not self._agent_running(a) for a in teammates):
            seconds = max(0, int(time.monotonic() - state['started_at']))
            return (f"[dim]{ASTERISK} {state['past']} for "
                    f"{self._duration(seconds)}[/]")
        return f"[dim]{ASTERISK} {IDLE_TEXT}[/]"

    def _elapsed_seconds(self) -> int:
        if not self._turn_started_at:
            return 0
        return max(0, int(time.monotonic() - self._turn_started_at))

    def _turn_tokens(self, *, running: bool) -> int:
        tokens = round(self._response_chars / 4)
        if running and self._expanded_view != 'teammates':
            for agent in self._teammates():
                if self._agent_running(agent):
                    tokens += _agent_tokens(agent)
        return tokens

    def _leader_thinking(self) -> bool:
        return bool(self._state(str(self._team.lead.name)).get('think'))

    @staticmethod
    def _duration(seconds: int) -> str:
        if seconds < 60:
            return f'{seconds}s'
        return f'{seconds // 60}m {seconds % 60}s'

    async def _finish_work(self):
        self._discard_think()
        elapsed = self._elapsed_seconds()
        text = (f"[#9A9A9A]{ASTERISK} {self._turn_past} for "
                f"{self._duration(elapsed)}[/]")
        if self._work_block is None or self._work_block.parent is None:
            self._work_block = await self._append_block(text)
        else:
            self._work_block.update(text)
        await self._refresh_git()
        self._render_readouts()

    def _log_turn(self):
        if self._session is None:
            return
        for m in reversed(self._team.transcript()[self._turn_start:]):
            if m.get('role') != 'assistant':
                continue
            text = _content_text(m.get('content'))
            if text:
                append_conv(self._session.conv_session_id, "assistant", text,
                            reasoning_content=m.get('thinking') or None)
            return

    def _note_progress(self, ev):
        name = ev.agent or ""
        data = ev.data
        if data.get('tool_use_id'):
            self._spawns[name] = str(data['tool_use_id'])
            state = self._state(name)
            state['busy'] = True
            state['started_at'] = time.monotonic()
            state['idle_since'] = None
            logger.info("sub-agent %s started (tool_use_id=%s, type=%s)",
                        name, data['tool_use_id'],
                        data.get('subagent_type') or '')
        st = self._subagents.get(name)
        if st is None:
            st = {'type': data.get('subagent_type') or 'Agent', 'tools': 0,
                  'tokens': None, 'last_tool': None, 'done': False,
                  'tool_names': {}}
            self._subagents[name] = st
        if data.get('done'):
            st['done'] = True
            self._finish_agent(name)
            return
        msg = data.get('message')
        if msg is None:
            return
        if msg.get('role') == 'assistant':
            usage = data.get('usage') or {}
            st['tokens'] = ((usage.get('prompt_tokens') or 0)
                            + (usage.get('completion_tokens') or 0))
            content = msg.get('content')
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        st['tool_names'][b.get('id', '')] = b.get('name', 'tool')
            block = self._spawn_block(name)
            if block is not None:
                rows, uses = _agent_progress_rows(msg, self._cwd(),
                                                  block._width())
                if rows or uses:
                    block.add_progress(rows, uses)
        elif msg.get('role') == 'user':
            content = msg.get('content')
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get('type') == 'tool_result':
                        st['tools'] += 1
                        uid = b.get('tool_use_id', '')
                        tname = st['tool_names'].get(uid, 'tool')
                        st['last_tool'] = (f"{tname}: "
                                           f"{_summarize(b.get('content', ''))}")

    def _tool_spin_tick(self):
        if not self._tools and not self._think and self._agents_pane is None:
            return
        conv = next(iter(self.query(_Conv)), None)
        if conv is None:
            if self._spin_timer is not None:
                self._spin_timer.stop()
            return
        self._spin_i += 1
        char = self._spin_char()
        if self._think is not None:
            self._think["widget"].update(self._spinner_text(char))
        self._sync_tool_states()
        if self._group is not None and self._group.active:
            self._group.tick(char)
        for block in self.query(_ToolBlock):
            block.tick(char)
        if self._agents_pane is not None:
            teammates = self._teammates()
            rows = bool(teammates) and self._expanded_view == 'teammates'
            idle_line = bool(teammates) and self._processing is None
            self._agents_pane.update(self._tree_markup(rows, idle_line))
        if self._follow:
            conv.scroll_end(animate=False)

    def _sync_tool_states(self):
        results = {}
        for msg in self._team.transcript():
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[block.get("tool_use_id")] = block.get("content", "")
        for uid, block in self._tools.items():
            if not block._done and uid in results:
                block.set_result(str(results[uid]),
                                 meta=self._tool_meta.get(uid))

    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        self._history_index = None
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        if self._view_selection == 'selecting-agent':
            await self._confirm_selection()
            return
        if self._viewing is not None and text and not text.startswith('/'):
            agent = self._agent_by_name(self._viewing)
            if agent is not None:
                self.query_one("#input", Input).value = ""
                agent.submit(text)
                await self._render_agent_view()
                self._render_status()
                return
        direct = None if self._viewing is not None else _direct_message(text)
        if direct is not None:
            target = self._agent_by_name(direct[0])
            if target is not None:
                self.query_one("#input", Input).value = ""
                await self._send_direct(target, direct[1])
                return
        if text == '/permissions':
            self.query_one("#input", Input).value = ""
            await self._append_user(text)
            self.push_screen(PermissionsScreen(self._session))
            return
        if text == '/agents':
            self.query_one("#input", Input).value = ""
            await self._append_user(text)
            self.push_screen(AgentsScreen(self._session))
            return
        if self._suggest_items:
            item = self._suggest_items[min(self._suggest_selected,
                                           len(self._suggest_items) - 1)]
            if self._typeahead == 'at':
                inp = self.query_one("#input", Input)
                inp.value = _apply_at(inp.value, item['name'],
                                      item.get('dir', False))
                inp.cursor_position = len(inp.value)
                return
            if item.get('hint'):
                inp = self.query_one("#input", Input)
                inp.value = f"/{item['name']} "
                inp.cursor_position = len(inp.value)
                self._suggest_items = []
                self._show_suggest_widget(False)
                return
            text = f"/{item['name']}"
        self.query_one("#input", Input).value = ""
        if not text:
            return
        if text.startswith("/"):
            from pyclaw.slash import handle_slash

            reply = await handle_slash(text, self._session)
            await self._append_user(text)
            follow = None
            if isinstance(reply, tuple):
                reply, follow = reply
            if reply:
                await self._append_block(escape(reply))
            self._render_status()
            await self._render_queued()
            if follow:
                self._pending_inputs.put_nowait(follow)
            return
        self._pending_inputs.put_nowait(text)
        self._render_status()
        await self._render_queued()

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        self._render_status()
        if value == '?':
            self.query_one("#input", Input).value = ""
            self.action_toggle_help()
            return
        items = []
        kind = None
        if value.startswith('/'):
            items = slash_suggest(value)
            kind = 'slash'
        else:
            token = _at_token(value[:event.input.cursor_position])
            if token is not None:
                items = _file_suggest(self._cwd(), token)
                kind = 'at'
        if not items or self._suggest_dismissed == value:
            self._suggest_items = []
            self._typeahead = None
            self._show_suggest_widget(False)
            return
        self._suggest_dismissed = None
        self._suggest_items = items
        self._typeahead = kind
        self._suggest_selected = 0
        self._show_suggest_widget(True)

    def _show_suggest_widget(self, show: bool):
        if show:
            self.register_overlay('autocomplete')
        else:
            self.unregister_overlay('autocomplete')
        try:
            self.query_one('#suggest', Static).display = show
        except Exception:
            pass
        if show:
            self._render_suggestions()

    def _render_suggestions(self):
        try:
            widget = self.query_one('#suggest', Static)
        except Exception:
            return
        items = self._suggest_items
        start = max(0, min(self._suggest_selected - 2, len(items) - 6))
        window = items[start:start + 6]
        labels = [_suggest_label(i) for i in window]
        width = max((len(l) for l in labels), default=0)
        lines = []
        for i, item in enumerate(window):
            index = start + i
            desc = item.get('desc', '')
            row = escape(labels[i].ljust(width) + (f"  {desc}" if desc else ''))
            lines.append(f"[#B1B9F9]{row}[/]" if index == self._suggest_selected
                         else f"[dim]{row}[/]")
        widget.update('\n'.join(lines))

    def action_prompt_prev(self):
        if self._suggest_items:
            self.action_suggest_prev()
            return
        self._history_step(-1)

    def action_prompt_next(self):
        if self._suggest_items:
            self.action_suggest_next()
            return
        self._history_step(1)

    def _history_step(self, delta: int):
        if not self._history:
            return
        inp = self.query_one("#input", Input)
        if self._history_index is None:
            if delta > 0:
                return
            self._draft = inp.value
            self._history_index = len(self._history)
        index = min(max(0, self._history_index + delta), len(self._history))
        self._history_index = index
        inp.value = (self._draft if index == len(self._history)
                     else self._history[index])
        inp.cursor_position = len(inp.value)

    def action_suggest_next(self):
        if self._suggest_items:
            self._suggest_selected = ((self._suggest_selected + 1)
                                      % len(self._suggest_items))
            self._render_suggestions()

    def action_suggest_prev(self):
        if self._suggest_items:
            self._suggest_selected = ((self._suggest_selected - 1)
                                      % len(self._suggest_items))
            self._render_suggestions()

    def action_suggest_tab(self):
        if not self._suggest_items:
            return
        item = self._suggest_items[self._suggest_selected]
        inp = self.query_one("#input", Input)
        if self._typeahead == 'at':
            inp.value = _apply_at(inp.value, item['name'],
                                  item.get('dir', False))
            inp.cursor_position = len(inp.value)
            return
        inp.value = f"/{item['name']} "
        inp.cursor_position = len(inp.value)

    def action_suggest_dismiss(self):
        self._suggest_dismissed = self.query_one("#input", Input).value
        self._suggest_items = []
        self._typeahead = None
        self._show_suggest_widget(False)

    async def _render_queued(self):
        inp = self.query_one("#input", Input)
        queued = [] if self._viewing is not None else self._peek_queue()
        inp.placeholder = ("up edits what you queued" if queued
                           else "Message PyClaw\u2026")
        if not queued:
            if self._queued is not None:
                self._queued.remove()
                self._queued = None
            return
        text = "\n\n".join(escape(str(t)) for t in queued)
        if self._queued is None:
            self._queued = Static(text, markup=True, classes="user")
            await self.screen.mount(self._queued,
                                    before=self.query_one("#prompt"))
        else:
            self._queued.update(text)

    def _peek_queue(self) -> list:
        return list(self._pending_inputs._queue)

    async def _drive(self):
        if self._driving:
            return
        self._driving = True
        try:
            while True:
                text = await self._pending_inputs.get()
                self._processing = text
                self._render_status()
                await self._render_queued()
                await self._refresh_agents()
                await self._append_user(text)
                self._begin_turn()
                await self._mount_spinner()
                await self._converse(text)
                await self._settle_paint()
                self._processing = None
                self._render_status()
                await self._render_queued()
                await self._refresh_agents()
        finally:
            self._driving = False

    async def _ask_permission(self, tool_name: str, tool_input,
                              *, tool_use_id: str = '',
                              agent: str | None = None) -> PermissionChoice:
        inp = tool_input if isinstance(tool_input, dict) else {}
        rule = self._session.permission_rule(tool_name, inp)
        prompt = _PermissionPrompt(tool_name, inp, cwd=self._cwd(),
                                   rememberable=bool(rule) or tool_name != 'Bash',
                                   rule=rule, agent=self._badge(agent))
        block = self._tools.get(tool_use_id) if tool_use_id else None
        approval = _Approval(tool_use_id, tool_name, prompt, block,
                             asyncio.get_running_loop().create_future())
        prompt.on_choice = lambda choice: self._answer_approval(approval, choice)
        self._approvals.append(approval)
        if self._approvals[0] is approval:
            await self._mount_approval(approval)
        try:
            return await approval.future
        except asyncio.CancelledError:
            self._withdraw_approval(approval)
            raise

    def _withdraw_approval(self, approval: _Approval):
        if approval in self._approvals:
            self._approvals.remove(approval)
        approval.prompt.remove()
        block = approval.block
        if isinstance(block, _ToolBlock):
            block.set_waiting_permission(False)
        if not approval.future.done():
            approval.future.cancel()

    def _badge(self, agent: str | None) -> str:
        if not agent:
            return ''
        return '' if agent == self._team.lead.name else agent

    async def _mount_approval(self, approval: _Approval):
        block = approval.block
        if isinstance(block, _ToolBlock):
            block.set_waiting_permission(True)
        await self._conv().mount(approval.prompt)
        await self._after_mount()

    def _answer_approval(self, approval: _Approval, choice: PermissionChoice):
        self._settle_approval(approval, choice)
        if self._approvals:
            asyncio.create_task(self._mount_approval(self._approvals[0]))

    def _settle_approval(self, approval: _Approval, choice: PermissionChoice):
        if approval in self._approvals:
            self._approvals.remove(approval)
        approval.prompt.remove()
        block = approval.block
        if isinstance(block, _ToolBlock):
            block.set_waiting_permission(False)
        if not approval.future.done():
            approval.future.set_result(choice)
        self.query_one("#input", Input).focus()

    async def _deny_pending_permission(self) -> bool:
        if not self._approvals:
            return False
        for approval in list(self._approvals):
            self._settle_approval(approval, PermissionChoice("denied"))
            block = approval.block
            if isinstance(block, _ToolBlock) and not block._done:
                block.reject()
        self._interrupted_call = True
        return True

    async def action_cycle_permission(self):
        if self._session is None:
            return
        nxt = next_mode(self._session.permission_mode,
                        bypass_available=self._session.bypass_available)
        self._session.set_permission_mode(nxt.value)
        self._render_status()

    async def action_escape(self):
        if self._suggest_items:
            self.action_suggest_dismiss()
            return
        if self._viewing is not None:
            agent = self._agent_by_name(self._viewing)
            if agent is not None and self._agent_running(agent):
                agent.abort_work()
                await self._render_agent_view()
                return
            await self._exit_agent_view()
            await self._refresh_agents()
            return
        if self._view_selection == 'selecting-agent':
            self._view_selection = 'none'
            self._selected_index = -1
            await self._refresh_agents()
            return
        if self._processing is None:
            return
        await self._interrupt()

    async def action_interrupt(self):
        now = asyncio.get_running_loop().time()
        if self._processing is None and now - self._last_interrupt < 2.0:
            self.exit()
            return
        self._last_interrupt = now
        await self._interrupt()

    async def _interrupt(self):
        rejected = await self._deny_pending_permission()
        if self._team is not None:
            self._team.lead.abort_work()
        if self._processing and not self._wrote_body:
            self.query_one("#input", Input).value = self._processing
            self._processing = None
        if not rejected:
            await self._append_block(
                f"[#9A9A9A]{INTERRUPTED_TEXT}[/]")

    def _begin_turn(self):
        self._live = None
        self._live_text = ""
        self._response_chars = 0
        self._wrote_body = False
        self._interrupted_call = False
        self._turn_start = len(self._team.transcript())
        self._turn_verb = random.choice(SPINNER_VERBS)
        self._turn_past = self._completion_verb()
        self._turn_started_at = time.monotonic()

    async def _settle_paint(self):
        done = asyncio.Event()
        self.call_after_refresh(done.set)
        await done.wait()

    async def _wait_session_idle(self):
        lead = self._team.lead
        while True:
            if not getattr(lead, 'busy', False) and await lead.idle():
                return
            await asyncio.sleep(0.05)

    def _teammates_running(self) -> bool:
        return any(a is not self._team.lead and getattr(a, 'busy', False)
                   for a in self._team.agents.values())

    async def _finish_work_when_settled(self):
        while self._teammates_running():
            await asyncio.sleep(0.1)
        await self._finish_work()

    async def _converse(self, text: str):
        try:
            out = await self._session.chat(text)
        except Exception as exc:
            logger.exception("chat failed for prompt %r", _log_data(text))
            await self._append_error(str(exc))
            return
        await self._wait_session_idle()
        await self._queue.join()
        if out.strip() and not self._wrote_body and not self._interrupted_call \
                and len(self._team.transcript()) > self._turn_start:
            await self._append_block(escape(out))
        self._render_status()

    def _note(self, name, tools=None, think=None, busy=None):
        st = self._state(name)
        if tools:
            st["tools"] += tools
        if think is not None:
            st["think"] = think
        if busy is not None:
            st["busy"] = busy

    def _context_note(self) -> str:
        s = self._session
        if s is None:
            return ""
        limit = s.compact_threshold
        if limit <= 0:
            return ""
        used = s.context_tokens
        if used < limit - CONTEXT_WARNING_BUFFER_TOKENS:
            return ""
        percent_left = max(0, round((limit - used) / limit * 100))
        if s.auto_compact:
            return f"[dim]{percent_left}% left before auto-compact[/]"
        severity = ("error" if used >= limit - CONTEXT_ERROR_BUFFER_TOKENS
                    else "warning")
        return (f"[{severity}]Nearly out of context ({percent_left}% left) "
                f"\u00b7 run /compact to carry on[/]")

    def _render_status(self):
        s = self._session
        if s is None or not self.is_running:
            return
        self._schedule_statusline()
        viewing = self._viewing is not None
        viewing_busy = viewing and self._agent_running(
            self._agent_by_name(self._viewing))
        parts = []
        show_hint = not self._statusline_cmd and not self._prompt_has_text()
        if show_hint:
            if viewing and not viewing_busy:
                parts.append(TEAMMATE_VIEW_HINT)
            else:
                if self._processing is not None or viewing_busy:
                    parts.append("esc stops the turn")
                hint = self._tasks_hint()
                if hint:
                    parts.append(hint)
        if show_hint and not parts:
            parts.append("? lists the keys")
        self.query_one("#status", Static).update(" \u00b7 ".join(parts))
        self._paint_prompt()
        self._render_readouts()

    def _thinking_label(self) -> str:
        return f"[dim]thinking {'on' if self._session.thinking else 'off'}[/]"

    def _mode_pill(self) -> str:
        perm = self._session.permission_mode
        if perm not in MODE_SYMBOLS:
            return ''
        background = (self._viewing is not None or self._teammates_running()
                      or self._subagents_running())
        pill = (f"[{MODE_COLORS.get(perm, '#9A9A9A')}]{MODE_SYMBOLS[perm]} "
                f"{MODE_TITLES[perm]} on[/]")
        if not background:
            pill += " [dim](shift+tab to cycle)[/]"
        return pill

    def _render_readouts(self):
        if self._session is None or not self.is_running:
            return
        s = self._session
        room = (self.screen.size.width or self.size.width) - 2
        self.query_one("#status-right", Static).update(self._context_note())
        self.query_one("#hud", Static).update(_fit((
            s.model, self._thinking_label(), self._context_meter(),
            usage_meter(self._triple, s.usage, self._meter_cells()),
            usage_hud(s.usage), self._messages(), self._session_row()),
            room))
        self.query_one("#hud2", Static).update(_fit((
            _display_cwd(s.cwd), self._git, self._mode_pill()), room))

    def _context_meter(self) -> str:
        s = self._session
        return context_meter(self._triple, s.used_context, s.context_window,
                             self._meter_cells())

    def _messages(self) -> str:
        return f'{len(self._session.transcript())} msg'

    def _session_row(self) -> str:
        seconds = max(0, int(time.monotonic() - self._hud_started))
        return f'{ELAPSED_ICON} {self._duration(seconds)}'

    async def _refresh_git(self):
        cwd = self._cwd()
        status = await asyncio.to_thread(git_status, cwd)
        if cwd == self._cwd():
            self._git = git_label(status)

    def _meter_cells(self) -> int:
        width = self.screen.size.width or self.size.width
        return (CONTEXT_METER_CELLS if width >= NARROW_TERMINAL_COLUMNS
                else NARROW_CONTEXT_METER_CELLS)

    def _prompt_has_text(self) -> bool:
        return bool(self.query_one("#input", Input).value)

    def _statusline_state(self) -> tuple:
        s = self._session
        return (_last_assistant_key(s.transcript()), s.permission_mode,
                s.model, statusline.user_command())

    def _schedule_statusline(self):
        state = self._statusline_state()
        if state == self._statusline_seen:
            return
        self._statusline_seen = state
        if self._statusline_timer is not None:
            self._statusline_timer.stop()
        self._statusline_timer = self.set_timer(
            statusline.STATUS_LINE_DEBOUNCE_SECONDS,
            self._refresh_statusline)

    async def _refresh_statusline(self):
        self._statusline_timer = None
        self._statusline_cmd = statusline.user_command()
        if not self._statusline_cmd:
            self._statusline_text = ""
            self._paint_statusline()
            self._render_status()
            return
        if self._session is None:
            return
        previous = self._statusline_task
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(
            statusline.run(self._session, self._statusline_cmd))
        self._statusline_task = task
        try:
            text = await task
        except (asyncio.CancelledError, Exception):
            return
        if self._statusline_task is not task:
            return
        self._statusline_text = text
        self._paint_statusline()
        self._render_status()

    def _paint_statusline(self):
        if not self.is_running:
            return
        widget = self.query_one("#statusline", Static)
        widget.display = bool(self._statusline_text)
        widget.update(Text.from_ansi(self._statusline_text, style="dim",
                                     no_wrap=True))

    def _subagents_running(self) -> bool:
        return any(not st['done'] for st in self._subagents.values())

    def _paint_prompt(self):
        viewed = self._viewing
        accent = (self._agent_color(viewed) if viewed is not None
                  else banner.dimmed(self._triple, banner.RULE_LIGHTNESS))
        frame = self.query_one("#prompt", Horizontal)
        frame.styles.border_top = ("round", accent)
        frame.styles.border_bottom = ("round", accent)
        pointer = self.query_one("#prompt-pointer", Static)
        pointer.styles.color = accent if viewed is not None else self.brand
        pointer.styles.text_style = "dim" if self._processing else "none"

    def _tasks_hint(self) -> str:
        if not self._teammates():
            return ''
        if self._expanded_view == 'none':
            action = 'shows the task list'
        elif self._expanded_view == 'tasks':
            action = 'shows the teammate tree'
        else:
            action = 'hides them'
        return f"[dim]ctrl+t {action}[/]"

    def _render_tasks(self):
        if self._team is None:
            return
        lines = ["[bold]Agents[/bold]"]

        def walk(agent_id, prefix, is_last):
            a = self._team.agents.get(agent_id)
            if a is None:
                return
            st = self._agent_state.get(a.name, {})
            marker = "\u2514\u2500" if is_last else "\u251c\u2500"
            status = ("working\u2026" if st.get("think")
                      else ("busy" if st.get("busy") else "idle"))
            lines.append(f"{prefix}{marker} {escape(a.name)} \u00b7 "
                         f"{_tool_uses(st.get('tools', 0))} ({status})")
            kids = sorted(self._team.children.get(agent_id, ()))
            sub = prefix + ("   " if is_last else "\u2502  ")
            for i, k in enumerate(kids):
                walk(k, sub, i == len(kids) - 1)

        walk(self._team.lead.agent_id, "", True)
        if self._subagents:
            lines.append("")
            lines.append("[bold]Sub-agents[/bold]")
            subs = list(self._subagents.items())
            for i, (name, st) in enumerate(subs):
                is_last = i == len(subs) - 1
                tc = "\u2514\u2500" if is_last else "\u251c\u2500"
                tokens = (f" \u00b7 {_format_count(st['tokens'])} tokens"
                          if st['tokens'] is not None else "")
                status = ("Done" if st['done']
                          else (st['last_tool'] or "Initializing\u2026"))
                stat_pre = "   " if is_last else "\u2502  "
                label = escape(str(st['type'])) or "Agent"
                lines.append(f"{tc} [bold]{label}[/] \u00b7 "
                             f"{_tool_uses(st['tools'])}{tokens}")
                lines.append(f"{stat_pre}{RESULT_GLYPH}  {escape(status)}")
        lines.append("")
        lines.append(f"[bold]Tools[/bold] {len(self._session.available_tools)}")
        lines += [f"  {escape(str(t['name']))}"
                  for t in self._team.tool_schemas(self._team.tool_context)[:40]]
        self._tasks_pane.update("\n".join(lines))

    async def action_toggle_tasks(self):
        teammates = bool(self._teammates())
        view = self._expanded_view
        if teammates:
            order = {'none': 'tasks', 'tasks': 'teammates'}
            nxt = order.get(view, 'none')
        else:
            nxt = 'none' if view == 'tasks' else 'tasks'
        self._expanded_view = nxt
        if nxt != 'teammates':
            self._view_selection = 'none'
            self._selected_index = -1
        pane = self.query_one("#tasks", VerticalScroll)
        pane.display = nxt == 'tasks'
        if nxt == 'tasks':
            self._render_tasks()
        await self._refresh_agents()

    def action_redraw(self):
        self.refresh()

    def action_toggle_transcript(self):
        self.push_screen(TranscriptScreen(self))

    def action_history_search(self):
        if self._history:
            self.push_screen(HistorySearchScreen(self))

    def action_stash(self):
        inp = self.query_one("#input", Input)
        text = inp.value
        if text.strip():
            self._stashed = text
            inp.value = ""
        elif self._stashed is not None:
            inp.value = self._stashed
            inp.cursor_position = len(self._stashed)
            self._stashed = None

    def action_toggle_help(self):
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
            return
        self.push_screen(HelpScreen())

    async def action_quit(self):
        if self._spin_timer is not None:
            self._spin_timer.stop()
        self.exit()
