"""Inpainting masks: what's been painted, and what it means for a generation.

A mask is an 8-bit image with one pixel per cell of 8 x 8 canvas pixels. White
cells are regenerated. See inpaint_math.py for the sums.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from . import api, inpaint_math as im
from .qt import QColor, QImage, QPainter, Qt, image_bytes

_GRAY = QImage.Format.Format_Grayscale8
_ANY_TO_WHITE = bytes([0] + [255] * 255)  # A cell counts if any of it is painted.


def _tight(image):
    """The pixel bytes of an 8-bit image, without the padding after each row."""
    raw, width, line = image_bytes(image), image.width(), image.bytesPerLine()
    if width == line:
        return raw
    return b"".join(raw[y * line:y * line + width] for y in range(image.height()))


def _from_tight(data, width, height):
    return QImage(data, width, height, width, _GRAY).copy()


def _white_or_black(image):
    return _from_tight(_tight(image).translate(_ANY_TO_WHITE), image.width(), image.height())


def as_alpha(image):
    """An 8-bit grayscale image as an alpha channel: white is opaque."""
    # Reinterpret the bytes. (convertToFormat would treat grayscale as a
    # colour, and give a fully opaque image.)
    return QImage(image_bytes(image), image.width(), image.height(), image.bytesPerLine(),
                  QImage.Format.Format_Alpha8).copy()


def _scaled(image, width, height, smooth):
    mode = (Qt.TransformationMode.SmoothTransformation if smooth
            else Qt.TransformationMode.FastTransformation)
    return image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                        mode).convertToFormat(_GRAY)


def blank_cells(canvas_width, canvas_height):
    cells = QImage(*im.cells_for(canvas_width, canvas_height), _GRAY)
    cells.fill(0)
    return cells


def cells_from_pixels(image):
    """Mask cells from a grayscale image the size of the canvas (a selection)."""
    width, height = im.cells_for(image.width(), image.height())
    return _white_or_black(_scaled(image, width, height, True))


def is_painted(cells):
    return any(_tight(cells))


@dataclass
class Mask:
    cells: QImage
    canvas_size: Tuple[int, int]
    focus: Optional[Tuple[int, int, int, int]] = None  # x, y, width, height in cells.
    context: int = im.CONTEXT_MAX  # Canvas pixels kept around the focused part.


@dataclass
class Plan:
    """What a generation sends for a mask."""

    area: Tuple[int, int, int, int]  # x, y, width, height of the canvas to send.
    size: Tuple[int, int]  # The generation's width and height.
    cells: QImage  # The mask of ``area``, one pixel per cell of the generation.


def effective_cells(mask):
    """The cells that will really be regenerated.

    With a focus area, only its part inside the context counts. If nothing is
    painted there, all of that part is regenerated.
    """
    if mask.focus is None:
        return mask.cells
    result = QImage(mask.cells.width(), mask.cells.height(), _GRAY)
    result.fill(0)
    inner = im.inner_rect(*mask.focus, mask.context)
    if inner is None:
        return result
    part = mask.cells.copy(*inner)
    painter = QPainter(result)
    if is_painted(part):
        painter.drawImage(inner[0], inner[1], part)
    else:
        painter.fillRect(*inner, QColor(255, 255, 255))
    painter.end()
    return result


def _focus_crop(mask):
    return im.focus_crop(*mask.focus, mask.cells.width(), mask.cells.height())


def area_of(mask):
    """The (x, y, width, height) of the canvas that gets sent."""
    canvas_width, canvas_height = mask.canvas_size
    if mask.focus is None:
        return 0, 0, canvas_width, canvas_height
    x, y, width, height = (value * im.CELL for value in _focus_crop(mask))
    return x, y, min(width, canvas_width - x), min(height, canvas_height - y)


def _size_for(mask, area, max_pixels):
    _, _, width, height = area
    if mask.focus is not None:
        return im.focus_size(width, height)
    return api.fit_resolution(width, height, max_pixels, upscale=False)


def generation_size(mask, max_pixels):
    """Focus areas are scaled up; the whole canvas goes at its own size if it can."""
    return _size_for(mask, area_of(mask), max_pixels)


def plan(mask, max_pixels):
    """What to send for ``mask``, or None if it regenerates nothing."""
    cells = effective_cells(mask)
    if not is_painted(cells):
        return None
    if mask.focus is not None:
        cells = cells.copy(*_focus_crop(mask))
    area = area_of(mask)
    width, height = _size_for(mask, area, max_pixels)
    grid_w, grid_h = width // im.CELL, height // im.CELL
    if (grid_w, grid_h) != (cells.width(), cells.height()):
        if grid_w < cells.width() or grid_h < cells.height():
            cells = _white_or_black(_scaled(cells, grid_w, grid_h, True))
        else:
            cells = _scaled(cells, grid_w, grid_h, False)
    return Plan(area, (width, height), cells)


def request_mask(a_plan):
    """The mask image for the request: white is regenerated."""
    return _scaled(a_plan.cells, a_plan.size[0], a_plan.size[1], False)


def blend_alpha(a_plan):
    """Grayscale image of how much of the result to keep, one pixel per cell."""
    width, height = a_plan.cells.width(), a_plan.cells.height()
    return _from_tight(im.blend_alpha(_tight(a_plan.cells), width, height), width, height)
