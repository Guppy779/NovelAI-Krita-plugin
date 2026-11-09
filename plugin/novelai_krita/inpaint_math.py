"""The sums behind inpainting, following what the NovelAI site does.

Plain Python on purpose (no Qt, no Krita), so it can be unit tested. Masks are
worked on in cells of 8 x 8 canvas pixels, the grid NovelAI generates on.
"""

import math

from . import api

CELL = 8
MAX_FOCUS_PIXELS = 589824  # 768 x 768: the most a focus area may cover.
FOCUS_GENERATION_PIXELS = 1048576  # A focus area is scaled up to about this.
MIN_FOCUS_CELLS = 4
CONTEXT_MIN, CONTEXT_MAX, CONTEXT_STEP = 32, 96, 8  # Canvas pixels.

_DILATE_CELLS = 4
# Two passes of a box blur of radius 20 canvas pixels (5 cells across).
_BOX = (1, 1, 1, 1, 1)
_BOX_TOTAL = float(sum(_BOX))


def _convolve(a, b):
    out = [0.0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            out[i + j] += x * y
    return out


_KERNEL = [value / (_BOX_TOTAL * _BOX_TOTAL) for value in _convolve(_BOX, _BOX)]
_REACH = len(_KERNEL) // 2


def cells_for(width, height):
    """How many cells cover a canvas of (width, height) pixels."""
    return int(math.ceil(width / float(CELL))), int(math.ceil(height / float(CELL)))


def context_cells(context):
    """Canvas pixels of context, in whole cells."""
    return int(math.ceil(context / float(CELL)))


def limit_corner(from_x, from_y, to_x, to_y):
    """The corner opposite (from_x, from_y) of a focus area, in cells, pulled
    in if the area would cover too many pixels."""
    area = abs(to_x - from_x) * abs(to_y - from_y) * CELL * CELL
    if area > MAX_FOCUS_PIXELS:
        scale = math.sqrt(MAX_FOCUS_PIXELS / float(area))
        to_x = from_x + int((to_x - from_x) * scale)
        to_y = from_y + int((to_y - from_y) * scale)
    return to_x, to_y


def inner_rect(x, y, width, height, context):
    """The part of a focus area (cells) left to inpaint once ``context``
    canvas pixels are kept around it, or None if nothing is left."""
    margin = context_cells(context)
    inner = (x + margin, y + margin, width - 2 * margin, height - 2 * margin)
    return inner if inner[2] > 0 and inner[3] > 0 else None


def moved_focus(x, y, width, height, grid_w, grid_h, context):
    """Where a focus area (cells) may be: its context can hang off the canvas,
    but the part being inpainted stays on it."""
    margin = context_cells(context)
    return (max(-margin, min(grid_w - width + margin, x)),
            max(-margin, min(grid_h - height + margin, y)))


def focus_crop(x, y, width, height, grid_w, grid_h):
    """The part of the canvas sent for a focus area (cells): (x, y, width, height).

    As on the site, an area hanging off the left or top is sent from the
    canvas edge, and is as much wider or taller as it hangs off by.
    """
    def axis(start, size, grid):
        first = max(0, start)
        return first, min(min(size, grid - start) - min(0, start), grid - first)

    left, crop_w = axis(x, width, grid_w)
    top, crop_h = axis(y, height, grid_h)
    return left, top, crop_w, crop_h


def focus_size(width, height):
    """Generation size for a focus area of (width, height) canvas pixels."""
    scale = math.sqrt(FOCUS_GENERATION_PIXELS / float(width * height))

    def side(value):
        snapped = int(value * scale) // api.SIZE_MULTIPLE * api.SIZE_MULTIPLE
        return max(api.SIZE_MULTIPLE, min(api.MAX_SIDE, snapped))

    return side(width), side(height)


def focus_position(x, y, canvas_w, canvas_h, area):
    """A character's position (0-1 of the canvas) as a position in a focus
    area of (x, y, width, height) canvas pixels, kept inside it."""
    left, top, width, height = area
    return (min(1.0, max(0.0, (x * canvas_w - left) / float(width))),
            min(1.0, max(0.0, (y * canvas_h - top) / float(height))))


def _spread_line(line, radius):
    out = bytearray(len(line))
    for index, value in enumerate(line):
        if value:
            start = max(0, index - radius)
            end = min(len(line), index + radius + 1)
            out[start:end] = b"\xff" * (end - start)
    return out


def _blur_line(line):
    if min(line) == max(line):
        return bytearray(line)
    last = len(line) - 1
    out = bytearray(len(line))
    for index in range(len(line)):
        total = 0.0
        for offset, weight in enumerate(_KERNEL):
            total += line[max(0, min(last, index + offset - _REACH))] * weight
        out[index] = int(round(min(255.0, total)))
    return out


def _both_ways(cells, width, height, transform):
    """Apply ``transform`` to every row, then to every column."""
    rows = bytearray()
    for y in range(height):
        rows += transform(cells[y * width:(y + 1) * width])
    result = bytearray(len(rows))
    for x in range(width):
        result[x::width] = transform(bytes(rows[x::width]))
    return bytes(result)


def blend_alpha(cells, width, height):
    """How much of the result to keep, per cell (0-255), from a mask of cells:
    the mask expanded by 32 canvas pixels and softened, so that the new image
    fades into the old one outside the masked area."""
    spread = _both_ways(cells, width, height, lambda line: _spread_line(line, _DILATE_CELLS))
    return _both_ways(spread, width, height, _blur_line)
