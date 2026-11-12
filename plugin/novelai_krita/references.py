"""The Precise Reference section of the panel (V4.5 only).

A reference is a picture for NovelAI to take a character, a style or both
from, with the site's Strength and Fidelity sliders.

Each open document has its own references. They aren't stored anywhere, so
that documents stay small: they last until they're removed, or the document
or Krita is closed. Each picture is kept as the PNG that is sent: on black,
at one of NovelAI's sizes. It's scaled with the site's own arithmetic (see
scaling.py): NovelAI draws a visibly different image from a reference that
is a shade off.
"""

import os

from . import api, canvas, scaling, settings
from .import_dialog import MAX_FILE_SIZE
from .prompt_edit import IMAGE_EXTENSIONS
from .qt import (
    QCheckBox, QColor, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QImage,
    QImageReader, QLabel, QMenu, QPainter, QPixmap, QSlider, Qt, QTimer, QToolButton, QVBoxLayout, QWidget,
    ignore_wheel, image_bytes,
)

THUMBNAIL_SIZE = 84
STEP = 0.05  # Of the sliders, as on the site.
# The site's sliders run from 0 to 1, but take typed numbers beyond that.
AMOUNT_MIN, AMOUNT_MAX = -1.0, 2.0
_RGB = QImage.Format.Format_RGB32
_RGBA = QImage.Format.Format_RGBA8888

ADD_TIP = ("Add a picture for NovelAI to take a character, a style or both from.\n"
           "You can also drop an image file here. V4.5 Full and V4.5 Curated only.\n"
           "Each reference costs %d Anlas per image, on every plan.\n"
           "Each open document has its own references. They aren't saved: they\n"
           "last until you remove them, or close the document or Krita."
           % api.REFERENCE_ANLAS)
TYPE_TIP = ("What NovelAI takes from the picture:\n"
            "Character & Style: the character's looks and the art style.\n"
            "Character: only the character's looks.\n"
            "Style: only the art style.")
STRENGTH_TIP = ("How strongly the reference is followed. Lower it if poses and\n"
                "expressions copy the reference too closely.")
FIDELITY_TIP = ("How exactly the reference is applied. At 1 it is hard to change\n"
                "with the prompt; lower values give the prompt more say.")
NUMBER_TIP = "\nType a number to go beyond the slider, below 0 included."


def read_picture(path):
    """The image in a file (null if it can't be read). A JPEG is turned the way
    its EXIF data says, as the site and image viewers show it; Qt leaves it
    as stored unless asked."""
    reader = QImageReader(path)
    reader.setAutoTransform(bytes(reader.format()).lower() in (b"jpeg", b"jpg"))
    return reader.read()


def prepare(image):
    """``image`` as a reference: (the PNG that is sent, as base64; its size;
    the picture in it, to show).

    Pixel for pixel what the NovelAI site makes of the same picture: scaled
    to fit one of NovelAI's sizes, centred, with black around and behind it.
    """
    source = image.convertToFormat(_RGBA)
    width, height = source.width(), source.height()
    size, (to_width, to_height), (left, top) = api.reference_layout(width, height)
    data = scaling.without_hidden_data(image_bytes(source), width, height)
    data = scaling.on_black(scaling.resize(data, width, height, to_width, to_height))
    picture = QImage(data, to_width, to_height, to_width * 4, _RGBA).convertToFormat(_RGB)
    whole = QImage(size[0], size[1], _RGB)
    whole.fill(QColor(0, 0, 0))
    painter = QPainter(whole)
    painter.drawImage(left, top, picture)
    painter.end()
    # The site's PNG has an alpha channel too.
    return canvas.to_base64_png(whole.convertToFormat(_RGBA)), size, picture


