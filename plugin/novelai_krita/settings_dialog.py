"""Settings dialog: API token, image size limit, inpainting context, autocomplete."""

from . import api, settings
from .qt import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, Qt, QVBoxLayout, ignore_wheel,
)
from .version import __version__

PROJECT_URL = "https://github.com/Guppy779/NovelAI-Krita-plugin"

TOKEN_HELP = (
    "Use a <b>Persistent API Token</b>: on novelai.net open "
    "User Settings &rarr; Account &rarr; Get Persistent API Token. "
    "It is saved in Krita's settings file on this computer."
)


class SettingsDialog(QDialog):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self._client = client
        self._job = None
        self.setWindowTitle("NovelAI Settings")
        self.setMinimumWidth(460)

        self.token = QLineEdit(settings.get("token"))
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("pst-...")
        show = QPushButton("Show")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.token.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        test = QPushButton("Test")
        test.clicked.connect(self._test)
        token_row = QHBoxLayout()
        token_row.addWidget(self.token)
        token_row.addWidget(show)
        token_row.addWidget(test)

        help_label = QLabel(TOKEN_HELP)
        help_label.setWordWrap(True)
        help_label.setTextFormat(Qt.TextFormat.RichText)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        self.max_side = QSpinBox()
        self.max_side.setRange(256, 2048)
        self.max_side.setSingleStep(64)
        self.max_side.setSuffix(" px")
        self.max_side.setValue(int(round(settings.get_int("max_pixels") ** 0.5)))
        self.max_side.setToolTip(
            "Generations are scaled to about this many pixels squared.\n"
            "1024 (1024 x 1024 area) is free for Opus subscribers; larger costs Anlas.")

        self.padding = QSpinBox()
        self.padding.setRange(0, 200)
        self.padding.setSuffix(" %")
        self.padding.setValue(settings.get_int("context_padding"))
        self.padding.setToolTip(
            "How much of the surrounding canvas is sent along with a selection,\n"
            "relative to the selection size. More context gives better blending.")

        form = QFormLayout()
        form.addRow("API token", token_row)
        form.addRow("", help_label)
        form.addRow("", self.status)
        form.addRow("Max generation size", self.max_side)
        form.addRow("Selection context", self.padding)

        self.autocomplete = QCheckBox("Suggest tags while typing prompts")
        self.autocomplete.setChecked(settings.get_bool("autocomplete"))
        self.autocomplete.setToolTip(
            "Looks up the tag at the cursor with NovelAI (free, no Anlas).\n"
            "Tab or Enter inserts the highlighted tag, Escape closes the list.")
        form.addRow("Autocomplete", self.autocomplete)

        self.full_image = QCheckBox("Add the whole image as the layer")
        self.full_image.setChecked(settings.get_bool("inpaint_full_image"))
        self.full_image.setToolTip(
            "Off: the inpainted layer holds only the pixels NovelAI changed and is\n"
            "transparent elsewhere, so layers below stay visible.\n"
            "On: the layer is NovelAI's whole image for the area it was sent\n"
            "(often the whole canvas), covering everything below it.")
        form.addRow("Inpainting", self.full_image)

        self.live_preview = QCheckBox("Show a live preview on the canvas while generating")
        self.live_preview.setChecked(settings.get_bool("live_preview"))
        self.live_preview.setToolTip(
            "Shows each step in a temporary 'NovelAI preview' layer, which is\n"
            "replaced by the result when it's done. V4 and newer models only.\n"
            "Turn off to generate without previews (no temporary layer).")
        form.addRow("Live preview", self.live_preview)

        self.history_limit = QSpinBox()
        self.history_limit.setRange(1, 500)
        self.history_limit.setSuffix(" results")
        self.history_limit.setValue(max(1, settings.get_int("history_limit")))
        self.history_limit.setToolTip(
            "How many results the history at the bottom of the panel keeps in\n"
            "memory. The oldest are dropped first; layers already added stay.")
        form.addRow("History size", self.history_limit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        about = QLabel('NovelAI for Krita %s &middot; <a href="%s">Project page</a>'
                       % (__version__, PROJECT_URL))
        about.setTextFormat(Qt.TextFormat.RichText)
        about.setOpenExternalLinks(True)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(about)
        layout.addWidget(buttons)
        ignore_wheel(self)

    def _test(self):
        token = self.token.text().strip()
        if not token:
            self.status.setText("Enter a token first.")
            return
        self.status.setText("Checking...")

        def ok(account):
            self.status.setText("Token works. " + api.account_summary(account))

        self._job = self._client.fetch_account(
            token, ok, lambda message: self.status.setText(message))

    def accept(self):
        settings.set("token", self.token.text().strip())
        side = self.max_side.value()
        settings.set("max_pixels", side * side)
        settings.set("context_padding", self.padding.value())
        settings.set("autocomplete", self.autocomplete.isChecked())
        settings.set("inpaint_full_image", self.full_image.isChecked())
        settings.set("live_preview", self.live_preview.isChecked())
        settings.set("history_limit", self.history_limit.value())
        super().accept()

    def done(self, result):
        if self._job is not None:
            self._job.cancel()
        super().done(result)


def max_pixels():
    return settings.get_int("max_pixels") or api.FREE_MAX_PIXELS
