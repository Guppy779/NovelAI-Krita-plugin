"""Reading from and writing to the Krita canvas."""

import base64
from dataclasses import dataclass
from typing import Optional

from krita import Krita

from . import inpaint, scaling
from .qt import QBuffer, QByteArray, QColor, QIODevice, QImage, QPainter, Qt, image_bytes


@dataclass
class Region:
    """The part of the document a generation works on (document pixels)."""

    x: int
    y: int
    width: int
    height: int
    # Inpainting: how much of the result to keep (8-bit grayscale of any size).
    mask: Optional[QImage] = None
    # A focus area of inpainting. The site scales its result back to the
    # area, so that's done pixel for pixel the same. A result for the whole
    # of a large canvas is scaled by Krita: the site has no such step.
    focused: bool = False


def active_document():
    return Krita.instance().activeDocument()


def main_window():
    """Krita's main window as a Qt widget, or None."""
    window = Krita.instance().activeWindow()
    return window.qwindow() if window is not None else None


def has_selection(doc) -> bool:
    selection = doc.selection() if doc is not None else None
    return selection is not None and selection.width() > 0 and selection.height() > 0


def selection_cells(doc):
    """The selection as inpainting mask cells, or None if nothing is selected."""
    if not has_selection(doc):
        return None
    width, height = doc.width(), doc.height()
    data = bytes(doc.selection().pixelData(0, 0, width, height))
    return inpaint.cells_from_pixels(
        QImage(data, width, height, width, QImage.Format.Format_Grayscale8).copy())


_RGBA = QImage.Format.Format_RGBA8888


def without_hidden_data(image: QImage) -> QImage:
    """``image`` without the settings NovelAI hides in its alpha channel.

    They leave it a shade transparent here and there, and so a shade off once
    it's on a canvas. The site takes them out before it works with an image.
    """
    source = image.convertToFormat(_RGBA)
    width, height = source.width(), source.height()
    data = image_bytes(source)
    cleaned = scaling.without_hidden_data(data, width, height)
    if cleaned == data:
        return image
    return QImage(cleaned, width, height, width * 4, _RGBA).copy()