class _Amount(QWidget):
    """A slider from 0 to 1, and a number box that also takes values beyond it."""

    def __init__(self, tip, on_change):
        super().__init__()
        self._on_change = on_change
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, int(round(1 / STEP)))
        self.slider.setToolTip(tip)
        self.box = QDoubleSpinBox()
        self.box.setRange(AMOUNT_MIN, AMOUNT_MAX)
        self.box.setDecimals(2)
        self.box.setSingleStep(STEP)
        self.box.setToolTip(tip + NUMBER_TIP)
        self.box.setValue(1.0)
        self.slider.setValue(self._position(1.0))
        self.slider.valueChanged.connect(self._slid)
        self.box.valueChanged.connect(self._typed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.box)

    @staticmethod
    def _position(value):
        return int(round(max(0.0, min(1.0, value)) / STEP))

    def _slid(self, position):
        if position != self._position(self.box.value()):  # A typed in-between value stays.
            self.box.setValue(position * STEP)

    def _typed(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(self._position(value))
        self.slider.blockSignals(False)
        self._on_change()

    def value(self):
        return round(self.box.value(), 2)


class ReferenceCard(QFrame):
    """One reference: its picture, on/off, type, strength and fidelity."""

    def __init__(self, editor, sent, size, picture):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._sent = sent  # The PNG that is sent, as base64.
        changed = editor.changed

        thumbnail = QLabel()
        thumbnail.setFixedWidth(THUMBNAIL_SIZE)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail.setPixmap(QPixmap.fromImage(picture.scaled(
            THUMBNAIL_SIZE, THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)))
        thumbnail.setToolTip("Sent at %d x %d, with black around it where it doesn't fill that."
                             % size)

        self.enabled = QCheckBox()
        self.enabled.setChecked(True)
        self.enabled.setToolTip("Untick to leave this reference out without deleting it.")
        self.enabled.toggled.connect(changed)
        self.kind = QComboBox()
        for value, label in api.REFERENCE_TYPES:
            self.kind.addItem(label, value)
        self.kind.setToolTip(TYPE_TIP)
        self.kind.currentIndexChanged.connect(changed)
        remove = QToolButton()
        remove.setText("✕")
        remove.setAutoRaise(True)
        remove.setToolTip("Remove this reference")
        remove.clicked.connect(lambda: editor.remove(self))
        self.strength = _Amount(STRENGTH_TIP, changed)
        self.fidelity = _Amount(FIDELITY_TIP, changed)

        header = QHBoxLayout()
        header.addWidget(self.enabled)
        header.addWidget(self.kind, 1)
        header.addWidget(remove)
        controls = QVBoxLayout()
        controls.setSpacing(3)
        controls.addLayout(header)
        labels = [QLabel("Strength"), QLabel("Fidelity")]
        width = max(label.sizeHint().width() for label in labels)
        for label, amount in zip(labels, (self.strength, self.fidelity)):
            label.setMinimumWidth(width)
            row = QHBoxLayout()
            row.addWidget(label)
            row.addWidget(amount, 1)
            controls.addLayout(row)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(thumbnail)
        layout.addLayout(controls, 1)
        ignore_wheel(self)  # Scrolling the panel mustn't change these.
        self._greyed = [thumbnail, self.enabled, self.kind, self.strength, self.fidelity] + labels

    def set_used(self, used):
        """Grey the card out while the model takes no references. Removing it
        still works."""
        for widget in self._greyed:
            widget.setEnabled(used)

    def request(self):
        """This reference as it goes in a request."""
        return api.Reference(self._sent, self.kind.currentData(), self.strength.value(),
                             self.fidelity.value())


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
        self.setAcceptDrops(True)

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

    def add_image(self, image):
        """Add ``image`` (a QImage) as a reference. False if it couldn't be."""
        self._follow()  # It's for the document that is active now.
        if len(self._cards) >= api.MAX_REFERENCES:
            self._say("NovelAI takes at most %d precise references." % api.MAX_REFERENCES)
            return False
        if image.isNull() or image.width() < 1 or image.height() < 1:
            return False
        card = ReferenceCard(self, *prepare(image))
        self._cards.append(card)
        self._list.addWidget(card)
        self._refresh()
        self.changed()
        return True

    def add_file(self, path):
        name = os.path.basename(path)
        try:
            too_large = os.path.getsize(path) > MAX_FILE_SIZE
        except OSError as exc:
            self._say("Could not read %s: %s." % (name, exc.strerror or exc))
            return
        if too_large:
            self._say("%s is too large for a reference (over %d MB)."
                      % (name, MAX_FILE_SIZE // (1024 * 1024)))
            return
        if not self.add_image(read_picture(path)):
            if len(self._cards) < api.MAX_REFERENCES:
                self._say("Could not read %s as an image." % name)
            return
        self._say("Added %s as a precise reference." % name)

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
        self._refresh()
        self.changed()

    # ---- Dropped image files -------------------------------------------------

    @staticmethod
    def _dropped_image(event):
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            path = url.toLocalFile() if url.isLocalFile() else ""
            if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS:
                return path
        return None

    def dragEnterEvent(self, event):
        if self._dropped_image(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self._dropped_image(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = self._dropped_image(event)
        if path:
            event.acceptProposedAction()
            # After the drop has finished: the system's drag is still running here.
            QTimer.singleShot(0, lambda: self.add_file(path))

    # ---- What gets sent --------------------------------------------------------

    def used(self):
        """How many references the current model will be sent."""
        if not api.supports_references(self._model):
            return 0
        return sum(1 for card in self._cards if card.enabled.isChecked())

    def for_request(self):
        """The references to send (api.Reference), none if the model takes none."""
        if not api.supports_references(self._model):
            return []
        return [card.request() for card in self._cards if card.enabled.isChecked()]

    def set_model(self, model):
        if model != self._model:
            self._model = model
            self._refresh()

    def _refresh(self):
        supported = api.supports_references(self._model)
        for card in self._cards:
            card.set_used(supported)
        self._holder.setVisible(bool(self._cards))
        if self._cards and not supported:
            text = ("Precise reference needs V4.5 Full or V4.5 Curated. These aren't used "
                    "with the current model.")
        elif len(self._cards) >= api.MAX_REFERENCES:
            text = "That's as many references as NovelAI takes (%d)." % api.MAX_REFERENCES
        else:
            text = ""
        self.note.setText(text)
        self.note.setVisible(bool(text))
