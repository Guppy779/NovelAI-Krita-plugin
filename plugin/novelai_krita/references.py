"""The Precise Reference section of the panel (V4.5 and V5).

A reference is a picture for NovelAI to take a character, a style, an outfit
or several of these from, with the site's settings for it. What the two model
families do differently with one is listed in api.py.

Each open document has its own references. They aren't stored anywhere, so
that documents stay small: they last until they're removed, or the document
or Krita is closed. Each is kept as the picture that is sent, which V4.5 and
V5 want differently: fitted on black at one of three sizes, or cut to one of
many. It's made the first time a model of that family needs it, scaled with
the site's own arithmetic (see scaling.py): NovelAI draws a visibly different
image from a reference that is a shade off. Until it has been made both ways,
the picture it's made from is kept too, packed as a PNG: unpacked, a large one
takes tens of megabytes.
"""

import base64
import os

from . import api, canvas, scaling, settings
from .import_dialog import MAX_FILE_SIZE
from .prompt_edit import IMAGE_EXTENSIONS, dropped_image_file
from .qt import (
    QApplication, QCheckBox, QColor, QComboBox, QDoubleSpinBox, QEventLoop, QFileDialog, QFrame,
    QHBoxLayout, QImage, QImageReader, QLabel, QMenu, QPainter, QPixmap, QSlider, Qt, QTimer,
    QToolButton, QVBoxLayout, QWidget, ignore_wheel, image_bytes,
)

THUMBNAIL_SIZE = 84
STEP = 0.05  # Of the sliders, as on the site.
# The site's sliders take typed numbers beyond their ends.
AMOUNT_MIN, AMOUNT_MAX = -10.0, 10.0
# A slider that ends beyond 1 has 1 in its middle, as on the site: this many
# places up to 1, and as many from there to its end.
HALF_SLIDER = 100
PREPARE_DELAY = 400  # Milliseconds after a change that needs pictures made, in case more follow.
_RGB = QImage.Format.Format_RGB32
_RGBA = QImage.Format.Format_RGBA8888

ADD_TIP = ("Add a picture for NovelAI to take a character, a style or an outfit from.\n"
           "You can also drop an image file here. V4.5 and V5 models only.\n"
           "Each reference costs %d Anlas per image, on every plan.\n"
           "Each open document has its own references. They aren't saved: they\n"
           "last until you remove them, or close the document or Krita."
           % api.REFERENCE_ANLAS)
TYPE_TIP = ("What NovelAI takes from the picture:\n"
            "Character & Style: the character's looks and the art style.\n"
            "Character: only the character's looks.\n"
            "Style: only the art style.\n"
            "Outfit (V5 only): only the clothes.")
OUTFIT_TIP = ("Ticked: the character tends to wear what they wear in the picture.\n"
              "Unticked: what they wear is left to the prompt.")
STRENGTH_TIP = ("How strongly the reference is followed. Lower it if poses and\n"
                "expressions copy the reference too closely.")
FIDELITY_TIP = ("How exactly the reference is applied. At 1 it is hard to change\n"
                "with the prompt; lower values give the prompt more say.")
NUMBER_TIP = "\nType a number to go beyond the slider, below 0 included."

# What the first bytes of the kinds of file the site reads look like.
_FILE_STARTS = ((0, b"\x89PNG"), (0, b"\xff\xd8\xff"), (8, b"WEBP"))


def read_picture(path):
    """The image in a file (null if it can't be read). A JPEG is turned the way
    its EXIF data says, as the site and image viewers show it; Qt leaves it
    as stored unless asked."""
    reader = QImageReader(path)
    reader.setAutoTransform(bytes(reader.format()).lower() in (b"jpeg", b"jpg"))
    return reader.read()


