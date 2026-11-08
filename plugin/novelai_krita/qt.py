"""Qt import shim: Krita 5 ships PyQt5, Krita 6 ships PyQt6.

Code elsewhere uses fully scoped enum names (``Qt.AlignmentFlag.AlignLeft``),
which work with both bindings.
"""

try:
    from PyQt5.QtCore import (  # noqa: F401
        QBuffer, QByteArray, QIODevice, QModelIndex, QObject, QSize, QTimer,
        QUrl, Qt,
    )
    from PyQt5.QtGui import (  # noqa: F401
        QColor, QFont, QIcon, QImage, QPainter, QPixmap, QStandardItem,
        QStandardItemModel, QTextCursor,
    )
    from PyQt5.QtNetwork import (  # noqa: F401
        QNetworkAccessManager, QNetworkReply, QNetworkRequest,
    )
    from PyQt5.QtWidgets import (  # noqa: F401
        QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
        QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
        QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox,
        QToolButton, QVBoxLayout, QWidget,
    )
except ImportError:
    from PyQt6.QtCore import (  # noqa: F401
        QBuffer, QByteArray, QIODevice, QModelIndex, QObject, QSize, QTimer,
        QUrl, Qt,
    )
    from PyQt6.QtGui import (  # noqa: F401
        QColor, QFont, QIcon, QImage, QPainter, QPixmap, QStandardItem,
        QStandardItemModel, QTextCursor,
    )
    from PyQt6.QtNetwork import (  # noqa: F401
        QNetworkAccessManager, QNetworkReply, QNetworkRequest,
    )
    from PyQt6.QtWidgets import (  # noqa: F401
        QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
        QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
        QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox,
        QToolButton, QVBoxLayout, QWidget,
    )


def image_bytes(image):
    """Return the raw pixel bytes of a QImage (works with PyQt5 and PyQt6)."""
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    return ptr.asstring()
