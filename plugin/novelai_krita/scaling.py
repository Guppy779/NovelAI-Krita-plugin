"""Scaling a picture exactly as the NovelAI site does.

The site scales with the pica library's Lanczos3 filter (version 9.0.1).
NovelAI draws a slightly different image from a reference that is a shade
off here and there, so Krita's own Lanczos scaling isn't close enough. This
is pica's arithmetic, number for number: its fixed point weights, its
rounding and its handling of transparency. The other steps the site takes
with a reference are here too: taking NovelAI's hidden data out of the alpha
channel, and putting the picture on black.

Plain Python, no Qt, and no loop over pixels, which would take minutes. A
whole row or column is one big integer with a few bytes (a "lane") for each
value, so adding rows and multiplying them by a weight happens in C.

Pictures are bytes of RGBA, four for each pixel, rows from the top, with
colours that aren't premultiplied by alpha.
"""

import math
import re
import struct
import sys
from array import array
from operator import mul
from typing import Callable, List, Tuple

MAX_CLEANED = 0x1000000  # Pixels. Larger pictures keep hidden data, as on the site.
_MAGIC = b"stealth_pngcomp"
_WITHOUT_HIDDEN = bytes(255 if value == 254 else 0 if value == 1 else value
                        for value in range(256))
_NOT_CLEAR = bytes([0]) + bytes([255]) * 255
_PARTLY_CLEAR = re.compile(rb"[\x01-\xfe]")

Filters = List[Tuple[int, List[int]]]


# ---- The weights -------------------------------------------------------------

def _lanczos3(x: float) -> float:
    if x < 0:
        x = -x
    if x >= 3.0:
        return 0.0
    if x < 1.1920929e-07:
        return 1.0
    xpi = x * math.pi
    return math.sin(xpi) / xpi * math.sin(xpi / 3.0) / (xpi / 3.0)


def _fixed(value: float) -> int:
    return math.floor(value * 16383 + 0.5)  # 14 bits, halves up.


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def filters(size: int, to_size: int) -> Filters:
    """For each pixel along the new size: the first old pixel that counts
    towards it, and the weights of that one and the ones after it."""
    scale = to_size / size
    clamped = min(1.0, scale)  # Enlarging uses the filter as it is.
    window = 3.0 / clamped
    result = []
    for pixel in range(to_size):
        centre = (pixel + 0.5) * (1.0 / scale)
        first = max(0, math.floor(centre - window))
        last = min(size - 1, math.ceil(centre + window))
        values, total = [], 0.0
        for old in range(first, last + 1):
            value = _lanczos3((old + 0.5 - centre) * clamped)
            total += value
            values.append(_float32(value))  # pica keeps them as 32 bit floats.
        weights, weights_total = [], 0.0
        for value in values:
            value = value / total
            weights_total += value
            weights.append(_fixed(value))
        middle = to_size >> 1
        if middle < len(weights):  # pica means the middle weight; this is what it does.
            wrapped = weights[middle] + _fixed(1.0 - weights_total)
            weights[middle] = (wrapped + 0x8000 & 0xFFFF) - 0x8000
        start, end = 0, len(weights) - 1
        while start <= end and weights[start] == 0:
            start += 1
        while end > start and weights[end] == 0:
            end -= 1
        result.append((first + start, weights[start:end + 1]))
    return result


# ---- Many values in one integer ----------------------------------------------

class _Lanes:
    """``count`` values side by side in one integer, ``size`` bytes for each."""

    def __init__(self, count: int, size: int):
        self.size = size
        self.length = count * size
        self.ones = int.from_bytes((b"\x01" + bytes(size - 1)) * count, "little")

    def each(self, number: int) -> int:
        return self.ones * number

    def pack(self, low: bytes, high: bytes = b"") -> int:
        """The values whose lowest bytes are ``low`` and next bytes ``high``."""
        wide = bytearray(self.length)
        wide[0::self.size] = low
        if high:
            wide[1::self.size] = high
        return int.from_bytes(wide, "little")

    def unpack(self, value: int) -> bytes:
        return value.to_bytes(self.length, "little")


