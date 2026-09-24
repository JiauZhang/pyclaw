"""When a long turn ends while you have looked away, the terminal should say so."""
import pytest

from pyclaw import notify
from pyclaw.notify import BACKENDS, detect, escape_for, message


def test_auto_follows_the_terminal_that_is_running():
    assert detect({'TERM_PROGRAM': 'iTerm.app'}) == 'iterm2'
    assert detect({'TERM_PROGRAM': 'kitty'}) == 'kitty'
    assert detect({'TERM_PROGRAM': 'ghostty'}) == 'ghostty'
    assert detect({'TERM_PROGRAM': 'Apple_Terminal'}) == 'bell'
    assert detect({}) == 'bell'


def test_an_explicit_choice_is_honoured():
    assert detect({'TERM_PROGRAM': 'kitty',
                   'PYCLAW_NOTIFICATIONS': 'iterm2'}) == 'iterm2'
    assert detect({'PYCLAW_NOTIFICATIONS': 'disabled'}) == 'disabled'


def test_an_unknown_backend_is_refused():
    assert set(BACKENDS) == {'auto', 'bell', 'iterm2', 'kitty', 'ghostty',
                             'disabled'}
    with pytest.raises(ValueError):
        detect({'PYCLAW_NOTIFICATIONS': ' carrier-pigeon'})


def test_each_terminal_gets_its_own_sequence():
    assert escape_for('bell', 'PyClaw', 'done thinking') == (
        '\033]0;PyClaw\007\007')
    assert escape_for('iterm2', 'PyClaw', 'done thinking') == (
        '\033]0;PyClaw\007\033]9;done thinking\007')
    assert escape_for('kitty', 'PyClaw', 'done thinking') == (
        '\033]0;PyClaw\007\033]99;d=done thinking;t=PyClaw\033\\')
    assert escape_for('ghostty', 'PyClaw', 'done thinking') == (
        '\033]0;PyClaw\007\033]777;notify;PyClaw;done thinking\033\\')
    assert escape_for('disabled', 'PyClaw', 'done thinking') == (
        '\033]0;PyClaw\007')


def test_a_title_never_carries_a_newline_or_a_control_character():
    sequence = escape_for('iterm2', 'two\nlines\x07', 'ok')
    assert '\n' not in sequence and sequence.count('\007') == 2


def test_the_writer_is_the_only_way_out():
    sent = []
    escape = notify.notify(sent.append, title='PyClaw', body='done',
                           env={'TERM_PROGRAM': 'kitty'})
    assert sent == [escape]
    sent.clear()
    notify.notify(sent.append, title='PyClaw', body='done',
                  backend='disabled', env={})
    assert sent == ['\033]0;PyClaw\007']
    sent.clear()
    assert notify.set_title(sent.append, 'PyClaw \u00b7 working') == (
        '\033]0;PyClaw \u00b7 working\007')
    assert sent == ['\033]0;PyClaw \u00b7 working\007']


def test_the_message_says_what_finished():
    assert message('reply', 8) == 'PyClaw has an answer for you'
    assert message('', 8) == 'PyClaw is waiting for you'
    assert message('', 0) == 'PyClaw is waiting for you'
