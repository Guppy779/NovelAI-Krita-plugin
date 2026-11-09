"""The Characters section of the panel: character prompts (V4 and newer).

Each character has its own prompt and undesired content, an on/off switch,
and a position, used when "Custom positions" is on. Positions are placed on
a small canvas in the image's shape: anywhere for V5, on the 5 x 5 grid for
V4 and V4.5, which only accept grid cells. Characters are handled as dicts
with the fields of api.CharacterPrompt, the form they're stored in with the
document (see doc_state.py).
"""

from dataclasses import asdict

from . import api
from .prompt_edit import PromptEdit
from .qt import (
    QCheckBox, QComboBox, QFont, QFrame, QHBoxLayout, QLabel, QMenu, QPainter, QPen,
    QPointF, QRectF, Qt, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

CANVAS_SIZE = 220  # Long side of the position canvas, in pixels.
DOT_RADIUS = 11


def _event_point(event):
    """The mouse position of an event as a QPointF (PyQt5 and PyQt6)."""
    return QPointF(event.position() if hasattr(event, "position") else event.pos())


class PositionCanvas(QWidget):
    """The image's shape with a numbered dot per character; drag to place them.

    Opened from one character's position button, which character is
    highlighted; clicking an empty spot moves it there. Any dot can be dragged.
    """

    def __init__(self, editor, card):
        super().__init__()
        self._editor = editor
        self._owner = card
        self._active = card
        self._dragging = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.prepare()

    def prepare(self):
        """Before showing: fit the image's shape and highlight the owner."""
        self._active = self._owner
        self._dragging = None
        width, height = self._editor.aspect()
        scale = CANVAS_SIZE / float(max(width, height, 1))
        w = max(60, int(round(width * scale)))
        h = max(60, int(round(height * scale)))
        self.setFixedSize(w + 2 * DOT_RADIUS, h + 2 * DOT_RADIUS)
        self.update()

    def _area(self):
        """The image's rectangle (dots at its edges still fit in the widget)."""
        return QRectF(self.rect()).adjusted(DOT_RADIUS, DOT_RADIUS, -DOT_RADIUS, -DOT_RADIUS)

    def _center(self, card):
        area = self._area()
        x, y = self._editor.shown_position(card)
        return QPointF(area.left() + x * area.width(), area.top() + y * area.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        area = self._area()
        painter.fillRect(area, palette.base())

        # The 5 x 5 grid: where V4 and V4.5 can place characters; a guide on V5.
        grid_pen = QPen(palette.mid().color())
        grid_pen.setStyle(Qt.PenStyle.DotLine if self._editor.free_positions()
                          else Qt.PenStyle.SolidLine)
        painter.setPen(grid_pen)
        for step in range(1, 5):
            x = area.left() + area.width() * step / 5.0
            y = area.top() + area.height() * step / 5.0
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        painter.setPen(QPen(palette.text().color()))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(area)

        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        cards = self._editor.cards()
        others = [card for card in cards if card is not self._active]
        for card in others + [self._active]:  # The highlighted one on top.
            if card not in cards:
                continue
            center = self._center(card)
            enabled = card.enabled.isChecked()
            if card is self._active:
                fill, text = palette.highlight().color(), palette.highlightedText().color()
            else:
                fill, text = palette.text().color(), palette.base().color()
            outline = QPen(palette.text().color(), 1.5)
            if not enabled:  # Unticked: a dashed ring, not sent.
                outline.setStyle(Qt.PenStyle.DashLine)
                fill, text = None, palette.text().color()
            painter.setPen(outline)
            painter.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, DOT_RADIUS, DOT_RADIUS)
            painter.setPen(text)
            painter.drawText(
                QRectF(center.x() - DOT_RADIUS, center.y() - DOT_RADIUS,
                       2 * DOT_RADIUS, 2 * DOT_RADIUS),
                Qt.AlignmentFlag.AlignCenter, str(cards.index(card) + 1))
        painter.end()

    def _dot_at(self, point):
        cards = self._editor.cards()
        for card in [self._active] + [c for c in reversed(cards) if c is not self._active]:
            if card in cards:
                offset = self._center(card) - point
                if offset.x() ** 2 + offset.y() ** 2 <= DOT_RADIUS ** 2:
                    return card
        return None

    def _move(self, card, point):
        area = self._area()
        x = min(1.0, max(0.0, (point.x() - area.left()) / area.width()))
        y = min(1.0, max(0.0, (point.y() - area.top()) / area.height()))
        self._editor.place(card, x, y)
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = _event_point(event)
        hit = self._dot_at(point)
        if hit is not None:
            self._active = hit  # Grabbed where it is: no jump.
            self._dragging = hit
            self.update()
            return
        self._dragging = self._active
        self._move(self._active, point)

    def mouseMoveEvent(self, event):
        if self._dragging is not None:
            self._move(self._dragging, _event_point(event))

    def mouseReleaseEvent(self, event):
        self._finish_drag()

    def hideEvent(self, event):
        self._finish_drag()  # Closed mid-drag (Esc): there will be no release.
        super().hideEvent(event)

    def _finish_drag(self):
        if self._dragging is not None:
            self._dragging = None
            self._editor.changed()  # Stored once per drag, not on every move.


class PositionButton(QToolButton):
    """Shows a character's position; clicking opens the position canvas."""

    def __init__(self, editor, card):
        super().__init__()
        self._editor = editor
        self._x = self._y = 0.5
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self)
        self.canvas = PositionCanvas(editor, card)
        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("font-style: italic;")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._hint)
        self._action = QWidgetAction(menu)
        self._action.setDefaultWidget(content)
        menu.addAction(self._action)
        menu.aboutToShow.connect(self._about_to_show)
        self.setMenu(menu)
        self.refresh()

    def _about_to_show(self):
        self.canvas.prepare()
        self._hint.setText(
            "Drag the dots, or click to move the highlighted one. V5 places "
            "characters anywhere." if self._editor.free_positions() else
            "Drag the dots, or click to move the highlighted one. This model "
            "places characters on the 5 x 5 grid; V5 places them anywhere.")
        self._hint.setFixedWidth(max(self.canvas.width(), 200))
        # QMenu only measures its items again after one of them changes.
        self._action.setVisible(False)
        self._action.setVisible(True)

    def position(self):
        return self._x, self._y

    def set_position(self, x, y):
        self._x, self._y = x, y
        self.refresh()

    def refresh(self):
        """Show the position the way the current model uses it."""
        if self._editor.free_positions():
            self.setText("%d%%, %d%%" % (round(self._x * 100), round(self._y * 100)))
            self.setToolTip("Where the character's centre goes: %d%% from the left, "
                            "%d%% from the top." % (round(self._x * 100), round(self._y * 100)))
        else:
            name = api.position_name(self._x, self._y)
            self.setText(name)
            self.setToolTip("Where the character's centre goes: grid cell %s (columns A-E "
                            "left to right, rows 1-5 top to bottom)." % name)


