"""The welcome wordmark and the gradient painted across it.

A logo is three numbers: the colour at the start of the ramp, the colour at
the end, and the angle the ramp runs at. Vertical and horizontal sweeps are
just 90 and 0 degrees, so there is one renderer.
"""
from __future__ import annotations

import colorsys
import math
import random

WORDMARK = (
    "██████╗ ██╗   ██╗ ██████╗██╗      █████╗ ██╗    ██╗",
    "██╔══██╗╚██╗ ██╔╝██╔════╝██║     ██╔══██╗██║    ██║",
    "██████╔╝ ╚████╔╝ ██║     ██║     ███████║██║ █╗ ██║",
    "██╔═══╝   ╚██╔╝  ██║     ██║     ██╔══██║██║███╗██║",
    "██║        ██║   ╚██████╗███████╗██║  ██║╚███╔███╔╝",
    "╚═╝        ╚═╝    ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝",
)

BLUE = (0x00, 0x84, 0xE4)
YELLOW = (0xF0, 0xCC, 0x00)

MIN_SATURATION = 0.72
MIN_VALUE = 0.85
ANGLE_RANGE = (15.0, 105.0)
CELL_RATIO = 2.0

LAST_ROW = len(WORDMARK) - 1
LAST_COL = max(len(row) for row in WORDMARK) - 1


def hex_to_rgb(text: str) -> tuple:
    digits = text.lstrip('#')
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(colour: tuple) -> str:
    return '#%02X%02X%02X' % colour


def ramp(top: tuple, bottom: tuple, t: float) -> tuple:
    ht, st, vt = colorsys.rgb_to_hsv(*[c / 255 for c in top])
    hb, sb, vb = colorsys.rgb_to_hsv(*[c / 255 for c in bottom])
    arc = (hb - ht + 0.5) % 1.0 - 0.5
    h, s, v = (ht + arc * t) % 1.0, st + (sb - st) * t, vt + (vb - vt) * t
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))


def _direction(angle: float) -> tuple:
    theta = math.radians(angle)
    return (round(math.cos(theta), 9), round(math.sin(theta) * CELL_RATIO, 9))


def directional(top: tuple, bottom: tuple, angle: float) -> str:
    """The wordmark as markup, coloured by how far each cell lies along angle."""
    dx, dy = _direction(angle)
    seen = [c * dx + r * dy for c in (0, LAST_COL) for r in (0, LAST_ROW)]
    lo, hi = min(seen), max(seen)
    lines = []
    for r, row in enumerate(WORDMARK):
        cells = []
        for c, glyph in enumerate(row):
            if glyph == ' ':
                cells.append(' ')
                continue
            colour = ramp(top, bottom, (c * dx + r * dy - lo) / (hi - lo))
            cells.append('[rgb(%d,%d,%d)]%s[/]' % (*colour, glyph))
        lines.append(''.join(cells))
    return '\n'.join(lines)


def random_triple(rng: random.Random) -> tuple:
    """Two endpoints and an angle, bright enough to read on a dark background.

    Saturation and value interpolate linearly in HSV, so holding the two
    endpoints above the floors holds the whole sweep above them.
    """
    hue = rng.random()
    top = _from_hsv(hue, rng.uniform(MIN_SATURATION, 1.0),
                    rng.uniform(MIN_VALUE, 1.0))
    bottom = _from_hsv((hue + rng.uniform(0.25, 0.58)) % 1.0,
                       rng.uniform(MIN_SATURATION, 1.0),
                       rng.uniform(MIN_VALUE, 1.0))
    return top, bottom, rng.uniform(*ANGLE_RANGE)


def _from_hsv(h: float, s: float, v: float) -> tuple:
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))


def palette(cfg: dict) -> tuple:
    if cfg.get('style') == 'gradient':
        return hex_to_rgb(cfg['from']), hex_to_rgb(cfg['to']), float(cfg['angle'])
    seed = cfg.get('seed')
    return random_triple(random.Random(seed) if seed is not None else random.Random())


def brand(triple: tuple) -> str:
    top, bottom, _ = triple
    return rgb_to_hex(ramp(top, bottom, 0.5))
