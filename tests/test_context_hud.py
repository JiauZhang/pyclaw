"""Data behind the visual context meter: the model's budget and what the last
response actually consumed."""
import asyncio
import re
import tempfile

from chatchat.client import MockClient
from chatchat.team import Team

from pyclaw import agents, banner, config
from pyclaw.tui import (CONTEXT_METER_CELLS, METER_TRACK_LIGHTNESS,
                        context_meter, git_label, usage_hud, usage_meter)

from fakes import Usage
from markup import plain as _plain

TOP = (0x00, 0x84, 0xE4)
BOTTOM = (0xF0, 0xCC, 0x00)
TRIPLE = (TOP, BOTTOM, 60.0)


def _hex(markup: str) -> str:
    return re.search(r'\[(#[0-9A-F]{6})\]', markup).group(1)


def _track_hex(markup: str) -> str:
    return re.search(r'\[on (#[0-9A-F]{6})\]', markup).group(1)


def _team(usage=None):
    async def respond(messages, tools=None, *, stream_cb=None):
        return 'ok'
    return Team('hud', client_factory=lambda inst, model=None: MockClient(
        handler=respond, usage=usage))


def _session(usage=None):
    return agents.Session(_team(usage), session_id='hud')


def test_context_window_comes_from_config(monkeypatch):
    monkeypatch.setattr(config, 'load', lambda: {'contextWindow': 128_000})

    async def main():
        return _session().context_window

    assert asyncio.run(main()) == 128_000


def test_a_built_team_compacts_at_the_configured_window(monkeypatch):
    monkeypatch.setattr(config, 'load', lambda: {'contextWindow': 128_000})

    async def main():
        with tempfile.TemporaryDirectory() as d:
            return agents.build_team('agnes', 'agnes-2.5-flash', cwd=d)

    team = asyncio.run(main())
    assert team.context_window == 128_000
    assert team.auto_compact


def test_context_window_defaults_to_two_hundred_thousand(isolated_config):
    assert isolated_config.load()['contextWindow'] == 200_000


def test_used_context_is_the_last_responses_input_plus_output():
    """`prompt_tokens` already contains the cached read, so adding it again
    would overstate how full the window is."""
    usage = {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120,
             'prompt_tokens_details': {'cached_tokens': 80}}

    async def main():
        session = _session(usage)
        await session.chat('hi')
        return session.used_context

    assert asyncio.run(main()) == 120


def test_used_context_is_zero_before_the_first_response():
    async def main():
        return _session().used_context

    assert asyncio.run(main()) == 0


def test_meter_is_blank_without_a_window():
    assert context_meter(TRIPLE, 5_000, 0) == ''


def test_meter_cells_track_the_ratio_the_window_and_the_width():
    for used, window, cells, expected in (
            (34_000, 100_000, CONTEXT_METER_CELLS, '│███░░░░░░░│ 34%'),
            (50_000, 100_000, CONTEXT_METER_CELLS, '│█████░░░░░│ 50%'),
            (0, 100_000, CONTEXT_METER_CELLS, '│░░░░░░░░░░│ 0%'),
            (250_000, 100_000, CONTEXT_METER_CELLS, '│██████████│ 100%'),
            (40_000, 100_000, 5, '│██░░░│ 40%')):
        assert _plain(context_meter(TRIPLE, used, window,
                                    cells=cells)) == expected


def test_the_empty_part_of_the_meter_sits_on_a_theme_tinted_track():
    """On a black terminal the bar has to be visible before anything of it is
    filled, so the unfilled cells get a darkened stop of the same palette."""
    markup = context_meter(TRIPLE, 0, 100_000)
    assert _track_hex(markup) == banner.dimmed(TRIPLE, METER_TRACK_LIGHTNESS)
    assert _track_hex(context_meter(TRIPLE, 34_000, 100_000)) == \
        banner.dimmed(TRIPLE, METER_TRACK_LIGHTNESS)


def test_meter_is_painted_at_the_logo_colour_for_its_fill():
    """The bar's colour is the point on the logo gradient it has reached, so
    it moves continuously as the window fills."""
    assert _hex(context_meter(TRIPLE, 50_000, 100_000)) == \
        banner.rgb_to_hex(banner.ramp(TOP, BOTTOM, 0.5))
    assert _hex(context_meter(TRIPLE, 80_000, 100_000)) == \
        banner.rgb_to_hex(banner.ramp(TOP, BOTTOM, 0.8))


def _segments(markup: str) -> list:
    """The (colour, glyph-run) pairs a bar is built from."""
    return re.findall(r'\[(#[0-9A-F]{6})\]([^[]*)\[/\]', markup)


def test_usage_meter_shows_the_empty_track_before_the_first_response():
    """The bar is on the row from the start, so the row never grows a widget
    half way through a session."""
    markup = usage_meter(TRIPLE, Usage())
    assert _plain(markup) == '│░░░░░░░░░░│'
    assert _track_hex(markup) == banner.dimmed(TRIPLE, METER_TRACK_LIGHTNESS)
    assert usage_meter(TRIPLE, Usage(prompt=10, completion=5, total=15),
                       cells=0) == ''


def test_usage_meter_is_one_bar_of_three_kinds_of_token():
    """Cached reads, input that had to be sent again, and the reply, each with
    its own glyph so the bar reads without colour too."""
    markup = usage_meter(TRIPLE, Usage(prompt=1901, completion=500,
                                        total=2401, cached=1200))
    assert _segments(markup)[:3] == [
        (banner.rgb_to_hex(banner.ramp(TOP, BOTTOM, 0)), '\u2592' * 5),
        (banner.rgb_to_hex(banner.ramp(TOP, BOTTOM, 0.5)), '\u2593' * 3),
        (banner.rgb_to_hex(banner.ramp(TOP, BOTTOM, 1.0)), '\u2588' * 2),
    ]
    assert _plain(markup) == '│▒▒▒▒▒▓▓▓██│'


def test_usage_meter_gives_a_cell_to_every_kind_that_exists():
    """A 20-token reply inside a 200k turn would round away; it keeps a cell by
    taking one from the widest span, so the bar still adds up to its width."""
    markup = usage_meter(TRIPLE, Usage(prompt=200_000, completion=20,
                                        total=200_020, cached=100_000))
    assert _plain(markup) == '│▒▒▒▒▓▓▓▓▓█│'


def test_usage_hud_lists_the_numbers_the_bar_is_made_of():
    """`cache` is the hit rate, not an absolute count, and every token figure
    compacts to its own unit."""
    for usage, expected in (
            (Usage(prompt=61_200, completion=3_400, total=64_600, cached=48_000),
             'in: 61.2k  out: 3.4k  cache: 78%  total: 64.6k'),
            (Usage(), 'in: 0  out: 0  cache: 0%  total: 0'),
            (Usage(prompt=1_000, completion=20, total=1_020),
             'in: 1k  out: 20  cache: 0%  total: 1k')):
        assert usage_hud(usage) == expected


def test_git_label_names_the_branch_and_the_dirty_files():
    status = '\n'.join([
        '# branch.head main',
        '# branch.upstream origin/main',
        '# branch.ab +0 -0',
        '1 N... 100644 100644 100644 0 0 pyclaw/tui.py',
        '? tests/test_context_hud.py',
    ])
    assert git_label(status) == 'main \u00b12'


def test_git_label_keeps_a_clean_tree_short():
    assert git_label('# branch.head main\n') == 'main'
    assert git_label('# branch.head (detached)\n') == '(detached)'


def test_git_label_is_blank_when_git_says_nothing_useful():
    assert git_label('') == ''
    assert git_label('# branch.oid abc\n') == ''