class CharacterCard(QFrame):
    """One character: on/off, position, reorder/remove, prompt and undesired content."""

    def __init__(self, editor, suggest, on_image):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        changed = editor.changed

        self.enabled = QCheckBox("Character")
        self.enabled.setChecked(True)
        self.enabled.setToolTip("Untick to leave this character out without deleting it.")
        self.enabled.toggled.connect(changed)
        self.position = PositionButton(editor, self)
        self.up = self._tool_button(Qt.ArrowType.UpArrow, "Move up (earlier in the order)",
                                    lambda: editor.move(self, -1))
        self.down = self._tool_button(Qt.ArrowType.DownArrow, "Move down",
                                      lambda: editor.move(self, 1))
        remove = self._tool_button(None, "Remove this character", lambda: editor.remove(self))
        remove.setText("✕")

        self.prompt = PromptEdit("", suggest, on_image=on_image)
        self.prompt.setPlaceholderText("Character prompt, e.g. girl, red hair, smile")
        self._set_lines(self.prompt, 3)
        self.negative = PromptEdit("", suggest, on_image=on_image)
        self.negative.setPlaceholderText("Undesired content for this character")
        self._set_lines(self.negative, 2)
        self.prompt.textChanged.connect(changed)
        self.negative.textChanged.connect(changed)

        header = QHBoxLayout()
        header.addWidget(self.enabled)
        header.addWidget(self.position)
        header.addStretch(1)
        for button in (self.up, self.down, remove):
            header.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        layout.addLayout(header)
        layout.addWidget(self.prompt)
        layout.addWidget(self.negative)

    @staticmethod
    def _set_lines(edit, lines):
        """Make a text box exactly ``lines`` lines tall (longer text scrolls)."""
        margins = edit.contentsMargins()
        height = (edit.fontMetrics().lineSpacing() * lines
                  + 2 * edit.document().documentMargin() + margins.top() + margins.bottom())
        edit.setFixedHeight(int(height) + 2)

    @staticmethod
    def _tool_button(arrow, tip, slot):
        button = QToolButton()
        if arrow is not None:
            button.setArrowType(arrow)
        button.setAutoRaise(True)
        button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    def value(self):
        x, y = self.position.position()
        return asdict(api.CharacterPrompt(
            self.prompt.toPlainText(), self.negative.toPlainText(), x, y,
            self.enabled.isChecked()))

    def set_value(self, value):
        character = api.CharacterPrompt(**value)
        self.prompt.setPlainText(character.prompt)
        self.negative.setPlainText(character.negative)
        self.position.set_position(character.x, character.y)
        self.enabled.setChecked(character.enabled)


ADD_TIP = ("Add a character prompt. Put the number of characters in the main\n"
           "prompt (e.g. 2girls) and describe each one here (girl, boy or other).")


