"""Reusable FFmpeg/OpenCV backend for the desktop frontend."""
from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import closing
import math
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable

from . import paths, stream


Log = Callable[[str], None]
Progress = Callable[[str, float], None]



@dataclass(frozen=True)
class RunRequest:
    video: Path | str
    output: Path
    start: float
    end: float
    config: dict[str, Any]


def parse_time(value: str) -> float:
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("Time must be seconds, MM:SS, or HH:MM:SS.")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as error:
        raise ValueError("Time contains a non-numeric value.") from error
    if any(not math.isfinite(number) or number < 0 for number in numbers):
        raise ValueError("Time cannot be negative.")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def format_time(seconds: float) -> str:
    seconds = max(0, round(seconds))
    return f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}"


def ffprobe_duration(video: Path) -> float:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe was not found on PATH. Install FFmpeg and ensure it is available on PATH.")
    command = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(video)]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, **stream.NO_WINDOW)
        return float(result.stdout.strip())
    except FileNotFoundError as error:
        raise RuntimeError("ffprobe was not found on PATH. Install FFmpeg first.") from error
    except (subprocess.CalledProcessError, ValueError) as error:
        raise RuntimeError(f"FFmpeg could not read the selected video: {error}") from error


def ffprobe_dimensions(video: Path) -> tuple[int, int]:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe was not found on PATH. Install FFmpeg and ensure it is available on PATH.")
    command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, **stream.NO_WINDOW)
        width, height = result.stdout.strip().split(",", 1)
        return int(width), int(height)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as error:
        raise RuntimeError(f"Could not read video resolution: {error}") from error


def hwaccel_args(value: str) -> list[str]:
    value = value.lower()
    return [] if value == "none" else ["-hwaccel", value]


def available_encoders(kind: str) -> list[str]:
    """Return encoders exposed by this FFmpeg build for video or audio."""
    if kind not in {"video", "audio"}:
        raise ValueError("Encoder kind must be 'video' or 'audio'.")
    if shutil.which("ffmpeg") is None:
        return []
    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=True, **stream.NO_WINDOW)
    except subprocess.CalledProcessError:
        return []
    marker = "V" if kind == "video" else "A"
    encoders = []
    listing = result.stdout.split(" ------", 1)[-1]  # skip the flag legend above the table
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].startswith(marker) and fields[1] != "=":
            encoders.append(fields[1])
    return sorted(set(encoders))


