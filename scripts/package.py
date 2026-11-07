"""Build dist/novelai_krita.zip for Krita's "Import Python Plugin from File"."""

import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
DIST = ROOT / "dist"


def main():
    DIST.mkdir(exist_ok=True)
    target = DIST / "novelai_krita.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            archive.write(path, path.relative_to(PLUGIN).as_posix())
    print("Wrote", target)


if __name__ == "__main__":
    main()