class _Lines:
    """Rows or columns as integers, made when first needed and dropped once
    the filter has moved past them."""

    def __init__(self, make: Callable[[int], int]):
        self._make = make
        self._kept = {}

    def weighted(self, first: int, weights: List[int], bias: int) -> int:
        """The sum of the lines from ``first`` times their weights, plus
        ``bias`` in each lane to keep every lane above zero."""
        for index in [index for index in self._kept if index < first]:
            del self._kept[index]
        plus = minus = 0
        for index, weight in enumerate(weights, first):
            if weight:
                line = self._kept.get(index)
                if line is None:
                    line = self._kept[index] = self._make(index)
                if weight > 0:
                    plus += line * weight
                else:
                    minus += line * -weight
        return plus + bias - minus


def _not_below_zero(lanes: _Lanes, total: int) -> int:
    """``total >> 7`` in each lane, or 0 where that's negative. ``total``
    carries a bias of 2**38."""
    shifted = (total >> 7) & lanes.each((1 << 33) - 1)  # The bias is now 2**31.
    return shifted & (((shifted >> 31) & lanes.ones) * 0x7FFFFFFF)


# ---- Across, then down -------------------------------------------------------
#
# The picture is first scaled across into "middle": two bytes for each value,
# four values for each pixel, rows of the old height and the new width.

def _across(data: bytes, width: int, height: int, to_width: int, weights: Filters) -> bytearray:
    lanes = _Lanes(height * 4, 5)
    bias = lanes.each(1 << 38)
    stride = width * 4

    def column(x):
        values = bytearray(height * 4)
        for channel in range(4):
            values[channel::4] = data[x * 4 + channel::stride]
        return lanes.pack(values)

    columns = _Lines(column)
    row = to_width * 8
    middle = bytearray(height * row)
    for x, (first, line) in enumerate(weights):
        raw = lanes.unpack(_not_below_zero(lanes, columns.weighted(first, line, bias)))
        for channel in range(4):
            middle[x * 8 + channel * 2::row] = raw[channel * 5::20]
            middle[x * 8 + channel * 2 + 1::row] = raw[channel * 5 + 1::20]
    return middle


