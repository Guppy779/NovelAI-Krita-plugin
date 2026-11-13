"""Prompt text box with NovelAI tag autocomplete.

While typing, the tag under the cursor is looked up (after a short pause)
and suggestions appear in a popup. Up/Down choose, Tab or Enter insert,
Escape closes. Suggestions are fetched through a ``suggest`` callback so
this widget knows nothing about tokens or models.

Image files dropped on the box go to an ``on_image`` callback (to import
their prompt) instead of being inserted as text.

Weighted parts of the prompt are coloured as on the NovelAI site.
"""

import os

from . import api, weights
from .qt import (
    QColor, QCompleter, QFont, QModelIndex, QPlainTextEdit, QStandardItem,
    QStandardItemModel, QSyntaxHighlighter, Qt, QTextCharFormat, QTextCursor, QTimer,
)
from .tags import completion_text, tag_at_cursor

DELAY_MS = 250
TAG_ROLE = Qt.ItemDataRole.UserRole

_ACCEPT_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab)
_COMPLETER_KEYS = _ACCEPT_KEYS + (Qt.Key.Key_Escape, Qt.Key.Key_Backtab)
_HIDE_KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End)


IMAGE_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg")


def dropped_image_file(mime):
    """The path of the first image file among what is being dragged or
    dropped (its QMimeData), or None."""
    if not mime.hasUrls():
        return None
    for url in mime.urls():
        path = url.toLocalFile() if url.isLocalFile() else ""
        if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS:
            return path
    return None

# The NovelAI site's default dark theme.
WEIGHT_COLORS = {weights.HIGH: "#b83700", weights.MID: "#009707", weights.LOW: "#0466ce"}


def _utf16_length(text):
    """``text``'s length in Qt's units, where an emoji counts as two."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


class _WeightHighlighter(QSyntaxHighlighter):
    """Colours weighted text. A line starts with the weight the last one ended on."""

    numeric = True  # The model understands 1.2:: weights.

    def highlightBlock(self, text):
        found, weight = weights.spans(
            text, weights.from_state(self.previousBlockState()), self.numeric)
        for span in found:
            color = QColor(WEIGHT_COLORS[span.color])
            color.setAlphaF(span.opacity)
            style = QTextCharFormat()
            style.setBackground(color)
            start = _utf16_length(text[:span.start])
            self.setFormat(start, _utf16_length(text[span.start:span.end]), style)
        self.setCurrentBlockState(weights.to_state(weight))


class PromptEdit(QPlainTextEdit):
    def __init__(self, text="", suggest=None, parent=None, on_image=None):
        """``suggest(query, lang, on_results)`` starts a lookup and returns a
        job with ``cancel()``, or None when suggestions are unavailable.
        ``on_image(path)`` is called with an image file dropped on the box."""
        super().__init__(text, parent)
        self._suggest = suggest
        self._on_image = on_image
        self._job = None
        self._context = None

        self._model = QStandardItemModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._completer.setMaxVisibleItems(10)
        self._completer.activated[QModelIndex].connect(self._insert)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DELAY_MS)
        self._timer.timeout.connect(self._lookup)
        self.textChanged.connect(self._text_changed)
        self._highlighter = _WeightHighlighter(self.document())

    def set_model(self, model):
        """Colour the weights the way ``model`` reads them."""
        numeric = api.is_v4(model)
        if numeric != self._highlighter.numeric:
            self._highlighter.numeric = numeric
            blocked = self.blockSignals(True)  # New colours aren't an edit.
            self._highlighter.rehighlight()
            self.blockSignals(blocked)

    def _text_changed(self):
        # Close stale suggestions as soon as there is no tag at the cursor
        # (typed a comma, text replaced, ...). While a tag is still being
        # typed the list stays open until fresh suggestions arrive.
        if self.popup_visible() and self._current_context() is None:
            self.hide_popup()

    def popup_visible(self):
        return self._completer.popup().isVisible()

    def hide_popup(self):
        self._timer.stop()
        if self._job is not None:
            self._job.cancel()
            self._job = None
        self._completer.popup().hide()

    # ---- Keyboard ---------------------------------------------------------

    def keyPressEvent(self, event):
        key = event.key()
        if self.popup_visible():
            if key in _ACCEPT_KEYS:
                # Normally QCompleter handles these itself; this is a fallback
                # in case the key reaches the editor anyway.
                index = self._completer.popup().currentIndex()
                self._completer.popup().hide()
                if index.isValid():
                    self._insert(index)
                    return
            elif key in _COMPLETER_KEYS:
                event.ignore()
                return

        super().keyPressEvent(event)

        text = event.text()
        if key == Qt.Key.Key_Backspace or (text and text.isprintable()):
            self._timer.start()
        elif key in _HIDE_KEYS:
            self.hide_popup()

    def inputMethodEvent(self, event):
        # Japanese/Chinese input arrives from the IME here, not as key presses.
        super().inputMethodEvent(event)
        if event.commitString():
            self._timer.start()

    # ---- Dropped images ---------------------------------------------------

    def _dropped_image(self, event):
        """The path of the image file being dragged, or None."""
        if self._on_image is None:
            return None
        return dropped_image_file(event.mimeData())

    def dragEnterEvent(self, event):
        if self._dropped_image(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._dropped_image(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        path = self._dropped_image(event)
        if not path:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        # Open the import dialog after the drop has finished, not inside it
        # (the system's drag and drop is still running during this event).
        QTimer.singleShot(0, lambda: self._on_image(path))

    def focusOutEvent(self, event):
        # The popup takes focus while open; only hide for other widgets.
        if not self.popup_visible():
            self._timer.stop()
        super().focusOutEvent(event)

    # ---- Lookup -----------------------------------------------------------

    def _current_context(self):
        return tag_at_cursor(self.toPlainText(), self.textCursor().position())

    def _lookup(self):
        context = self._current_context()
        if context is None or self._suggest is None:
            self.hide_popup()
            return
        if self._job is not None:
            self._job.cancel()
        self._context = context
        self._job = self._suggest(
            context.query, context.lang,
            lambda results: self._show(context, results))

    def _show(self, context, results):
        self._job = None
        # Ignore answers for text the user has since changed.
        current = self._current_context()
        if current is None or current.query != context.query:
            return
        self._context = current
        if not results or not self.hasFocus():
            self._completer.popup().hide()
            return

        self._model.clear()
        for result in results:
            item = QStandardItem("%s   %s" % (result.label, api.format_count(result.count)))
            item.setData(result.tag, TAG_ROLE)
            item.setToolTip(result.tag)
            if not result.exact:  # The server's fuzzy matches.
                font = QFont(item.font())
                font.setItalic(True)
                item.setFont(font)
            self._model.appendRow(item)

        popup = self._completer.popup()
        rect = self.cursorRect()
        rect.setWidth(max(220, popup.sizeHintForColumn(0)
                          + popup.verticalScrollBar().sizeHint().width() + 8))
        self._completer.complete(rect)
        popup.setCurrentIndex(self._model.index(0, 0))

    def _insert(self, index):
        tag = index.data(TAG_ROLE)
        context = self._current_context() or self._context
        if not tag or context is None:
            return
        text = self.toPlainText()
        cursor = self.textCursor()
        cursor.setPosition(context.start)
        cursor.setPosition(context.end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(completion_text(text, context, tag))
        self.setTextCursor(cursor)
        self._context = None
