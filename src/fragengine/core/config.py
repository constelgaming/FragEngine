"""Persistent calibration settings; per-run paths and times are never saved."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import paths

CONFIG_PATH = paths.CONFIG_PATH
LEGACY_CONFIG_PATH = paths.PROJECT_ROOT / "config.json"
VALID_ACCELERATION = {"none", "cuda", "vaapi", "videotoolbox"}


def default_config() -> dict[str, Any]:
    return {
        "source_resolution": {"width": 0, "height": 0},
        "kill_feed_crop": {"x": 0, "y": 0, "width": 0, "height": 0},
        "sampling": {"fps": "2/3"},
        "download": {"height": 1080, "segment_seconds": 60},
        "ffmpeg": {"hwaccel": "cuda"},
        "encoding": {"video": "copy", "audio": "copy"},
        "templates_dir": "assets/templates",
        "detection": {"threshold": 0.75, "temporal_window_seconds": 3.0, "min_observations": 1},
        "editorial": {"grouping_seconds": 10, "clip_before_seconds": 10, "clip_after_seconds": 10},
        "template_settings": {"disabled": []},
    }


def validate(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")
    crop = config.get("kill_feed_crop")
    resolution = config.get("source_resolution")
    if not isinstance(crop, dict) or not isinstance(resolution, dict):
        raise ValueError("Configuration is missing source_resolution or kill_feed_crop.")
    for key in ("x", "y", "width", "height"):
        if not isinstance(crop.get(key), int):
            raise ValueError(f"kill_feed_crop.{key} must be an integer.")
    if crop["x"] < 0 or crop["y"] < 0 or crop["width"] <= 0 or crop["height"] <= 0:
        raise ValueError("Kill-feed crop must have non-negative coordinates and positive dimensions.")
    for key in ("width", "height"):
        if not isinstance(resolution.get(key), int) or resolution[key] <= 0:
            raise ValueError(f"source_resolution.{key} must be a positive integer.")
    if crop["x"] + crop["width"] > resolution["width"] or crop["y"] + crop["height"] > resolution["height"]:
        raise ValueError("Kill-feed crop extends outside the calibrated source resolution.")
    merged = default_config()
    for section, value in config.items():
        if isinstance(value, dict) and isinstance(merged.get(section), dict):
            merged[section].update(value)
        else:
            merged[section] = value
    if merged["ffmpeg"]["hwaccel"].lower() not in VALID_ACCELERATION:
        raise ValueError("ffmpeg.hwaccel must be None, CUDA, VAAPI, or VideoToolbox.")
    try:
        numerator, denominator = (float(part) for part in merged["sampling"]["fps"].split("/", 1))
        if numerator <= 0 or denominator <= 0:
            raise ValueError
    except (AttributeError, ValueError):
        raise ValueError("sampling.fps must be a positive fraction such as '2/3'.") from None
    threshold = merged["detection"].get("threshold")
    if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise ValueError("detection.threshold must be between 0 and 1.")
    for section, field in (("detection", "temporal_window_seconds"), ("editorial", "grouping_seconds"), ("editorial", "clip_before_seconds"), ("editorial", "clip_after_seconds")):
        value = merged[section].get(field)
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{section}.{field} must be a non-negative number.")
    for field in ("height", "segment_seconds"):
        if type(merged["download"].get(field)) is not int or merged["download"][field] <= 0:
            raise ValueError(f"download.{field} must be a positive integer.")
    encoding = merged.get("encoding")
    if not isinstance(encoding, dict) or not all(isinstance(encoding.get(stream), str) and encoding[stream].strip() for stream in ("video", "audio")):
        raise ValueError("encoding.video and encoding.audio must be non-empty encoder names or 'copy'.")
    return merged


def load(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if path == CONFIG_PATH and not path.exists() and LEGACY_CONFIG_PATH.exists():
        path = LEGACY_CONFIG_PATH
    try:
        config = validate(json.loads(path.read_text(encoding="utf-8")))
        # Preserve configurations created before templates were moved.
        if config.get("templates_dir") == "KillFeed":
            config["templates_dir"] = "assets/templates"
        return config
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"Invalid configuration at {path}: {error}") from error


def save(config: dict[str, Any], path: Path = CONFIG_PATH, backup: bool = False) -> None:
    config = validate(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def active_path() -> Path:
    """Return the configuration location currently visible to the application."""
    if CONFIG_PATH.exists() or not LEGACY_CONFIG_PATH.exists():
        return CONFIG_PATH
    return LEGACY_CONFIG_PATH
