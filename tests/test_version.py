import re

import novelai_krita
from novelai_krita.version import __version__


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_package_exposes_version():
    assert novelai_krita.__version__ == __version__
