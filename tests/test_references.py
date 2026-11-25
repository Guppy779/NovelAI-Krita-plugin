"""Precise references: the pictures made for V4.5 and V5, and the panel's
cards for them.

Skipped where no PyQt is installed. Made-up pictures only.
"""
import base64
import os
import sys
import types

import pytest

try:
    from novelai_krita.qt import QApplication, QColor, QImage, image_bytes
except ImportError:
    pytest.skip("needs PyQt", allow_module_level=True)

sys.modules.setdefault("krita", types.SimpleNamespace(Krita=None))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from novelai_krita import api, references, scaling  # noqa: E402

RGBA = QImage.Format.Format_RGBA8888
V45, V5, V4 = "nai-diffusion-4-5-full", "nai-diffusion-5-full", "nai-diffusion-4-full"
MARKER = b"stealth_pngcomp"


@pytest.fixture(scope="module", autouse=True)
def application():
    yield QApplication.instance() or QApplication([])


def rgba(image):
    return image_bytes(image.convertToFormat(RGBA))


def sent_picture(sent):
    return QImage.fromData(base64.b64decode(sent))


def plain(width, height, colour=QColor(200, 120, 40, 255)):
    image = QImage(width, height, RGBA)
    image.fill(colour)
    return image


def striped(width, height):
    """Opaque, with a different colour in every row and column."""
    data = bytearray()
    for y in range(height):
        for x in range(width):
            data += bytes([x * 255 // (width - 1), y * 255 // (height - 1), (x + y) % 256, 255])
    return QImage(bytes(data), width, height, width * 4, RGBA).copy()


# ---- The picture V5 is sent ----------------------------------------------------

def test_v5_cuts_off_what_sticks_out_evenly():
    image = striped(64, 96)  # The shape of 1024 x 1536: a little taller than 832 x 1216.
    sent, size, shown = references.prepare_cut(image)
    assert size == (832, 1216) == (shown.width(), shown.height())
    picture = sent_picture(sent)
    assert (picture.width(), picture.height()) == (832, 1216)
    # Scaled to 832 x 1248 as the site scales, less 16 rows at the top and bottom.
    whole = scaling.resize(rgba(image), 64, 96, 832, 1248)
    assert rgba(picture) == whole[16 * 832 * 4:(16 + 1216) * 832 * 4]


def test_v5_keeps_a_reference_transparent():
    image = plain(32, 32)
    for y in range(32):
        for x in range(16):
            image.setPixelColor(x, y, QColor(0, 0, 0, 0))
    sent, size, _ = references.prepare_cut(image)
    picture = sent_picture(sent)
    assert size == (1024, 1024) and picture.hasAlphaChannel()
    assert picture.pixelColor(100, 500).alpha() == 0  # Not black behind it, as for V4.5.
    assert picture.pixelColor(900, 500) == QColor(200, 120, 40, 255)


def test_v5_is_sent_a_picture_of_the_right_size_as_it_is():
    image = plain(832, 1216, QColor(10, 200, 30, 255))
    image.setPixelColor(5, 7, QColor(90, 80, 70, 60))
    sent, size, _ = references.prepare_cut(image)
    assert size == (832, 1216)
    assert rgba(sent_picture(sent)) == rgba(image)  # Its part-clear pixel too.


def test_v5_is_sent_the_file_itself_when_it_has_the_right_size():
    image = plain(1024, 1024)
    file = b"\xff\xd8\xff made-up JPEG"
    sent, size, _ = references.prepare_cut(image, file)
    assert base64.b64decode(sent) == file and size == (1024, 1024)
    # Not when the picture has to be scaled anyway.
    sent, size, _ = references.prepare_cut(plain(1024, 64), file)
    assert base64.b64decode(sent) != file and size == (4096, 256)


def test_v5_is_not_sent_a_file_with_hidden_data_in_it():
    image = plain(1024, 1024)
    bits = [byte >> shift & 1 for byte in MARKER for shift in range(7, -1, -1)]
    for index, bit in enumerate(bits):  # Down the columns, from the left.
        x, y = divmod(index, 1024)
        image.setPixelColor(x, y, QColor(200, 120, 40, 254 | bit))
    sent, _, _ = references.prepare_cut(image, b"\x89PNG the file")
    picture = sent_picture(sent)
    assert picture.width() == 1024  # A picture made from it, not the file.
    assert rgba(picture) == rgba(plain(1024, 1024))  # Whole again.


def test_only_a_file_v5_takes_as_it_is_is_kept(tmp_path):
    right, other, odd = tmp_path / "right.png", tmp_path / "other.png", tmp_path / "odd.png"
    plain(1024, 1024).save(str(right))
    plain(1000, 1000).save(str(other))
    odd.write_bytes(b"BM not one of the kinds of file the site reads")
    assert references.file_as_sent(str(right), plain(1024, 1024)) == right.read_bytes()
    assert references.file_as_sent(str(other), plain(1000, 1000)) is None
    assert references.file_as_sent(str(odd), plain(1024, 1024)) is None
    assert references.file_as_sent(str(tmp_path / "gone.png"), plain(1024, 1024)) is None


def test_v45_still_gets_its_picture_on_black():
    sent, size, _ = references.prepare(plain(30, 10, QColor(250, 250, 250, 255)))
    picture = sent_picture(sent)
    assert size == (1536, 1024) == (picture.width(), picture.height())
    assert picture.pixelColor(700, 5) == QColor(0, 0, 0, 255)  # Above the picture.
    assert picture.pixelColor(700, 512) == QColor(250, 250, 250, 255)


# ---- The sliders ---------------------------------------------------------------

def test_a_slider_that_ends_at_1_moves_evenly():
    amount = references._Amount("", lambda: None)
    assert amount.slider.maximum() == 20
    amount.slider.setValue(7)
    assert amount.value() == 0.35


def test_a_slider_that_ends_at_5_has_1_in_its_middle():
    amount = references._Amount("", lambda: None)
    amount.set_top(5)
    assert amount.slider.maximum() == 200 and amount.slider.value() == 100  # At 1.
    for place, number in ((0, 0.0), (3, 0.05), (50, 0.5), (100, 1.0), (102, 1.1),
                          (150, 3.0), (200, 5.0)):
        amount.slider.setValue(place)
        assert amount.value() == number
        assert amount.slider.value() == place  # It stays where it was put.


def test_a_typed_number_stays_when_the_slider_ends_elsewhere():
    changes = []
    amount = references._Amount("", lambda: changes.append(1))
    amount.set_top(5)
    amount.box.setValue(3.33)
    assert amount.value() == 3.33 and amount.slider.value() == 158
    amount.set_top(1)  # Back on V4.5: the number isn't cut down.
    assert amount.value() == 3.33 and amount.slider.value() == 20
    amount.box.setValue(-0.5)  # Beyond the slider's ends.
    assert amount.value() == -0.5 and amount.slider.value() == 0
    amount.box.setValue(7)
    assert amount.value() == 7.0 and len(changes) == 3


# ---- The cards -----------------------------------------------------------------

@pytest.fixture
def made(monkeypatch):
    """Stand-ins for making the pictures, which takes a while: what was made."""
    made = []

    def fitted(image):
        made.append("fitted")
        return "Zml0dGVk", (1024, 1536), image

    def cut(image, file=None):
        made.append("cut")
        return "Y3V0", (832, 1216), image

    monkeypatch.setattr(references, "prepare", fitted)
    monkeypatch.setattr(references, "prepare_cut", cut)
    return made


def editor_for(model):
    said = []
    editor = references.ReferenceEditor(lambda: None, said.append, lambda: None, lambda: None)
    editor.set_model(model)
    editor.said = said
    return editor


def choose(card, kind):
    card.kind.setCurrentIndex(card.kind.findData(kind))


def types_listed(card):
    return [card.kind.itemData(index) for index in range(card.kind.count())]


def test_a_card_offers_what_v5_takes(made):
    editor = editor_for(V5)
    assert editor.add_image(plain(8, 8))
    card = editor._cards[0]
    assert types_listed(card) == ["character&style", "character", "style", "costume"]
    assert not card.same_outfit.isHidden() and card.same_outfit.isChecked()
    assert card.fidelity.isHidden() and card.strength.slider.maximum() == 200
    assert made == ["cut"]
    reference = editor.for_request()[0]
    assert (reference.image, reference.kind, reference.same_outfit) == (
        "Y3V0", "character&style", True)


def test_a_card_offers_what_v45_takes(made):
    editor = editor_for(V45)
    editor.add_image(plain(8, 8))
    card = editor._cards[0]
    assert types_listed(card) == ["character&style", "character", "style"]
    assert card.same_outfit.isHidden()
    assert not card.fidelity.isHidden() and card.strength.slider.maximum() == 20
    assert made == ["fitted"] and editor.for_request()[0].image == "Zml0dGVk"


def test_prefer_same_outfit_follows_the_type_as_on_the_site(made):
    editor = editor_for(V5)
    editor.add_image(plain(8, 8))
    card = editor._cards[0]
    choose(card, "character")
    assert not card.same_outfit.isHidden() and not card.same_outfit.isChecked()
    card.same_outfit.setChecked(True)
    assert editor.for_request()[0].same_outfit is True
    choose(card, "style")
    assert card.same_outfit.isHidden()
    choose(card, "costume")
    assert card.same_outfit.isHidden()
    choose(card, "character&style")
    assert not card.same_outfit.isHidden() and card.same_outfit.isChecked()


def test_the_picture_is_made_again_for_the_other_kind_of_model(made):
    editor = editor_for(V45)
    editor.add_image(plain(8, 8))
    card = editor._cards[0]
    editor.set_model(V5)
    assert made == ["fitted"]  # Not until it's needed, or a moment has passed.
    assert card._packed  # What the other one is made from.
    assert editor.for_request()[0].image == "Y3V0" and made == ["fitted", "cut"]
    assert card._packed is None  # Made both ways: the picture it's from is let go.
    editor.set_model(V45)
    assert editor.for_request()[0].image == "Zml0dGVk"
    editor.set_model(V5)
    editor.prepare_pictures()
    assert made == ["fitted", "cut"]  # Each only once.


def test_a_card_keeps_its_picture_packed_and_makes_the_same_from_it():
    image = striped(24, 16)
    image.setPixelColor(3, 4, QColor(90, 80, 70, 60))
    image.setPixelColor(5, 6, QColor(90, 80, 70, 0))  # Colour under a clear pixel counts too.
    file = b"\x89PNG the file"
    editor = editor_for(V45)
    editor.add_image(image, file)
    card = editor._cards[0]
    assert card._image is None  # Not the picture itself, which is far larger.
    assert rgba(QImage.fromData(card._packed)) == rgba(image)
    assert card._file == file  # V5 may still want it.
    assert card.thumbnail.pixmap().width() == references.THUMBNAIL_SIZE
    editor.set_model(V5)
    assert editor.for_request()[0].image == references.prepare_cut(image)[0]
    assert card._packed is None and card._file is None
    assert "1216 x 832" in card.thumbnail.toolTip()
    editor.set_model(V45)
    assert editor.for_request()[0].image == references.prepare(image)[0]
    assert "1536 x 1024" in card.thumbnail.toolTip()


def test_a_file_v5_takes_as_it_is_reaches_the_request(tmp_path):
    path = tmp_path / "right.png"
    plain(1024, 1024).save(str(path))
    editor = editor_for(V5)
    editor.add_file(str(path))
    assert base64.b64decode(editor.for_request()[0].image) == path.read_bytes()
    assert editor.said[-1] == "Added right.png as a precise reference."


def test_a_moment_after_the_model_changes_the_pictures_are_made(made):
    editor = editor_for(V45)
    editor.add_image(plain(8, 8))
    editor.add_image(plain(8, 8))
    assert not editor._prepare_timer.isActive()  # Nothing to make.
    editor._cards[1].enabled.setChecked(False)
    editor.set_model(V5)
    assert editor._prepare_timer.isActive()
    editor.prepare_pictures()  # What the timer does.
    assert editor.said[-1] == "The references are ready for this model."
    assert not editor._prepare_timer.isActive()
    # The one that's switched off too: it shows what V5 would get as well.
    assert made == ["fitted", "fitted", "cut", "cut"]
    assert all("832 x 1216" in card.thumbnail.toolTip() for card in editor._cards)
    editor.set_model(V45)
    assert all("1024 x 1536" in card.thumbnail.toolTip() for card in editor._cards)
    assert not editor._prepare_timer.isActive()  # Made before.


def test_only_what_is_sent_is_made_for_a_request(made):
    editor = editor_for(V45)
    editor.add_image(plain(8, 8))
    editor.add_image(plain(8, 8))
    editor._cards[1].enabled.setChecked(False)
    editor.set_model(V5)
    assert len(editor.for_request()) == 1 and made == ["fitted", "fitted", "cut"]
    assert editor._prepare_timer.isActive()  # The other follows.


def test_the_pictures_are_made_for_v5_under_a_mask_too(made):
    editor = editor_for(V45)
    editor.add_image(plain(8, 8))
    editor.set_inpainting(True)
    editor.set_model(V5)
    assert editor.used() == 0 and editor._prepare_timer.isActive()
    editor.prepare_pictures()
    assert made == ["fitted", "cut"] and "832 x 1216" in editor._cards[0].thumbnail.toolTip()


def test_no_pictures_are_made_while_a_generation_runs(made):
    editor = editor_for(V45)
    editor.add_image(plain(8, 8))
    editor.add_image(plain(8, 8))
    editor._cards[1].enabled.setChecked(False)
    editor.set_model(V5)
    assert len(editor.for_request()) == 1  # What the generation is sent is made.
    editor.set_generating(True)
    said = list(editor.said)
    editor.prepare_pictures()  # The timer, set off before it started.
    editor.set_model(V45)
    editor.set_model(V5)
    assert not editor._prepare_timer.isActive()
    assert made == ["fitted", "fitted", "cut"] and editor.said == said
    editor.set_generating(False)
    assert editor._prepare_timer.isActive()  # Now the other one follows.
    editor.prepare_pictures()
    assert made == ["fitted", "fitted", "cut", "cut"]


def test_a_reference_the_model_cant_take_isnt_made_for_it(made):
    editor = editor_for(V5)
    editor.add_image(plain(8, 8))
    choose(editor._cards[0], "costume")
    editor.set_model(V45)
    editor.prepare_pictures()
    editor.set_model(V4)
    editor.prepare_pictures()
    assert made == ["cut"] and not editor._prepare_timer.isActive()


def test_v5_takes_no_references_when_inpainting(made):
    editor = editor_for(V5)
    editor.add_image(plain(8, 8))
    assert editor.used() == 1 and editor.note.isHidden()
    editor.set_inpainting(True)
    assert editor.used() == 0 and editor.for_request() == []
    assert "when inpainting" in editor.note.text()
    assert len(editor.for_request(inpainting=False)) == 1  # The request decides.
    editor.set_model(V45)  # V4.5 does take them.
    assert editor.used() == 1 and editor.note.isHidden()
    assert len(editor.for_request(inpainting=True)) == 1


def test_an_outfit_reference_is_skipped_by_v45(made):
    editor = editor_for(V5)
    editor.add_image(plain(8, 8))
    editor.add_image(plain(8, 8))
    outfit, other = editor._cards
    choose(outfit, "costume")
    assert editor.used() == 2
    editor.set_model(V45)
    assert editor.used() == 1 and [r.kind for r in editor.for_request()] == ["character&style"]
    assert "Outfit references need a V5 model" in editor.note.text()
    assert not outfit.strength.isEnabled() and other.strength.isEnabled()
    assert "costume" in types_listed(outfit) and "costume" not in types_listed(other)
    assert outfit.kind.isEnabled()  # It can be given a type V4.5 takes.
    choose(outfit, "style")
    assert editor.used() == 2 and editor.note.isHidden()
    assert types_listed(outfit) == ["character&style", "character", "style"]
    assert outfit.strength.isEnabled()


def test_references_wait_greyed_out_with_a_model_that_takes_none(made):
    editor = editor_for(V5)
    editor.add_image(plain(8, 8))
    card = editor._cards[0]
    card.strength.box.setValue(2.5)
    editor.set_model(V4)
    assert editor.used() == 0 and editor.for_request() == []
    assert "needs a V4.5 or V5 model" in editor.note.text()
    assert not card.kind.isEnabled() and not card.fidelity.isHidden()
    assert not (card.enabled.isEnabled() or card.strength.isEnabled()
                or card.fidelity.isEnabled() or card.thumbnail.isEnabled())
    editor.set_model(V5)
    assert editor.used() == 1 and card.kind.isEnabled() and card.strength.isEnabled()
    assert editor.for_request()[0].strength == 2.5


def test_no_more_references_than_novelai_takes(made):
    editor = editor_for(V5)
    for _ in range(api.MAX_REFERENCES):
        assert editor.add_image(plain(8, 8))
    assert not editor.add_image(plain(8, 8))
    assert len(editor._cards) == api.MAX_REFERENCES == editor.used()
    assert "as many references as NovelAI takes" in editor.note.text()