def extraction_command(request: RunRequest, phase: float = 0.0) -> list[str]:
    cfg = request.config
    crop, size = cfg["kill_feed_crop"], cfg["source_resolution"]
    # Normalize to calibration coordinates, including lower resolution downloads.
    interval = sampling_interval(cfg["sampling"]["fps"])
    filters = (f"setpts=PTS-STARTPTS,trim=start={phase},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={interval},fps={cfg['sampling']['fps']}:start_time=0:round=up:eof_action=pass,"
               f"scale={size['width']}:{size['height']},"
               f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}:exact=1")
    duration = ["-t", str(request.end-request.start)] if math.isfinite(request.end) else []
    frames = ["-frames:v", str(math.ceil((request.end-request.start-phase)/interval-1e-9))] if duration else []
    return ["ffmpeg", "-nostdin", "-v", "error", *hwaccel_args(cfg["ffmpeg"]["hwaccel"]),
            "-ss", str(request.start), *duration, "-i", str(request.video),
            *frames, "-an", "-sn", "-vf", filters,
            "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]


def sampling_interval(fps: str) -> float:
    """Convert configured sampling FPS to seconds per extracted frame."""
    try:
        numerator, denominator = (float(part) for part in fps.split("/", 1))
        if numerator <= 0 or denominator <= 0:
            raise ValueError
        return denominator / numerator
    except ValueError as error:
        raise RuntimeError(f"Invalid sampling FPS {fps!r}; use a positive fraction such as 2/3.") from error


def load_templates(config: dict[str, Any]):
    try:
        import cv2
    except ModuleNotFoundError as error:
        raise RuntimeError("OpenCV is not installed. Run: python -m pip install -r requirements.txt") from error
    directory = paths.project_path(config["templates_dir"])
    if not directory.is_dir():
        raise RuntimeError(f"Templates directory does not exist: {directory}")
    disabled = set(config["template_settings"].get("disabled", []))
    templates = []
    for path in sorted(directory.glob("*.PNG")):
        if path.name in disabled:
            continue
        image = cv2.imread(str(path))
        if image is not None:
            templates.append((path.name, image))
    if not templates:
        raise RuntimeError(f"No enabled PNG templates could be loaded from {directory}.")
    return cv2, templates


def scan(request, templates, origin, grid_start, log, progress, monitor):
    import numpy as np
    cv2, images = templates
    crop = request.config["kill_feed_crop"]
    width, height = crop["width"], crop["height"]
    interval = sampling_interval(request.config["sampling"]["fps"])
    tick = math.ceil((origin-grid_start)/interval-1e-8)
    phase = max(0, grid_start + tick*interval-origin)
    if phase >= request.end-request.start:
        return []
    command = extraction_command(request, phase)
    while True:
        hits = []
        last_update = 0.0
        monitor("Frame extraction", f"{width}×{height} crop at ({crop['x']}, {crop['y']}) · "
                f"{request.config['sampling']['fps']} FPS · "
                f"{command[command.index('-hwaccel')+1] if '-hwaccel' in command else 'software'} decoding")

        with tempfile.TemporaryFile() as errors, subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, **stream.NO_WINDOW) as decoder:
            try:
                index = 0
                while data := decoder.stdout.read(width*height*3):
                    if len(data) != width*height*3:
                        break
                    image = np.frombuffer(data, np.uint8).reshape(height, width, 3)
                    scores = [(cv2.minMaxLoc(cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED))[1], name)
                              for name, template in images if template.shape[0] <= height and template.shape[1] <= width]
                    timestamp = grid_start + (tick+index)*interval
                    if timestamp >= origin+request.end-request.start-1e-8:
                        index += 1
                        continue
                    if scores:
                        score, name = max(scores)
                        if score >= request.config["detection"]["threshold"]:
                            hits.append(dict(timestamp=timestamp, confidence=score, template=name, frame=tick+index))
                    index += 1
                    now = time.monotonic()
                    matched = bool(scores and score >= request.config["detection"]["threshold"])
                    if matched or now-last_update >= .5:
                        best = f"{name} {score:.3f}" if scores else "no template fits the crop"
                        monitor("Frame matching", f"Sample {tick+index} at {timestamp:.2f}s · "
                                f"{len(scores)} templates compared · best: {best} · "
                                f"{'match' if matched else 'no match'}")
                        last_update = now
                    progress(timestamp, index, len(hits), score if scores else None,
                             name if scores else None, matched)
                if decoder.wait() == 0:
                    monitor("Scan complete", f"{index} frames examined · {len(hits)} raw matches")
                    return hits
                errors.seek(0)
                reason = errors.read()[-800:].decode(errors="replace")
            finally:
                decoder.kill()
                decoder.stdout.close()
        if "-hwaccel" not in command:
            raise RuntimeError("Frame extraction failed: " + reason)
        i = command.index("-hwaccel")
        del command[i:i+2]
        monitor("Decoder retry", f"Hardware attempt discarded. Retrying in software. {reason.strip()}")
        log("Retrying frame extraction with software decoding.")


