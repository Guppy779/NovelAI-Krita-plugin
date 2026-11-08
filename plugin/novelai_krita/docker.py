"""The NovelAI docker: prompt, parameters, Generate button and result history."""

import os
import time
from dataclasses import dataclass

from krita import DockWidget

from . import api, canvas, doc_state, presets, settings
from .client import NovelAIClient
from .prompt_edit import PromptEdit
from .settings_dialog import SettingsDialog, max_pixels
from .qt import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QIcon, QImage, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPixmap, QProgressBar, QPushButton, QSize, QSlider,
    QSpinBox, Qt, QTimer, QToolButton, QVBoxLayout, QWidget,
)

THUMBNAIL_SIZE = 96


@dataclass
class Result:
    image: QImage  # Already composited to the region, ARGB32.
    x: int
    y: int
    seed: int
    prompt: str
    document: object
    original: bytes = b""  # The file exactly as NovelAI sent it.


class NovelAIDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NovelAI")
        self._client = NovelAIClient(self)
        self._job = None
        self._started = 0.0
        self._results = {}  # History item id -> Result.
        self._next_id = 0
        self._preview = None  # Temporary "NovelAI preview" layer while streaming.
        self._preview_doc = None
        # Each document keeps its own prompt and settings (see doc_state.py).
        self._doc = None  # The document the panel currently shows.
        self._loading = False  # Filling the panel from a document's settings.
        self._edited_without_doc = False

        root = QWidget(self)
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
        self.prompt = PromptEdit("", self._suggest_tags)
        self.prompt.setPlaceholderText("Prompt, e.g. 1girl, silver hair, night sky, city lights")
        self.prompt.setMinimumHeight(70)
        layout.addWidget(self.prompt, 2)
        self.negative = PromptEdit("", self._suggest_tags)
        self.negative.setPlaceholderText("Undesired content (added after the preset)")
        self.negative.setMinimumHeight(40)
        layout.addWidget(self.negative, 1)

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
        row = QHBoxLayout()
        row.addWidget(self.generate_button, 1)
        row.addWidget(self.cancel_button)
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

        # History of results.
        self.history = QListWidget()
        self.history.setViewMode(QListWidget.ViewMode.IconMode)
        self.history.setIconSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        self.history.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.history.setMovement(QListWidget.Movement.Static)
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self._history_menu)
        self.history.itemDoubleClicked.connect(lambda item: self._insert(self._result(item)))
        self.history.setToolTip("Double-click a result to add it again as a new layer.")
        self.history.setMinimumHeight(THUMBNAIL_SIZE + 16)
        layout.addWidget(self.history, 2)

        self.setWidget(root)

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
        elif canvas.has_selection(doc):
            mode = api.selection_mode(self._model_name(), value / 100.0)
            if mode == "infill":
                text = ("Inpaint the selection" if value >= 100
                        else "Inpaint the selection at %d%% strength" % value)
            else:
                text = "Refine the selection (image to image, edges may show)"
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
            _, _, w, h, selected = canvas.region_bounds(
                doc, settings.get_int("context_padding"), max_pixels())
            width, height = self._generation_size(
                w, h, self.strength.value() < 100 or selected)
            reasons = api.anlas_warnings(
                width, height, self.steps.value(), self.n_samples.value(),
                self._model_name(), self._account)
        self.cost_label.setText("Costs Anlas: " + "; ".join(reasons) if reasons else "")
        self.cost_label.setVisible(bool(reasons))

    def _set_busy(self, busy):
        self.generate_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)

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
        finally:
            self._loading = False

    def _follow_document(self):
        """Show the active document's settings when another document becomes active."""
        doc = canvas.active_document()
        if canvas.same_document(doc, self._doc):
            return
        self._doc = doc
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

    def _save_last_used(self):
        """Remember the settings (not the prompts) for documents without their own."""
        for key, value in self._panel_state().items():
            if key not in doc_state.PROMPTS:
                settings.set(key, value)
        settings.set("show_advanced", self.advanced_toggle.isChecked())

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
            self.status.setText("Open or create a document first.")
            return
        self._follow_document()  # Use this document's own settings.
        if not self._model_name():
            self.status.setText("Choose or type a model first.")
            return
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            self.status.setText("Write a prompt first.")
            return
        token = settings.get("token")
        if not token:
            self.status.setText("Add your NovelAI API token in Settings.")
            self.open_settings()
            token = settings.get("token")
            if not token:
                return
        try:
            seed = self._seed()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._save_last_used()

        strength = self.strength.value() / 100.0
        region = canvas.get_region(doc, settings.get_int("context_padding"), max_pixels())
        width, height = self._generation_size(
            region.width, region.height, strength < 1.0 or region.mask is not None)
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
            strength=strength,
        )
        mode = api.selection_mode(params.model, strength) if region.mask is not None else None
        # For inpainting, the image sent is kept to find which pixels NovelAI
        # changed; the result layer keeps exactly those (see compose_result).
        sent_image = None
        if strength < 1.0 or region.mask is not None:
            source = canvas.resize(canvas.read_image(doc, region), width, height)
            params.image = canvas.to_base64_png(source)
            if mode == "infill":
                params.mask = canvas.to_base64_png(canvas.api_mask(region, width, height))
                if settings.get_bool("inpaint_full_image"):
                    # Keep NovelAI's whole image: drop the selection cut.
                    region = canvas.Region(region.x, region.y, region.width, region.height)
                else:
                    sent_image = source
        if self.random_seed.isChecked():
            self.seed.setText(str(seed))  # Show which random seed was used.

        payload = api.build_payload(params)
        self.status.setText("Generating %d x %d (%s)..." % (width, height, params.action))
        self._started = time.monotonic()
        self._set_busy(True)
        on_images = lambda images: self._on_images(images, doc, region, seed, prompt, sent_image)
        if settings.get_bool("live_preview") and api.supports_streaming(params.model):
            self._preview_doc = doc
            self._job = self._client.generate_stream(
                token, payload,
                lambda event: self._on_preview(event, doc, region, params.steps),
                on_images, self._on_error)
        else:
            self._job = self._client.generate(token, payload, on_images, self._on_error)

    def cancel(self):
        if self._job is not None:
            self._job.cancel()
            self._job = None
        self._clear_preview()
        self._set_busy(False)
        self.status.setText("Cancelled.")

    def _on_error(self, message):
        self._job = None
        self._clear_preview()
        self._set_busy(False)
        self.status.setText(message)
        self.refresh_account()

    def _on_preview(self, event, doc, region, steps):
        """Show an in-progress image from the stream (first image of a batch)."""
        if event.sample != 0 or self._job is None or not canvas.document_is_open(doc):
            return
        image = QImage.fromData(event.image)
        if image.isNull():
            return
        try:
            self._preview = canvas.show_preview(doc, self._preview, image, region)
        except Exception:  # A preview must never break the generation itself.
            self._preview = None
            return
        self.status.setText("Generating... step %d of %d" % (event.step + 1, steps))

    def _clear_preview(self):
        try:
            canvas.remove_preview(self._preview_doc, self._preview)
        finally:
            self._preview = None
            self._preview_doc = None

    def _on_images(self, images, doc, region, seed, prompt, sent_image=None):
        self._job = None
        self._clear_preview()
        self._set_busy(False)
        self.refresh_account()  # Anlas / V5 allowance may have changed.
        elapsed = time.monotonic() - self._started
        if not images:
            self.status.setText("NovelAI returned no images.")
            return
        if not canvas.document_is_open(doc):
            doc = None

        for index, (data, image_seed) in enumerate(images):
            image = QImage.fromData(data)
            if image.isNull():
                continue
            result = Result(
                image=canvas.compose_result(image, region, sent_image),
                x=region.x, y=region.y,
                seed=image_seed if image_seed is not None else seed + index,
                prompt=prompt, document=doc, original=data)
            self._add_history(result)
            if doc is not None:
                self._insert(result, visible=(index == 0))

        self.status.setText("Done in %.1f s (seed %d)." % (elapsed, seed))

    # ---- Results ----------------------------------------------------------

    def _add_history(self, result):
        thumb = result.image.scaled(
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
        self.status.setText("History cleared.")

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
            self.status.setText("Could not save the image: %s" % exc)
            return
        settings.set("save_dir", os.path.dirname(path))
        self.status.setText("Saved %s" % path)

    def _insert(self, result, visible=True):
        doc = result.document if canvas.document_is_open(result.document) else None
        doc = doc or canvas.active_document()
        if doc is None:
            self.status.setText("Open a document to add the result.")
            return
        name = "NovelAI %d: %s" % (result.seed, result.prompt[:40])
        canvas.insert_layer(doc, result.image, result.x, result.y, name, visible)

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
