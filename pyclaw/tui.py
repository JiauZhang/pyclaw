from __future__ import annotations

import asyncio
import difflib
import json
import os
import random
import re
import sys
from pathlib import Path

from rich.markup import escape

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Markdown, Static


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

from pyclaw.agents import Session, append_conv
from pyclaw.spinner_verbs import SPINNER_VERBS
from pyclaw.slash import suggest as slash_suggest
from pyclaw.tools.coding import next_mode


BULLET = "\u23fa" if sys.platform == "darwin" else "\u25cf"
POINTER = "\u276f"
RESULT_GLYPH = "\u23bf"
ASTERISK = "\u273b"
BULLET_PREFIX = f"{BULLET} "
BULLET_HANG = " " * len(BULLET_PREFIX)
RESULT_PREFIX = f"  {RESULT_GLYPH}  "
RESULT_HANG = " " * len(RESULT_PREFIX)

MODE_SYMBOLS = {"acceptEdits": "\u23f5\u23f5",
                "bypassPermissions": "\u23f5\u23f5", "plan": "\u23f8"}
MODE_TITLES = {"acceptEdits": "accept edits", "plan": "plan mode",
               "bypassPermissions": "bypass permissions"}
MODE_COLORS = {"acceptEdits": "#AF87FF", "plan": "#48968C",
               "bypassPermissions": "#FF6B80"}

SPINNER_CHARS = ["\u00b7", "\u2722", "\u2733", "\u2736", "\u273b", "\u273d"]
SPINNER_FRAMES = SPINNER_CHARS + list(reversed(SPINNER_CHARS))
SPINNER_INTERVAL = 0.05

MAX_COMMAND_LINES = 2
MAX_COMMAND_CHARS = 160
MAX_RESULT_LINES = 3
MAX_USE_ARG_CHARS = 80
MAX_WRITE_PREVIEW_LINES = 10

DISPLAY_NAMES = {"Edit": "Update", "MultiEdit": "Update", "Grep": "Search",
                 "Glob": "Search", "LS": "List", "create_agent": "Task"}
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
    ('search', 'Searching for', 'Searched for', 'pattern', 'patterns'),
    ('read', 'Reading', 'Read', 'file', 'files'),
    ('list', 'Listing', 'Listed', 'directory', 'directories'),
    ('bash', 'Running', 'Ran', 'bash command', 'bash commands'),
    ('memory_read', 'Recalling', 'Recalled', 'memory', 'memories'),
    ('memory_write', 'Writing', 'Wrote', 'memory', 'memories'),
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
                        f"(ctrl+o to expand)"])


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


def _result_summary(name: str, tool_input, output, cwd, width: int) -> str:
    text = str(output if output is not None else "")
    if not text:
        return "Done"
    if text.startswith("Error"):
        return text.split("\n")[0]
    data = tool_input if isinstance(tool_input, dict) else {}
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


class _Conv(VerticalScroll):

    def watch_scroll_y(self, value):
        try:
            self.app._set_follow(bool(self.is_vertical_scroll_end))
        except Exception:
            pass


class _JumpToBottom(Static):

    def __init__(self, text: str = "", **kw):
        super().__init__(text, markup=True, **kw)

    async def on_click(self):
        await self.app.action_jump_to_bottom()


def _hang(prefix: str, body: str) -> str:
    return prefix + body.replace("\n", "\n" + " " * len(prefix))


