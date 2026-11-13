"""The director tools window: Remove BG, Line Art, Sketch, Colorize and Upscale.

Like the Director Tools page of the NovelAI site: a picture goes in on the
left, a tool is run on it, and the result shows on the right. The picture is
the canvas or any image file, dropped, chosen or pasted.

A picture is sent as the site sends it (see api.tool_size and
canvas.tool_picture), except that one above Max generation size is made
smaller first, as for generating, so that Opus stays free.

The window stays above Krita without blocking it. The docker keeps the
results (in the history of the preview window) and puts them into documents.
"""

import base64
import os
import time

from . import api, canvas, settings
from .import_dialog import MAX_FILE_SIZE
from .preview import ImageView
from .prompt_edit import IMAGE_EXTENSIONS, PromptEdit
from .references import read_picture
from .settings_dialog import max_pixels
from .qt import (
    QApplication, QButtonGroup, QByteArray, QEventLoop, QFileDialog, QHBoxLayout, QImage,
    QKeySequence, QLabel, QProgressBar, QPushButton, Qt, QTimer, QToolButton, QVBoxLayout,
    QWidget,
)

_RGBA = QImage.Format.Format_RGBA8888
SUGGEST_MODEL = "nai-diffusion-3"  # The site looks Colorize's tags up with V3.

TOOL_TIPS = {
    "bg-removal": "Remove the background. NovelAI answers with three pictures:\n"
                  "Masked (your picture, cut out), Generated (redrawn without the\n"
                  "background) and Blend (a mix). It always costs Anlas.",
    "lineart": "Turn the picture into line art.",
    "sketch": "Turn the picture into a sketch.",
    "colorize": "Colour line art, or recolour a picture. Defry and the prompt\n"
                "steer it.",
}
DEFRY_TIP = ("Higher values tone down noise and colours that come out too strong.\n"
             "Useful for pictures that already have colour.")
UPSCALE_TIP = ("Enlarge the picture with NovelAI's upscaler. It takes pictures up to\n"
               "1024 x 1024 in area, as they are, and costs Anlas.")
ADD_TIP = ("Add the result to the current document as a new layer. A result made\n"
           "from the canvas is fitted to it. Any other goes in at its own size, centred.")
RESIZE_TIP = ("Enlarge the whole current document to the result's size, layers included,\n"
              "then add the result as a new layer. For a result that is larger than the\n"
              "canvas and has its shape, such as the canvas upscaled.")
DOCUMENT_TIP = "Open the result as a new document."
SOURCE_TIP = "Use the result as the picture, to run another tool on it."
SAVE_TIP = ("Save the result shown as an image file: the picture exactly as NovelAI\n"
            "sent it, at its own size. Remove BG's three pictures are saved one at a\n"
            "time: choose Masked, Generated or Blend first.")