def _has_hidden_data(doc) -> bool:
    """Whether the canvas is a NovelAI image that still has its hidden data
    (one opened in Krita, say). The site's test, on the whole canvas."""
    width, height = doc.width(), doc.height()
    if width < 1 or height < 1 or width * height > scaling.MAX_CLEANED:
        return False
    columns = min(width, -(-scaling.MARKER_PIXELS // height))  # The ones the marker is in.
    strip = doc.projection(0, 0, columns, height).convertToFormat(_RGBA)
    return scaling.has_hidden_data(image_bytes(strip), columns, height)


def read_image(doc, region: Region) -> QImage:
    """The visible canvas inside ``region``, flattened onto white.

    A NovelAI image's hidden data is taken out first, as the site does: left
    in, it would let a little white through.
    """
    projection = doc.projection(region.x, region.y, region.width, region.height)
    if _has_hidden_data(doc):
        source = projection.convertToFormat(_RGBA)
        projection = QImage(scaling.whole_alpha(image_bytes(source)), source.width(),
                            source.height(), source.width() * 4, _RGBA).copy()
    flat = QImage(region.width, region.height, QImage.Format.Format_RGB32)
    flat.fill(QColor(255, 255, 255))
    painter = QPainter(flat)
    painter.drawImage(0, 0, projection)
    painter.end()
    return flat


def small_image(doc, side: int) -> QImage:
    """The visible canvas at most ``side`` pixels across, flattened onto white.

    Krita keeps it cheap: nothing like reading the whole canvas.
    """
    thumbnail = doc.thumbnail(side, side)
    flat = QImage(thumbnail.size(), QImage.Format.Format_RGB32)
    flat.fill(QColor(255, 255, 255))
    painter = QPainter(flat)
    painter.drawImage(0, 0, thumbnail)
    painter.end()
    return flat


def resize(image: QImage, width: int, height: int) -> QImage:
    if image.width() == width and image.height() == height:
        return image
    return image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


scaling_problem = ""  # Why the last careful_resize had to fall back, if it did.


def sharp_resize(image: QImage, width: int, height: int) -> QImage:
    """Scale with Lanczos3, using Krita's own scaler.

    For a result that goes back onto a canvas larger than it was generated
    at. The site leaves its results at the size they were generated at, so
    there is nothing of the site's to copy, and Krita is quick with large
    pictures. Qt's smooth scaling is softer. Falls back to it if Krita can't
    do the scaling.
    """
    global scaling_problem
    scaling_problem = ""
    if image.width() == width and image.height() == height:
        return image
    source = image.convertToFormat(QImage.Format.Format_ARGB32)
    try:
        doc = Krita.instance().createDocument(
            source.width(), source.height(), "NovelAI scaling", "RGBA", "U8", "", 72.0)
        try:
            doc.setBatchmode(True)
            node = doc.createNode("image", "paintlayer")
            doc.rootNode().addChildNode(node, None)
            node.setPixelData(QByteArray(image_bytes(source)), 0, 0,
                              source.width(), source.height())
            doc.scaleImage(width, height, 72, 72, "Lanczos3")
            doc.waitForDone()
            data = bytes(node.pixelData(0, 0, width, height))
        finally:
            doc.close()
    except Exception as exc:  # Scaling is never a reason to lose a generation.
        scaling_problem = str(exc) or type(exc).__name__
        return resize(image, width, height)
    if len(data) != width * height * 4:
        scaling_problem = "Krita returned the wrong amount of image data"
        return resize(image, width, height)
    return QImage(data, width, height, width * 4, QImage.Format.Format_ARGB32).copy()


def exact_resize(image: QImage, width: int, height: int) -> QImage:
    """Scale pixel for pixel as the NovelAI site does in the browser (see scaling.py)."""
    if image.width() == width and image.height() == height:
        return image
    source = image.convertToFormat(_RGBA)
    data = scaling.resize(image_bytes(source), source.width(), source.height(), width, height)
    return QImage(data, width, height, width * 4, _RGBA).copy()


def picture_to_send(doc, region: Region, width: int, height: int) -> QImage:
    """The canvas inside ``region`` as the site sends a picture for image to
    image and inpainting: scaled to the generation's size with the site's own
    arithmetic, and with an alpha channel."""
    return exact_resize(read_image(doc, region), width, height).convertToFormat(_RGBA)


def read_picture(doc) -> QImage:
    """The visible canvas as a picture with its transparency: what a director
    tool is given. The site sends a tool the picture as it is, clear parts
    and NovelAI's hidden data included, unlike image to image."""
    return doc.projection(0, 0, doc.width(), doc.height()).convertToFormat(_RGBA)


def tool_picture(image: QImage, width: int, height: int) -> QImage:
    """``image`` as the site sends it to a director tool: scaled to the size
    with the site's own arithmetic, transparency kept, nothing put behind it."""
    return exact_resize(image, width, height).convertToFormat(_RGBA)


def careful_resize(image: QImage, width: int, height: int, region: Region) -> QImage:
    """The scaling for a finished result of ``region``."""
    global scaling_problem
    scaling_problem = ""  # Also when Krita's scaler, which sets it, isn't used.
    scale = exact_resize if region.focused else sharp_resize
    return scale(image, width, height)


def to_png(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def to_base64_png(image: QImage) -> str:
    return base64.b64encode(to_png(image)).decode("ascii")


def _apply_alpha(image: QImage, mask: QImage) -> None:
    """Multiply ``image``'s alpha by a grayscale mask of the same size."""
    painter = QPainter(image)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, inpaint.as_alpha(mask))
    painter.end()


def compose_result(result: QImage, region: Region, sharp: bool = False) -> QImage:
    """Scale a generated image back to the region and make it a layer.

    With ``region.mask`` (inpainting) it fades out around the masked area.
    ``sharp``: use the slower scaling meant for finished results.
    """
    if sharp:
        image = careful_resize(result, region.width, region.height, region)
    else:
        image = resize(result, region.width, region.height)
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if region.mask is not None:
        _apply_alpha(image, resize(region.mask, region.width, region.height)
                     .convertToFormat(QImage.Format.Format_Grayscale8))
    return image.convertToFormat(QImage.Format.Format_ARGB32)


PREVIEW_SIDE = 1024  # Long side of the picture an inpainted result is shown on.


def preview_backdrop(doc) -> QImage:
    """A small copy of the canvas, to show inpainted results on."""
    whole = read_image(doc, Region(0, 0, doc.width(), doc.height()))
    return whole.scaled(PREVIEW_SIDE, PREVIEW_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def compose_preview(result: QImage, region: Region, backdrop: QImage, canvas_width: int) -> QImage:
    """``backdrop`` with an inpainted result pasted where it will go."""
    scale = backdrop.width() / float(canvas_width)
    small = Region(0, 0, max(1, int(round(region.width * scale))),
                   max(1, int(round(region.height * scale))), region.mask)
    shown = backdrop.copy()
    painter = QPainter(shown)
    painter.drawImage(int(round(region.x * scale)), int(round(region.y * scale)),
                      compose_result(result, small))
    painter.end()
    return shown


def insert_layer(doc, image: QImage, x: int, y: int, name: str):
    """Add ``image`` (ARGB32) as a new paint layer above the active layer."""
    root = doc.rootNode()
    active = doc.activeNode()
    parent = active.parentNode() if active is not None else None
    if parent is None:
        parent, active = root, None

    node = doc.createNode(name, "paintlayer")
    parent.addChildNode(node, active)
    write_pixels(doc, node, image, x, y)
    doc.refreshProjection()
    return node


def write_pixels(doc, node, image: QImage, x: int, y: int) -> None:
    """Replace ``node``'s pixels in the image's rectangle with ``image``."""
    # Krita stores 8-bit RGBA as BGRA, the same layout as QImage ARGB32 on
    # little-endian machines. Other document color spaces are converted.
    native = (doc.colorModel(), doc.colorDepth()) == ("RGBA", "U8")
    if not native:
        node.setColorSpace("RGBA", "U8", "sRGB-elle-V2-srgbtrc.icc")
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    node.setPixelData(QByteArray(image_bytes(image)), x, y, image.width(), image.height())
    if not native:
        node.setColorSpace(doc.colorModel(), doc.colorDepth(), doc.colorProfile())


def scale_document(doc, width: int, height: int) -> None:
    """Scale the whole document, layers and all, to a new size (Image > Scale
    Image To New Size)."""
    doc.scaleImage(width, height, int(doc.xRes()), int(doc.yRes()), "Bicubic")
    doc.waitForDone()


def new_document(image: QImage, name: str, like=None):
    """Open ``image`` as a new document with one layer, and show it.

    ``like``: a document to take the resolution from.
    """
    app = Krita.instance()
    resolution = float(like.xRes()) if like is not None else 300.0
    doc = app.createDocument(image.width(), image.height(), name, "RGBA", "U8", "", resolution)
    root = doc.rootNode()
    filled = root.childNodes()  # Krita starts a document with a white layer.
    node = doc.createNode(name, "paintlayer")
    root.addChildNode(node, None)
    write_pixels(doc, node, image, 0, 0)
    for other in filled:
        other.remove()
    doc.refreshProjection()
    window = app.activeWindow()
    if window is not None:
        window.addView(doc)
    return doc


def read_annotation(doc, key: str) -> bytes:
    """A document annotation's bytes (saved in the .kra), or b"" if it has none."""
    data = doc.annotation(key)
    return bytes(data) if data is not None and data.size() > 0 else b""


def write_annotation(doc, key: str, description: str, data: bytes) -> None:
    # Doesn't mark the document as modified; it's saved with the next save.
    doc.setAnnotation(key, description, QByteArray(data))


def same_document(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return a == b


def document_is_open(doc) -> bool:
    return doc is not None and any(d == doc for d in Krita.instance().documents())
