from __future__ import annotations

import difflib
import re
from rich.markup import escape

from pyclaw.tui.formatting import _edit_summary


_DIFF_HUNK_RE = re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')
_DIFF_TOOLS = ('Edit', 'Write')
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
