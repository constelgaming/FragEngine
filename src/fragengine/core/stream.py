"""Continuous YouTube ingest through a three-slot ring of independently decodable segments.

Slot X is the segment being analysed, Y the next one and Z the one after. While X is processed,
Y and Z download one after the other. When X finishes, Y shifts into X, Z into Y and a new
segment starts downloading into the freed Z slot, so at most three segments are in flight."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import av
from yt_dlp import YoutubeDL

# Without this, every FFmpeg child of a console-less (pythonw) GUI opens its own console window.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}


def is_youtube(value: str) -> bool:
    url = urlsplit(value)
    return url.scheme in {"http", "https"} and url.hostname in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
        "www.youtu.be", "music.youtube.com",
    }


def resolve(url, height):
    if not is_youtube(url):
        raise ValueError("Enter a YouTube video or live stream URL.")
    limit = f"[height<={height}]"
    options = {"format": f"bv{limit}[ext=mp4]+ba[ext=m4a]/b{limit}/bv{limit}+ba/bv{limit}",
               "noplaylist": True, "quiet": True, "socket_timeout": 30}
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
    if not info or info.get("_type") == "playlist":
        raise ValueError("Choose one YouTube video or live stream.")
    return info


@dataclass
class Segment:
    path: Path
    start: float
    end: float
    number: int = 0


def input_command(info, start, end):
    if info.get("is_live") and start:
        raise ValueError("For live capture, use start time zero. Times are relative to capture start.")
    formats = info.get("requested_formats") or [info]
    command = ["ffmpeg", "-nostdin", "-v", "error"]
    for fmt in formats:
        if start:
            command += ["-ss", str(start)]
        headers = fmt.get("http_headers") or info.get("http_headers", {})
        if headers:
            command += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        command += ["-i", fmt["url"]]
    command += ["-map", "0:v:0", "-map", f"{len(formats)-1}:a:0?"]
    if end is not None:
        command += ["-t", str(end-start)]
    codecs = ["-c:v", "libx264", "-c:a", "aac"] if start else ["-c", "copy"]
    return command + codecs + [ "-f", "matroska", "pipe:1"]


def split_packets(source, folder, seconds, start, monitor):
    """Cut at video keyframes and expose actual media times for stitching."""
    streams = [s for s in source.streams if s.type in {"video", "audio"}]
    video = next(s for s in streams if s.type == "video")
    output = None
    origin = boundary = last = None
    index = 0
    try:
        for packet in source.demux(streams):
            if packet.pts is None:
                continue
            stamp = float(packet.pts * packet.time_base)
            key = packet.stream == video and packet.is_keyframe
            if origin is None:
                if not key:
                    continue
                origin = boundary = last = stamp
            if key and output is not None and stamp-boundary >= seconds-0.0005:
                output.close()
                output = None
                monitor(f"Segment {index+1}", f"Ready | {stamp-boundary:.1f}s downloaded")
                yield Segment(path, start+boundary-origin, start+stamp-origin, index+1)
                index += 1
                boundary = stamp
            if output is None:
                path = folder / f"segment{index+1:05d}.mkv"
                monitor(f"Segment {index+1}", f"Downloading | 0.0s of {seconds:g}s · starts at {start+boundary-origin:.2f}s")
                output = av.open(str(path), "w", options={"avoid_negative_ts": "disabled"})
                byte_count = 0
                last_report = time.monotonic()
                targets = {s.index: output.add_stream_from_template(s, opaque=True) for s in streams}
            if packet.stream == video:
                last = max(last, stamp + float(packet.duration * packet.time_base or 1 / (video.average_rate or 30)))
            offset = round(boundary / float(packet.time_base))
            packet.pts -= offset
            if packet.dts is not None:
                packet.dts -= offset
            packet.stream = targets[packet.stream.index]
            byte_count += packet.size
            output.mux(packet)
            now = time.monotonic()
            if now-last_report >= 1:
                monitor(f"Segment {index+1}", f"Downloading | {last-boundary:.1f}s of {seconds:g}s · "
                        f"{byte_count/1048576:.2f} MiB")
                last_report = now
        if output is not None:
            output.close()
            output = None
            monitor(f"Segment {index+1}", f"Ready | final, {last-boundary:.1f}s downloaded")
            yield Segment(path, start+boundary-origin, start+last-origin, index+1)
    finally:
        if output is not None:
            output.close()


class PipeReader:
    """Expose a forward-only FFmpeg pipe to the demuxer on all platforms."""
    def __init__(self, pipe):
        self.read = pipe.read


def download_segments(info, folder, seconds, start, end, monitor=lambda *_: None):
    command = input_command(info, start, end)
    with tempfile.TemporaryFile() as errors, subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, **NO_WINDOW) as process:
        try:
            with av.open(PipeReader(process.stdout), format="matroska") as source, closing(split_packets(source, folder, seconds, start, monitor)) as segments:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    # Ring of three: the head (X) is handed to the caller for processing while the
                    # single worker downloads Y then Z. A new download is queued into Z only after
                    # the caller has finished X, so Y shifts to X and Z to Y.
                    futures = deque(worker.submit(next, segments, None) for _ in range(3))
                    try:
                        while futures:
                            item = futures.popleft().result()
                            if item is None:
                                break
                            yield item
                            futures.append(worker.submit(next, segments, None))
                    except BaseException:
                        if process.poll() is None:
                            process.kill()
                        raise
                    finally:
                        for future in futures:
                            future.cancel()
            if process.wait() not in (0,):
                errors.seek(0)
                raise RuntimeError("Stream download failed: " + errors.read()[-800:].decode(errors="replace"))
        finally:
            process.kill()
            process.stdout.close()

