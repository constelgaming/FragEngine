"""Scan extracted screenshots for the player's name in the Valorant kill feed.

This intentionally keeps the original RGB template-matching and full-frame mask
behaviour. It only makes the existing stage deterministic and safe to rerun.
"""

from pathlib import Path
import re

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "assets" / "templates"
MATCH_THRESHOLD = 0.75
SAMPLE_INTERVAL_SECONDS = 1.50
FRAME_NAME = re.compile(r"^frame_(\d{6})\.png$", re.IGNORECASE)


def load_templates() -> list[tuple[str, np.ndarray]]:
    """Load every *.PNG template once, failing clearly if none can be read."""
    templates = []
    for path in sorted(TEMPLATE_DIR.glob("*.PNG")):
        image = cv2.imread(str(path))
        if image is None:
            raise FileNotFoundError(f"Could not load template: {path}")
        templates.append((path.name, image))
    if not templates:
        raise FileNotFoundError(f"No *.PNG templates found in {TEMPLATE_DIR}")
    return templates


def main() -> None:
    main_folder = Path(input("Enter main folder: ").strip())
    try:
        start_time = int(input("Enter start time from ffmpeg (in sec): ").strip())
    except ValueError as error:
        raise SystemExit("Start time must be an integer number of seconds.") from error

    folder_path = main_folder / "screenshots"
    output_file = main_folder / f"{main_folder.name}_killtime.txt"
    if not main_folder.is_dir():
        raise SystemExit(f"Main folder does not exist: {main_folder}")
    if not folder_path.is_dir():
        raise SystemExit(f"Screenshots folder does not exist: {folder_path}")
    try:
        kill_feed_images = load_templates()
    except FileNotFoundError as error:
        raise SystemExit(str(error)) from error

    detections: list[int] = []
    screenshot_paths = sorted(path for path in folder_path.iterdir() if path.is_file())
    if not screenshot_paths:
        print(f"No screenshots found in {folder_path}; wrote an empty {output_file.name}.")
    for full_image_path in screenshot_paths:
        match = FRAME_NAME.match(full_image_path.name)
        if not match:
            print(f"Skipping malformed screenshot filename: {full_image_path.name}")
            continue
        full_image = cv2.imread(str(full_image_path))
        if full_image is None:
            print(f"Error: Could not open {full_image_path.name}")
            continue

        # Preserve the original full-size mask and coordinates in this
        # correctness-only pass; ROI cropping is a later, benchmarked change.
        mask = np.zeros_like(full_image)
        roi_mask = cv2.rectangle(mask, (80, 40), (365, 235), (255, 255, 255), -1)
        masked_image = cv2.bitwise_and(full_image, roi_mask)
        for template_name, kill_image in kill_feed_images:
            if (kill_image.shape[0] > masked_image.shape[0] or
                    kill_image.shape[1] > masked_image.shape[1]):
                print(f"Skipping {template_name}: template is larger than {full_image_path.name}")
                continue
            result = cv2.matchTemplate(masked_image, kill_image, cv2.TM_CCOEFF_NORMED)
            max_value = cv2.minMaxLoc(result)[1]
            if max_value >= MATCH_THRESHOLD:
                frame_number = int(match.group(1))
                detections.append(start_time + int(frame_number * SAMPLE_INTERVAL_SECONDS))
                print(f"Kill found: {full_image_path.name} ({template_name}, {max_value:.3f})")
                break

    # Overwrite results so a rerun cannot mix old detections with new ones.
    with output_file.open("w", encoding="utf-8") as file:
        file.writelines(f"{timestamp}\n" for timestamp in detections)
    print(f"Wrote {len(detections)} detections to {output_file}")


if __name__ == "__main__":
    main()
