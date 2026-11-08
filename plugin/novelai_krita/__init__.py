"""NovelAI image generation for Krita.

Registers the NovelAI docker. Everything that talks to Krita or Qt lives in
the submodules; ``api`` is plain Python so it can be tested outside Krita.
"""

from .version import __version__

try:
    from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita
except ImportError:  # Imported outside Krita (e.g. by the test suite).
    Krita = None

if Krita is not None:
    from .docker import NovelAIDocker

    _position = getattr(DockWidgetFactoryBase, "DockRight", None)
    if _position is None:  # Scoped enum naming (PyQt6 / Krita 6).
        _position = DockWidgetFactoryBase.DockPosition.DockRight

    Krita.instance().addDockWidgetFactory(
        DockWidgetFactory("novelai_krita_docker", _position, NovelAIDocker)
    )
