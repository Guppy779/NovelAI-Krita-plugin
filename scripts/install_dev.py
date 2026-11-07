"""Install the plugin into Krita's pykrita folder for development.

The package folder is linked (a directory junction on Windows, a symlink
elsewhere), so edits in this repo show up after restarting Krita. The small
.desktop file is copied.

    python scripts/install_dev.py            # link
    python scripts/install_dev.py --remove   # undo
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
NAME = "novelai_krita"


def pykrita_dir():
    if sys.platform == "win32":
        return pathlib.Path(os.environ["APPDATA"]) / "krita" / "pykrita"
    if sys.platform == "darwin":
        return pathlib.Path.home() / "Library" / "Application Support" / "krita" / "pykrita"
    return pathlib.Path.home() / ".local" / "share" / "krita" / "pykrita"


def is_link(path):
    """True for symlinks and Windows directory junctions."""
    try:
        os.readlink(path)  # Also reads junctions on Windows (Python 3.8+).
        return True
    except (OSError, ValueError):
        return False


def remove(target):
    package = target / NAME
    if is_link(package):
        if package.is_symlink():
            package.unlink()
        else:
            os.rmdir(package)  # Removes the junction, not the files it points to.
    elif package.exists():
        sys.exit("%s is a real folder (an installed copy?). Remove it manually first." % package)
    desktop = target / (NAME + ".desktop")
    if desktop.exists():
        desktop.unlink()


def install(target):
    target.mkdir(parents=True, exist_ok=True)
    remove(target)
    shutil.copy2(PLUGIN / (NAME + ".desktop"), target / (NAME + ".desktop"))
    source, link = PLUGIN / NAME, target / NAME
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(source)], check=True)
    else:
        link.symlink_to(source, target_is_directory=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remove", action="store_true", help="uninstall instead")
    args = parser.parse_args()
    target = pykrita_dir()
    if args.remove:
        remove(target)
        print("Removed from", target)
    else:
        install(target)
        print("Installed into", target)
        print("Restart Krita, then enable 'NovelAI Image Generation' in")
        print("Settings > Configure Krita > Python Plugin Manager.")


if __name__ == "__main__":
    main()
