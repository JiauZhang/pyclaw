"""The dialog shapes agree across screens because they come from here."""
from pyclaw.tui import ui
from pyclaw.tui.theme import POINTER


def test_a_heading_carries_its_value():
    assert ui.heading('Background tasks') == ['[bold]Background tasks[/]']
    assert ui.heading('Permission mode', 'plan') == \
        [f'[bold]Permission mode[/] [{ui.SELECTED}]plan[/]']


def test_an_aside_is_dim():
    assert ui.aside('nothing running') == '[dim]nothing running[/]'


def test_a_selected_row_is_marked_and_tinted():
    assert ui.row('worker', selected=True) == \
        f'[{ui.SELECTED}]{POINTER} worker[/]'


def test_an_unselected_row_is_indented_and_dim():
    assert ui.row('worker', selected=False) == '  [dim]worker[/]'


def test_rows_mark_exactly_the_selected_one():
    lines = ui.rows(['one', 'two', 'three'], 1)
    assert [POINTER in line for line in lines] == [False, True, False]


def test_a_marked_row_keeps_its_own_colors():
    assert ui.marked('[bold]x[/]', selected=False) == '[bold]x[/]'
    assert ui.marked('[bold]x[/]', selected=True) == \
        f'[{ui.SELECTED}][bold]x[/][/]'


def test_a_footer_is_a_gap_and_the_hints():
    assert ui.footer(('dismiss', 'closes')) == ['', '[dim]esc closes[/]']
    assert ui.footer() == []


def test_the_selection_follows_the_theme():
    assert ui.SELECTED == '$brand'


def test_text_from_outside_can_never_open_a_tag():
    """Textual starts a tag at any bracket that is not already escaped, so the
    escaping layer has to cover every bracket and not just the tag-shaped
    ones: a quoted list in tool output used to swallow the markup after it."""
    from textual.content import Content
    from pyclaw.tui.formatting import escape
    for raw in ['Git -> ["ssh -i \'/tmp/k\' -o IdentitiesOnly=yes"]',
                'a [/bold] b [bold]c[/] d',
                'see [the README] for @[user] and $brand']:
        assert str(Content.from_markup(escape(raw))) == raw
