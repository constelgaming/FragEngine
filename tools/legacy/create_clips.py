"""Create stream-copy clips from the editorial groups."""

import ast
from pathlib import Path

import ffmpeg

CLIP_BEFORE_SECONDS = 5
CLIP_AFTER_SECONDS = 5


def video_duration(path: Path) -> float | None:
    try:
        duration = ffmpeg.probe(str(path))["format"].get("duration")
        return float(duration) if duration is not None else None
    except ffmpeg.Error as error:
        print(f"Warning: could not determine video duration ({error}); not capping end times.")
        return None


def parse_group(line: str, line_number: int) -> list[float] | None:
    try:
        value = ast.literal_eval(line)
    except (SyntaxError, ValueError):
        print(f"Skipping malformed group on line {line_number}: {line!r}")
        return None
    if not isinstance(value, list) or not value:
        print(f"Skipping empty or invalid group on line {line_number}: {line!r}")
        return None
    if not all(isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) for timestamp in value):
        print(f"Skipping non-numeric group on line {line_number}: {line!r}")
        return None
    return sorted(float(timestamp) for timestamp in value)


def main() -> None:
    main_folder = Path(input("Enter main folder: ").strip())
    input_file = main_folder / f"{main_folder.name}_grouping.txt"
    input_video = main_folder / f"{main_folder.name}.mkv"
    clips_dir = main_folder / "clips"
    if not input_file.is_file():
        raise SystemExit(f"Grouping file does not exist: {input_file}")
    if not input_video.is_file():
        raise SystemExit(f"Input video does not exist: {input_video}")
    clips_dir.mkdir(parents=True, exist_ok=True)
    # These filenames are generated exclusively by this stage. Removing them
    # avoids leaving old clips behind when a later run produces fewer groups.
    stale_clips = list(clips_dir.glob(f"{main_folder.name}_clip*.mp4"))
    for stale_clip in stale_clips:
        stale_clip.unlink()
    if stale_clips:
        print(f"Removed {len(stale_clips)} stale generated clip(s).")
    duration = video_duration(input_video)
    count = 1
    for line_number, raw_line in enumerate(input_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        killshot_time = parse_group(raw_line.strip(), line_number)
        if killshot_time is None:
            continue
        start_time = max(0.0, killshot_time[0] - CLIP_BEFORE_SECONDS)
        end_time = killshot_time[-1] + CLIP_AFTER_SECONDS
        if duration is not None:
            end_time = min(end_time, duration)
        if end_time <= start_time:
            print(f"Skipping group on line {line_number}: clip range is empty after bounds checks.")
            continue
        output_clip = clips_dir / f"{main_folder.name}_clip{count}.mp4"
        ffmpeg.input(str(input_video), ss=start_time, to=end_time).output(
            str(output_clip), codec="copy"
        ).run(overwrite_output=True)
        print(f"Processed clip {count}: Start time {start_time:g}, End time {end_time:g}")
        count += 1


if __name__ == "__main__":
    main()
