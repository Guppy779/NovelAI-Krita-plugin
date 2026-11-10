"""Reading from and writing to the Krita canvas."""

import base64
from dataclasses import dataclass
from typing import Optional

from krita import Krita

from . import inpaint
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


def read_image(doc, region: Region) -> QImage:
    """The visible canvas inside ``region``, flattened onto white."""
    projection = doc.projection(region.x, region.y, region.width, region.height)
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


scaling_problem = ""  # Why the last sharp_resize had to fall back, if it did.


def sharp_resize(image: QImage, width: int, height: int) -> QImage:
    """Scale with Lanczos3, as the NovelAI site does, using Krita's own scaler.

    Qt's smooth scaling is softer, and NovelAI generates blurry detail from a
    soft image. Falls back to it if Krita can't do the scaling.
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


def to_base64_png(image: QImage) -> str:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return base64.b64encode(bytes(buffer.data())).decode("ascii")


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
    scale = sharp_resize if sharp else resize
    image = scale(result, region.width, region.height)
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
