"""Qt import shim: Krita 5 ships PyQt5, Krita 6 ships PyQt6.

Code elsewhere uses fully scoped enum names (``Qt.AlignmentFlag.AlignLeft``),
which work with both bindings.
"""

try:
    from PyQt5.QtCore import (  # noqa: F401
        QBuffer, QByteArray, QEvent, QIODevice, QModelIndex, QObject, QPointF, QRectF, QSize,
        QTimer, QUrl, Qt,
    )
    from PyQt5.QtGui import (  # noqa: F401
        QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap, QStandardItem,
        QStandardItemModel, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
    )
    from PyQt5.QtNetwork import (  # noqa: F401
        QNetworkAccessManager, QNetworkReply, QNetworkRequest,
    )
    from PyQt5.QtWidgets import (  # noqa: F401
        QAbstractItemView, QAbstractSpinBox, QButtonGroup, QCheckBox, QComboBox, QCompleter,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
        QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
        QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
    )
except ImportError:
    from PyQt6.QtCore import (  # noqa: F401
        QBuffer, QByteArray, QEvent, QIODevice, QModelIndex, QObject, QPointF, QRectF, QSize,
        QTimer, QUrl, Qt,
    )
    from PyQt6.QtGui import (  # noqa: F401
        QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap, QStandardItem,
        QStandardItemModel, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
    )
    from PyQt6.QtNetwork import (  # noqa: F401
        QNetworkAccessManager, QNetworkReply, QNetworkRequest,
    )
    from PyQt6.QtWidgets import (  # noqa: F401
        QAbstractItemView, QAbstractSpinBox, QButtonGroup, QCheckBox, QComboBox, QCompleter,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
        QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
        QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
    )


def image_bytes(image):
    """Return the raw pixel bytes of a QImage (works with PyQt5 and PyQt6)."""
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    return ptr.asstring()


def event_point(event):
    """Where a mouse event happened (PyQt5 and PyQt6 name this differently)."""
    return QPointF(event.position() if hasattr(event, "position") else event.pos())


class _PassWheel(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            event.ignore()  # Qt then offers it to the parent, which scrolls.
            return True
        return False


def ignore_wheel(root):
    """Stop the combo boxes, number boxes and sliders under ``root`` from
    changing when the mouse wheel passes over them on the way down a panel."""
    passer = _PassWheel(root)
    for kind in (QComboBox, QAbstractSpinBox, QSlider):
        for widget in root.findChildren(kind):
            widget.installEventFilter(passer)