class ToolsWindow(QWidget):
    def __init__(self, client, host):
        """``host`` is the docker: it knows the account, keeps the results
        and puts them into documents."""
        super().__init__(None, Qt.WindowType.Tool)
        self.setWindowTitle("NovelAI director tools")
        self.setAcceptDrops(True)
        self.resize(940, 640)
        self._client = client
        self._host = host
        self._restored = False
        self._job = None
        self._started = 0.0
        self._source = None  # The picture, as it was read (RGBA).
        self._source_file = b""  # An image file's own bytes: the upscaler gets those.
        self._source_name = ""
        self._from_canvas = False  # Read the canvas again when a tool is run.
        # (document, width, height) of the canvas the picture shows, so that
        # a result can be fitted back onto it.
        self._origin = None
        self._results = []  # The last run's, as the docker keeps them.
        self._results_origin = None

        # The picture and the result, side by side.
        self.source_view = ImageView("Drop or paste a picture here,\nor use the buttons below.",
                                     checks=True)
        self.result_view = ImageView("The result appears here.", checks=True)
        self.canvas_button = QPushButton("From canvas")
        self.canvas_button.setToolTip(
            "Use what is visible on the canvas. It is read again each time a tool\n"
            "is run, so the tool works on the canvas as it is then.")
        self.canvas_button.clicked.connect(self.use_canvas)
        self.file_button = QPushButton("From file...")
        self.file_button.setToolTip("Use an image file. You can also drop one on this window,\n"
                                    "or paste a picture with Ctrl+V.")
        self.file_button.clicked.connect(self.choose_file)
        self.source_label = QLabel("")
        self.source_label.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.canvas_button)
        row.addWidget(self.file_button)
        row.addStretch(1)
        left = QVBoxLayout()
        left.addWidget(self._heading("Picture"))
        left.addWidget(self.source_view, 1)
        left.addLayout(row)
        left.addWidget(self.source_label)

        # Remove BG answers with three pictures; these choose the one shown.
        self.choice_row = QHBoxLayout()
        self.choice_row.addWidget(self._heading("Result"))
        self.choice_row.addStretch(1)
        self._choices = QButtonGroup(self)
        self._choices.setExclusive(True)
        self._choice_buttons = []
        self.add_button = QPushButton("Add as layer")
        self.add_button.setToolTip(ADD_TIP)
        self.add_button.clicked.connect(lambda: self._place(self._host.insert_result))
        self.resize_button = QPushButton("Resize canvas to it")
        self.resize_button.setToolTip(RESIZE_TIP)
        self.resize_button.clicked.connect(lambda: self._place(self._host.resize_canvas_to))
        self.document_button = QPushButton("New document")
        self.document_button.setToolTip(DOCUMENT_TIP)
        self.document_button.clicked.connect(lambda: self._place(self._host.open_as_document))
        self.reuse_button = QPushButton("Use as picture")
        self.reuse_button.setToolTip(SOURCE_TIP)
        self.reuse_button.clicked.connect(self.use_result)
        self.save_button = QPushButton("Save...")
        self.save_button.setToolTip(SAVE_TIP)
        self.save_button.clicked.connect(self.save_result)
        self._result_buttons = (self.add_button, self.resize_button, self.document_button,
                                self.reuse_button, self.save_button)
        row = QHBoxLayout()
        for button in self._result_buttons:
            button.setEnabled(False)
            row.addWidget(button)
        row.addStretch(1)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        right = QVBoxLayout()
        right.addLayout(self.choice_row)
        right.addWidget(self.result_view, 1)
        right.addLayout(row)
        right.addWidget(self.result_label)

        pictures = QHBoxLayout()
        pictures.addLayout(left, 1)
        pictures.addLayout(right, 1)

        # The tools.
        self._tools = QButtonGroup(self)
        self._tools.setExclusive(True)
        tools = QHBoxLayout()
        chosen = settings.get("tool")
        if chosen not in dict(api.TOOLS):
            chosen = settings.DEFAULTS["tool"]
        for tool, label in api.TOOLS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(tool == chosen)
            button.setToolTip(TOOL_TIPS.get(tool, ""))
            button.setProperty("tool", tool)
            self._tools.addButton(button)
            tools.addWidget(button)
        tools.addStretch(1)
        self._tools.buttonClicked.connect(self._tool_chosen)

        # Colorize's own settings.
        self._defry = QButtonGroup(self)
        self._defry.setExclusive(True)
        defry_row = QHBoxLayout()
        defry_row.setSpacing(2)
        defry = max(0, min(api.MAX_DEFRY, settings.get_int("tool_defry")))
        for value in range(api.MAX_DEFRY + 1):
            button = QToolButton()
            button.setText(str(value))
            button.setCheckable(True)
            button.setChecked(value == defry)
            button.setToolTip(DEFRY_TIP)
            self._defry.addButton(button, value)
            defry_row.addWidget(button)
        self.prompt = PromptEdit("", self._suggest_tags, on_image=self.use_file)
        self.prompt.setPlaceholderText("Prompt (optional), e.g. red hair, blue eyes")
        self.prompt.setFixedHeight(52)
        self.colorize = QWidget()
        row = QHBoxLayout(self.colorize)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel("Defry")
        label.setToolTip(DEFRY_TIP)
        row.addWidget(label)
        row.addLayout(defry_row)
        row.addSpacing(8)
        row.addWidget(self.prompt, 1)

        # Running them.
        self.transform_button = QPushButton("Transform")
        self.transform_button.setMinimumHeight(32)
        self.transform_button.setMinimumWidth(140)
        self.transform_button.clicked.connect(self.transform)
        self.upscale_button = QPushButton("Upscale")
        self.upscale_button.setMinimumHeight(32)
        self.upscale_button.setToolTip(UPSCALE_TIP)
        self.upscale_button.clicked.connect(self.upscale)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.cost_label = QLabel("")
        self.cost_label.setWordWrap(True)
        self.cost_label.setStyleSheet("color: #e8a33d;")
        run = QHBoxLayout()
        run.addWidget(self.transform_button)
        run.addWidget(self.upscale_button)
        run.addWidget(self.cancel_button)
        run.addWidget(self.cost_label, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        self.progress.setVisible(False)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(pictures, 1)
        layout.addLayout(tools)
        layout.addWidget(self.colorize)
        layout.addLayout(run)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        self.refresh()

    @staticmethod
    def _heading(text):
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold;")
        return label

    # ---- The window ----------------------------------------------------------

    def open(self, owner=None):
        """Show the window, kept above ``owner`` (Krita's main window)."""
        if owner is not None and self.parent() is None:
            self.setParent(owner, self.windowFlags())
        if not self._restored:
            self._restored = True
            saved = settings.get("tools_geometry")
            if saved:
                self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii")))
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh()

    def save_state(self):
        settings.set("tools_geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))
        settings.set("tool", self._tool())
        settings.set("tool_defry", self._defry.checkedId())

    def hideEvent(self, event):
        self.save_state()
        super().hideEvent(event)

    def _say(self, text):
        self.status.setText(text)

    def _tool(self):
        button = self._tools.checkedButton()
        return button.property("tool") if button is not None else settings.DEFAULTS["tool"]

    def _tool_chosen(self, *_):
        self.refresh()

    def _sent_size(self):
        return api.tool_size(self._source.width(), self._source.height(), max_pixels())

    def refresh(self):
        """Bring the notes up to date: the size sent, what it costs, which
        settings show. The docker calls this too, as the account and Max
        generation size can change."""
        tool = self._tool()
        self.colorize.setVisible(tool == api.COLORIZE)
        self.transform_button.setToolTip(
            "Run %s on the picture.\n%s" % (api.tool_name(tool), TOOL_TIPS.get(tool, "")))
        if self._source is None:
            self.source_label.setText("")
            self.cost_label.setText("")
            return
        width, height = self._source.width(), self._source.height()
        sent = self._sent_size()
        text = "%s, %d x %d" % (self._source_name, width, height)
        if sent != (width, height):
            text += ". The tools get it at %d x %d." % sent
        self.source_label.setText(text)
        notes = []
        cost = api.tool_cost(tool, sent[0], sent[1], self._host.account())
        if cost:
            notes.append("%s costs %s." % (api.tool_name(tool), cost))
        price = api.upscale_price(width, height)
        if price:
            notes.append("Upscale costs %d Anlas." % price)
        self.cost_label.setText(" ".join(notes))

    def _suggest_tags(self, query, lang, on_results):
        token = settings.get("token")
        if not token or not settings.get_bool("autocomplete"):
            return None
        return self._client.suggest_tags(token, SUGGEST_MODEL, query, lang, on_results,
                                         lambda error: None)

    # ---- The picture ---------------------------------------------------------

    def _set_source(self, image, name, file=b"", origin=None, from_canvas=False):
        self._source = image.convertToFormat(_RGBA)
        self._source_file = file
        self._source_name = name
        self._origin = origin
        self._from_canvas = from_canvas
        self.source_view.set_image(self._source)
        self.refresh()

    def _read_canvas(self):
        """Take the active document's canvas as the picture. False without one."""
        doc = canvas.active_document()
        if doc is None:
            self._say("Open a document first, or use a picture from a file.")
            return False
        self._set_source(canvas.read_picture(doc), "The canvas",
                         origin=(doc, doc.width(), doc.height()), from_canvas=True)
        return True

    def use_canvas(self):
        if self._read_canvas():
            self._say("")

    def use_file(self, path):
        name = os.path.basename(path)
        try:
            if os.path.getsize(path) > MAX_FILE_SIZE:
                self._say("%s is too large (over %d MB)." % (name, MAX_FILE_SIZE // (1024 * 1024)))
                return
            with open(path, "rb") as file:
                data = file.read()
        except OSError as exc:
            self._say("Could not read %s: %s." % (name, exc.strerror or exc))
            return
        image = read_picture(path)
        if image.isNull() or image.width() < 1 or image.height() < 1:
            self._say("Could not read %s as an image." % name)
            return
        self._set_source(image, name, file=data)
        self._say("")

    def choose_file(self):
        folder = settings.get("tool_dir") or os.path.join(os.path.expanduser("~"), "Pictures")
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a picture", folder,
            "Images (%s)" % " ".join("*" + extension for extension in IMAGE_EXTENSIONS))
        if path:
            settings.set("tool_dir", os.path.dirname(path))
            self.use_file(path)

    def paste(self):
        image = QApplication.clipboard().image()
        if image.isNull():
            self._say("There is no picture on the clipboard.")
            return
        self._set_source(image, "The pasted picture")
        self._say("")

    def use_result(self):
        result = self._shown()
        if result is None:
            return
        image = QImage.fromData(result.original)
        if image.isNull():
            return
        # As the site does it: the result's own file becomes the picture.
        self._set_source(image, result.label, file=result.original,
                         origin=self._results_origin)
        self._say("")

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _dropped(event):
        """The image file being dragged, or the picture itself, or None."""
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile() if url.isLocalFile() else ""
                if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS:
                    return path
        if mime.hasImage():
            image = QImage(mime.imageData())
            return None if image.isNull() else image
        return None

    def dragEnterEvent(self, event):
        if self._dropped(event) is not None:
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self._dropped(event) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event):
        dropped = self._dropped(event)
        if dropped is None:
            return
        event.acceptProposedAction()
        # After the drop has finished: the system's drag is still running here.
        if isinstance(dropped, str):
            QTimer.singleShot(0, lambda: self.use_file(dropped))
        else:
            QTimer.singleShot(0, lambda: self._set_source(dropped, "The dropped picture"))

    # ---- Running a tool ------------------------------------------------------

    def _ready(self):
        """The API token once there is a picture to work on, else ""."""
        if self._job is not None:
            return ""
        if self._from_canvas and not self._read_canvas():
            return ""  # The canvas as it is now.
        if self._source is None:
            self._say("Choose a picture first: From canvas, From file, or drop one here.")
            return ""
        token = settings.get("token")
        if not token:
            self._say("Add your NovelAI API token in Settings.")
            self._host.open_settings()
            token = settings.get("token")
        return token

    def _prepared(self, width, height):
        """The picture at the size a tool gets it."""
        if (self._source.width(), self._source.height()) == (width, height):
            return self._source
        # Scaling as the site does takes a second or more for a large picture.
        self._say("Preparing the picture...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            return canvas.tool_picture(self._source, width, height)
        finally:
            QApplication.restoreOverrideCursor()

    def _set_busy(self, busy):
        for button in (self.transform_button, self.upscale_button, self.canvas_button,
                       self.file_button):
            button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)

    def transform(self):
        token = self._ready()
        if not token:
            return
        tool = self._tool()
        width, height = self._sent_size()
        picture = self._prepared(width, height)
        prompt = self.prompt.toPlainText().replace("\n", " ")  # One line, as on the site.
        payload = api.build_tool_payload(tool, canvas.to_base64_png(picture), width, height,
                                         self._defry.checkedId(), prompt)
        name, origin = api.tool_name(tool), self._origin
        self.save_state()
        self._say("%s: sending %d x %d..." % (name, width, height))
        self._started = time.monotonic()
        self._set_busy(True)
        self._job = self._client.augment(
            token, payload,
            lambda images: self._on_images(images, name, api.TOOL_RESULTS.get(tool, []), origin),
            self._on_error)

    def upscale(self):
        token = self._ready()
        if not token:
            return
        width, height = self._source.width(), self._source.height()
        if not api.can_upscale(width, height):
            self._say("Upscale takes pictures up to 1024 x 1024 in area. This one is %d x %d."
                      % (width, height))
            return
        # The site sends the picture as it is: a file's own bytes.
        if self._source_file:
            image = base64.b64encode(self._source_file).decode("ascii")
        else:
            image = canvas.to_base64_png(self._source)
        self._say("Upscale: sending %d x %d..." % (width, height))
        self._started = time.monotonic()
        self._set_busy(True)
        self._job = self._client.upscale(
            token, api.build_upscale_payload(image),
            lambda images: self._on_images(images, "Upscale", [], None), self._on_error)

    def cancel(self):
        if self._job is not None:
            self._job.cancel()
            self._job = None
        self._set_busy(False)
        self._say("Cancelled.")

    def _on_error(self, message):
        self._job = None
        self._set_busy(False)
        self._say(message)
        self._host.refresh_account()

    def _on_images(self, images, name, names, origin):
        self._job = None
        self._set_busy(False)
        self._host.refresh_account()  # Anlas may have been spent.
        elapsed = time.monotonic() - self._started
        if origin is not None and not canvas.document_is_open(origin[0]):
            origin = None
        results = []
        for index, (data, _) in enumerate(images):
            image = QImage.fromData(data)
            if image.isNull():
                continue
            label = "%s (%s)" % (name, names[index]) if index < len(names) else name
            # A result that shows the canvas goes back onto it (the docker
            # fits it when it is added).
            document = None
            if origin is not None and api.same_shape(image.width(), image.height(), *origin[1:]):
                document = origin[0]
            # The layer gets the picture alone, without anything NovelAI hides in it.
            layer = canvas.without_hidden_data(image).convertToFormat(QImage.Format.Format_ARGB32)
            results.append(self._host.add_tool_result(layer, document, data, label))
        if not results:
            self._say("NovelAI returned no picture.")
            return
        self._results = results
        self._results_origin = origin
        self._show_choices(names if len(results) == len(names) else [])
        self._show_result(0)
        self._say("%s done in %.1f s." % (name, elapsed))

    # ---- The result ----------------------------------------------------------

    def _show_choices(self, names):
        for button in self._choice_buttons:
            self._choices.removeButton(button)
            self.choice_row.removeWidget(button)
            button.hide()  # At once: it is only deleted later.
            button.deleteLater()
        self._choice_buttons = []
        for index, name in enumerate(names):
            button = QToolButton()
            button.setText(name)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _=False, index=index: self._show_result(index))
            self._choices.addButton(button, index)
            self.choice_row.addWidget(button)
            self._choice_buttons.append(button)

    def _shown(self):
        if not self._results:
            return None
        index = self._choices.checkedId() if self._choice_buttons else 0
        return self._results[max(0, min(index, len(self._results) - 1))]

    def _show_result(self, index):
        result = self._results[index]
        image = QImage.fromData(result.original)
        self.result_view.set_image(image)
        self.result_label.setText("%s, %d x %d" % (result.label, image.width(), image.height()))
        for button in self._result_buttons:
            button.setEnabled(True)

    def save_result(self):
        result = self._shown()
        if result is None:
            return
        text = self._host.save_result(result, self)
        if text:  # Nothing to say if the file dialog was closed.
            self._say(text)

    def _place(self, action):
        """Run one of the docker's ways of putting the shown result somewhere."""
        result = self._shown()
        if result is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._say(action(result))
        finally:
            QApplication.restoreOverrideCursor()
