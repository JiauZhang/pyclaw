from __future__ import annotations

import sys

from textual.binding import Binding

# Every key is written down once, here. Widgets build their BINDINGS from this
# table and every "press X to ..." hint reads it back, so changing a key cannot
# leave stale copy behind.
ACTIONS: dict[str, str] = {
    # the app
    'quit': 'ctrl+d',
    'interrupt': 'ctrl+c',
    'escape': 'escape',
    'toggle_tasks': 'ctrl+t',
    'redraw': 'ctrl+l',
    'toggle_transcript': 'ctrl+o',
    'agent_preview': 'ctrl+shift+o',
    'toggle_thinking': 'alt+t',
    'history_search': 'ctrl+r',
    'stash': 'ctrl+s',
    'conv_page_up': 'pageup',
    'conv_page_down': 'pagedown',
    'conv_scroll_top': 'ctrl+home',
    'jump_to_bottom': 'ctrl+end',
    'agent_prev': 'shift+up',
    'agent_next': 'shift+down',
    'stop_agent': 'k',
    'cycle_permission': 'shift+tab',
    'prompt_next': 'down',
    'prompt_prev': 'up',
    'suggest_tab': 'tab',
    # the rows and dialogs a person navigates
    'prev': 'up',
    'next': 'down',
    'open': 'enter',
    'choose': 'enter',
    'dismiss': 'escape',
    'stop_selected': 'x',
    'stop_all': 'a',
    'remove_rule': 'd',
    'accept': 'tab',
    'type_answer': 'tab',
    'toggle_option': 'space',
    'save': 's',
}

APP_ROWS: tuple[tuple[str, str, bool], ...] = (
    ('quit', 'Exit', True),
    ('interrupt', 'Stop current work', True),
    ('escape', 'Cancel / dismiss', False),
    ('toggle_tasks', 'Show/hide tasks', False),
    ('redraw', 'Redraw', False),
    ('toggle_transcript', 'Transcript', False),
    ('agent_preview', 'Preview teammate activity', True),
    ('toggle_thinking', 'Turn thinking on or off', True),
    ('history_search', 'Search history', False),
    ('stash', 'Stash prompt', False),
    ('conv_page_up', 'Scroll up', False),
    ('conv_page_down', 'Scroll down', False),
    ('conv_scroll_top', 'Scroll to top', True),
    ('jump_to_bottom', 'Scroll to latest', True),
    ('agent_prev', 'Previous agent', True),
    ('agent_next', 'Next agent', True),
    ('stop_agent', 'Stop selected agent', True),
    ('cycle_permission', 'Cycle permission mode', True),
    ('prompt_next', 'Next', True),
    ('prompt_prev', 'Previous', True),
    ('suggest_tab', 'Complete suggestion', True),
)

ARROWS = {'up': '\u2191', 'down': '\u2193', 'left': '\u2190',
          'right': '\u2192', 'pageup': 'PgUp', 'pagedown': 'PgDn'}

TYPED = {'escape': 'esc', 'enter': 'enter', 'tab': 'tab', 'space': 'space'}

ALT = 'opt' if sys.platform == 'darwin' else 'alt'


def key(action: str) -> str:
    return ACTIONS[action]


def display(action: str) -> str:
    parts = []
    for part in key(action).split('+'):
        if part in ('alt', 'meta'):
            parts.append(ALT)
        else:
            parts.append(ARROWS.get(part) or TYPED.get(part, part))
    return '+'.join(parts)


def chord(first: str, second: str, text: str) -> str:
    """Two keys under one modifier, as a person would read them."""
    head, tail = display(first), display(second)
    if '+' in head and head.split('+')[0] == tail.split('+')[0]:
        return f'{head}/{tail.split("+", 1)[1]} {text}'
    return f'{head}/{tail} {text}'


def either(first: str, second: str, text: str) -> str:
    """One key or another does the same thing, e.g. "s or enter saves"."""
    return f'{display(first)} or {display(second)} {text}'


def binding(action: str, label: str, priority: bool = False) -> Binding:
    return Binding(key(action), action, label, priority=priority)


def app_bindings() -> list:
    rows = [binding(action, label, priority)
            for action, label, priority in APP_ROWS]
    rows.append(Binding('ctrl+q', 'quit', 'Exit (fallback)'))
    return rows


def hint(action: str, text: str) -> str:
    return f'{display(action)} {text}'


def hints(*pairs) -> str:
    return ' \u00b7 '.join(hint(action, text) for action, text in pairs)