def validate_events(raw: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Collapse repeated samples of one visible feed entry into a kill event."""
    if not raw:
        return []
    window = float(config["detection"]["temporal_window_seconds"])
    minimum = int(config["detection"]["min_observations"])
    clusters: list[list[dict[str, Any]]] = [[raw[0]]]
    for item in raw[1:]:
        if item["timestamp"] - clusters[-1][-1]["timestamp"] <= window:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    events = []
    for observations in clusters:
        if len(observations) >= minimum:
            best = max(observations, key=lambda item: item["confidence"]).copy()
            best["supporting_observations"] = len(observations)
            best["last_observation"] = observations[-1]["timestamp"]
            events.append(best)
    return events


def group_events(events: list[dict[str, Any]], config: dict[str, Any]) -> list[list[dict[str, Any]]]:
    if not events:
        return []
    threshold = float(config["editorial"]["grouping_seconds"])
    groups = [[events[0]]]
    for event in events[1:]:
        if event["timestamp"] - groups[-1][-1]["timestamp"] <= threshold:
            groups[-1].append(event)
        else:
            groups.append([event])
    return groups



def write_clip(request, group, number, start, end, media, offset, stem, concat=False, monitor=lambda *_: None, segments=None):
    folder = request.output / "clips"
    folder.mkdir(exist_ok=True)
    base = f"{stem}_clip{number}"
    suffix = 1
    while True:
        name = base if suffix == 1 else f"{base}_{suffix}"
        destination = folder / (name + ".mp4")
        sidecar = destination.with_suffix(".json")
        if not sidecar.exists():
            try:
                destination.touch(exist_ok=False)
                break
            except FileExistsError:
                pass
        suffix += 1
    encoding = request.config["encoding"]
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if concat:
        command += ["-f", "concat", "-safe", "0"]
    command += ["-ss", str(max(0, start-offset)), "-i", (media.name if concat else str(media)), "-t", str(end-start),
                "-map", "0:v:0", "-map", "0:a?", "-c:v", encoding["video"], "-c:a", encoding["audio"], str(destination.resolve())]
    try:
        monitor("Clip encoding", f"{destination.name} · {start:.2f}–{end:.2f}s · video {encoding['video']}, audio {encoding['audio']}")
        command[1:1] = ["-progress", "pipe:1", "-nostats", "-stats_period", "1"]
        with tempfile.TemporaryFile() as errors, subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=errors, text=True,
            cwd=media.parent if concat else None, **stream.NO_WINDOW,
        ) as encoder:
            try:
                for line in encoder.stdout:
                    key, _, value = line.strip().partition("=")
                    if key == "out_time_us" and value.lstrip("-").isdigit():
                        encoded = max(0, int(value)/1_000_000)
                        monitor("Clip encoding", f"{destination.name} · {encoded:.1f}s written of {end-start:.1f}s requested")
                if encoder.wait():
                    errors.seek(0)
                    raise RuntimeError("Clip encoding failed: " + errors.read()[-800:].decode(errors="replace"))
            finally:
                encoder.kill()
                encoder.stdout.close()
        record = dict(file=destination.name, source=str(request.video), start=start, end=end, events=group)
        if segments:
            record["segments"] = segments
        monitor("Clip log", f"Writing {sidecar.name} for {len(group)} validated events")
        sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except BaseException:
        destination.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise
    monitor("Clip saved", str(destination))
    return record


def run(request: RunRequest, log: Log, progress: Progress, monitor=lambda *_: None, metrics=lambda _: None) -> dict[str, Any]:
    started = time.monotonic()
    monitor("Prepare output", str(request.output))
    request.output.mkdir(parents=True, exist_ok=True)
    monitor("Load templates", str(paths.project_path(request.config["templates_dir"])))
    templates = load_templates(request.config)
    remote = isinstance(request.video, str) and stream.is_youtube(request.video)
    monitor("Templates loaded", ", ".join(name for name, _ in templates[1]))
    monitor("Resolve YouTube input" if remote else "Probe recording", str(request.video))
    info = stream.resolve(request.video, request.config["download"]["height"]) if remote else None
    if info:
        duration = None if info.get("is_live") else info.get("duration")
        metrics(dict(stream={key: info.get(key) for key in (
            "title", "channel", "uploader", "thumbnail", "is_live", "was_live", "duration",
            "webpage_url", "width", "height", "fps", "view_count", "upload_date")}))
    else:
        request = replace(request, video=Path(request.video))
        duration = ffprobe_duration(request.video)
    end = min(request.end, duration) if duration else request.end
    if end <= request.start:
        raise ValueError("The selected range is outside the recording.")
    stop_label = f"{end:.2f}s" if math.isfinite(end) else "live end"
    monitor("Input ready", f"Range {request.start:.2f}s to {stop_label} · threshold {request.config['detection']['threshold']}")
    request = replace(request, end=end)
    metrics(dict(start=request.start, end=end, position=request.start, threshold=request.config["detection"]["threshold"]))
    scanned = segment_samples = 0
    last_update = 0.0
    def report(stamp, index, hits, score, template=None, matched=False):
        nonlocal segment_samples, last_update
        segment_samples = index
        if matched:
            metrics(dict(hit=dict(timestamp=stamp, confidence=score, template=template)))
        now = time.monotonic()
        if now-last_update < .2 and not matched:
            return
        last_update = now
        metrics(dict(position=stamp, samples=scanned+index, hits=total+hits, score=score, template=template))
        stage = "Extracting and matching" if math.isfinite(end) else f"Live capture {format_time(stamp)}"
        progress(stage, min(.95, .95*(stamp-request.start)/(end-request.start)))
    editorial = request.config["editorial"]
    before, after, gap = (editorial[k] for k in ("clip_before_seconds", "clip_after_seconds", "grouping_seconds"))
    window = request.config["detection"]["temporal_window_seconds"]
    pending, records, events = [], [], []
    total = 0
    with tempfile.TemporaryDirectory(prefix="fragengine_") as directory:
        folder = Path(directory)
        retained = []
        # Temporary buffer: processed segments kept on disk because a clip may still need them.
        # "edge" = a kill near the end of the segment whose clip continues into the next one;
        # "lead-in" = the tail may become the pre-roll of a kill early in the next segment.
        awaiting = []
        def buffer_reason(segment):
            for clip in awaiting:
                if clip["start"] < segment.end and clip["end"] > segment.start:
                    return "edge", clip
            return "lead-in", None
        def report_buffer():
            entries = []
            for segment in retained:
                kind, clip = buffer_reason(segment)
                try:
                    size = segment.path.stat().st_size
                except OSError:
                    size = 0
                entries.append(dict(number=segment.number, start=segment.start, end=segment.end, size=size,
                                    reason=kind, clip=clip))
            metrics(dict(buffer=dict(segments=entries, awaiting=list(awaiting))))
        def flush(now, final=False):
            nonlocal pending
            awaiting.clear()
            # A cluster touching the processing frontier can still gain support
            # or a new highest-confidence timestamp in the next segment.
            closed = len(pending)
            if not final and pending and now-pending[-1]["timestamp"] <= window:
                closed -= 1
                while closed and pending[closed]["timestamp"]-pending[closed-1]["timestamp"] <= window:
                    closed -= 1
            frontier = pending[closed]["timestamp"] if closed < len(pending) else now
            monitor("Temporal validation", f"{closed} observations ready, {len(pending)-closed} open · "
                    f"{window:g}s window · minimum {request.config['detection']['min_observations']}")
            validated = validate_events(pending[:closed], request.config)
            groups = group_events(validated, request.config)
            monitor("Group events", f"{len(validated)} validated events → {len(groups)} groups · gap {gap:g}s")
            for position, group in enumerate(groups):
                first, last = group[0]["timestamp"], group[-1]["timestamp"]
                # A newer, still-open observation cluster can change its best timestamp.
                cutoff = group[-1]["last_observation"]
                if not final and (frontier <= last+gap or now < last+after):
                    monitor("Await clip boundary", f"Clip {max(request.start, first-before):.2f}–{last+after:.2f}s "
                            f"crosses the processed edge at {now:.2f}s · waiting for the next segment")
                    for waiting in groups[position:]:
                        awaiting.append(dict(start=max(request.start, waiting[0]["timestamp"]-before),
                                             end=waiting[-1]["timestamp"]+after, events=len(waiting), confirmed=True))
                    break
                start, stop = max(request.start, first-before), min(now, last+after)
                if stop > start:
                    media, offset, stem = request.video, 0, Path(str(request.video)).stem
                    if remote:
                        pieces = [s for s in retained if s.start < stop and s.end > start]
                        numbers = "+".join(str(s.number) for s in pieces)
                        monitor("Join segments", f"Cross-cut from segments {numbers} · {start:.2f}–{stop:.2f}s"
                                if len(pieces) > 1 else f"Cut from segment {numbers} · {start:.2f}–{stop:.2f}s")
                        media = folder / "clip.ffconcat"
                        media.write_text("ffconcat version 1.0\n" + "".join(
                            f"file '{s.path.name}'\nduration {s.end-s.start:.9f}\n" for s in pieces), encoding="utf-8")
                        offset, stem = pieces[0].start, "youtube"
                    record = write_clip(request, group, len(records)+1, start, stop, media, offset, stem, remote, monitor,
                                        [s.number for s in pieces] if remote else None)
                    records.append(record)
                    metrics(dict(clip=record))
                    log("Created " + record["file"])
                events.extend(group)
                metrics(dict(events=len(events)))
                pending =[hit for hit in pending if hit["timestamp"] > cutoff]
            if not final and pending:
                # Drop isolated observations that failed temporal validation.
                while pending and now-pending[0]["timestamp"] > window:
                    split = 1
                    while split < len(pending) and pending[split]["timestamp"]-pending[split-1]["timestamp"] <= window:
                        split += 1
                    if split >= request.config["detection"]["min_observations"] or now-pending[split-1]["timestamp"] <= window:
                        break
                    del pending[:split]
            if not final and pending:
                # Observations not yet validated or grouped (an open kill at the edge) also hold media.
                covered = awaiting[-1]["end"] - after if awaiting else float("-inf")
                open_hits = [hit for hit in pending if hit["timestamp"] > covered]
                if open_hits:
                    awaiting.append(dict(start=max(request.start, open_hits[0]["timestamp"]-before),
                                         end=open_hits[-1]["timestamp"]+after, events=None, confirmed=False))
            keep_from = (pending[0]["timestamp"] if pending else now)-before
            while retained and retained[0].end <= keep_from:
                expired = retained.pop(0)
                expired.path.unlink()
                monitor(f"Segment {expired.number}", f"Deleted | no longer needed ({expired.start:.1f}–{expired.end:.1f}s)")
            if remote:
                report_buffer()

        if remote:
            download = request.config["download"]
            limit = end if math.isfinite(end) else None
            now = request.start
            monitor("Open download", f"Target {download['segment_seconds']}s segments · up to {download['height']}p")
            with closing(stream.download_segments(info, folder, download["segment_seconds"], request.start, limit, monitor)) as segments:
                for index, segment in enumerate(segments):
                    now = min(end, segment.end)
                    monitor(f"Segment {segment.number}", f"Processing | {segment.start:.2f}–{now:.2f}s")
                    log(f"Processing segment {segment.number} at {format_time(segment.start)}")
                    # Segments are deleted by flush() once no clip can still need them.
                    retained.append(segment)
                    part = replace(request, video=segment.path, start=0, end=now-segment.start)
                    segment_samples = 0
                    hits = scan(part, templates, segment.start, request.start, log, report, monitor)
                    scanned += segment_samples
                    total += len(hits)
                    metrics(dict(position=now, samples=scanned, hits=total, segments=index+1))
                    pending.extend(hits)
                    flush(now)
                    if any(item is segment for item in retained):
                        kind, clip = buffer_reason(segment)
                        if kind == "edge":
                            monitor(f"Segment {segment.number}", f"Held | edge clip {clip['start']:.1f}–{clip['end']:.1f}s "
                                    f"continues into segment {segment.number+1}")
                        else:
                            monitor(f"Segment {segment.number}", f"Held | last {before:g}s kept as lead-in for segment {segment.number+1}")
            if now == request.start:
                raise RuntimeError("The stream supplied no video in the requested range.")
            flush(now, final=True)
            for item in retained:  # removed with the temporary folder below
                monitor(f"Segment {item.number}", "Deleted | run finished")
            metrics(dict(buffer=dict(segments=[], awaiting=[])))
        else:
            pending = scan(request, templates, request.start, request.start, log, report, monitor)
            total = len(pending)
            metrics(dict(position=end, samples=segment_samples, hits=total))
            flush(end, final=True)
    summary = dict(raw_detections=total, validated_kills=len(events), groups=len(group_events(events, request.config)),
                   clips=len(records), clip_logs=[str(Path("clips") / Path(r["file"]).with_suffix(".json")) for r in records], output=str(request.output),
                   elapsed=time.monotonic()-started, events=events)
    monitor("Run log", f"Writing {request.output / 'detection_log.json'}")
    (request.output / "detection_log.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    progress("Completed", 1)
    return summary


