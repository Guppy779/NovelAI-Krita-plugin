"""Reading from and writing to the Krita canvas."""

import base64
from dataclasses import dataclass
from typing import Optional

from krita import Krita

from . import api
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


def region_bounds(doc, padding_percent: int, max_pixels: int):
    """(x, y, width, height, has_selection) of the area a generation uses:
    around the selection (see api.plan_region) or the whole canvas. Cheap:
    doesn't read the selection's pixels."""
    if not has_selection(doc):
        return 0, 0, doc.width(), doc.height(), False
    selection = doc.selection()
    x, y, width, height = api.plan_region(
        doc.width(), doc.height(), selection.x(), selection.y(),
        selection.width(), selection.height(), padding_percent, max_pixels)
    return x, y, width, height, True


def get_region(doc, padding_percent: int, max_pixels: int) -> Region:
    """The area a generation uses, with the selection's mask if there is one."""
    x, y, width, height, selected = region_bounds(doc, padding_percent, max_pixels)
    if not selected:
        return Region(x, y, width, height)

    data = bytes(doc.selection().pixelData(x, y, width, height))
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


MASK_GRID = 8  # NovelAI works on 8x8 pixel cells (one latent pixel each).


def api_mask(region: Region, width: int, height: int) -> QImage:
    """Black/white mask at generation size (white = regenerate).

    Snapped to NovelAI's 8 px grid like the NovelAI site does: a cell is
    regenerated if any part of it is selected, and edges stay hard (no
    smoothing when scaling up).
    """
    cells_w, cells_h = width // MASK_GRID, height // MASK_GRID
    cells = resize(region.mask, cells_w, cells_h).convertToFormat(QImage.Format.Format_Grayscale8)
    data = image_bytes(cells).translate(_BINARIZE)
    cells = QImage(data, cells_w, cells_h, cells.bytesPerLine(),
                   QImage.Format.Format_Grayscale8).copy()
    return cells.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.FastTransformation)


def to_base64_png(image: QImage) -> str:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return base64.b64encode(bytes(buffer.data())).decode("ascii")


# Per-channel differences up to this much (out of 255) count as unchanged.
CHANGE_THRESHOLD = 2


def changed_pixels(result: QImage, sent: QImage) -> QImage:
    """Grayscale mask (white = changed) of where ``result`` differs from ``sent``.

    For inpainting, NovelAI returns the whole image: the masked cells newly
    generated and blended into the original at their edges, everything else
    the original. Keeping exactly the changed pixels reproduces what the
    NovelAI site shows, without cutting through its blended edge.
    """
    a = result.convertToFormat(QImage.Format.Format_RGB32)
    b = resize(sent, a.width(), a.height()).convertToFormat(QImage.Format.Format_RGB32)
    painter = QPainter(a)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
    painter.drawImage(0, 0, b)
    painter.end()
    # Threshold every channel byte, then any channel over -> pixel changed.
    table = bytes([0] * (CHANGE_THRESHOLD + 1) + [255] * (255 - CHANGE_THRESHOLD))
    data = image_bytes(a).translate(table)
    flags = QImage(data, a.width(), a.height(), a.bytesPerLine(),
                   QImage.Format.Format_RGB32).copy()
    gray = flags.convertToFormat(QImage.Format.Format_Grayscale8)
    data = image_bytes(gray).translate(_BINARIZE)
    return QImage(data, gray.width(), gray.height(), gray.bytesPerLine(),
                  QImage.Format.Format_Grayscale8).copy()


def _apply_alpha(image: QImage, mask: QImage) -> None:
    """Multiply ``image``'s alpha by a grayscale mask of the same size."""
    # Reinterpret the grayscale bytes as alpha. (convertToFormat would
    # treat grayscale as opaque and give a fully opaque mask.)
    alpha = QImage(image_bytes(mask), mask.width(), mask.height(),
                   mask.bytesPerLine(), QImage.Format.Format_Alpha8).copy()
    painter = QPainter(image)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, alpha)
    painter.end()


def compose_result(result: QImage, region: Region, sent: Optional[QImage] = None) -> QImage:
    """Scale a generated image back to the region and make it a layer.

    Inpainting (``sent`` given): keep the pixels NovelAI changed.
    Otherwise with a selection: keep the selection. Without one: keep all.
    """
    if region.mask is not None and sent is not None:
        # Scaling only happens when the area was too big to send 1:1.
        mask = _map_binarize(resize(changed_pixels(result, sent), region.width, region.height))
    else:
        mask = region.mask
    image = resize(result, region.width, region.height)
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if mask is not None:
        _apply_alpha(image, mask)
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _map_binarize(mask: QImage) -> QImage:
    gray = mask.convertToFormat(QImage.Format.Format_Grayscale8)
    data = image_bytes(gray).translate(_BINARIZE)
    return QImage(data, gray.width(), gray.height(), gray.bytesPerLine(),
                  QImage.Format.Format_Grayscale8).copy()


def insert_layer(doc, image: QImage, x: int, y: int, name: str, visible: bool = True):
    """Add ``image`` (ARGB32) as a new paint layer above the active layer."""
    root = doc.rootNode()
    active = doc.activeNode()
    parent = active.parentNode() if active is not None else None
    if parent is None:
        parent, active = root, None

    node = doc.createNode(name, "paintlayer")
    parent.addChildNode(node, active)
    write_pixels(doc, node, image, x, y)
    node.setVisible(visible)
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


PREVIEW_LAYER_NAME = "NovelAI preview"


def show_preview(doc, node, image: QImage, region: Region):
    """Show an in-progress image on the canvas in a temporary layer.

    Creates the layer the first time (``node`` None) and updates it after
    that; returns the layer. Previews are cut to the selection, if any.
    """
    layer = compose_result(image, region)
    if node is None:
        return insert_layer(doc, layer, region.x, region.y, PREVIEW_LAYER_NAME)
    write_pixels(doc, node, layer, region.x, region.y)
    doc.refreshProjection()
    return node


def remove_preview(doc, node) -> None:
    if node is None or not document_is_open(doc):
        return  # A closed document took its layers with it.
    node.remove()
    doc.refreshProjection()


def document_is_open(doc) -> bool:
    return doc is not None and any(d == doc for d in Krita.instance().documents())
