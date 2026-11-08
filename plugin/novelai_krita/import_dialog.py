"""Importing the prompt and settings of a NovelAI image dropped on a prompt box."""

import os

from . import metadata, settings
from .qt import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QImage, QLabel, QPixmap, Qt,
    QVBoxLayout, image_bytes,
)

MAX_FILE_SIZE = 200 * 1024 * 1024
THUMBNAIL_SIZE = 200

LABELS = {
    metadata.PROMPT: "Prompt",
    metadata.NEGATIVE: "Undesired content",
    metadata.CHARACTERS: "Characters (added to the prompt)",
    metadata.SETTINGS: "Settings",
    metadata.SEED: "Seed",
}
CLEAN = "clean"


def read_image(path):
    """(ImageInfo or None, QImage) of an image file. Raises OSError."""
    if os.path.getsize(path) > MAX_FILE_SIZE:
        raise OSError("the file is too large")
    with open(path, "rb") as file:
        data = file.read()
    image = QImage.fromData(data)
    info = metadata.parse(metadata.png_text(data))
    if info is None and not image.isNull() and image.hasAlphaChannel():
        # Text chunks gone (e.g. re-saved by another program): try the copy
        # hidden in the alpha channel.
        alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
        info = metadata.parse(metadata.stealth_text(
            image_bytes(alpha), alpha.width(), alpha.height(), alpha.bytesPerLine()))
    return info, image


def _short(text, limit=600):
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "..."


class ImportDialog(QDialog):
    """Choose which parts of an image's generation info to import."""

    def __init__(self, info, image, name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import from image")
        self._info = info

        thumbnail = QLabel()
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if not image.isNull():
            thumbnail.setPixmap(QPixmap.fromImage(image.scaled(
                THUMBNAIL_SIZE, THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)))
        if len(name) > 60:  # Keep the dialog a sensible width.
            name = name[:28] + "..." + name[-28:]
        heading = QLabel("<b>%s</b><br>%s" % (
            name.replace("&", "&amp;").replace("<", "&lt;"),
            "Made with %s" % info.model if info.model else "Made with NovelAI"))
        heading.setWordWrap(True)
        heading.setTextFormat(Qt.TextFormat.RichText)

        details = {
            metadata.PROMPT: _short(info.prompt),
            metadata.NEGATIVE: _short(info.negative),
            metadata.CHARACTERS: "\n\n".join(
                _short(c.prompt, 300) for c in info.characters if c.prompt.strip()),
            metadata.SETTINGS: self._settings_text(info.settings),
            metadata.SEED: str(info.seed) if info.seed is not None else "",
        }
        available = {
            metadata.PROMPT: bool(info.prompt.strip()),
            metadata.NEGATIVE: bool(info.negative.strip()),
            metadata.CHARACTERS: info.has_characters,
            metadata.SETTINGS: bool(info.settings),
            metadata.SEED: info.seed is not None,
        }
        remembered = set(settings.get("import_parts").split(","))
        self._boxes = {}
        layout = QVBoxLayout(self)
        layout.addWidget(thumbnail)
        layout.addWidget(heading)
        layout.addWidget(QLabel("Import:"))
        for part in metadata.PARTS:
            box = QCheckBox(LABELS[part])
            box.setEnabled(available[part])
            box.setChecked(available[part] and part in remembered)
            box.setToolTip(details[part] if available[part] else "Not in this image.")
            box.toggled.connect(self._update_button)
            layout.addWidget(box)
            self._boxes[part] = box

        self.clean = QCheckBox("Move quality tags and preset into their boxes")
        self.clean.setChecked(CLEAN in remembered)
        self.clean.setToolTip(
            "NovelAI records the prompt as it was sent: with the quality tags at the\n"
            "end and the Undesired Content preset at the start.\n"
            "On: they are taken out of the text and chosen in the Quality tags and\n"
            "Undesired preset boxes instead.\n"
            "Off: the text is kept as recorded and both boxes are turned off.\n"
            "Either way, the request sent to NovelAI is the same.")
        row = QHBoxLayout()
        row.addSpacing(16)
        row.addWidget(self.clean)
        layout.addSpacing(6)
        layout.addLayout(row)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._import = self._buttons.addButton(
            "Import", QDialogButtonBox.ButtonRole.AcceptRole)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._update_button()

    @staticmethod
    def _settings_text(values):
        names = [("steps", "Steps"), ("scale", "Prompt guidance"),
                 ("cfg_rescale", "Guidance rescale"), ("sampler", "Sampler"),
                 ("noise_schedule", "Noise schedule")]
        return "\n".join("%s: %s" % (label, values[key]) for key, label in names if key in values)

    def parts(self):
        return [part for part, box in self._boxes.items() if box.isEnabled() and box.isChecked()]

    def _update_button(self, *_):
        self._import.setEnabled(bool(self.parts()))
        self.clean.setEnabled(any(
            self._boxes[part].isChecked() for part in (metadata.PROMPT, metadata.NEGATIVE)))

    def accept(self):
        # Remember the choices; parts this image doesn't have keep their old setting.
        remembered = set(settings.get("import_parts").split(","))
        for part, box in self._boxes.items():
            if box.isEnabled():
                remembered.discard(part)
                if box.isChecked():
                    remembered.add(part)
        remembered.discard(CLEAN)
        if self.clean.isChecked():
            remembered.add(CLEAN)
        settings.set("import_parts", ",".join(sorted(p for p in remembered if p)))
        super().accept()
