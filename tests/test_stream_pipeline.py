"""Exercise real FFmpeg ingest with a deterministic YouTube media resolver."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from fragengine.core import config, pipeline, stream


class StreamingPipelineTest(unittest.TestCase):
    def test_local_and_segmented_clips_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tpl = np.random.default_rng(123).integers(0, 256, (18, 30, 1), dtype=np.uint8).repeat(3, axis=2)
            cv2.imwrite(str(root / "player.PNG"), tpl)
            video = root / "recording.mp4"
            command = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pixel_format", "bgr24",
                       "-video_size", "160x90", "-framerate", "4", "-i", "pipe:0",
                       "-f", "lavfi", "-i", "sine=frequency=440", "-shortest", "-c:a", "aac",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "28", "-sc_threshold", "0", str(video)]
            frames = []
            for i in range(4*48):
                image = np.zeros((90, 160, 3), dtype=np.uint8)
                if any(t <= i/4 < t+2 for t in (6, 15, 34)):
                    image[10:28, 110:140] = tpl
                frames.append(image.tobytes())
            subprocess.run(command, input=b"".join(frames), check=True)
            cfg = config.default_config()
            cfg.update(source_resolution={"width": 160, "height": 90},
                       kill_feed_crop={"x": 100, "y": 4, "width": 56, "height": 30},
                       templates_dir=str(root))
            cfg["ffmpeg"]["hwaccel"] = "none"
            cfg["detection"]["threshold"] = .7
            cfg["download"]["segment_seconds"] = 5
            cfg["encoding"]["video"] = "libx264"
            results = []
            for mode, source in (("local", video), ("url", "https://youtu.be/test")):
                out = root / mode
                req = pipeline.RunRequest(source, out, 0, 48, cfg)
                with patch.object(stream, "resolve", return_value={"url": str(video), "duration": 48}):
                    activity = []
                    result = pipeline.run(req, lambda *_: None, lambda *_: None,
                                          lambda stage, detail: activity.append((stage, detail)))
                stages = [stage for stage, detail in activity]
                for stage in ("Frame extraction", "Frame matching", "Temporal validation", "Group events",
                              "Clip encoding", "Clip log", "Clip saved", "Run log"):
                    self.assertIn(stage, stages)
                self.assertEqual(stages.count("Clip saved"), 2)
                self.assertEqual("Segment 1" in stages, mode == "url")
                if mode == "url":
                    lifecycle = [detail.split(" | ")[0] for stage, detail in activity if stage == "Segment 1"]
                    for state in ("Downloading", "Ready", "Processing", "Deleted"):
                        self.assertIn(state, lifecycle)
                    # Never more than three segments in flight (X processing, Y and Z downloading/ready).
                    live, peak = set(), 0
                    for stage, detail in activity:
                        if stage.startswith("Segment "):
                            state = detail.split(" | ")[0]
                            (live.add if state in {"Downloading", "Ready", "Processing"} else live.discard)(stage)
                            peak = max(peak, len(live))
                    self.assertLessEqual(peak, 3)
                self.assertEqual(result["clips"], 2, result)
                logs = [json.loads((out / name).read_text()) for name in result["clip_logs"]]
                self.assertEqual([len(item["events"]) for item in logs], [2, 1])
                for item in logs:
                    path = out / "clips" / item["file"]
                    self.assertAlmostEqual(pipeline.ffprobe_duration(path), item["end"]-item["start"], delta=.6)
                    result = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)], text=True)
                    self.assertIn("audio", result)
                results.append([(item["start"], item["end"]) for item in logs])
            self.assertEqual(*results)

    def test_kill_across_segment_boundary_is_cut_from_both_segments(self):
        """A kill at the very end of segment X continuing into Y yields one correct cross-cut clip."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tpl = np.random.default_rng(321).integers(0, 256, (18, 30, 1), dtype=np.uint8).repeat(3, axis=2)
            cv2.imwrite(str(root / "player.PNG"), tpl)
            video = root / "recording.mp4"
            # Keyframe every 7 s; 20 s target segments therefore cut at 21 s.
            command = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pixel_format", "bgr24",
                       "-video_size", "160x90", "-framerate", "4", "-i", "pipe:0",
                       "-f", "lavfi", "-i", "sine=frequency=440", "-shortest", "-c:a", "aac",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "28", "-sc_threshold", "0", str(video)]
            frames = []
            for i in range(4*70):
                image = np.zeros((90, 160, 3), dtype=np.uint8)
                if any(t <= i/4 < t+2.5 for t in (19.5, 25)):  # first kill straddles the 21 s cut
                    image[10:28, 110:140] = tpl
                frames.append(image.tobytes())
            subprocess.run(command, input=b"".join(frames), check=True)
            cfg = config.default_config()
            cfg.update(source_resolution={"width": 160, "height": 90},
                       kill_feed_crop={"x": 100, "y": 4, "width": 56, "height": 30}, templates_dir=str(root))
            cfg["ffmpeg"]["hwaccel"] = "none"
            cfg["detection"]["threshold"] = .7
            cfg["sampling"]["fps"] = "2/1"
            cfg["download"]["segment_seconds"] = 20
            cfg["encoding"]["video"] = "libx264"
            clips = {}
            for mode, source in (("local", video), ("url", "https://youtu.be/edge")):
                out = root / mode
                activity, values = [], []
                with patch.object(stream, "resolve", return_value={"url": str(video), "duration": 70}):
                    result = pipeline.run(pipeline.RunRequest(source, out, 0, 70, cfg), lambda *_: None, lambda *_: None,
                                          lambda stage, detail: activity.append((stage, detail)), values.append)
                logs = [json.loads((out / name).read_text()) for name in result["clip_logs"]]
                self.assertEqual(len(logs), 1, logs)
                clip = logs[0]
                path = out / "clips" / clip["file"]
                self.assertAlmostEqual(pipeline.ffprobe_duration(path), clip["end"] - clip["start"], delta=.6)
                clips[mode] = (clip["start"], clip["end"])
                if mode == "url":
                    self.assertEqual(clip["segments"], [1, 2])  # joined from X and Y
                    held = [detail for stage, detail in activity if stage == "Segment 1" and detail.startswith("Held")]
                    self.assertTrue(held and "edge clip" in held[0], held)
                    buffers = [v["buffer"] for v in values if "buffer" in v]
                    self.assertTrue(any(entry["reason"] == "edge" for b in buffers for entry in b["segments"]))
                    self.assertEqual(buffers[-1]["segments"], [])  # buffer emptied once the clip is written
                    self.assertIn("Cross-cut from segments 1+2", " ".join(d for s, d in activity if s == "Join segments"))
            self.assertEqual(clips["local"], clips["url"])
            self.assertLess(clips["url"][0], 21)  # starts inside segment 1 …
            self.assertGreater(clips["url"][1], 21)  # … and ends inside segment 2


if __name__ == "__main__":
    unittest.main()


