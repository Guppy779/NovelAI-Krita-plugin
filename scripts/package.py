"""Build dist/novelai_krita.zip for Krita's "Import Python Plugin from File"."""

import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
DIST = ROOT / "dist"


def build(target):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN.rglob("*")):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            # Folders get entries too: Krita's importer finds the plugin by
            # its folder entry ("novelai_krita/"), not by the files in it.
            archive.write(path, path.relative_to(PLUGIN).as_posix())
        # Ship the license inside the plugin folder so it is installed with it.
        archive.write(ROOT / "LICENSE", "novelai_krita/LICENSE")


def main():
    DIST.mkdir(exist_ok=True)
    target = DIST / "novelai_krita.zip"
    build(target)
    print("Wrote", target)


if __name__ == "__main__":
    main()
