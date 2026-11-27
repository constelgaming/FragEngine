"""One native picker implementation shared by FragEngine's desktop windows."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _zenity(arguments: list[str]) -> str | None:
    """Use the GTK/GNOME file chooser (the same chooser Nautilus uses on Linux)."""
    if not sys.platform.startswith("linux") or shutil.which("zenity") is None:
        return None
    result = subprocess.run(
        ["zenity", "--file-selection", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else ""


def choose_video() -> str | None:
    return _zenity([
        "--title=Select Valorant recording",
        "--file-filter=Video files | *.mkv *.mp4 *.mov *.avi",
        "--file-filter=All files | *",
    ])


def choose_image() -> str | None:
    return _zenity([
        "--title=Load gameplay screenshot",
        "--file-filter=Image files | *.png *.jpg *.jpeg *.bmp *.webp",
        "--file-filter=All files | *",
    ])


def choose_folder() -> str | None:
    return _zenity(["--title=Select output folder", "--directory"])


def open_folder(path: Path) -> None:
    """Open a directory using the current platform's file manager."""
    if sys.platform.startswith("win"):
        import os
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