def _input_text(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


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
        color = '#D77757' if self.active else '#4EBA65'
        marker = self._frame if self.active else BULLET
        hint = "" if self.active else " [dim](ctrl+o to expand)[/]"
        self.update(f"[{color}]{marker}[/] {body}{hint}")


CLAWD_BODY = "#D77757"
CLAWD_BG = "#000000"


def _logo_markup() -> str:
    row1 = (f"[{CLAWD_BODY}] \u2590[/]"
            f"[{CLAWD_BODY} on {CLAWD_BG}]\u259b\u2588\u2588\u2588\u259c[/]"
            f"[{CLAWD_BODY}]\u258c[/]")
    row2 = f"[{CLAWD_BODY}]\u259d\u259c\u259b\u2598[/]"
    return f"{row1}\n{row2}"


class _LogoBlock(Static):

    def __init__(self, model: str = "", **kw):
        lines = [_logo_markup()]
        if model:
            lines.append(f"\n[dim]{escape(model)}[/]")
        super().__init__("\n".join(lines), markup=True, classes="logo", **kw)


class _UserBlock(Static):

    def __init__(self, text: str, **kw):
        super().__init__(escape(text), markup=True, classes="user", **kw)


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
        self._draw()

    def tick(self, char: str):
        if not self._done:
            self._frame = char
            self._draw()

    def set_result(self, output, meta=None):
        self._done = True
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

    def _head(self) -> str:
        name = escape(_display_name(self._name))
        args = escape(_tool_use_args(self._name, self._input, self._cwd))
        color = "#FF6B80" if self._failed else (
            "#4EBA65" if self._done else "#D77757")
        marker = BULLET if (self._done or self._failed) else self._frame
        return f"[{color}]{marker}[/] [bold]{name}[/]({args})"

    def _draw(self):
        head = self._head()
        if self._output is None:
            self.update(head)
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
            summary = _result_summary(self._name, self._input, self._output,
                                      self._cwd, self._width())
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
        with VerticalScroll(id="transcript"):
            for entry in self._entries():
                yield Static(entry, markup=True)

    def on_mount(self):
        self.query_one("#transcript", VerticalScroll).focus()

    @staticmethod
    def _tool_entry(block) -> str:
        head = block._head()
        if block._output is None:
            return head
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
        lines = [f'[bold]Permission mode[/bold] [#D77757]{mode}[/]',
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


class _PermissionPrompt(Vertical):

    can_focus = True
    BINDINGS = [
        ("y", "pick_yes", "Yes"),
        ("n", "pick_no", "No"),
        ("up", "move_up", "Previous option"),
        ("down", "move_down", "Next option"),
        ("enter", "choose", "Confirm"),
        ("escape", "cancel", "Cancel"),
        ("tab", "amend", "Amend rule"),
    ]

    def __init__(self, tool_name: str, tool_input, cwd: str = ".",
                 rememberable: bool = True, rule: str = "", **kw):
        super().__init__(classes="permission", **kw)
        self._tool = tool_name
        self._input = tool_input
        self._cwd = cwd
        self._rememberable = rememberable
        self._rule = rule
        self._selected = 0
        self._amending = False
        self.on_choice = None

    def compose(self) -> ComposeResult:
        yield Static("", id="perm-body", markup=True)
        yield Input("", id="perm-amend")

    def on_mount(self):
        self._draw()
        self.focus()

    def _options(self) -> list:
        options = [('approved', 'Yes')]
        if self._rememberable:
            if self._rule:
                label = f"Yes, and don't ask again for: {self._rule}"
            else:
                label = (f"Yes, and don't ask again for {self._tool} "
                         f"commands in {self._cwd}")
            options.append(('dont_ask', label))
        options.append(('denied', 'No'))
        return options

    def _draw(self):
        body = self.query_one("#perm-body", Static)
        if self._amending:
            body.update(
                f"[#B1B9F9]{BULLET}[/] [bold]Amend rule[/bold]\n"
                f"[dim]  Esc to cancel \u00b7 enter to approve "
                f"with this rule[/]")
            return
        args = _tool_use_args(self._tool, self._input, self._cwd)
        lines = [f"[#B1B9F9]{BULLET}[/] [bold]Tool use[/bold]",
                 f"  {escape(_display_name(self._tool))}"
                 f"({escape(args)})",
                 "  Do you want to proceed?"]
        for index, (_value, label) in enumerate(self._options()):
            marker = POINTER if index == self._selected else ' '
            row = escape(f"  {marker} {index + 1}. {label}")
            lines.append(f"[#B1B9F9]{row}[/]" if index == self._selected
                         else f"[dim]{row}[/]")
        lines.append("[dim]  Esc to cancel \u00b7 enter to confirm"
                     + (" \u00b7 tab to amend" if self._rememberable else "")
                     + "[/]")
        body.update("\n".join(lines))

    def action_move_up(self):
        if self._amending:
            return
        self._selected = max(0, self._selected - 1)
        self._draw()

    def action_move_down(self):
        if self._amending:
            return
        self._selected = min(len(self._options()) - 1, self._selected + 1)
        self._draw()

    async def action_choose(self):
        if self._amending:
            return
        options = self._options()
        await self._finish(options[min(self._selected, len(options) - 1)][0])

    async def action_pick_yes(self):
        if not self._amending:
            await self._finish('approved')

    async def action_pick_no(self):
        if not self._amending:
            await self._finish('denied')

    async def action_cancel(self):
        if self._amending:
            self._set_amend(False)
            return
        await self._finish('denied')

    def action_amend(self):
        if not self._rememberable:
            return
        self._set_amend(not self._amending)

    def _set_amend(self, on: bool):
        self._amending = on
        inp = self.query_one("#perm-amend", Input)
        inp.display = on
        if on:
            inp.value = self._rule
            inp.focus()
        else:
            self.focus()
        self._draw()

    async def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        rule = event.value.strip()
        if rule:
            await self._finish(('dont_ask', rule))
        else:
            await self._finish('approved')

    async def _finish(self, decision: str):
        if self.on_choice is not None:
            self.on_choice(decision)
        self.remove()
        self.app.query_one("#input", Input).focus()


class PyClawApp(App[None]):
    TITLE = "PyClaw"
    CSS = """
    $background: #101010;
    $brand: #D77757;
    $shimmer: #EB9F7F;
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
    $prompt-border: #888888;
    $ide: #4782C8;
    $diff-added: #225C2B;
    $diff-removed: #7A2936;
    $user-message: #373737;
    $selection: #264F78;

    Screen { layout: vertical; background: $background; }
    #conv { width: 1fr; height: 1fr; background: $background; overflow-y: auto;
            scrollbar-gutter: stable; padding: 0 1; }
    #conv > Static { width: 100%; margin-bottom: 1; }
    .user { background: $user-message; }
    .diff { border-top: dashed $subtle; border-bottom: dashed $subtle;
            border-left: none; border-right: none; padding: 0 1; }
    .permission { width: 100%; margin-bottom: 1; }
    #perm-amend { display: none; width: 100%; height: 1; margin-top: 1;
                  border: round $permission; background: $background;
                  color: $text; padding: 0 1; }
    .logo { width: auto; margin-bottom: 1; color: $brand; }
    .text-block { width: 100%; height: auto; margin-bottom: 1; }
    .text-row { width: 100%; height: auto; }
    .text-bullet { width: 2; height: 1; color: $brand; }
    .text-body { width: 1fr; height: auto; color: $text; background: $background; }
    #transcript { width: 1fr; height: 1fr; background: $background; padding: 0 1; }
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
    #prompt-pointer { width: 2; height: 1; color: $subtle; }
    #input { height: 1; width: 1fr; border: none; padding: 0;
             background: $background; color: $text; }
    #footer { height: 1; }
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
                ("ctrl+home", "conv_scroll_top", "Scroll to top"),
                ("ctrl+end", "jump_to_bottom", "Jump to bottom"),
                Binding("shift+tab", "cycle_permission", "Cycle permission mode",
                        priority=True),
                Binding("down", "prompt_next", "Next", priority=True),
                Binding("up", "prompt_prev", "Previous", priority=True),
                Binding("tab", "suggest_tab", "Complete suggestion",
                        priority=True)]

    def check_action(self, action: str, parameters) -> bool:
        if action in ('suggest_tab', 'suggest_dismiss'):
            return bool(self._suggest_items)
        if action in ('prompt_next', 'prompt_prev'):
            return len(self.screen_stack) <= 1
        return True

    def __init__(self, *, builder, session_id=None, resume=False,
                 resume_from=None):
        super().__init__()
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
        self._work_block: Static | None = None
        self._turn_started_at = 0.0
        self._turn_verb = SPINNER_VERBS[0]
        self._tools: dict[str, _ToolBlock] = {}
        self._spin_timer = None
        self._spin_i = 0
        self._think: dict | None = None
        self._subagents: dict[str, dict] = {}
        self._agent_state: dict[str, dict] = {}
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
        self._logo: _LogoBlock | None = None

    def compose(self) -> ComposeResult:
        yield _Conv(id="conv")
        yield Static('', id='suggest')
        yield VerticalScroll(id="tasks")
        with Horizontal(id="prompt"):
            yield Static(POINTER, id="prompt-pointer")
            yield Input(placeholder="Message PyClaw\u2026", id="input")
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
        self._logo = _LogoBlock(model=getattr(self._session, "model", ""))
        await self._conv().mount(self._logo)
        if self._resume:
            self._session.restore_transcript()
            await self._render_history()
        self.query_one(Input).focus()
        self._render_status()
        self._render_tasks()

    async def on_unmount(self):
        if self._spin_timer is not None:
            self._spin_timer.stop()
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
        return getattr(self._session, "cwd", "") or "."

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
        if self._follow or not self._new_messages:
            if self._hint is not None:
                self._hint.remove()
                self._hint = None
            return
        text = f"[#B1B9F9]\u2193 Jump to bottom \u00b7 {self._new_messages} new[/]"
        if self._hint is None:
            self._hint = _JumpToBottom(text)
            await self.screen.mount(self._hint,
                                    before=self.query_one("#prompt"))
        else:
            self._hint.update(text)

    async def _after_mount(self):
        self._drop_logo()
        if self._follow:
            self._follow_scroll()
        else:
            self._new_messages += 1
        await self._refresh_follow_hint()

    def _drop_logo(self):
        logo, self._logo = self._logo, None
        if logo is not None and logo.parent is not None:
            logo.remove()

    async def action_jump_to_bottom(self):
        self._follow = True
        self._new_messages = 0
        self._conv().scroll_end(animate=False)
        await self._refresh_follow_hint()

    async def action_conv_page_up(self):
        self._conv().scroll_page_up(animate=False)

    async def action_conv_page_down(self):
        self._conv().scroll_page_down(animate=False)

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
            except Exception as exc:
                try:
                    await self._append_error(
                        f"render error: {exc}")
                except Exception:
                    pass
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
        name = ev.agent or ""
        if ev.kind == AGENT_REASON_START:
            self._note(name, think=True)
            await self._add_think(ev)
        elif ev.kind == AGENT_TEXT:
            self._note(name, think=False, busy=True)
            delta = ev.data.get("delta", "")
            if not delta:
                return
            self._discard_think()
            if self._live is None:
                if not delta.strip():
                    return
                await self._start_live()
            self._live_text += delta
            self._update_live()
            self._wrote_body = True
            self._reset_tool_group()
        elif ev.kind == AGENT_TOOL_CALL:
            self._note(name, tools=1, think=False)
            self._discard_think()
            await self._frozen()
            await self._add_tool(ev)
        elif ev.kind == AGENT_PROGRESS:
            self._note_progress(ev)
        elif ev.kind == AGENT_WARN:
            await self._frozen()
            await self._append_error(ev.data.get('text', ''))
        elif ev.kind == AGENT_TOOL_RESULT:
            uid = ev.data.get('tool_use_id') or ev.data.get('tool', '')
            self._tool_meta[uid] = dict(ev.data)
        elif ev.kind == AGENT_TURN_FINISHED:
            self._note(name, think=False, busy=False)
            self._discard_think()
            self._reset_tool_group()
            if name == self._team.lead.name:
                await self._frozen()
                self._sync_tool_states()
                await self._finish_work()
                self._log_turn()
        elif ev.kind == AGENT_STATE:
            self._note(name, busy=bool(ev.data.get("busy", False)))

    _SPIN = "".join(SPINNER_FRAMES)

    @staticmethod
    def _fmt(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}m"
        if n >= 1000:
            s = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
            return f"{s}k"
        return str(n)

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
        kinds = _collapsible_kinds(name, raw_input)
        if kinds:
            if self._group is None:
                self._group = _GroupBlock()
                self._group._frame = self._SPIN[self._spin_i % len(self._SPIN)]
                await self._conv().mount(self._group)
            self._group.add(kinds, _read_key(name, raw_input), uid)
            await self._after_mount()
            return
        self._reset_tool_group()
        await self._conv().mount(block)
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
        self._discard_think()
        widget = Static("", markup=True)
        await self._conv().mount(widget)
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
        suffix = 'esc to interrupt'
        if self._session is not None:
            tokens = int(getattr(self._session.usage, 'total_tokens', 0) or 0)
            if tokens:
                suffix += f' \u00b7 \u2193 {self._fmt(tokens)} tokens'
        return (f"[#D77757]{char}[/] {self._turn_verb}\u2026 "
                f"[dim]({suffix})[/]")

    @staticmethod
    def _duration(seconds: int) -> str:
        if seconds < 60:
            return f'{seconds}s'
        return f'{seconds // 60}m {seconds % 60}s'

    async def _finish_work(self):
        self._discard_think()
        elapsed = 0
        if self._turn_started_at:
            elapsed = max(0, int(asyncio.get_running_loop().time()
                                 - self._turn_started_at))
        text = (f"[#9A9A9A]{ASTERISK} Worked for "
                f"{self._duration(elapsed)}[/]")
        if self._work_block is None or self._work_block.parent is None:
            self._work_block = await self._append_block(text)
        else:
            self._work_block.update(text)

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
        st = self._subagents.get(name)
        if st is None:
            st = {'type': data.get('subagent_type') or 'Agent', 'tools': 0,
                  'tokens': None, 'last_tool': None, 'done': False,
                  'tool_names': {}}
            self._subagents[name] = st
        if data.get('done'):
            st['done'] = True
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
        if not self._tools and not self._think:
            return
        conv = next(iter(self.query(_Conv)), None)
        if conv is None:
            if self._spin_timer is not None:
                self._spin_timer.stop()
            return
        self._spin_i += 1
        char = self._SPIN[self._spin_i % len(self._SPIN)]
        if self._think is not None:
            self._think["widget"].update(self._spinner_text(char))
        self._sync_tool_states()
        if self._group is not None and self._group.active:
            self._group.tick(char)
        for block in self.query(_ToolBlock):
            block.tick(char)
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
        if text == '/permissions':
            self.query_one("#input", Input).value = ""
            await self._append_user(text)
            self.push_screen(PermissionsScreen(self._session))
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
                self.query_one("#input", Input).value = f"/{item['name']} "
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

    def action_suggest_dismiss(self):
        self._suggest_dismissed = self.query_one("#input", Input).value
        self._suggest_items = []
        self._typeahead = None
        self._show_suggest_widget(False)

    async def _render_queued(self):
        inp = self.query_one("#input", Input)
        queued = self._peek_queue()
        inp.placeholder = ("Press up to edit queued messages" if queued
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
                await self._append_user(text)
                self._begin_turn()
                await self._mount_spinner()
                await self._converse(text)
                await self._settle_paint()
                self._processing = None
                self._render_status()
                await self._render_queued()
        finally:
            self._driving = False

    async def _ask_permission(self, tool_name: str, tool_input) -> str:
        inp = tool_input if isinstance(tool_input, dict) else {}
        rule = ""
        if tool_name == 'Bash':
            from pyclaw.tools.coding.shell_rules import suggested_rule
            rule = suggested_rule(str(inp.get('command') or '')) or ""
        else:
            from pyclaw.tools.coding.permission import suggested_path_rule
            rule = suggested_path_rule(tool_name, inp, self._cwd()) or ""
        prompt = _PermissionPrompt(tool_name, inp, cwd=self._cwd(),
                                   rememberable=bool(rule) or tool_name != 'Bash',
                                   rule=rule)
        fut = asyncio.get_running_loop().create_future()
        prompt.on_choice = fut.set_result
        conv = self._conv()
        await conv.mount(prompt)
        await self._after_mount()
        return await fut

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
        if self._team is not None:
            self._team.lead.abort_work()
        if self._processing and not self._wrote_body:
            self.query_one("#input", Input).value = self._processing
            self._processing = None
        await self._append_block(
            "[#9A9A9A]Interrupted \u00b7 What should PyClaw do instead?[/]")

    def _begin_turn(self):
        self._live = None
        self._live_text = ""
        self._wrote_body = False
        self._turn_start = len(self._team.transcript())
        self._turn_verb = random.choice(SPINNER_VERBS)
        self._turn_started_at = asyncio.get_running_loop().time()

    async def _settle_paint(self):
        done = asyncio.Event()
        self.call_after_refresh(done.set)
        await done.wait()

    async def _wait_session_idle(self):
        while True:
            members = list(self._team.agents.values())
            if not any(getattr(a, 'busy', False) for a in members) \
                    and await self._team.lead.idle():
                return
            await asyncio.sleep(0.05)

    async def _converse(self, text: str):
        try:
            out = await self._session.chat(text)
        except Exception as exc:
            await self._append_error(str(exc))
            return
        await self._wait_session_idle()
        await self._queue.join()
        if out.strip() and not self._wrote_body \
                and len(self._team.transcript()) > self._turn_start:
            await self._append_block(escape(out))
        self._render_status()

    def _note(self, name, tools=None, think=None, busy=None):
        st = self._agent_state.setdefault(name, {"tools": 0, "think": False, "busy": False})
        if tools:
            st["tools"] += tools
        if think is not None:
            st["think"] = think
        if busy is not None:
            st["busy"] = busy

    def _context_percent(self) -> int | None:
        if self._session is None:
            return None
        try:
            limit = int(getattr(self._session, "compact_threshold", 0) or 0)
        except Exception:
            return None
        if limit <= 0:
            return None
        total = int(getattr(self._session.usage, "total_tokens", 0) or 0)
        return max(0, min(100, round(total * 100 / limit)))

    def _render_status(self):
        s = self._session
        if s is None:
            return
        left = ["esc to interrupt" if self._processing is not None
                else "? for shortcuts"]
        perm = s.permission_mode
        symbol = MODE_SYMBOLS.get(perm)
        if symbol:
            color = MODE_COLORS.get(perm, "#9A9A9A")
            left.append(f"[{color}]{symbol} {MODE_TITLES[perm]} on[/] "
                        f"[dim](shift+tab to cycle)[/]")
        self.query_one("#status", Static).update(
            "  [dim]\u00b7[/]  ".join(left))
        used = self._context_percent()
        if used is None:
            right = ""
        elif used < 80:
            right = f"[dim]{used}% context used[/]"
        else:
            right = (f"[#FFC107]{used}% context used \u00b7 "
                     f"run /compact to compact & continue[/]")
        self.query_one("#status-right", Static).update(right)

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
                         f"{st.get('tools', 0)} tools ({status})")
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
                uses = "use" if st['tools'] == 1 else "uses"
                tokens = (f" \u00b7 {self._fmt(st['tokens'])} tokens"
                          if st['tokens'] is not None else "")
                status = ("Done" if st['done']
                          else (st['last_tool'] or "Initializing\u2026"))
                stat_pre = "   " if is_last else "\u2502  "
                label = escape(str(st['type'])) or "Task"
                lines.append(f"{tc} [bold]{label}[/] \u00b7 {st['tools']} "
                             f"tool {uses}{tokens}")
                lines.append(f"{stat_pre}{RESULT_GLYPH}  {escape(status)}")
        lines.append("")
        lines.append(f"[bold]Tools[/bold] {len(self._session.available_tools)}")
        lines += [f"  {escape(str(t['name']))}"
                  for t in self._team.tool_schemas(self._team.tool_context)[:40]]
        self._tasks_pane.update("\n".join(lines))

    async def action_toggle_tasks(self):
        pane = self.query_one("#tasks", VerticalScroll)
        pane.display = not pane.display
        if pane.display:
            self._render_tasks()

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
