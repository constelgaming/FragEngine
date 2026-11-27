"""Central project paths used by the application."""
from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ASSETS_DIR = PROJECT_ROOT / "assets"
TEMPLATES_DIR = ASSETS_DIR / "templates"
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "config.json"
CALIBRATION_OUTPUT_DIR = PROJECT_ROOT / "output" / "calibration"


def project_path(value: str | Path) -> Path:
    """Resolve a configured project-relative path."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path
