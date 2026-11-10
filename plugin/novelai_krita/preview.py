"""The preview window: the image being generated and the results so far.

It stays above Krita without blocking it. Only the widgets are here; the
docker decides what they do.
"""

from . import settings
from .qt import (
    QAbstractItemView, QByteArray, QHBoxLayout, QLabel, QListWidget, QPainter, QPen,
    QProgressBar, QPushButton, QRectF, QSize, QStyle, QStyledItemDelegate, Qt, QVBoxLayout,
    QWidget,
)

THUMBNAIL_SIZE = 96
_THUMBNAIL_PADDING = 4


class _ThumbnailDelegate(QStyledItemDelegate):
    """Draws a result as just its picture, framed when it's the one shown.

    The style's own drawing keeps a line for text under each picture, and
    highlights it when selected, even with no text.
    """

    def sizeHint(self, option, index):
        side = THUMBNAIL_SIZE + 2 * _THUMBNAIL_PADDING
        return QSize(side, side)

    def paint(self, painter, option, index):
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon is None:
            return
        cell = QRectF(option.rect)
        size = icon.actualSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        picture = QRectF(cell.center().x() - size.width() / 2.0,
                         cell.center().y() - size.height() / 2.0, size.width(), size.height())
        painter.save()
        icon.paint(painter, picture.toRect())
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(option.palette.windowText().color(), 2))
            painter.drawRoundedRect(picture.adjusted(-2, -2, 2, 2), 3, 3)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            color = option.palette.windowText().color()
            color.setAlpha(90)
            painter.setPen(QPen(color, 1))
            painter.drawRect(picture.adjusted(-1, -1, 0, 0))
        painter.restore()


class _ImageView(QWidget):
    """Shows an image as large as fits, keeping its shape."""

    def __init__(self):
        super().__init__()
        self._image = None
        self.setMinimumSize(256, 256)

    def set_image(self, image):
        self._image = image
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._image is None or self._image.isNull():
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Generated images appear here.")
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        painter.drawImage(QRectF((self.width() - size.width()) / 2.0,
                                 (self.height() - size.height()) / 2.0,
                                 size.width(), size.height()), self._image)


class PreviewWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool)
        self.setWindowTitle("NovelAI preview")
        # Opening it must not take the keyboard away from a prompt box.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(520, 720)
        self._restored = False

        self.view = _ImageView()

        self.generate_button = QPushButton("Generate")
        self.generate_button.setMinimumHeight(32)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.add_button = QPushButton("Add as layer")
        self.add_button.setMinimumHeight(32)
        self.add_button.setEnabled(False)
        self.add_button.setToolTip("Add the image shown above to the document as a new layer.")
        buttons = QHBoxLayout()
        buttons.addWidget(self.generate_button, 1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.add_button, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        self.progress.setVisible(False)

        self.status = QLabel("")
        self.status.setWordWrap(True)

        self.history = QListWidget()
        self.history.setViewMode(QListWidget.ViewMode.IconMode)
        self.history.setFlow(QListWidget.Flow.LeftToRight)
        self.history.setWrapping(False)
        self.history.setIconSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        self.history.setItemDelegate(_ThumbnailDelegate(self.history))
        self.history.setMouseTracking(True)  # For the frame on hover.
        self.history.setMovement(QListWidget.Movement.Static)
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.setToolTip("Click a result to see it. Double-click to add it as a new layer.")
        # Room for one row of thumbnails and a horizontal scroll bar.
        self.history.setFixedHeight(THUMBNAIL_SIZE + 36)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.view, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.history)

    def open(self, owner=None):
        """Show the window, kept above ``owner`` (Krita's main window)."""
        if owner is not None and self.parent() is None:
            self.setParent(owner, self.windowFlags())
        if not self._restored:
            self._restored = True
            saved = settings.get("preview_geometry")
            if saved:
                self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii")))
        self.show()
        self.raise_()

    def set_busy(self, busy):
        self.generate_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)

    def save_geometry(self):
        settings.set("preview_geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))

    def hideEvent(self, event):
        self.save_geometry()
        super().hideEvent(event)
