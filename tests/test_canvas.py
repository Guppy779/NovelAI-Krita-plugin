"""The picture handling in canvas.py that needs Qt but not Krita.

Skipped where no PyQt is installed. Made-up pictures only.
"""
import sys
import types

import pytest

try:
    from novelai_krita.qt import QColor, QImage, image_bytes
except ImportError:
    pytest.skip("needs PyQt", allow_module_level=True)

sys.modules.setdefault("krita", types.SimpleNamespace(Krita=None))
from novelai_krita import canvas, scaling  # noqa: E402

RGBA = QImage.Format.Format_RGBA8888
MARKER = b"stealth_pngcomp"


def made_up(width, height, alpha=255):
    data = bytearray()
    for index in range(width * height):
        data += bytes([index * 7 % 256, index * 13 % 256, index * 29 % 256, alpha])
    return bytes(data)


def as_image(data, width, height):
    return QImage(data, width, height, width * 4, RGBA).copy()


def rgba(image):
    return image_bytes(image.convertToFormat(RGBA))


def with_hidden_data(width, height):
    """A dark made-up picture with NovelAI's marker in the lowest bit of its alpha."""
    data = bytearray(value // 8 if index % 4 < 3 else 255
                     for index, value in enumerate(made_up(width, height)))
    bits = [byte >> shift & 1 for byte in MARKER for shift in range(7, -1, -1)]
    for index, bit in enumerate(bits):  # Down the columns, from the left.
        x, y = divmod(index, height)
        data[(y * width + x) * 4 + 3] = 254 | bit
    return bytes(data)


class Document:
    def __init__(self, image):
        self.image = image

    def width(self):
        return self.image.width()

    def height(self):
        return self.image.height()

    def projection(self, x, y, width, height):
        return self.image.copy(x, y, width, height)


def krita_must_not_scale(image, width, height):
    raise AssertionError("Krita's scaler was used")


def test_a_focus_area_is_scaled_as_the_site_does(monkeypatch):
    monkeypatch.setattr(canvas, "sharp_resize", krita_must_not_scale)
    data = made_up(8, 6)
    region = canvas.Region(0, 0, 8, 6, focused=True)
    scaled = canvas.careful_resize(as_image(data, 8, 6), 16, 12, region)
    assert (scaled.width(), scaled.height()) == (16, 12)
    assert rgba(scaled) == scaling.resize(data, 8, 6, 16, 12)


def test_a_finished_focus_result_is_scaled_as_the_site_does(monkeypatch):
    monkeypatch.setattr(canvas, "sharp_resize", krita_must_not_scale)
    data = made_up(16, 12)
    region = canvas.Region(40, 24, 8, 6, focused=True)
    layer = canvas.compose_result(as_image(data, 16, 12), region, sharp=True)
    assert rgba(layer) == scaling.resize(data, 16, 12, 8, 6)


def test_the_canvas_is_sent_scaled_as_the_site_does(monkeypatch):
    monkeypatch.setattr(canvas, "sharp_resize", krita_must_not_scale)
    data = made_up(8, 6)
    doc = Document(as_image(data, 8, 6))
    sent = canvas.picture_to_send(doc, canvas.Region(0, 0, 8, 6), 4, 3)
    assert sent.hasAlphaChannel()
    assert rgba(sent) == scaling.resize(data, 8, 6, 4, 3)


def test_a_canvas_at_the_size_it_is_generated_at_is_sent_as_it_is():
    data = made_up(8, 6)
    doc = Document(as_image(data, 8, 6))
    sent = canvas.picture_to_send(doc, canvas.Region(0, 0, 8, 6), 8, 6)
    assert sent.hasAlphaChannel()  # As the site's picture has.
    assert rgba(sent) == data


def test_a_result_for_the_whole_canvas_is_left_to_kritas_scaler(monkeypatch):
    calls = []
    scaled = as_image(made_up(4, 3), 4, 3)

    def krita(image, width, height):
        calls.append((width, height))
        return scaled

    monkeypatch.setattr(canvas, "sharp_resize", krita)
    result = canvas.careful_resize(as_image(made_up(8, 6), 8, 6), 4, 3, canvas.Region(0, 0, 8, 6))
    assert result is scaled and calls == [(4, 3)]


def test_exact_scaling_forgets_an_earlier_scaling_problem(monkeypatch):
    monkeypatch.setattr(canvas, "scaling_problem", "something from an earlier generation")
    region = canvas.Region(0, 0, 8, 6, focused=True)
    canvas.careful_resize(as_image(made_up(8, 6), 8, 6), 16, 12, region)
    assert canvas.scaling_problem == ""


def test_a_result_loses_its_hidden_data():
    data = with_hidden_data(16, 16)
    assert data[3::4].count(254) > 0
    cleaned = rgba(canvas.without_hidden_data(as_image(data, 16, 16)))
    assert cleaned[3::4] == b"\xff" * 256
    assert [cleaned[c::4] for c in range(3)] == [data[c::4] for c in range(3)]


def test_a_picture_without_hidden_data_is_left_alone():
    image = as_image(made_up(16, 16, alpha=254), 16, 16)
    assert canvas.without_hidden_data(image) is image


def test_a_novelai_image_on_the_canvas_is_read_without_its_hidden_data():
    data = with_hidden_data(16, 16)
    doc = Document(as_image(data, 16, 16))
    read = rgba(canvas.read_image(doc, canvas.Region(0, 0, 16, 16)))
    assert [read[c::4] for c in range(3)] == [data[c::4] for c in range(3)]
    # And in a part of it that doesn't hold the marker.
    part = rgba(canvas.read_image(doc, canvas.Region(2, 9, 5, 7)))
    expected = b"".join(data[(row * 16 + 2) * 4:(row * 16 + 7) * 4] for row in range(9, 16))
    assert [part[c::4] for c in range(3)] == [expected[c::4] for c in range(3)]


def test_another_canvas_is_flattened_onto_white_as_it_is():
    # Black at alpha 254 lets a little white through: that isn't hidden data.
    doc = Document(as_image(bytes([0, 0, 0, 254]) * 256, 16, 16))
    read = canvas.read_image(doc, canvas.Region(0, 0, 16, 16))
    assert read.pixelColor(3, 3) == QColor(1, 1, 1)
