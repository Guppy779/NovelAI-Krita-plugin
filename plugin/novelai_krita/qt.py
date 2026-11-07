"""Qt import shim: Krita 5 ships PyQt5, Krita 6 ships PyQt6.

Code elsewhere uses fully scoped enum names (``Qt.AlignmentFlag.AlignLeft``),
which work with both bindings.
"""

try:
    from PyQt5.QtCore import (  # noqa: F401
        QBuffer, QByteArray, QIODevice, QObject, QRect, QSize, QTimer, QUrl, Qt,
        pyqtSignal,
    )
    from PyQt5.QtGui import QColor, QIcon, QImage, QPainter, QPixmap  # noqa: F401
    from PyQt5.QtNetwork import (  # noqa: F401
        QNetworkAccessManager, QNetworkReply, QNetworkRequest,
    )
    from PyQt5.QtWidgets import (  # noqa: F401
        QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
        QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
        QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox,
        QToolButton, QVBoxLayout, QWidget,
    )

    QT_VERSION = 5
except ImportError:
    from PyQt6.QtCore import (  # noqa: F401
        QBuffer, QByteArray, QIODevice, QObject, QRect, QSize, QTimer, QUrl, Qt,
        pyqtSignal,
    )
    from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap  # noqa: F401
    from PyQt6.QtNetwork import (  # noqa: F401
        QNetworkAccessManager, QNetworkReply, QNetworkRequest,
    )
    from PyQt6.QtWidgets import (  # noqa: F401
        QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
        QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
        QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox,
        QToolButton, QVBoxLayout, QWidget,
    )

    QT_VERSION = 6


def image_bytes(image):
    """Return the raw pixel bytes of a QImage (works with PyQt5 and PyQt6)."""
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    return ptr.asstring()