_BY_32640 = -(-(1 << 60) // 32640)  # Times this and >> 60 divides by 255 * 128.


def _across_with_alpha(data: bytes, width: int, height: int, to_width: int,
                       weights: Filters) -> bytearray:
    """As _across, for a picture with transparency: colours are multiplied by
    their alpha before they're added up."""
    alpha = data[3::4]
    planes = []
    for channel in range(3):
        plane = array("H", map(mul, data[channel::4], alpha))
        if sys.byteorder == "big":
            plane.byteswap()
        planes.append(plane.tobytes())
    colours, alphas = _Lanes(height * 3, 12), _Lanes(height, 5)
    colour_bias, alpha_bias = colours.each(32640 << 24), alphas.each(1 << 38)
    keep, positive = colours.each((1 << 36) - 1), (1 << 24) - 1

    def colour_column(x):
        low, high = bytearray(height * 3), bytearray(height * 3)
        for channel in range(3):
            low[channel::3] = planes[channel][x * 2::width * 2]
            high[channel::3] = planes[channel][x * 2 + 1::width * 2]
        return colours.pack(low, high)

    colour_columns = _Lines(colour_column)
    alpha_columns = _Lines(lambda x: alphas.pack(alpha[x::width]))
    row = to_width * 8
    middle = bytearray(height * row)
    for x, (first, line) in enumerate(weights):
        # pica: (sum / 255 | 0) >> 7, then 0 if negative. Dividing by 255 * 128
        # and rounding down gives the same once negatives are 0.
        total = colour_columns.weighted(first, line, colour_bias)
        divided = ((total * _BY_32640) >> 60) & keep  # The bias is now 2**24.
        value = divided & (((divided >> 24) & colours.ones) * positive)
        raw = colours.unpack(value)
        for channel in range(3):
            middle[x * 8 + channel * 2::row] = raw[channel * 12::36]
            middle[x * 8 + channel * 2 + 1::row] = raw[channel * 12 + 1::36]
        raw = alphas.unpack(_not_below_zero(alphas, alpha_columns.weighted(first, line, alpha_bias)))
        middle[x * 8 + 6::row] = raw[0::5]
        middle[x * 8 + 7::row] = raw[1::5]
    return middle


def _down(middle: bytearray, to_width: int, to_height: int, weights: Filters,
          opaque: bool) -> bytes:
    lanes = _Lanes(to_width * 4, 5)
    bias, half = lanes.each(1 << 38), lanes.each(8192)
    keep_33, keep_26 = lanes.each((1 << 33) - 1), lanes.each((1 << 26) - 1)
    all_17, to_255 = lanes.each(0x1FFFF), lanes.each(0x20000 - 256)
    row = to_width * 8

    def middle_row(y):
        part = middle[y * row:(y + 1) * row]
        return lanes.pack(part[0::2], part[1::2])

    rows = _Lines(middle_row)
    result = bytearray(to_width * to_height * 4)
    solid = b"\xff" * to_width
    for y, (first, line) in enumerate(weights):
        # pica: sum >> 7, then (that + 8192) >> 14, kept between 0 and 255.
        once = (rows.weighted(first, line, bias) >> 7) & keep_33  # The bias is now 2**31.
        twice = ((once + half) >> 14) & keep_26  # And now 2**17.
        low = twice & (((twice >> 17) & lanes.ones) * 0x1FFFF)  # 0 where negative.
        over = ((low + to_255) >> 17) & lanes.ones  # 1 where above 255.
        pixels = bytearray(lanes.unpack((low & (all_17 ^ (over * 0x1FFFF))) | (over * 255))[0::5])
        if opaque:
            pixels[3::4] = solid
        else:
            _divide_by_alpha(pixels, lanes.unpack(once))
        result[y * to_width * 4:(y + 1) * to_width * 4] = pixels
    return bytes(result)


def _divide_by_alpha(pixels: bytearray, once: bytes) -> None:
    """Colours were multiplied by alpha: divide the ones of partly clear pixels
    by it again. With alpha 255 that changes nothing, and with 0 pica doesn't."""
    alpha = pixels[3::4]
    for found in _PARTLY_CLEAR.finditer(alpha):
        pixel = found.start()
        for at in range(pixel * 4, pixel * 4 + 3):
            value = int.from_bytes(once[at * 5:at * 5 + 5], "little") - (1 << 31)
            value = int(255 * value / alpha[pixel])
            pixels[at] = min(255, max(0, (value + 8192) >> 14))


def resize(data: bytes, width: int, height: int, to_width: int, to_height: int) -> bytes:
    """The picture at another size, as pica's Lanczos3 filter gives it."""
    to_width, to_height = max(1, to_width), max(1, to_height)
    if (to_width, to_height) == (width, height):
        return bytes(data)
    data = bytes(data)
    opaque = data[3::4].count(255) == width * height
    across = _across if opaque else _across_with_alpha
    middle = across(data, width, height, to_width, filters(width, to_width))
    return _down(middle, to_width, to_height, filters(height, to_height), opaque)


# ---- The site's other steps with a reference ---------------------------------

def without_hidden_data(data: bytes, width: int, height: int) -> bytes:
    """NovelAI hides an image's settings in the lowest bit of its alpha
    channel, which leaves alpha at 254 or 255. Such an image gets its alpha
    back at 255, so that it isn't taken for a partly clear one."""
    if not len(_MAGIC) * 8 <= width * height <= MAX_CLEANED:
        return bytes(data)
    alpha = bytes(data[3::4])
    for index, letter in enumerate(_MAGIC):
        value = 0
        for bit in range(index * 8, index * 8 + 8):  # Down the columns, from the left.
            value = value << 1 | alpha[bit % height * width + bit // height] & 1
        if value != letter:
            return bytes(data)
    result = bytearray(data)
    result[3::4] = alpha.translate(_WITHOUT_HIDDEN)
    return bytes(result)


def on_black(data: bytes) -> bytes:
    """The picture with black behind it, and so without transparency."""
    alpha = bytes(data[3::4])
    if alpha.count(255) == len(alpha):
        return bytes(data)
    result = bytearray(data)
    visible = int.from_bytes(alpha.translate(_NOT_CLEAR), "little")
    for channel in range(3):  # Fully clear pixels turn black.
        plane = int.from_bytes(data[channel::4], "little") & visible
        result[channel::4] = plane.to_bytes(len(alpha), "little")
    for found in _PARTLY_CLEAR.finditer(alpha):
        pixel = found.start()
        share = alpha[pixel] / 255
        whole = share + (1 - share)
        for at in range(pixel * 4, pixel * 4 + 3):
            result[at] = min(255, round(data[at] * share / whole))
    result[3::4] = b"\xff" * len(alpha)
    return bytes(result)
