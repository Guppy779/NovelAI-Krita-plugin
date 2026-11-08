import importlib.util
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_package_script():
    spec = importlib.util.spec_from_file_location("package", ROOT / "scripts" / "package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zip_has_what_kritas_importer_looks_for(tmp_path):
    target = tmp_path / "novelai_krita.zip"
    load_package_script().build(target)
    names = zipfile.ZipFile(target).namelist()
    # Krita's plugin_importer.py needs the .desktop file, the package's own
    # folder entry and an __init__.py inside that folder.
    assert "novelai_krita.desktop" in names
    assert "novelai_krita/" in names
    assert "novelai_krita/__init__.py" in names
    assert "novelai_krita/LICENSE" in names
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
