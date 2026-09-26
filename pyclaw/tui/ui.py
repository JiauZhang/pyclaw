"""The shapes every dialog is made of.

A screen writes its content, not its chrome: the heading, the selection marker,
the dim asides and the hint line live here, so every dialog agrees on them and
the theme can move them all at once.
"""
from __future__ import annotations

from pyclaw.tui.formatting import escape

from pyclaw.tui import keys
from pyclaw.tui.theme import POINTER

SELECTED = '$brand'


def heading(label: str, value: str = '', note: str = '') -> list[str]:
    line = f'[bold]{escape(label)}[/]'
    if value:
        line += f' [{SELECTED}]{escape(value)}[/]'
    lines = [line]
    if note:
        lines.append(aside(note))
    return lines


def aside(text: str) -> str:
    return f'[dim]{escape(text)}[/]'


def row(text: str, *, selected: bool, marker: str = POINTER) -> str:
    """A selectable line of plain text: marked and tinted, or dim and indented."""
    shown = escape(text)
    if selected:
        return f'[{SELECTED}]{marker} {shown}[/]'
    return f'{" " * (len(marker) + 1)}[dim]{shown}[/]'


def rows(labels, selected: int) -> list[str]:
    return [row(label, selected=index == selected)
            for index, label in enumerate(labels)]


def marked(markup: str, *, selected: bool) -> str:
    """A line that carries its own colors; selection only tints it."""
    return f'[{SELECTED}]{markup}[/]' if selected else markup


def hint_line(*pairs) -> str:
    return f'[dim]{keys.hints(*pairs)}[/]'


def footer(*pairs) -> list[str]:
    return ['', hint_line(*pairs)] if pairs else []
