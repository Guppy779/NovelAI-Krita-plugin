"""The inpainting window: paint a mask over a picture of the canvas."""

from . import inpaint, inpaint_math as im
from .qt import (
    QButtonGroup, QColor, QDialog, QDialogButtonBox, QHBoxLayout, QImage, QLabel, QPainter, QPen,
    QPointF, QRectF, QSlider, Qt, QToolButton, QVBoxLayout, QWidget, event_point, image_bytes,
)

BRUSH, ERASER, FOCUS = "brush", "eraser", "focus"
_DEFAULT_BRUSH = 4  # Cells.
_MAX_BRUSH = 64
_UNDO_STEPS = 30
_MASK_COLOR = QColor(70, 110, 255, 150)
_CONTEXT_COLOR = QColor(255, 60, 60, 80)
_MAX_ZOOM = 32.0
_ZOOM_STEP = 1.25  # Per notch of the wheel.
_MARGIN = im.CONTEXT_MAX  # Canvas pixels of room around the picture, for the focus area.


class _MaskCanvas(QWidget):
    """The picture, with the mask on top of it. Editing happens in cells."""

    def __init__(self, image, mask, on_change):
        super().__init__()
        self._image = image
        self.mask = mask
        self._on_change = on_change
        self.tool = BRUSH
        self.brush = _DEFAULT_BRUSH
        self._undo = []
        self._painting = None  # The last cell of the current stroke.
        self._moving = None  # The cell the focus area was last dragged from.
        self._anchor = None  # The first corner of a focus area being drawn.
        self._mouse = None
        self._overlay = None
        self._zoom = 1.0  # Relative to the whole picture fitting the window.
        self._pan = QPointF(0, 0)  # Offset of the picture from the window's centre.
        self._panning = None  # Where a pan drag last was.
        self.setMouseTracking(True)
        self.setMinimumSize(320, 320)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ---- Geometry -----------------------------------------------------------

    def _layout(self):
        """(left, top, widget pixels per canvas pixel)"""
        scale = self._zoom * min(self.width() / float(self._image.width() + 2 * _MARGIN),
                                 self.height() / float(self._image.height() + 2 * _MARGIN))
        return ((self.width() - self._image.width() * scale) / 2.0 + self._pan.x(),
                (self.height() - self._image.height() * scale) / 2.0 + self._pan.y(), scale)

    def _limit_pan(self):
        """Keep the picture on screen: centred if it fits, else filling the window."""
        _, _, scale = self._layout()
        room_x = max(0.0, ((self._image.width() + 2 * _MARGIN) * scale - self.width()) / 2.0)
        room_y = max(0.0, ((self._image.height() + 2 * _MARGIN) * scale - self.height()) / 2.0)
        self._pan = QPointF(max(-room_x, min(room_x, self._pan.x())),
                            max(-room_y, min(room_y, self._pan.y())))

    def fit(self):
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self.update()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        point = event_point(event)
        left, top, scale = self._layout()
        x, y = (point.x() - left) / scale, (point.y() - top) / scale  # Stays under the mouse.
        self._zoom = max(1.0, min(_MAX_ZOOM, self._zoom * _ZOOM_STEP ** steps))
        left, top, scale = self._layout()
        self._pan += QPointF(point.x() - (left + x * scale), point.y() - (top + y * scale))
        self._limit_pan()
        self._mouse = point
        self.update()
        event.accept()

    def _cell_at(self, point):
        left, top, scale = self._layout()
        return ((point.x() - left) / scale / im.CELL, (point.y() - top) / scale / im.CELL)

    def _rounded_cell(self, point):
        x, y = self._cell_at(point)
        return int(round(x)), int(round(y))

    def _clamped_cell(self, point):
        x, y = self._rounded_cell(point)
        return (max(0, min(self.mask.cells.width(), x)),
                max(0, min(self.mask.cells.height(), y)))

    def _cell_rect(self, x, y, width, height):
        left, top, scale = self._layout()
        size = im.CELL * scale
        return QRectF(left + x * size, top + y * size, width * size, height * size)

    # ---- Painting the widget ------------------------------------------------

    def _rebuild_overlay(self):
        cells = inpaint.effective_cells(self.mask)
        # Reinterpret the bytes: convertToFormat would treat grayscale as opaque.
        alpha = QImage(image_bytes(cells), cells.width(), cells.height(),
                       cells.bytesPerLine(), QImage.Format.Format_Alpha8).copy()
        overlay = QImage(cells.width(), cells.height(),
                         QImage.Format.Format_ARGB32_Premultiplied)
        overlay.fill(_MASK_COLOR)
        painter = QPainter(overlay)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, alpha)
        painter.end()
        self._overlay = overlay

    def changed(self):
        self._overlay = None
        self.update()
        self._on_change()

    def paintEvent(self, event):
        left, top, scale = self._layout()
        target = QRectF(left, top, self._image.width() * scale, self._image.height() * scale)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, self._image)
        painter.setClipRect(target)

        if self._overlay is None:
            self._rebuild_overlay()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(self._cell_rect(0, 0, self.mask.cells.width(), self.mask.cells.height()),
                          self._overlay)

        painter.setClipping(False)  # The focus area can reach past the picture.
        if self.mask.focus is not None:
            self._paint_focus(painter)
        if self._mouse is not None and self.tool != FOCUS:
            radius = self.brush * im.CELL * scale / 2.0
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawEllipse(self._mouse, radius, radius)
            painter.setPen(QPen(QColor(0, 0, 0), 1))
            painter.drawEllipse(self._mouse, radius + 1, radius + 1)

    def _paint_focus(self, painter):
        outer = self._cell_rect(*self.mask.focus)
        inner_cells = im.inner_rect(*self.mask.focus, self.mask.context)
        if inner_cells is None:
            painter.fillRect(outer, _CONTEXT_COLOR)
        else:
            inner = self._cell_rect(*inner_cells)
            painter.fillRect(QRectF(outer.left(), outer.top(), outer.width(),
                                    inner.top() - outer.top()), _CONTEXT_COLOR)
            painter.fillRect(QRectF(outer.left(), inner.bottom(), outer.width(),
                                    outer.bottom() - inner.bottom()), _CONTEXT_COLOR)
            painter.fillRect(QRectF(outer.left(), inner.top(), inner.left() - outer.left(),
                                    inner.height()), _CONTEXT_COLOR)
            painter.fillRect(QRectF(inner.right(), inner.top(), outer.right() - inner.right(),
                                    inner.height()), _CONTEXT_COLOR)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawRect(outer)
        painter.setPen(QPen(QColor(0, 0, 0), 1, Qt.PenStyle.DashLine))
        painter.drawRect(outer)

    def resizeEvent(self, event):
        self._limit_pan()
        self.update()

    # ---- Editing ------------------------------------------------------------

    def _remember(self):
        self._undo.append((self.mask.cells.copy(), self.mask.focus))
        del self._undo[:-_UNDO_STEPS]

    def undo(self):
        if self._undo:
            self.mask.cells, self.mask.focus = self._undo.pop()
            self.changed()

    def clear(self):
        self._remember()
        self.mask.cells.fill(0)
        self.mask.focus = None
        self.changed()

    def _stroke(self, start, end):
        painter = QPainter(self.mask.cells)
        color = QColor(255, 255, 255) if self.tool == BRUSH else QColor(0, 0, 0)
        painter.setPen(QPen(color, self.brush, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        if start == end:
            painter.drawPoint(QPointF(*end))
        else:
            painter.drawLine(QPointF(*start), QPointF(*end))
        painter.end()

    def _set_focus_corner(self, corner):
        from_x, from_y = self._anchor
        to_x, to_y = im.limit_corner(from_x, from_y, *corner)
        self.mask.focus = (min(from_x, to_x), min(from_y, to_y),
                           abs(to_x - from_x), abs(to_y - from_y))

    def _inside_focus(self, cell):
        if self.mask.focus is None:
            return False
        x, y, width, height = self.mask.focus
        return x <= cell[0] < x + width and y <= cell[1] < y + height

    def _move_focus(self, cell):
        x, y, width, height = self.mask.focus
        x, y = im.moved_focus(x + cell[0] - self._moving[0], y + cell[1] - self._moving[1], width,
                              height, self.mask.cells.width(), self.mask.cells.height(),
                              self.mask.context)
        self.mask.focus = (x, y, width, height)
        self._moving = cell

    def mousePressEvent(self, event):
        point = event_point(event)
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._panning = point
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._remember()
        if self.tool == FOCUS:
            cell = self._rounded_cell(point)
            if self._inside_focus(cell):
                self._moving = cell
            else:
                self._anchor = self._clamped_cell(point)
                self.mask.focus = None
        else:
            self._painting = self._cell_at(point)
            self._stroke(self._painting, self._painting)
        self.changed()

    def mouseMoveEvent(self, event):
        point = event_point(event)
        self._mouse = point
        if self._panning is not None:
            self._pan += point - self._panning
            self._panning = point
            self._limit_pan()
            self.update()
        elif self._painting is not None:
            cell = self._cell_at(point)
            self._stroke(self._painting, cell)
            self._painting = cell
            self.changed()
        elif self._moving is not None:
            self._move_focus(self._rounded_cell(point))
            self.changed()
        elif self._anchor is not None:
            self._set_focus_corner(self._clamped_cell(point))
            self.changed()
        else:
            self.update()

    def mouseReleaseEvent(self, event):
        if self._panning is not None:
            if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
                self._panning = None
                self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._anchor is not None:
            focus = self.mask.focus
            if focus is not None and min(focus[2], focus[3]) < im.MIN_FOCUS_CELLS:
                self.mask.focus = None
            self.changed()
        self._painting = self._moving = self._anchor = None

    def leaveEvent(self, event):
        self._mouse = None
        self.update()


class InpaintDialog(QDialog):
    """Returns the edited mask from ``mask()`` when accepted."""

    def __init__(self, image, mask, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inpaint")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(900, 820)

        self.canvas = _MaskCanvas(image, mask, self._update_state)

        self._tools = QButtonGroup(self)
        row = QHBoxLayout()
        for tool, label, tip in (
                (BRUSH, "Brush", "Paint the area to regenerate."),
                (ERASER, "Eraser", "Remove from the painted area."),
                (FOCUS, "Focus", "Drag a rectangle. It is enlarged to about one megapixel while\n"
                                 "inpainting, for more detail. Paint inside it, or leave it empty\n"
                                 "to regenerate all of it. Drag inside it to move it; its red\n"
                                 "border can hang off the edge of the picture.")):
            button = QToolButton()
            button.setText(label)
            button.setToolTip(tip)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, name=tool: self._use_tool(name))
            self._tools.addButton(button)
            row.addWidget(button)
            if tool == BRUSH:
                button.setChecked(True)

        self._slider_label = QLabel()
        self._slider_label.setMinimumWidth(170)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.valueChanged.connect(self._slider_moved)
        row.addSpacing(8)
        row.addWidget(self._slider_label)
        row.addWidget(self._slider, 1)

        undo = QToolButton()
        undo.setText("Undo")
        undo.setShortcut("Ctrl+Z")
        undo.clicked.connect(self.canvas.undo)
        clear = QToolButton()
        clear.setText("Clear")
        clear.setToolTip("Remove the painted area and the focus rectangle.")
        clear.clicked.connect(self.canvas.clear)
        fit = QToolButton()
        fit.setText("Fit")
        fit.setToolTip("Scroll over the picture to zoom, and drag with the middle or right\n"
                       "mouse button to move around. This shows the whole picture again.")
        fit.clicked.connect(self.canvas.fit)
        row.addSpacing(8)
        row.addWidget(fit)
        row.addWidget(undo)
        row.addWidget(clear)

        self._hint = QLabel()
        self._hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._use = buttons.addButton("Use mask", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self._hint)
        layout.addWidget(buttons)

        self._use_tool(BRUSH)
        self._update_state()

    def mask(self):
        return self.canvas.mask

    def _use_tool(self, tool):
        self.canvas.tool = tool
        self._slider.blockSignals(True)
        if tool == FOCUS:
            self._slider.setRange(im.CONTEXT_MIN // im.CONTEXT_STEP,
                                  im.CONTEXT_MAX // im.CONTEXT_STEP)
            self._slider.setValue(self.canvas.mask.context // im.CONTEXT_STEP)
        else:
            self._slider.setRange(1, _MAX_BRUSH)
            self._slider.setValue(self.canvas.brush)
        self._slider.blockSignals(False)
        self._show_slider_value()
        self.canvas.update()

    def _slider_moved(self, value):
        if self.canvas.tool == FOCUS:
            self.canvas.mask.context = value * im.CONTEXT_STEP
            self.canvas.changed()
        else:
            self.canvas.brush = value
            self.canvas.update()
        self._show_slider_value()

    def _show_slider_value(self):
        if self.canvas.tool == FOCUS:
            self._slider_label.setText("Minimum context: %d px" % self.canvas.mask.context)
        else:
            self._slider_label.setText("Size: %d px" % (self.canvas.brush * im.CELL))

    def _update_state(self):
        mask = self.canvas.mask
        ready = inpaint.is_painted(inpaint.effective_cells(mask))
        self._use.setEnabled(ready)
        if mask.focus is not None and im.inner_rect(*mask.focus, mask.context) is None:
            self._hint.setText("The focus area is too small for its context. Make it larger, "
                               "or lower Minimum context.")
        elif mask.focus is not None:
            _, _, width, height = inpaint.area_of(mask)
            self._hint.setText(
                "Focus area %d x %d, generated at %d x %d. Only the part inside the red "
                "border is regenerated." % ((width, height) + im.focus_size(width, height)))
        elif ready:
            self._hint.setText("The whole canvas is sent, and only the blue area is regenerated.")
        else:
            self._hint.setText("Paint the area to regenerate, or use Focus to work on part of "
                               "the canvas at a higher resolution.")
