"""The NovelAI docker: prompt, parameters and the Generate button."""

import os
import time
from dataclasses import dataclass, field

from krita import DockWidget

from . import api, canvas, doc_state, inpaint, inpaint_math, metadata, presets, settings
from .characters import CharacterEditor
from .client import NovelAIClient
from .import_dialog import ImportDialog, LABELS as IMPORT_LABELS, read_image
from .inpaint_dialog import InpaintDialog, mask_thumbnail
from .preview import PreviewWindow, THUMBNAIL_SIZE
from .prompt_edit import PromptEdit
from .references import ReferenceEditor
from .settings_dialog import SettingsDialog, max_pixels
from .qt import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QIcon, QImage, QLabel, QLineEdit, QListWidgetItem, QMenu, QMessageBox, QPixmap,
    QProgressBar, QPushButton, QScrollArea, QSize, QSlider, QSpinBox, Qt, QTimer, QToolButton,
    QVBoxLayout, QWidget, ignore_wheel,
)

MASK_THUMBNAIL_SIZE = 64


@dataclass
class Result:
    image: QImage  # Already composited to the region, ARGB32.
    x: int
    y: int
    seed: int
    prompt: str
    document: object
    original: bytes = b""  # The file exactly as NovelAI sent it.
    # The panel's character prompts when it was made, for "Use this prompt".
    characters: list = field(default_factory=list)
    custom_positions: bool = False
    shown: QImage = None  # Inpainting: the result as it will look on the canvas.


class _Panel(QWidget):
    """The panel's contents, inside a scroll area.

    The word-wrapped labels make Qt size the contents to their preferred
    height, which would scroll even when everything fits by shrinking the
    prompt boxes. Reporting the minimum instead fills the docker's height and
    scrolls only when the docker is shorter than the panel's minimum.
    """

    def heightForWidth(self, width):
        return self.minimumSizeHint().height()


class NovelAIDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NovelAI")
        self._client = NovelAIClient(self)
        self._job = None
        self._started = 0.0
        self._results = {}  # History item id -> Result.
        self._next_id = 0
        self.preview = PreviewWindow()  # Shows the image forming, and the results.
        self._mask = None  # The active document's inpainting mask.
        self._mask_picture = None  # What the mask thumbnail shows.
        # Each document keeps its own prompt and settings (see doc_state.py).
        self._doc = None  # The document the panel currently shows.
        self._loading = False  # Filling the panel from a document's settings.
        self._edited_without_doc = False

        root = _Panel(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)

        # Model + settings.
        self.model = QComboBox()
        self.model.setEditable(True)
        # Pressing Enter must not add the typed name as a new item (it would
        # have no model name attached as data).
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for name, label in api.MODELS:
            self.model.addItem(label, name)
        self._set_model(settings.get("model"))
        self.model.setToolTip("NovelAI model. You can also type a model API name.")
        settings_button = QToolButton()
        settings_button.setText("Settings")
        settings_button.clicked.connect(self.open_settings)
        row = QHBoxLayout()
        row.addWidget(self.model, 1)
        row.addWidget(settings_button)
        layout.addLayout(row)

        # Account: plan, Anlas and V5 allowance, refreshed after each generation.
        self._account = None
        self._account_job = None
        self.account_label = QLabel()
        self.account_label.setWordWrap(True)
        refresh = QToolButton()
        refresh.setText("Refresh")
        refresh.setAutoRaise(True)
        refresh.setToolTip("Check your plan, Anlas and V5 allowance again.")
        refresh.clicked.connect(self.refresh_account)
        row = QHBoxLayout()
        row.addWidget(self.account_label, 1)
        row.addWidget(refresh)
        layout.addLayout(row)

        # Prompts.
        self.prompt = PromptEdit("", self._suggest_tags, on_image=self.import_image)
        self.prompt.setPlaceholderText(
            "Prompt, e.g. 1girl, silver hair, night sky, city lights\n"
            "Drop a NovelAI image here to import its prompt and settings.")
        self.prompt.setMinimumHeight(150)
        layout.addWidget(self.prompt, 2)
        self.negative = PromptEdit("", self._suggest_tags, on_image=self.import_image)
        self.negative.setPlaceholderText("Undesired content (added after the preset)")
        self.negative.setMinimumHeight(80)
        layout.addWidget(self.negative, 1)

        # Character prompts (V4 and newer).
        self.characters = CharacterEditor(self._suggest_tags, self.import_image, self._state_changed)
        self.characters.set_custom_positions(settings.get_bool("character_positions"))
        layout.addWidget(self.characters)

        # Precise references (V4.5). They cost Anlas, so the cost note follows
        # them. Each document has its own, but they aren't part of its stored
        # settings: the editor keeps them, and only while the document is open.
        self.references = ReferenceEditor(
            self._update_mode, self._say, self._canvas_picture, self._follow_document)
        layout.addWidget(self.references)

        # Quality tags and Undesired Content preset, as on the NovelAI site:
        # added to the request, not shown in the boxes. Choices depend on the model.
        self.quality = QComboBox()
        self.quality.setToolTip(
            "Adds the model's quality tags to the end of the prompt, like the\n"
            "NovelAI site's 'Add Quality Tags' toggle.")
        self.uc_preset = QComboBox()
        self.uc_preset.setToolTip(
            "Undesired Content preset, put in front of your own undesired content,\n"
            "like the NovelAI site's presets.")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Quality tags", self.quality)
        form.addRow("Undesired preset", self.uc_preset)
        layout.addLayout(form)
        # The user's own choices. A model without them shows the nearest option
        # without forgetting them (e.g. while a model name is being typed).
        self._quality_choice = settings.get("quality_tags")
        self._uc_choice = settings.get("uc_preset")
        self.quality.activated.connect(self._quality_chosen)
        self.uc_preset.activated.connect(self._uc_chosen)
        self._refresh_presets()
        self.model.currentIndexChanged.connect(self._refresh_presets)
        self.model.editTextChanged.connect(self._refresh_presets)

        # Strength.
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(1, 100)
        self.strength.setValue(settings.get_int("strength"))
        self.strength_label = QLabel()
        self.strength_label.setMinimumWidth(36)
        self.strength.valueChanged.connect(self._update_mode)
        row = QHBoxLayout()
        row.addWidget(QLabel("Strength"))
        row.addWidget(self.strength, 1)
        row.addWidget(self.strength_label)
        layout.addLayout(row)
        self.inpaint_button = QPushButton("Inpaint...")
        self.inpaint_button.setToolTip(
            "Paint which part of the image to regenerate. A selection is used as\n"
            "the starting point.")
        self.inpaint_button.clicked.connect(self.open_inpaint)
        self.clear_mask_button = QPushButton("Clear mask")
        self.clear_mask_button.setToolTip("Go back to generating the whole image.")
        self.clear_mask_button.clicked.connect(self._clear_mask)
        self.clear_mask_button.setVisible(False)
        # The mask on a small copy of the canvas, as the site shows it.
        self.mask_thumbnail = QToolButton()
        self.mask_thumbnail.setAutoRaise(True)
        self.mask_thumbnail.setIconSize(QSize(MASK_THUMBNAIL_SIZE, MASK_THUMBNAIL_SIZE))
        self.mask_thumbnail.setToolTip("The inpainting mask. Click to edit it.")
        self.mask_thumbnail.clicked.connect(self.open_inpaint)
        self.mask_thumbnail.setVisible(False)
        buttons = QVBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.inpaint_button)
        buttons.addWidget(self.clear_mask_button)
        buttons.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self.mask_thumbnail)
        row.addLayout(buttons, 1)
        layout.addLayout(row)
        self.mode_label = QLabel()
        self.mode_label.setWordWrap(True)
        # Normal text colour (readable in light and dark themes), set apart by italics.
        self.mode_label.setStyleSheet("font-style: italic;")
        layout.addWidget(self.mode_label)
        self.cost_label = QLabel()
        self.cost_label.setWordWrap(True)
        self.cost_label.setStyleSheet("color: #e8a33d;")
        self.cost_label.setToolTip(
            "Opus subscribers generate for free up to 1024 x 1024 pixels,\n"
            "28 steps and one image per request. Anything above costs Anlas.")
        self.cost_label.setVisible(False)
        layout.addWidget(self.cost_label)

        # Advanced options.
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced options")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setAutoRaise(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        form = QFormLayout(self.advanced)
        form.setContentsMargins(0, 0, 0, 0)

        self.steps = QSpinBox()
        self.steps.setRange(1, 50)
        self.steps.setValue(settings.get_int("steps"))
        self.steps.setToolTip("28 or fewer steps is free for Opus subscribers.")
        form.addRow("Steps", self.steps)

        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.0, 10.0)
        self.scale.setSingleStep(0.1)
        self.scale.setValue(settings.get_float("scale"))
        form.addRow("Prompt guidance", self.scale)

        self.rescale = QDoubleSpinBox()
        self.rescale.setRange(0.0, 1.0)
        self.rescale.setSingleStep(0.02)
        self.rescale.setValue(settings.get_float("cfg_rescale"))
        form.addRow("Guidance rescale", self.rescale)

        self.sampler = QComboBox()
        for name, label in api.SAMPLERS:
            self.sampler.addItem(label, name)
        self._select_data(self.sampler, settings.get("sampler"))
        form.addRow("Sampler", self.sampler)

        self.schedule = QComboBox()
        self.schedule.addItems(api.NOISE_SCHEDULES)
        self.schedule.setCurrentText(settings.get("noise_schedule"))
        form.addRow("Noise schedule", self.schedule)

        self.seed = QLineEdit(settings.get("seed"))
        self.seed.setToolTip("Seed used when 'Random' is off. 0 - %d" % api.MAX_SEED)
        self.random_seed = QCheckBox("Random")
        self.random_seed.setChecked(settings.get_bool("random_seed"))
        self.random_seed.toggled.connect(lambda on: self.seed.setEnabled(not on))
        self.seed.setEnabled(not self.random_seed.isChecked())
        row = QHBoxLayout()
        row.addWidget(self.seed, 1)
        row.addWidget(self.random_seed)
        form.addRow("Seed", row)

        self.n_samples = QSpinBox()
        self.n_samples.setRange(1, 4)
        self.n_samples.setValue(settings.get_int("n_samples"))
        self.n_samples.setToolTip("Images per request. More than one costs Anlas.")
        form.addRow("Images", self.n_samples)
        self.steps.valueChanged.connect(self._update_mode)
        self.n_samples.valueChanged.connect(self._update_mode)

        layout.addWidget(self.advanced)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        self.advanced_toggle.setChecked(settings.get_bool("show_advanced"))
        self._toggle_advanced(self.advanced_toggle.isChecked())

        # Generate / cancel.
        self.generate_button = QPushButton("Generate")
        self.generate_button.setMinimumHeight(32)
        self.generate_button.clicked.connect(self.generate)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setVisible(False)
        preview_button = QToolButton()
        preview_button.setText("Preview")
        preview_button.setToolTip("Open the preview window with your results.")
        preview_button.clicked.connect(self._show_preview)
        row = QHBoxLayout()
        row.addWidget(self.generate_button, 1)
        row.addWidget(self.cancel_button)
        row.addWidget(preview_button)
        layout.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # The preview window's controls and history of results.
        self.history = self.preview.history
        self.history.customContextMenuRequested.connect(self._history_menu)
        self.history.itemDoubleClicked.connect(lambda item: self._insert(self._result(item)))
        self.history.currentItemChanged.connect(self._show_current)
        self.preview.generate_button.clicked.connect(self.generate)
        self.preview.cancel_button.clicked.connect(self.cancel)
        self.preview.add_button.clicked.connect(self._add_current)

        # The panel scrolls when the docker is too short for it (e.g. with
        # several characters).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(root)
        self.setWidget(scroll)
        ignore_wheel(root)

        for signal in (
                self.prompt.textChanged, self.negative.textChanged,
                self.model.currentIndexChanged, self.model.editTextChanged,
                self.quality.activated, self.uc_preset.activated,
                self.strength.valueChanged, self.steps.valueChanged,
                self.scale.valueChanged, self.rescale.valueChanged,
                self.sampler.currentIndexChanged, self.schedule.currentIndexChanged,
                self.seed.textChanged, self.random_seed.toggled,
                self.n_samples.valueChanged):
            signal.connect(self._state_changed)

        # Krita has no selection-changed signal, so poll to keep the mode label current.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_mode)
        self._timer.start(600)
        self._update_mode()
        self.refresh_account()

    # ---- Account ----------------------------------------------------------

    def refresh_account(self):
        """Fetch plan, Anlas and V5 allowance (free; no generation)."""
        token = settings.get("token")
        if not token:
            self._account = None
            self.account_label.setText("Add your API token in Settings to see your Anlas.")
            self.account_label.setToolTip("")
            return
        if self._account_job is not None:
            self._account_job.cancel()
        if self._account is None:
            self.account_label.setText("Checking your account...")
        self._account_job = self._client.fetch_account(
            token, self._on_account, self._on_account_error)

    def _on_account(self, account):
        self._account_job = None
        self._account = account
        self.account_label.setText(api.account_summary(account))
        self.account_label.setToolTip(api.account_details(account))
        self._update_mode()  # The cost warning depends on it.

    def _on_account_error(self, message):
        self._account_job = None
        if self._account is None:  # Keep showing the last known values otherwise.
            self.account_label.setText("Couldn't load your account: %s" % message)

    # Krita calls this when the active canvas changes.
    def canvasChanged(self, canvas_):
        self._update_mode()

    # ---- UI helpers -------------------------------------------------------

    @staticmethod
    def _select_data(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_model(self, name):
        name = (name or "").strip() or api.DEFAULT_MODEL
        index = self.model.findData(name)
        if index >= 0:
            self.model.setCurrentIndex(index)
        else:
            self.model.setEditText(name)

    def _model_name(self):
        """The model API name: the selected entry's name, or the typed text."""
        text = self.model.currentText().strip()
        index = self.model.findText(text)
        if index >= 0:
            data = self.model.itemData(index)
            if data:
                return data
        return text

    def _quality_chosen(self, *_):
        self._quality_choice = self.quality.currentData()

    def _uc_chosen(self, *_):
        self._uc_choice = self.uc_preset.currentText()

    def _refresh_presets(self, *_):
        """Fill the Quality tags and Undesired preset boxes for the current
        model, showing the user's choices where the model has them."""
        model = self._model_name()
        self.characters.set_model(self._generating_model())
        self.references.set_model(model)
        self.prompt.set_model(model)
        self.negative.set_model(model)

        self.quality.blockSignals(True)
        self.quality.clear()
        for value, label in presets.quality_options(model):
            self.quality.addItem(label, value)
        index = self.quality.findData(self._quality_choice)
        if index < 0 and self._quality_choice == presets.LIGHT:  # Light is V5 only.
            index = self.quality.findData(presets.STANDARD)
        self.quality.setCurrentIndex(max(0, index))
        self.quality.blockSignals(False)

        self.uc_preset.blockSignals(True)
        self.uc_preset.clear()
        names = presets.uc_options(model)
        self.uc_preset.addItems(names)
        for choice in (self._uc_choice, presets.DEFAULT_UC_PRESET, presets.NONE):
            if choice in names:
                self.uc_preset.setCurrentIndex(names.index(choice))
                break
        self.uc_preset.blockSignals(False)

    def _toggle_advanced(self, on):
        self.advanced.setVisible(on)
        self.advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

    def _update_mode(self):
        self._follow_document()
        value = self.strength.value()
        self.strength_label.setText("%d%%" % value)
        if not self.isVisible():
            return
        doc = canvas.active_document()
        if doc is None:
            text = "Open a document to generate."
        elif self._active_mask(doc) is not None:
            self._update_mask_thumbnail(doc)  # The canvas may have changed.
            place = "focus area" if self._mask.focus is not None else "masked area"
            # V3 inpainting has no strength.
            if value >= 100 or not api.is_v4(self._model_name()):
                text = "Inpaint the %s" % place
            else:
                text = "Inpaint the %s at %d%% strength" % (place, value)
        else:
            text = ("Generate a new image for the whole canvas" if value >= 100
                    else "Refine the whole canvas (image to image)")
        self.mode_label.setText(text)
        self._update_cost_warning(doc)

    @staticmethod
    def _generation_size(width, height, sends_image):
        # Only plain text to image is scaled up to fill the budget; when the
        # canvas is sent, it goes at its own resolution so it stays sharp.
        return api.fit_resolution(width, height, max_pixels(), upscale=not sends_image)

    def _update_cost_warning(self, doc):
        """Warn when the next generation would spend Anlas (Opus free limits)."""
        reasons = []
        if doc is not None:
            mask = self._active_mask(doc)
            if mask is not None:
                width, height = inpaint.generation_size(mask, max_pixels())
            else:
                width, height = self._generation_size(
                    doc.width(), doc.height(), self.strength.value() < 100)
            # Character positions are picked on a canvas of the same shape. A
            # focus area sends part of it, but they're still picked on all of it.
            if mask is not None and mask.focus is not None:
                self.characters.set_aspect(doc.width(), doc.height())
            else:
                self.characters.set_aspect(width, height)
            reasons = api.anlas_warnings(
                width, height, self.steps.value(), self.n_samples.value(),
                self._model_name(), self._account, self.references.used())
        self.cost_label.setText("Costs Anlas: " + "; ".join(reasons) if reasons else "")
        self.cost_label.setVisible(bool(reasons))

    def _set_busy(self, busy):
        self.generate_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.preview.set_busy(busy)

    def _say(self, text):
        self.status.setText(text)
        self.preview.status.setText(text)

    # ---- Per-document settings ---------------------------------------------

    def _panel_state(self):
        """The panel's prompt and generation settings (see doc_state.FIELDS)."""
        return {
            "prompt": self.prompt.toPlainText(),
            "negative_prompt": self.negative.toPlainText(),
            "model": self._model_name(),
            "quality_tags": self._quality_choice,
            "uc_preset": self._uc_choice,
            "strength": self.strength.value(),
            "steps": self.steps.value(),
            "scale": self.scale.value(),
            "cfg_rescale": self.rescale.value(),
            "sampler": self.sampler.currentData(),
            "noise_schedule": self.schedule.currentText(),
            "seed": self.seed.text().strip() or "0",
            "random_seed": self.random_seed.isChecked(),
            "n_samples": self.n_samples.value(),
            "characters": self.characters.characters(),
            "character_positions": self.characters.custom_positions(),
        }

    @staticmethod
    def _last_used_state():
        """What a document without its own settings starts with: the settings
        of the last generation, with empty prompts."""
        return {
            "prompt": "",
            "negative_prompt": "",
            "model": settings.get("model"),
            "quality_tags": settings.get("quality_tags"),
            "uc_preset": settings.get("uc_preset"),
            "strength": settings.get_int("strength"),
            "steps": settings.get_int("steps"),
            "scale": settings.get_float("scale"),
            "cfg_rescale": settings.get_float("cfg_rescale"),
            "sampler": settings.get("sampler"),
            "noise_schedule": settings.get("noise_schedule"),
            "seed": settings.get("seed"),
            "random_seed": settings.get_bool("random_seed"),
            "n_samples": settings.get_int("n_samples"),
            "characters": [],
            "character_positions": settings.get_bool("character_positions"),
        }

    def _apply_state(self, state):
        """Fill the panel from a document's settings (missing ones: last used)."""
        values = self._last_used_state()
        values.update(state)
        self._loading = True
        try:
            self.prompt.setPlainText(values["prompt"])
            self.negative.setPlainText(values["negative_prompt"])
            self._quality_choice = values["quality_tags"]
            self._uc_choice = values["uc_preset"]
            self._set_model(values["model"])
            self._refresh_presets()
            self.strength.setValue(values["strength"])
            self.steps.setValue(values["steps"])
            self.scale.setValue(values["scale"])
            self.rescale.setValue(values["cfg_rescale"])
            self._select_data(self.sampler, values["sampler"])
            self.schedule.setCurrentText(values["noise_schedule"])
            self.seed.setText(values["seed"])
            self.random_seed.setChecked(values["random_seed"])
            self.n_samples.setValue(values["n_samples"])
            self.characters.set_characters(values["characters"])
            self.characters.set_custom_positions(values["character_positions"])
        finally:
            self._loading = False

    def _follow_document(self):
        """Show the active document's settings when another document becomes active."""
        doc = canvas.active_document()
        if canvas.same_document(doc, self._doc):
            return
        self._doc = doc
        self._set_mask(None)  # A mask belongs to the document it was painted on.
        self.references.set_document(doc)
        if doc is None:
            return  # Keep showing the last document's settings.
        stored = doc_state.decode(canvas.read_annotation(doc, doc_state.ANNOTATION))
        if stored:
            self._apply_state(stored)
        elif not self._edited_without_doc:
            self._apply_state(self._last_used_state())
        # Otherwise the document takes what was typed while none was open.
        self._edited_without_doc = False
        self._write_state()

    def _state_changed(self, *_):
        if self._loading:
            return
        if self._doc is None:
            self._edited_without_doc = True
        else:
            self._write_state()

    def _write_state(self):
        """Store the panel's settings in the document (saved with the .kra)."""
        if self._doc is None or not canvas.document_is_open(self._doc):
            return
        canvas.write_annotation(self._doc, doc_state.ANNOTATION, doc_state.DESCRIPTION,
                                doc_state.encode(self._panel_state()))

    @staticmethod
    def _canvas_picture():
        """The visible canvas as an image, or None if no document is open."""
        doc = canvas.active_document()
        if doc is None:
            return None
        return canvas.read_image(doc, canvas.Region(0, 0, doc.width(), doc.height()))

    def _save_last_used(self):
        """Remember the settings (not the prompts) for documents without their own."""
        for key, value in self._panel_state().items():
            if key not in doc_state.PROMPTS:
                settings.set(key, value)
        settings.set("show_advanced", self.advanced_toggle.isChecked())
        if self.preview.isVisible():
            self.preview.save_geometry()

    def import_image(self, path):
        """Import the prompt and settings recorded in a NovelAI image."""
        name = os.path.basename(path)
        try:
            info, image = read_image(path)
        except OSError as exc:
            self._say("Could not read %s: %s." % (name, exc.strerror or exc))
            return
        if info is None:
            self._say(
                "%s has no NovelAI prompt or settings in it. Images re-saved by other "
                "programs or websites often lose them." % name)
            return
        dialog = ImportDialog(info, image, name, self)
        if not dialog.exec():
            return
        parts = dialog.parts()
        self._follow_document()  # Import into the active document's settings.
        self._apply_state(metadata.import_state(
            info, self._panel_state(), parts, dialog.clean.isChecked(),
            dialog.append.isChecked()))
        self._state_changed()  # _apply_state doesn't store them; this does.
        if metadata.SETTINGS in parts or metadata.SEED in parts:
            self.advanced_toggle.setChecked(True)  # Show what changed.
        imported = [IMPORT_LABELS[part].lower() for part in parts]
        if len(imported) > 1:
            imported[-2:] = ["%s and %s" % (imported[-2], imported[-1])]
        self._say("Imported the %s from %s." % (", ".join(imported), name))

    # ---- Inpainting ---------------------------------------------------------

    def _generating_model(self):
        """The model doing the work: its limits are the ones that apply.
        (V5 Curated inpaints with V4.5 Curated's model, for one.)"""
        model = self._model_name()
        return api.inpainting_model(model) if self._mask is not None and model else model

    def _set_mask(self, mask):
        self._mask = mask
        self._mask_picture = None
        self.mask_thumbnail.setVisible(mask is not None)
        self.characters.set_model(self._generating_model())
        self.inpaint_button.setText("Edit mask..." if mask is not None else "Inpaint...")
        self.clear_mask_button.setVisible(mask is not None)

    def _update_mask_thumbnail(self, doc):
        """Show the mask on the canvas as it is now."""
        picture = mask_thumbnail(canvas.small_image(doc, MASK_THUMBNAIL_SIZE), self._mask,
                                 MASK_THUMBNAIL_SIZE)
        if picture != self._mask_picture:  # Only redraw when something changed.
            self._mask_picture = picture
            self.mask_thumbnail.setIcon(QIcon(QPixmap.fromImage(picture)))

    def _clear_mask(self):
        self._set_mask(None)
        self._update_mode()

    def _active_mask(self, doc):
        """The mask to use on ``doc``, unless the canvas has changed size since."""
        if self._mask is not None and self._mask.canvas_size != (doc.width(), doc.height()):
            self._set_mask(None)
            self._say("The canvas changed size, so the inpainting mask was cleared.")
        return self._mask

    def open_inpaint(self):
        doc = canvas.active_document()
        if doc is None:
            self._say("Open or create a document first.")
            return
        self._follow_document()
        width, height = doc.width(), doc.height()
        mask = self._active_mask(doc)
        if mask is not None:  # Cancelling must leave the old mask as it was.
            mask = inpaint.Mask(mask.cells.copy(), mask.canvas_size, mask.focus, mask.context)
        else:
            cells = canvas.selection_cells(doc)
            mask = inpaint.Mask(cells if cells is not None else inpaint.blank_cells(width, height),
                                (width, height))
        image = canvas.read_image(doc, canvas.Region(0, 0, width, height))
        dialog = InpaintDialog(image, mask, self)
        if dialog.exec():
            self._set_mask(dialog.mask())
            self._update_mode()

    def open_settings(self):
        SettingsDialog(self._client, self).exec()
        self._update_mode()  # The max generation size may have changed.
        self._trim_history()  # So may the history size.
        self.refresh_account()  # And the token.

    def _suggest_tags(self, query, lang, on_results):
        """Tag lookup used by both prompt boxes. Errors are ignored quietly:
        autocomplete is a convenience and must not interrupt typing."""
        token = settings.get("token")
        if not token or not settings.get_bool("autocomplete"):
            return None
        model = self._model_name()
        if model not in dict(api.MODELS):  # Typed names may be unknown to the endpoint.
            model = api.DEFAULT_MODEL
        return self._client.suggest_tags(token, model, query, lang, on_results, lambda error: None)

    # ---- Generation -------------------------------------------------------

    def _seed(self):
        if self.random_seed.isChecked():
            return api.random_seed()
        try:
            seed = int(self.seed.text().strip())
        except ValueError:
            raise ValueError("The seed must be a whole number.")
        if not 0 <= seed <= api.MAX_SEED:
            raise ValueError("The seed must be between 0 and %d." % api.MAX_SEED)
        return seed

    def generate(self):
        if self._job is not None:
            return
        doc = canvas.active_document()
        if doc is None:
            self._say("Open or create a document first.")
            return
        self._follow_document()  # Use this document's own settings.
        if not self._model_name():
            self._say("Choose or type a model first.")
            return
        prompt = self.prompt.toPlainText()  # As typed: the site doesn't trim it.
        if not prompt.strip():
            self._say("Write a prompt first.")
            return
        problem = self.characters.problem()
        if problem:
            self._say(problem)
            return
        characters = self.characters.characters()
        custom_positions = self.characters.custom_positions()
        token = settings.get("token")
        if not token:
            self._say("Add your NovelAI API token in Settings.")
            self.open_settings()
            token = settings.get("token")
            if not token:
                return
        try:
            seed = self._seed()
        except ValueError as exc:
            self._say(str(exc))
            return
        self._save_last_used()

        strength = self.strength.value() / 100.0
        canvas_width, canvas_height = doc.width(), doc.height()
        mask = self._active_mask(doc)
        plan = inpaint.plan(mask, max_pixels()) if mask is not None else None
        if mask is not None and plan is None:
            self._say("The inpainting mask is empty. Click Edit mask to paint one.")
            return
        if plan is not None:
            region = canvas.Region(*plan.area, focused=mask.focus is not None)
            width, height = plan.size
        else:
            region = canvas.Region(0, 0, canvas_width, canvas_height)
            width, height = self._generation_size(canvas_width, canvas_height, strength < 1.0)
        people = [api.CharacterPrompt(**c) for c in characters]
        if mask is not None and mask.focus is not None:
            for person in people:  # Positions are relative to what's sent.
                person.x, person.y = inpaint_math.focus_position(
                    person.x, person.y, canvas_width, canvas_height, plan.area)
        params = api.GenerationParams(
            prompt=prompt,
            negative_prompt=self.negative.toPlainText(),
            model=self._model_name(),
            width=width,
            height=height,
            steps=self.steps.value(),
            scale=self.scale.value(),
            cfg_rescale=self.rescale.value(),
            sampler=self.sampler.currentData(),
            noise_schedule=self.schedule.currentText(),
            seed=seed,
            n_samples=self.n_samples.value(),
            quality=self.quality.currentData(),
            uc_preset=self.uc_preset.currentText(),
            characters=people,
            use_coords=custom_positions,
            references=self.references.for_request(),
            strength=strength,
        )
        # Inpainted results are shown pasted into a small copy of the canvas.
        composer = None
        if plan is not None or strength < 1.0:
            params.image = canvas.to_base64_png(
                self._picture_to_send(doc, region, width, height))
        if plan is not None:
            params.mask = canvas.to_base64_png(inpaint.request_mask(plan))
            if not settings.get_bool("inpaint_full_image"):
                region.mask = inpaint.blend_alpha(plan)
            backdrop = canvas.preview_backdrop(doc)
            composer = lambda image: canvas.compose_preview(image, region, backdrop, canvas_width)
        if self.random_seed.isChecked():
            self.seed.setText(str(seed))  # Show which random seed was used.

        payload = api.build_payload(params)
        self._say("Generating %d x %d (%s)..." % (width, height, params.action))
        self._started = time.monotonic()
        self._set_busy(True)
        self._show_preview()
        on_images = lambda images: self._on_images(
            images, doc, region, seed, prompt, composer, characters, custom_positions)
        if settings.get_bool("live_preview") and api.supports_streaming(params.model):
            self._job = self._client.generate_stream(
                token, payload, lambda event: self._on_preview(event, params.steps, composer),
                on_images, self._on_error)
        else:
            self._job = self._client.generate(token, payload, on_images, self._on_error)

    def _picture_to_send(self, doc, region, width, height):
        if (region.width, region.height) == (width, height):
            return canvas.picture_to_send(doc, region, width, height)
        # Scaling as the site does takes a second or more for a large canvas.
        self._say("Preparing the picture...")
        self.status.repaint()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return canvas.picture_to_send(doc, region, width, height)
        finally:
            QApplication.restoreOverrideCursor()

    def cancel(self):
        if self._job is not None:
            self._job.cancel()
            self._job = None
        self._set_busy(False)
        self._show_current()
        self._say("Cancelled.")

    def _on_error(self, message):
        self._job = None
        self._set_busy(False)
        self._show_current()
        self._say(message)
        self.refresh_account()

    def _on_preview(self, event, steps, composer):
        """Show an in-progress image from the stream (first image of a batch)."""
        if event.sample != 0 or self._job is None:
            return
        image = QImage.fromData(event.image)
        if image.isNull():
            return
        self.preview.view.set_image(composer(image) if composer is not None else image)
        self._say("Generating... step %d of %d" % (event.step + 1, steps))

    def _on_images(self, images, doc, region, seed, prompt, composer, characters,
                   custom_positions):
        self._job = None
        self._set_busy(False)
        self.refresh_account()  # Anlas / V5 allowance may have changed.
        elapsed = time.monotonic() - self._started
        if not images:
            self._show_current()
            self._say("NovelAI returned no images.")
            return
        if not canvas.document_is_open(doc):
            doc = None

        added = 0
        for index, (data, image_seed) in enumerate(images):
            image = QImage.fromData(data)
            if image.isNull():
                continue
            # The layer gets the picture alone. NovelAI's file, with the
            # settings hidden in it, is kept for Save image.
            image = canvas.without_hidden_data(image)
            result = Result(
                image=canvas.compose_result(image, region, sharp=True),
                x=region.x, y=region.y,
                seed=image_seed if image_seed is not None else seed + index,
                prompt=prompt, document=doc, original=data,
                characters=list(characters), custom_positions=custom_positions,
                shown=composer(image) if composer is not None else None)
            self._add_history(result)
            added += 1

        # Newest first, so the batch's first image is the furthest along. It
        # may be gone already if the history is shorter than the batch.
        if added:
            self.history.setCurrentRow(min(added, self.history.count()) - 1)
        self._show_current()
        text = "Done in %.1f s (seed %d)." % (elapsed, seed)
        if canvas.scaling_problem:
            text += " Sharp scaling wasn't available: %s." % canvas.scaling_problem
        self._say(text)

    # ---- Results ----------------------------------------------------------

    def _show_preview(self):
        self.preview.open(canvas.main_window())

    def _show_current(self, *_):
        """Show the result picked in the history, as NovelAI sent it."""
        item = self.history.currentItem()
        image = None
        if item is not None:
            result = self._result(item)
            image = result.shown if result.shown is not None else QImage.fromData(result.original)
        self.preview.view.set_image(image)
        self.preview.add_button.setEnabled(item is not None)

    def _add_current(self):
        item = self.history.currentItem()
        if item is not None:
            self._insert(self._result(item))

    def _add_history(self, result):
        thumb = (result.shown if result.shown is not None else result.image).scaled(
            THUMBNAIL_SIZE, THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        item = QListWidgetItem(QIcon(QPixmap.fromImage(thumb)), "")
        item.setToolTip("Seed %d\n%s" % (result.seed, result.prompt[:200]))
        item.setData(Qt.ItemDataRole.UserRole, self._next_id)
        self._results[self._next_id] = result
        self._next_id += 1
        self.history.insertItem(0, item)
        self._trim_history()

    def _result(self, item):
        return self._results[item.data(Qt.ItemDataRole.UserRole)]

    def _remove(self, item):
        """Drop a result from the history (and from memory)."""
        self._results.pop(item.data(Qt.ItemDataRole.UserRole), None)
        self.history.takeItem(self.history.row(item))

    def _trim_history(self):
        """Keep only the newest results (Settings > History size)."""
        limit = max(1, settings.get_int("history_limit"))
        while self.history.count() > limit:
            self._remove(self.history.item(self.history.count() - 1))  # Oldest last.

    def clear_history(self):
        """Remove every result from the history, after asking."""
        count = self.history.count()
        if count == 0:
            return
        answer = QMessageBox.question(
            self, "Clear history",
            "Remove all %d results from the history?\n"
            "Layers you already added to documents are not affected." % count)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.history.clear()
        self._results.clear()
        self._show_current()
        self._say("History cleared.")

    def _save(self, result):
        """Save NovelAI's original file, which keeps its embedded prompt info."""
        is_webp = result.original[:4] == b"RIFF"
        extension = "webp" if is_webp else "png"
        folder = settings.get("save_dir") or os.path.join(os.path.expanduser("~"), "Pictures")
        suggested = os.path.join(folder, "novelai_%d.%s" % (result.seed, extension))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save generated image", suggested,
            "WebP image (*.webp)" if is_webp else "PNG image (*.png)")
        if not path:
            return
        try:
            with open(path, "wb") as file:
                file.write(result.original)
        except OSError as exc:
            self._say("Could not save the image: %s" % exc)
            return
        settings.set("save_dir", os.path.dirname(path))
        self._say("Saved %s" % path)

    def _insert(self, result):
        doc = result.document if canvas.document_is_open(result.document) else None
        doc = doc or canvas.active_document()
        if doc is None:
            self._say("Open a document to add the result.")
            return
        name = "NovelAI %d: %s" % (result.seed, result.prompt[:40])
        canvas.insert_layer(doc, result.image, result.x, result.y, name)
        self._say("Added as a new layer.")

    def _history_menu(self, pos):
        item = self.history.itemAt(pos)
        if item is None:
            # Right-click on empty space: only the clear option.
            if self.history.count():
                menu = QMenu(self)
                clear = menu.addAction("Clear history...")
                if menu.exec(self.history.mapToGlobal(pos)) == clear:
                    self.clear_history()
            return
        result = self._result(item)
        menu = QMenu(self)
        insert = menu.addAction("Add as new layer")
        save = menu.addAction("Save image...")
        menu.addSeparator()
        use_seed = menu.addAction("Use this seed")
        use_prompt = menu.addAction("Use this prompt")
        menu.addSeparator()
        remove = menu.addAction("Remove from history")
        clear = menu.addAction("Clear history...")
        chosen = menu.exec(self.history.mapToGlobal(pos))
        if chosen == insert:
            self._insert(result)
        elif chosen == save:
            self._save(result)
        elif chosen == remove:
            self._remove(item)
        elif chosen == clear:
            self.clear_history()
        elif chosen == use_seed:
            self.random_seed.setChecked(False)
            self.seed.setText(str(result.seed))
            self.advanced_toggle.setChecked(True)
        elif chosen == use_prompt:
            self.prompt.setPlainText(result.prompt)
            self.characters.set_characters(result.characters)
            self.characters.set_custom_positions(result.custom_positions)
            self._state_changed()  # Setting the characters doesn't store them.
