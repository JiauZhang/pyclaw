import colorsys
import random
import re

from pyclaw import banner

SPANS = re.compile(r"\[rgb\((\d+),(\d+),(\d+)\)\](.)\[/\]")
CELLS = sum(1 for row in banner.WORDMARK for ch in row if ch != " ")


def _hsv(c):
    return colorsys.rgb_to_hsv(*[v / 255 for v in c])


def _colours(markup: str) -> list[tuple]:
    return [tuple(int(v) for v in m.group(1, 2, 3)) for m in SPANS.finditer(markup)]


def test_the_wordmark_is_six_rows_of_pinned_shape():
    assert len(banner.WORDMARK) == 6
    assert banner.WORDMARK[0] == (
        "██████╗ ██╗   ██╗ ██████╗██╗      █████╗ ██╗    ██╗")
    assert [len(row) for row in banner.WORDMARK] == [51, 51, 51, 51, 51, 50]


def test_the_wordmark_only_holds_glyphs_the_renderer_paints():
    used = {ch for row in banner.WORDMARK for ch in row} - {" "}
    assert used == set("█╔═╗║╚╝")


def test_the_ramp_ends_exactly_on_the_two_colours():
    assert banner.ramp(banner.BLUE, banner.YELLOW, 0.0) == banner.BLUE
    assert banner.ramp(banner.BLUE, banner.YELLOW, 1.0) == banner.YELLOW


def test_the_hue_takes_the_short_way_round_the_wheel():
    middle = banner.ramp((0xFF, 0x00, 0x00), (0xFF, 0x00, 0xFF), 0.5)
    assert middle[1] == 0
    assert _hsv(middle)[0] > 0.83


def test_the_ramp_never_dips_below_its_own_endpoints():
    lowest = min(min(_hsv(c)[1:]) for c in (banner.BLUE, banner.YELLOW))
    steps = [banner.ramp(banner.BLUE, banner.YELLOW, i / 20) for i in range(21)]
    assert all(min(_hsv(c)[1:]) >= lowest - 0.01 for c in steps)


def test_a_vertical_angle_gives_each_row_one_colour():
    rows = banner.directional(banner.BLUE, banner.YELLOW, 90.0).split("\n")
    per_row = [{c for c in _colours(row)} for row in rows]
    assert all(len(colours) == 1 for colours in per_row)
    assert len({next(iter(colours)) for colours in per_row}) == len(rows)
    assert next(iter(per_row[0])) == banner.BLUE


def test_a_horizontal_angle_runs_from_the_first_cell_to_the_last():
    markup = banner.directional(banner.BLUE, banner.YELLOW, 0.0)
    # Row 0 spans the full width, so its own ends are the extremes of the ramp.
    cells = _colours(markup.split("\n")[0])
    assert cells[0] == banner.BLUE
    assert cells[-1] == banner.YELLOW


def test_the_markup_paints_every_cell_and_nothing_else():
    markup = banner.directional(banner.BLUE, banner.YELLOW, 60.0)
    assert len(markup.split("\n")) == 6
    assert len(_colours(markup)) == CELLS
    assert "#D77757" not in markup


def test_a_random_triple_is_reproducible_and_stays_bright():
    top, bottom, angle = banner.random_triple(random.Random(7))
    assert (top, bottom, angle) == banner.random_triple(random.Random(7))
    for colour in (top, bottom):
        _, s, v = _hsv(colour)
        assert s >= banner.MIN_SATURATION
        assert v >= banner.MIN_VALUE
    assert banner.ANGLE_RANGE[0] <= angle <= banner.ANGLE_RANGE[1]
    assert banner.random_triple(random.Random(8)) != (top, bottom, angle)


def test_a_gradient_style_uses_the_configured_colours_verbatim():
    cfg = {"style": "gradient", "from": "#123456", "to": "#654321",
           "angle": 30.0, "seed": None}
    assert banner.palette(cfg) == ((0x12, 0x34, 0x56), (0x65, 0x43, 0x21), 30.0)


def test_a_dark_gradient_style_is_not_quietly_brightened():
    cfg = {"style": "gradient", "from": "#0A0A14", "to": "#140A0A",
           "angle": 45.0, "seed": None}
    top, bottom, _ = banner.palette(cfg)
    middle = banner.ramp(top, bottom, 0.5)
    assert _hsv(middle)[2] <= max(_hsv(top)[2], _hsv(bottom)[2]) + 0.01
    assert _hsv(middle)[2] < 0.1


def test_a_random_style_is_pinned_by_its_seed():
    cfg = {"style": "random", "from": "#0084E4", "to": "#F0CC00",
           "angle": 60.0, "seed": 42}
    assert banner.palette(cfg) == banner.palette(dict(cfg, seed=42))
    assert banner.palette(cfg) != banner.palette(dict(cfg, seed=43))


def test_a_random_style_without_a_seed_changes_every_time():
    cfg = {"style": "random", "from": "#0084E4", "to": "#F0CC00",
           "angle": 60.0, "seed": None}
    assert banner.palette(cfg) != banner.palette(cfg)


def test_the_brand_colour_is_the_middle_of_the_logo():
    triple = (banner.BLUE, banner.YELLOW, 60.0)
    assert banner.brand(triple) == "#%02X%02X%02X" % banner.ramp(
        banner.BLUE, banner.YELLOW, 0.5)


def test_dimming_puts_the_brightest_channel_at_the_requested_lightness():
    triple = (banner.BLUE, banner.YELLOW, 60.0)
    mid = banner.ramp(banner.BLUE, banner.YELLOW, 0.5)
    assert banner.dimmed(triple, 55) == banner.rgb_to_hex(
        tuple(round(c * 55 / max(mid)) for c in mid))
    assert max(banner.hex_to_rgb(banner.dimmed(triple, 55))) == 55


def test_dimmed_paint_keeps_the_hue_and_the_contrast_budget():
    import random
    rng = random.Random(7)
    for _ in range(200):
        for lightness in (banner.BAND_LIGHTNESS, banner.RULE_LIGHTNESS):
            paint = banner.hex_to_rgb(
                banner.dimmed(banner.random_triple(rng), lightness))
            assert max(paint) == lightness
            assert len(set(paint)) > 1


def test_the_config_defaults_start_with_a_random_palette(tmp_path):
    from pyclaw import config
    real = config.__config_file__
    config.__config_file__ = tmp_path / "config.json"
    try:
        config.reload()
        assert config.load()["banner"]["style"] == "random"
    finally:
        config.__config_file__ = real
        config.reload()


def test_the_config_defaults_start_with_a_random_palette(tmp_path):
    from pyclaw import config
    real = config.__config_file__
    config.__config_file__ = tmp_path / "config.json"
    try:
        config.reload()
        cfg = config.load()["banner"]
        assert cfg["style"] == "random"
        assert cfg["seed"] is None
    finally:
        config.__config_file__ = real
        config.reload()
