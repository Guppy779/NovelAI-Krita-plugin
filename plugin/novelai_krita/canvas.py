"""Reading from and writing to the Krita canvas."""

import base64
from dataclasses import dataclass
from typing import Optional

from krita import Krita

from .qt import QBuffer, QByteArray, QColor, QIODevice, QImage, QPainter, Qt, image_bytes

# Lookup table that turns any partially selected pixel into fully selected,
# so feathered selection edges are regenerated (and blended back softly).
_BINARIZE = bytes([0] + [255] * 255)


@dataclass
class Region:
    """The part of the document a generation works on (document pixels)."""

    x: int
    y: int
    width: int
    height: int
    # Selection inside this region as an 8-bit grayscale image, or None.
    mask: Optional[QImage] = None


def active_document():
    return Krita.instance().activeDocument()


def has_selection(doc) -> bool:
    selection = doc.selection() if doc is not None else None
    return selection is not None and selection.width() > 0 and selection.height() > 0


def get_region(doc, padding_percent: int) -> Region:
    """Return the selection (grown by some context) or the whole canvas."""
    if not has_selection(doc):
        return Region(0, 0, doc.width(), doc.height())

    selection = doc.selection()
    sx, sy, sw, sh = selection.x(), selection.y(), selection.width(), selection.height()
    pad = int(max(sw, sh) * padding_percent / 100)
    x, y = max(0, sx - pad), max(0, sy - pad)
    right, bottom = min(doc.width(), sx + sw + pad), min(doc.height(), sy + sh + pad)
    width, height = right - x, bottom - y

    data = bytes(selection.pixelData(x, y, width, height))
    mask = QImage(data, width, height, width, QImage.Format.Format_Grayscale8).copy()
    return Region(x, y, width, height, mask)


def read_image(doc, region: Region) -> QImage:
    """The visible canvas inside ``region``, flattened onto white."""
    projection = doc.projection(region.x, region.y, region.width, region.height)
    flat = QImage(region.width, region.height, QImage.Format.Format_RGB32)
    flat.fill(QColor(255, 255, 255))
    painter = QPainter(flat)
    painter.drawImage(0, 0, projection)
    painter.end()
    return flat


def resize(image: QImage, width: int, height: int) -> QImage:
    if image.width() == width and image.height() == height:
        return image
    return image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def api_mask(mask: QImage, width: int, height: int) -> QImage:
    """Black/white mask at generation size (white = regenerate)."""
    scaled = resize(mask, width, height).convertToFormat(QImage.Format.Format_Grayscale8)
    stride = scaled.bytesPerLine()
    data = image_bytes(scaled).translate(_BINARIZE)
    return QImage(data, width, height, stride, QImage.Format.Format_Grayscale8).copy()


def to_base64_png(image: QImage) -> str:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return base64.b64encode(bytes(buffer.data())).decode("ascii")


def compose_result(result: QImage, region: Region) -> QImage:
    """Scale a generated image back to the region, keeping only the selection."""
    image = resize(result, region.width, region.height)
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if region.mask is not None:
        # Reinterpret the grayscale bytes as alpha. (convertToFormat would
        # treat grayscale as opaque and give a fully opaque mask.)
        mask = region.mask
        alpha = QImage(image_bytes(mask), mask.width(), mask.height(),
                       mask.bytesPerLine(), QImage.Format.Format_Alpha8).copy()
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, alpha)
        painter.end()
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def insert_layer(doc, image: QImage, x: int, y: int, name: str, visible: bool = True):
    """Add ``image`` (ARGB32) as a new paint layer above the active layer."""
    root = doc.rootNode()
    active = doc.activeNode()
    parent = active.parentNode() if active is not None else None
    if parent is None:
        parent, active = root, None

    node = doc.createNode(name, "paintlayer")
    parent.addChildNode(node, active)

    # Krita stores 8-bit RGBA as BGRA, the same layout as QImage ARGB32 on
    # little-endian machines. Other document color spaces are converted.
    native = (doc.colorModel(), doc.colorDepth()) == ("RGBA", "U8")
    if not native:
        node.setColorSpace("RGBA", "U8", "sRGB-elle-V2-srgbtrc.icc")
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    node.setPixelData(QByteArray(image_bytes(image)), x, y, image.width(), image.height())
    if not native:
        node.setColorSpace(doc.colorModel(), doc.colorDepth(), doc.colorProfile())

    node.setVisible(visible)
    doc.refreshProjection()
    return node


def document_is_open(doc) -> bool:
    return doc is not None and any(d == doc for d in Krita.instance().documents())
