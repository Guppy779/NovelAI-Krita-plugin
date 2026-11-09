"""Where a prompt's tags are weighted, to colour them as the NovelAI site does.

Plain Python on purpose (no Qt, no Krita), so it can be unit tested.

``{`` and ``]`` multiply the weight by 1.05, ``}`` and ``[`` divide it. On V4
and newer, ``1.2::`` sets the weight and a bare ``::`` puts it back to 1. A
weight carries on past the end of a line.
"""

import re
from dataclasses import dataclass
from typing import List, Tuple

HIGH, MID, LOW = "high", "mid", "low"

_STEP = 1.05
_FULL_STRENGTH = 2.0  # This far from 1, the highlight is at its strongest.
_MIN_OPACITY, _MAX_OPACITY = 0.2, 0.6
_MARKER_OPACITY = 0.5
_FULLY_LOW = 0.5  # Only used for zero and negative weights, as on the site.
_NEUTRAL = 0.01  # Closer to 1 than this counts as no weight at all.

_NUMBER_BEFORE = re.compile(r"-?[0-9]*\.?[0-9]*$")
_NOT_A_NUMBER = ("-", "-.", ".")  # Mean a weight of 0.

_STATE_LIMIT = 20000
_STATE_SCALE = 10000


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    color: str
    opacity: float


def _color(weight):
    if abs(weight - 1) < _NEUTRAL:
        return None
    return HIGH if weight > 1 else LOW


def _opacity(weight):
    full = _FULL_STRENGTH - 1 if weight > 0 else 1 - _FULLY_LOW
    amount = max(min(abs(weight - 1) / full, 1), 0)
    return _MIN_OPACITY + (_MAX_OPACITY - _MIN_OPACITY) * amount


def spans(line: str, weight: float = 1.0, numeric: bool = True) -> Tuple[List[Span], float]:
    """The coloured parts of one line, and the weight at its end.

    ``weight`` is the weight the line starts with. ``numeric``: the model
    understands ``1.2::`` weights (V4 and newer).
    """
    found = []
    start = 0

    def finish(end):
        color = _color(weight)
        if color is not None and start < end:
            found.append(Span(start, end, color, _opacity(weight)))

    index = 0
    while index < len(line):
        char = line[index]
        if char in "{}[]":
            finish(index)
            start = index
            weight = weight * _STEP if char in "{]" else weight / _STEP
            index += 1
        elif numeric and line.startswith("::", index):
            text = _NUMBER_BEFORE.search(line[:index]).group()
            value = 0.0 if text in _NOT_A_NUMBER else float(text) if text else 1.0
            first = index - len(text)
            finish(first)
            index += 2
            weight = value
            if value == 1:  # The end of a weighted part: only the marker is coloured.
                found.append(Span(first, index, MID, _MARKER_OPACITY))
                start = index
            else:
                start = first
        else:
            index += 1
    finish(len(line))
    return found, weight


def to_state(weight: float) -> int:
    """The weight as a non-negative int, to keep with a line of text."""
    limited = max(-_STATE_LIMIT, min(_STATE_LIMIT, weight))
    return int(round((limited + _STATE_LIMIT) * _STATE_SCALE))


def from_state(state: int) -> float:
    """The weight stored by ``to_state``. A negative state means none: 1."""
    return 1.0 if state < 0 else state / float(_STATE_SCALE) - _STATE_LIMIT