def open_picture(path):
    """Read an image file the user chose or dropped: (the image, "") or
    (None, why it couldn't be read, to tell them)."""
    name = os.path.basename(path)
    try:
        too_large = os.path.getsize(path) > MAX_FILE_SIZE
    except OSError as exc:
        return None, "Could not read %s: %s." % (name, exc.strerror or exc)
    if too_large:
        return None, "%s is too large (over %d MB)." % (name, MAX_FILE_SIZE // (1024 * 1024))
    image = read_picture(path)
    if image.isNull() or image.width() < 1 or image.height() < 1:
        return None, "Could not read %s as an image." % name
    return image, ""


def file_as_sent(path, image):
    """The bytes of an image file, if V5 could be sent them as they are: the
    site sends a picture that already has one of V5's sizes untouched, not as
    a PNG made from it. None otherwise."""
    if (image.width(), image.height()) not in api.REFERENCE_CUT_SIZES:
        return None
    try:
        with open(path, "rb") as file:
            data = file.read()
    except OSError:
        return None
    return data if any(data[at:at + len(start)] == start for at, start in _FILE_STARTS) else None


def _pixels(image):
    """A picture as the site reads it: (RGBA bytes, width, height, whether
    NovelAI's hidden data was taken out of it)."""
    source = image.convertToFormat(_RGBA)
    width, height = source.width(), source.height()
    data = image_bytes(source)
    cleaned = scaling.without_hidden_data(data, width, height)
    return cleaned, width, height, cleaned != data


def prepare(image):
    """``image`` as a reference for V4.5: (the PNG that is sent, as base64;
    its size; the picture in it, to show).

    Pixel for pixel what the NovelAI site makes of the same picture: scaled
    to fit one of NovelAI's sizes, centred, with black around and behind it.
    """
    data, width, height, _ = _pixels(image)
    size, (to_width, to_height), (left, top) = api.reference_layout(width, height)
    data = scaling.on_black(scaling.resize(data, width, height, to_width, to_height))
    picture = QImage(data, to_width, to_height, to_width * 4, _RGBA).convertToFormat(_RGB)
    whole = QImage(size[0], size[1], _RGB)
    whole.fill(QColor(0, 0, 0))
    painter = QPainter(whole)
    painter.drawImage(left, top, picture)
    painter.end()
    # The site's PNG has an alpha channel too.
    return canvas.to_base64_png(whole.convertToFormat(_RGBA)), size, picture


def prepare_cut(image, file=None):
    """``image`` as a reference for V5: (the picture that is sent, as base64;
    its size; the picture, to show).

    Pixel for pixel what the NovelAI site makes of the same picture: scaled
    to cover one of V5's sizes, with what sticks out cut off evenly on both
    sides. Nothing goes behind it: it stays transparent where it is. A picture
    that has one of the sizes already is sent as it is: as ``file``, the
    bytes of the file it's from, when there are any.
    """
    data, width, height, cleaned = _pixels(image)
    size, (to_width, to_height), (left, top) = api.cut_reference_layout(width, height)
    if size == (width, height):
        if file is not None and not cleaned:
            return base64.b64encode(file).decode("ascii"), size, image
    else:
        data = scaling.resize(data, width, height, to_width, to_height)
        row = size[0] * 4
        data = b"".join(
            data[(y * to_width + left) * 4:(y * to_width + left) * 4 + row]
            for y in range(top, top + size[1]))
    picture = QImage(data, size[0], size[1], size[0] * 4, _RGBA).copy()
    return canvas.to_base64_png(picture), size, picture


class _Amount(QWidget):
    """A slider from 0 to its end, and a number box that also takes values
    beyond it. A slider that ends at 1 moves evenly; one that ends further
    has 1 in its middle."""

    def __init__(self, tip, on_change):
        super().__init__()
        self._on_change = on_change
        self._top = 1.0
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setToolTip(tip)
        self.box = QDoubleSpinBox()
        self.box.setRange(AMOUNT_MIN, AMOUNT_MAX)
        self.box.setDecimals(2)
        self.box.setSingleStep(STEP)
        self.box.setToolTip(tip + NUMBER_TIP)
        self.box.setValue(1.0)
        self._place_slider()
        self.slider.valueChanged.connect(self._slid)
        self.box.valueChanged.connect(self._typed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.box)

    def set_top(self, top):
        """Have the slider end at ``top``. The number stays as it is."""
        if float(top) != self._top:
            self._top = float(top)
            self._place_slider()

    def _place_slider(self):
        self.slider.blockSignals(True)
        self.slider.setRange(0, self._position(self._top))
        self.slider.setValue(self._position(self.box.value()))
        self.slider.blockSignals(False)

    def _position(self, value):
        """Where the slider stands for a number."""
        value = max(0.0, min(self._top, value))
        if self._top <= 1.0:
            return int(round(value / STEP))
        if value <= 1.0:
            return int(round(value * HALF_SLIDER))
        return HALF_SLIDER + int(round((value - 1.0) / (self._top - 1.0) * HALF_SLIDER))

    def _number(self, position):
        """The number a place on the slider stands for: a whole step."""
        if self._top <= 1.0:
            return round(position * STEP, 2)
        if position <= HALF_SLIDER:
            value = position / float(HALF_SLIDER)
        else:
            value = 1.0 + (position - HALF_SLIDER) / float(HALF_SLIDER) * (self._top - 1.0)
        return round(round(value / STEP) * STEP, 2)

    def _slid(self, position):
        if position != self._position(self.box.value()):  # A typed in-between value stays.
            self.box.setValue(self._number(position))

    def _typed(self, value):
        # Several places on the slider stand for one number: it stays where
        # it was dragged to if that's one of them.
        if self._number(self.slider.value()) != round(value, 2):
            self.slider.blockSignals(True)
            self.slider.setValue(self._position(value))
            self.slider.blockSignals(False)
        self._on_change()

    def value(self):
        return round(self.box.value(), 2)


class ReferenceCard(QFrame):
    """One reference: its picture, on/off, type, outfit choice, strength and
    fidelity. ``image`` is the picture it's made from; ``file`` the bytes of
    its file, where V5 could be sent those (see file_as_sent)."""

    def __init__(self, editor, image, file=None):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # The picture as sent, for each of the two ways it is: False for
        # fitted on black (V4.5), True for cut (V5). (base64, size, thumbnail).
        self._prepared = {}
        # What they're made from: the picture until the first is made, then
        # the same packed as a PNG until the other is made too.
        self._image, self._packed = image, None
        self._file = file  # Until it's been made for V5.
        self._model = ""
        self._shown = None  # Which of the two the thumbnail shows.
        changed = self._changed = editor.changed

        self.thumbnail = QLabel()
        self.thumbnail.setFixedWidth(THUMBNAIL_SIZE)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.enabled = QCheckBox()
        self.enabled.setChecked(True)
        self.enabled.setToolTip("Untick to leave this reference out without deleting it.")
        self.enabled.toggled.connect(changed)
        self.kind = QComboBox()
        self.kind.setToolTip(TYPE_TIP)
        self.kind.currentIndexChanged.connect(self._kind_chosen)
        remove = QToolButton()
        remove.setText("✕")
        remove.setAutoRaise(True)
        remove.setToolTip("Remove this reference")
        remove.clicked.connect(lambda: editor.remove(self))
        self.same_outfit = QCheckBox("Prefer same outfit")
        self.same_outfit.setToolTip(OUTFIT_TIP)
        self.same_outfit.setChecked(api.default_same_outfit(api.DEFAULT_REFERENCE_TYPE))
        self.same_outfit.toggled.connect(changed)
        self.strength = _Amount(STRENGTH_TIP, changed)
        self.fidelity = _Amount(FIDELITY_TIP, changed)

        header = QHBoxLayout()
        header.addWidget(self.enabled)
        header.addWidget(self.kind, 1)
        header.addWidget(remove)
        controls = QVBoxLayout()
        controls.setSpacing(3)
        controls.addLayout(header)
        controls.addWidget(self.same_outfit)
        labels = [QLabel("Strength"), QLabel("Fidelity")]
        width = max(label.sizeHint().width() for label in labels)
        for label, amount in zip(labels, (self.strength, self.fidelity)):
            label.setMinimumWidth(width)
            row = QHBoxLayout()
            row.addWidget(label)
            row.addWidget(amount, 1)
            controls.addLayout(row)
        self._fidelity_row = [labels[1], self.fidelity]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.thumbnail)
        layout.addLayout(controls, 1)
        ignore_wheel(self)  # Scrolling the panel mustn't change these.
        self._greyed = [self.thumbnail, self.enabled, self.kind, self.same_outfit,
                        self.strength, self.fidelity] + labels
        self._fill_types(api.DEFAULT_REFERENCE_TYPE)

    # ---- The picture, as each kind of model is sent it ---------------------------

    def is_prepared(self, cut):
        return cut in self._prepared

    def prepare(self, cut):
        """Make the picture as it's sent: cut for V5, or fitted on black for
        V4.5. Takes about half a second, and several for a large picture or
        one with transparency."""
        if cut in self._prepared:
            return
        image = self._image if self._image is not None else QImage.fromData(self._packed)
        sent, size, picture = prepare_cut(image, self._file) if cut else prepare(image)
        thumbnail = QPixmap.fromImage(picture.scaled(
            THUMBNAIL_SIZE, THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self._prepared[cut] = sent, size, thumbnail
        if len(self._prepared) == 2:  # It's not needed again: let go of it.
            self._packed = None
        elif self._packed is None:
            self._packed = canvas.to_png(image.convertToFormat(_RGBA))  # As _pixels reads it.
        self._image = None
        if cut:
            self._file = None
        self._show_picture()

    def _show_picture(self):
        """Show the picture as the current model is sent it, once that's made."""
        cut = api.reference_cut(self._model)
        if cut == self._shown or cut not in self._prepared:
            return
        _, size, thumbnail = self._prepared[cut]
        self._shown = cut
        self.thumbnail.setPixmap(thumbnail)
        if cut:
            tip = ("Sent at %d x %d. Where the picture hasn't that shape, its edges are\n"
                   "cut off; this is what's left.")
        else:
            tip = "Sent at %d x %d, with black around it where it doesn't fill that."
        self.thumbnail.setToolTip(tip % size)

    # ---- What the model offers ---------------------------------------------------

    def _fill_types(self, kind):
        """List the model's types, with ``kind`` chosen. A type the model
        doesn't have stays listed while it's the one chosen."""
        types = list(api.reference_types(self._model))
        if kind not in dict(types):
            types += [pair for pair in api.OUTFIT_REFERENCE_TYPES if pair[0] == kind]
        listed = [self.kind.itemData(index) for index in range(self.kind.count())]
        if listed == [value for value, _ in types] and self.kind.currentData() == kind:
            return
        self.kind.blockSignals(True)
        self.kind.clear()
        for value, label in types:
            self.kind.addItem(label, value)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.kind.blockSignals(False)

    def _kind_chosen(self, *_):
        # As on the site: Character & Style starts with the outfit kept,
        # Character without.
        self.same_outfit.blockSignals(True)
        self.same_outfit.setChecked(api.default_same_outfit(self.kind.currentData()))
        self.same_outfit.blockSignals(False)
        self._changed()  # The editor has this card show what goes with the type.

    def _show_choices(self):
        model = self._model
        self.same_outfit.setVisible(
            api.reference_outfits(model) and api.has_outfit_choice(self.kind.currentData()))
        # Only a model that takes references without Fidelity hides it.
        fidelity = api.reference_fidelity(model) or not api.supports_references(model)
        for widget in self._fidelity_row:
            widget.setVisible(fidelity)

    def set_model(self, model):
        """Offer what ``model`` takes, and grey the card out if it isn't sent
        this reference. Removing it still works, and so does giving it a type
        the model does take."""
        self._model = model
        self._fill_types(self.kind.currentData())
        self.strength.set_top(api.reference_strength_max(model))
        self._show_choices()
        self._show_picture()
        used = self.is_taken()
        for widget in self._greyed:
            widget.setEnabled(used)
        self.kind.setEnabled(api.supports_references(model))

    def is_taken(self):
        """Whether the model takes a reference of this type at all."""
        return (api.supports_references(self._model) and api.reference_caption(
            self._model, self.kind.currentData(), self.same_outfit.isChecked()) is not None)

    def is_outfit(self):
        return self.kind.currentData() == api.OUTFIT_REFERENCE

    def is_sent(self):
        return self.enabled.isChecked() and self.is_taken()

    def request(self):
        """This reference as it goes in a request to the current model."""
        cut = api.reference_cut(self._model)
        self.prepare(cut)
        return api.Reference(self._prepared[cut][0], self.kind.currentData(),
                             self.strength.value(), self.fidelity.value(),
                             self.same_outfit.isChecked())


class ReferenceEditor(QWidget):
    """The list of references, with Add.

    ``say(text)`` shows a message; ``canvas_picture()`` gives the canvas as a
    QImage, or None if no document is open; ``follow()`` has the panel notice
    that another document became active, which ends in set_document here.
    """

    def __init__(self, on_changed, say, canvas_picture, follow, parent=None):
        super().__init__(parent)
        self._on_changed, self._say, self._canvas_picture = on_changed, say, canvas_picture
        self._follow = follow
        self._cards = []  # The ones shown.
        self._doc = None  # The document they belong to.
        self._others = []  # (document, cards) for the other open documents.
        self._active = False  # Whether a document is open.
        self._loose = False  # Whether they changed while none was open.
        self._model = ""
        self._inpainting = False
        self._generating = False
        self.setAcceptDrops(True)
        # Pictures are made for a newly chosen model a moment later, so that
        # going through the list of models doesn't stop at each.
        self._prepare_timer = QTimer(self)
        self._prepare_timer.setSingleShot(True)
        self._prepare_timer.setInterval(PREPARE_DELAY)
        self._prepare_timer.timeout.connect(self.prepare_pictures)

        title = QLabel("Precise Reference")
        self.add_button = QToolButton()
        self.add_button.setText("+ Add")
        self.add_button.setToolTip(ADD_TIP)
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_button)
        menu.addAction("From file...").triggered.connect(lambda *_: self.choose_file())
        menu.addAction("From canvas").triggered.connect(lambda *_: self.add_canvas())
        self.add_button.setMenu(menu)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("font-style: italic;")

        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.add_button)
        self._holder = QWidget()
        self._list = QVBoxLayout(self._holder)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(4)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addLayout(header)
        layout.addWidget(self.note)
        layout.addWidget(self._holder)
        self._refresh()

    def changed(self, *_):
        if not self._active:
            self._loose = True
        self._refresh()  # A type or tick may have changed what's sent.
        self._on_changed()

    # ---- One list for each open document ---------------------------------------

    def set_document(self, doc):
        """Show the references of ``doc``, the document that became active.
        With None, for no document, the list stays as it is."""
        self._active = doc is not None
        if doc is None:
            return
        if canvas.same_document(doc, self._doc):
            self._loose = False
            return
        mine = []
        for other, cards in self._others:
            if canvas.same_document(other, doc):
                mine = cards
            elif not canvas.document_is_open(other):
                self._discard(cards)
        self._others = [(other, cards) for other, cards in self._others
                        if cards is not mine and canvas.document_is_open(other)]
        if self._doc is not None and canvas.document_is_open(self._doc):
            self._others.append((self._doc, self._take()))
        elif mine or not self._loose:
            self._discard(self._take())
        # Otherwise the document takes what was added while none was open, as
        # it does with a prompt typed then.
        self._doc = doc
        self._loose = False
        if mine:
            self._cards = mine
            for card in mine:
                self._list.addWidget(card)
                card.show()
        self._refresh()

    def _take(self):
        """Take the shown references out of the list, without deleting them."""
        cards, self._cards = self._cards, []
        for card in cards:
            self._list.removeWidget(card)
            card.hide()
        return cards

    @staticmethod
    def _discard(cards):
        for card in cards:
            card.setParent(None)
            card.deleteLater()

    # ---- Adding and removing -------------------------------------------------

    def _busy(self, text, work):
        """Do ``work()``, which takes a while, saying ``text`` before it
        starts; what is clicked meanwhile waits until it's done."""
        self._say(text)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            return work()
        finally:
            QApplication.restoreOverrideCursor()

    def add_image(self, image, file=None):
        """Add ``image`` (a QImage) as a reference. False if it couldn't be.
        ``file``: see file_as_sent."""
        self._follow()  # It's for the document that is active now.
        if len(self._cards) >= api.MAX_REFERENCES:
            self._say("NovelAI takes at most %d precise references." % api.MAX_REFERENCES)
            return False
        if image.isNull() or image.width() < 1 or image.height() < 1:
            return False

        def make():
            card = ReferenceCard(self, image, file)
            card.set_model(self._model)
            card.prepare(api.reference_cut(self._model))
            return card

        card = self._busy("Preparing the reference...", make)
        self._cards.append(card)
        self._list.addWidget(card)
        self.changed()
        return True

    def add_file(self, path):
        image, problem = open_picture(path)
        if problem:
            self._say(problem)
        elif self.add_image(image, file_as_sent(path, image)):  # It says so if there are too many.
            self._say("Added %s as a precise reference." % os.path.basename(path))

    def choose_file(self):
        folder = settings.get("reference_dir") or os.path.join(
            os.path.expanduser("~"), "Pictures")
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a reference image", folder,
            "Images (%s)" % " ".join("*" + extension for extension in IMAGE_EXTENSIONS))
        if path:
            settings.set("reference_dir", os.path.dirname(path))
            self.add_file(path)

    def add_canvas(self):
        image = self._canvas_picture()
        if image is None:
            self._say("Open a document first.")
        elif self.add_image(image):
            self._say("Added the canvas as a precise reference.")

    def remove(self, card):
        self._follow()
        if card not in self._cards:
            return  # Another document became active: its references are shown now.
        self._cards.remove(card)
        self._list.removeWidget(card)
        self._discard([card])
        self.changed()

    # ---- Dropped image files -------------------------------------------------

    def dragEnterEvent(self, event):
        if dropped_image_file(event.mimeData()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if dropped_image_file(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = dropped_image_file(event.mimeData())
        if path:
            event.acceptProposedAction()
            # After the drop has finished: the system's drag is still running here.
            QTimer.singleShot(0, lambda: self.add_file(path))

    # ---- What gets sent --------------------------------------------------------

    def _sent(self, inpainting=None):
        """The references the current model will be sent."""
        if inpainting is None:
            inpainting = self._inpainting
        if not api.sends_references(self._model, inpainting):
            return []
        return [card for card in self._cards if card.is_sent()]

    def used(self):
        """How many references the current model will be sent."""
        return len(self._sent())

    def for_request(self, inpainting=None):
        """The references to send (api.Reference), none if the model takes
        none. ``inpainting``: whether the request inpaints, if that isn't as
        set_inpainting was last told."""
        sent = self._sent(inpainting)
        self._prepare(sent)
        return [card.request() for card in sent]

    def _unmade(self, cards):
        """Those of ``cards`` whose picture a model of the current one's kind
        hasn't been sent before, so that it has yet to be made."""
        cut = api.reference_cut(self._model)
        return [card for card in cards if not card.is_prepared(cut)]

    def _prepare(self, cards):
        waiting = self._unmade(cards)
        if waiting:
            cut = api.reference_cut(self._model)
            self._busy("Preparing the references for this model...",
                       lambda: [card.prepare(cut) for card in waiting])
            self._say("The references are ready for this model.")

    def _taken(self):
        """The references the current model takes: those it's sent, and those
        it would be if they were ticked and there were no mask."""
        return [card for card in self._cards if card.is_taken()]

    def prepare_pictures(self):
        """Make the pictures for the current model that aren't made yet: of
        every reference it takes, so that each shows what the model gets."""
        self._prepare_timer.stop()
        if not self._generating:
            self._prepare(self._taken())

    def _prepare_later(self):
        if not self._generating and self._unmade(self._taken()):
            self._prepare_timer.start()

    def set_generating(self, generating):
        """Tell whether a generation is running. Pictures it wasn't sent wait
        until it's done: making them would hold up its preview, and say so
        over its progress."""
        self._generating = bool(generating)
        self._prepare_later()

    def set_model(self, model):
        if model != self._model:
            self._model = model
            self._refresh()

    def set_inpainting(self, inpainting):
        """Tell whether there is an inpainting mask: V5 takes no references then."""
        if bool(inpainting) != self._inpainting:
            self._inpainting = bool(inpainting)
            self._refresh()

    def _refresh(self):
        model = self._model
        for card in self._cards:
            card.set_model(model)
        self._holder.setVisible(bool(self._cards))
        notes = []
        if self._cards and not api.supports_references(model):
            notes.append("Precise reference needs a V4.5 or V5 model. These aren't used with "
                         "the current model.")
        elif self._cards:
            if not api.sends_references(model, self._inpainting):
                notes.append("V5 takes no references when inpainting: these are left out, "
                             "and cost nothing, while there is a mask.")
            if not api.reference_outfits(model) and any(c.is_outfit() for c in self._cards):
                notes.append("Outfit references need a V5 model. They're skipped with the "
                             "current model.")
        if len(self._cards) >= api.MAX_REFERENCES:
            notes.append("That's as many references as NovelAI takes (%d)." % api.MAX_REFERENCES)
        self.note.setText("\n".join(notes))
        self.note.setVisible(bool(notes))
        self._prepare_later()
