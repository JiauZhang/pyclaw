from __future__ import annotations

import os
import re
from pathlib import Path


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