class CharacterEditor(QWidget):
    """The list of characters, with Add and the AI's choice / Custom positions switch."""

    def __init__(self, suggest, on_image, on_changed, parent=None):
        super().__init__(parent)
        self._suggest, self._on_image, self._on_changed = suggest, on_image, on_changed
        self._cards = []
        self._max = 6
        self._model = ""
        self._aspect = (1, 1)  # The generated image's width and height.

        title = QLabel("Characters")
        self.positions = QComboBox()
        self.positions.addItem("AI's choice", False)
        self.positions.addItem("Custom positions", True)
        self.positions.setToolTip(
            "AI's choice: NovelAI decides where each character goes.\n"
            "Custom positions: each character is centred where you place it\n"
            "(anywhere on V5, on a 5 x 5 grid on V4 and V4.5).")
        self.positions.currentIndexChanged.connect(self._positions_changed)
        self.add_button = QToolButton()
        self.add_button.setText("+ Add")
        self.add_button.setToolTip(ADD_TIP)
        self.add_button.clicked.connect(lambda: self.add())
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("font-style: italic;")

        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.positions)
        header.addWidget(self.add_button)
        self._list = QVBoxLayout()
        self._list.setSpacing(4)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addLayout(header)
        layout.addWidget(self.note)
        layout.addLayout(self._list)
        self._refresh()

    def changed(self, *_):
        self._update_note()
        self._on_changed()

    # ---- Editing -------------------------------------------------------------

    def _create(self, value=None):
        card = CharacterCard(self, self._suggest, self._on_image)
        if value is not None:
            card.set_value(value)
        self._cards.append(card)
        self._list.addWidget(card)
        return card

    def _discard(self, card):
        card.prompt.hide_popup()  # Also cancels a tag lookup still running.
        card.negative.hide_popup()
        self._cards.remove(card)
        self._list.removeWidget(card)
        card.setParent(None)
        card.deleteLater()

    def add(self):
        card = self._create()
        self._refresh()
        card.prompt.setFocus()
        self.changed()

    def remove(self, card):
        self._discard(card)
        self._refresh()
        self.changed()

    def move(self, card, step):
        index = self._cards.index(card)
        target = index + step
        if not 0 <= target < len(self._cards):
            return
        self._cards.insert(target, self._cards.pop(index))
        self._list.insertWidget(target, card)
        self._refresh()
        self.changed()

    def _positions_changed(self, *_):
        self._refresh()
        self.changed()

    # ---- State ---------------------------------------------------------------

    def characters(self):
        return [card.value() for card in self._cards]

    def set_characters(self, values):
        for card in list(self._cards):
            self._discard(card)
        for value in values:
            self._create(value)
        self._refresh()

    # ---- Positions -----------------------------------------------------------

    def cards(self):
        return list(self._cards)

    def free_positions(self):
        return api.free_positions(self._model)

    def aspect(self):
        return self._aspect

    def set_aspect(self, width, height):
        """The shape of the image being generated, for the position canvas."""
        if width > 0 and height > 0:
            self._aspect = (width, height)

    def shown_position(self, card):
        """A character's position as the current model uses it."""
        return api.character_position(self._model, *card.position.position())

    def place(self, card, x, y):
        """Move a character (anywhere on V5, to the nearest grid cell before)."""
        card.position.set_position(*api.character_position(self._model, x, y))

    def custom_positions(self):
        return bool(self.positions.currentData())

    def set_custom_positions(self, on):
        self.positions.blockSignals(True)  # Loading a state isn't an edit.
        self.positions.setCurrentIndex(1 if on else 0)
        self.positions.blockSignals(False)
        self._refresh()

    def set_model(self, model):
        self._model = model
        self._max = api.max_characters(model)
        self._refresh()

    def problem(self):
        """Why the characters can't be sent with this model, or ""."""
        used = sum(1 for c in self.characters() if c["enabled"] and c["prompt"].strip())
        if used and self._max == 0:
            return ("V3 models don't take character prompts. Choose a V4 or newer model, "
                    "or untick the characters.")
        if used > self._max:
            return "This model takes up to %d characters (%d are ticked)." % (self._max, used)
        return ""

    def _refresh(self):
        custom = self.custom_positions()
        count = len(self._cards)
        for index, card in enumerate(self._cards):
            card.enabled.setText("Character %d" % (index + 1))
            card.position.setVisible(custom)
            card.position.refresh()  # Grid cell or exact, depending on the model.
            card.up.setEnabled(index > 0)
            card.down.setEnabled(index < count - 1)
        self.positions.setVisible(count > 0)
        self.add_button.setEnabled(count < self._max)
        self.add_button.setToolTip(
            ADD_TIP if self._max else "V3 models don't take character prompts.")
        self._update_note()

    def _update_note(self):
        problem = self.problem()
        self.note.setText(problem)
        self.note.setVisible(bool(problem))
