"""One table holds every key; bindings and the hints that name them both read it."""
from pyclaw.tui import keys


def test_a_hint_is_the_key_and_what_it_does():
    assert keys.hint('dismiss', 'closes') == 'esc closes'
    assert keys.hint('toggle_transcript', 'shows more') == 'ctrl+o shows more'


def test_two_keys_on_one_modifier_share_it():
    assert keys.chord('agent_prev', 'agent_next', 'picks a row') == \
        'shift+\u2191/\u2193 picks a row'


def test_either_key_names_both():
    assert keys.either('save', 'open', 'saves') == 's or enter saves'


def test_arrows_and_escape_read_as_a_person_writes_them():
    assert keys.display('prev') == '\u2191'
    assert keys.display('next') == '\u2193'
    assert keys.display('dismiss') == 'esc'
    assert keys.display('conv_page_up') == 'PgUp'


def test_alt_is_named_for_the_platform():
    expected = 'opt+t' if keys.ALT == 'opt' else 'alt+t'
    assert keys.display('toggle_thinking') == expected


def _by_action(bindings) -> dict:
    found: dict[str, set] = {}
    for entry in bindings:
        key = entry.key if hasattr(entry, 'key') else entry[0]
        action = entry.action if hasattr(entry, 'action') else entry[1]
        found.setdefault(action, set()).add(key)
    return found


def test_the_app_bindings_are_the_table():
    bound = _by_action(keys.app_bindings())
    for action, _label, _priority in keys.APP_ROWS:
        assert keys.key(action) in bound[action], action


def test_a_screen_binding_is_the_table():
    from pyclaw.tui.screens import TasksScreen

    bound = _by_action(TasksScreen.BINDINGS)
    for action in ('stop_selected', 'stop_all', 'open', 'dismiss'):
        assert keys.key(action) in bound[action], action


def test_the_rendered_hints_name_the_bound_key():
    from pyclaw.tui.theme import EXPAND_HINT, SELECT_HINT, TEAMMATE_VIEW_HINT

    assert SELECT_HINT.startswith(keys.display('agent_prev'))
    assert EXPAND_HINT.startswith(keys.display('toggle_transcript'))
    assert TEAMMATE_VIEW_HINT.startswith(keys.display('dismiss'))
