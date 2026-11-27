"""Live run dashboard: progress, throughput, detections, pipeline stages and activity."""
from __future__ import annotations

from collections import deque
from datetime import datetime
import html
import math
import re
from pathlib import Path
import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme as t
from .widgets import (BufferView, Card, ConfidenceChart, KeyValueList, PipelineStrip, ProgressBar, SlotView, StatTile,
                      StatusPill, StreamCard, TemplateBars, Timeline, Toggle, fmt_count, fmt_time)

STAGES = ("Input", "Download", "Extract", "Match", "Validate", "Group", "Encode", "Save")
EVENT_STAGE = {
    "Prepare output": "Input", "Load templates": "Input", "Templates loaded": "Input",
    "Probe recording": "Input", "Resolve YouTube input": "Input", "Input ready": "Input",
    "Open download": "Download", "Frame extraction": "Extract", "Decoder retry": "Extract",
    "Frame matching": "Match", "Scan complete": "Match", "Temporal validation": "Validate",
    "Group events": "Group", "Await clip boundary": "Group", "Join segments": "Encode",
    "Clip encoding": "Encode", "Clip log": "Save", "Clip saved": "Save", "Run log": "Save",
}
LOG_COLORS = {"Input": t.TEXT_2, "Download": t.AMBER, "Extract": t.CYAN, "Match": t.ACCENT, "Validate": t.VIOLET,
              "Group": t.VIOLET, "Encode": t.GREEN, "Save": t.GREEN, "Completed": t.GREEN, "Failed": t.RED,
              "Stopped": t.AMBER, "Decoder retry": t.AMBER}


class RunMonitor(QScrollArea):
    def __init__(self, open_folder: Callable[[], None], open_file: Callable[[Path], None], parent=None):
        super().__init__(parent)
        self.open_folder_cb, self.open_file_cb = open_folder, open_file
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root = QWidget()
        self.setWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 8, 8)
        layout.setSpacing(14)

        # Hero: status, source and overall progress.
        hero = Card(padding=20)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.pill = StatusPill()
        top.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(2)
        self.source_label = QLabel("No run yet", objectName="h2")
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source_sub = QLabel("Start a run from Process to see live telemetry here.", objectName="faint")
        names.addWidget(self.source_label)
        names.addWidget(self.source_sub)
        top.addLayout(names, 1)
        self.percent = QLabel("—")
        self.percent.setFont(t.font(22, t.QFont.Weight.DemiBold))
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.percent)
        hero.body().addLayout(top)
        self.progress = ProgressBar()
        hero.body().addWidget(self.progress)
        facts = QHBoxLayout()
        facts.setSpacing(22)
        self.facts: dict[str, QLabel] = {}
        for key in ("Scanned", "ETA", "Speed", "Elapsed"):
            column = QVBoxLayout()
            column.setSpacing(1)
            column.addWidget(QLabel(key.upper(), objectName="eyebrow"))
            value = QLabel("—", objectName="mono")
            value.setStyleSheet(f"color:{t.TEXT}; font-size:10pt;")
            column.addWidget(value)
            self.facts[key] = value
            facts.addLayout(column)
        facts.addStretch(1)
        self.open_button = QPushButton("Open clips folder")
        self.open_button.clicked.connect(self.open_folder_cb)
        facts.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignBottom)
        hero.body().addLayout(facts)
        layout.addWidget(hero)
        self.stream_card = StreamCard()
        layout.addWidget(self.stream_card)

        # KPI tiles.
        tiles = QGridLayout()
        tiles.setHorizontalSpacing(12)
        self.t_samples = StatTile("Samples scanned", spark=False)
        self.t_hits = StatTile("Raw hits", t.VIOLET)
        self.t_events = StatTile("Kill events", t.TEXT)
        self.t_clips = StatTile("Clips saved", t.AMBER)
        self.t_rate = StatTile("Throughput", t.ACCENT, lambda v: f"{v:,.1f}/s", spark=True)
        self.t_speed = StatTile("Speed", t.GREEN, lambda v: f"{v:,.1f}×", spark=True)
        for i, tile in enumerate((self.t_samples, self.t_hits, self.t_events, self.t_clips, self.t_rate, self.t_speed)):
            tiles.addWidget(tile, 0, i)
            tiles.setColumnStretch(i, 1)
        layout.addLayout(tiles)

        # Pipeline stages.
        pipeline_card = Card("Pipeline", "Stage status and time spent in each stage")
        self.stage_now = QLabel("", objectName="faint")
        pipeline_card.add_header_widget(self.stage_now)
        self.strip = PipelineStrip(STAGES)
        pipeline_card.body().addWidget(self.strip)
        layout.addWidget(pipeline_card)

        # Confidence chart and template leaderboard.
        row = QHBoxLayout()
        row.setSpacing(14)
        conf = Card("Match confidence", "Best template score for each sampled frame")
        self.conf_stats = QLabel("", objectName="mono")
        conf.add_header_widget(self.conf_stats)
        self.chart = ConfidenceChart()
        conf.body().addWidget(self.chart, 1)
        row.addWidget(conf, 3)
        tpl = Card("Templates", "Hits and best score per template")
        self.bars = TemplateBars()
        tpl.body().addWidget(self.bars, 1)
        row.addWidget(tpl, 2)
        layout.addLayout(row)

        # Timeline.
        timeline = Card("Media timeline", "Hover to inspect a point in the recording")
        legend = QLabel(f"<span style='color:{t.ACCENT}'>●</span> scanned &nbsp; "
                        f"<span style='color:{t.VIOLET}'>●</span> hits &nbsp; "
                        f"<span style='color:{t.AMBER}'>●</span> clips", objectName="faint")
        timeline.add_header_widget(legend)
        self.timeline = Timeline()
        timeline.body().addWidget(self.timeline)
        layout.addWidget(timeline)

        # Clips table + run details.
        row = QHBoxLayout()
        row.setSpacing(14)
        clips = Card("Saved clips", "Double-click a clip to play it")
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "FILE", "RANGE", "LENGTH", "EVENTS", "PEAK", "SEGMENTS"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setMinimumHeight(200)
        self.table.cellDoubleClicked.connect(self._open_clip)
        self.clips_empty = QLabel("Clips appear here as soon as they are written.", objectName="faint")
        self.clips_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clips.body().addWidget(self.clips_empty)
        clips.body().addWidget(self.table, 1)
        row.addWidget(clips, 3)
        details = Card("Run details")
        self.info = KeyValueList()
        details.body().addWidget(self.info)
        self.slot_title = QLabel("DOWNLOAD RING", objectName="eyebrow")
        details.body().addWidget(self.slot_title)
        self.slot_view = SlotView()
        details.body().addWidget(self.slot_view)
        self.buffer_title = QLabel("TEMP BUFFER", objectName="eyebrow")
        self.buffer_title.setToolTip("Processed segments kept on disk because a clip may still need them.\n"
                                     "edge = a kill near the end whose clip continues into the next segment\n"
                                     "lead-in = the tail may become the pre-roll of a kill early in the next segment")
        details.body().addWidget(self.buffer_title)
        self.buffer_view = BufferView()
        details.body().addWidget(self.buffer_view)
        details.body().addStretch(1)
        row.addWidget(details, 2)
        layout.addLayout(row)

        # Activity log.
        activity = Card("Activity")
        self.filters = QButtonGroup(self)
        self.filters.setExclusive(True)
        for i, name in enumerate(("All", "Detections", "Clips", "Issues")):
            chip = QPushButton(name, objectName="chip")
            chip.setCheckable(True)
            chip.setChecked(i == 0)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            self.filters.addButton(chip, i)
            activity.add_header_widget(chip)
        self.filters.idClicked.connect(lambda _: self._render_log())
        spacer = QWidget()
        spacer.setFixedWidth(12)
        activity.add_header_widget(spacer)
        activity.add_header_widget(QLabel("Follow", objectName="muted"))
        self.follow = Toggle(True)
        activity.add_header_widget(self.follow)
        self.log = QPlainTextEdit(objectName="log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2500)
        self.log.setMinimumHeight(240)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        activity.body().addWidget(self.log)
        layout.addWidget(activity)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.output: Path | None = None
        self.reset(None, False)

    # ------------------------------------------------------------------ lifecycle

    def reset(self, source, remote: bool, output: Path | None = None, settings: dict | None = None,
              queue_label: str | None = None) -> None:
        self.source, self.remote, self.output = source, remote, output
        self.running = source is not None
        self.started = time.monotonic()
        self.finished_at: float | None = None
        self.states = dict.fromkeys(STAGES, "Waiting")
        self.stage_start: dict[str, float] = {}
        self.stage_spent: dict[str, float] = {}
        if source is not None and not remote:
            self.states["Download"] = "Bypassed"
        self.segments: dict[int, dict] = {}
        self.m = dict(start=0.0, end=None, position=0.0, samples=0, hits=0, segments=0, events=0, threshold=None)
        self.score_count = 0
        self.score_sum = 0.0
        self.score_peak = 0.0
        self.last_score: float | None = None
        self.templates: dict[str, list] = {}
        self.records: list[dict] = []
        self.entries: deque[tuple[float, str, str]] = deque(maxlen=4000)
        self.rate_mark = (time.monotonic(), 0, 0.0)
        self.decoder = "—"
        self.template_count = "—"
        self.current = None

        self.pill.set_state("Running" if self.running else "Idle", "Starting" if self.running else "Idle")
        if source is None:
            self.source_label.setText("No run yet")
            self.source_sub.setText("Start a run from Process to see live telemetry here.")
        else:
            name = str(source) if remote else Path(source).name
            self.source_label.setText(self.source_label.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, 640))
            self.source_label.setToolTip(str(source))
            self.source_sub.setText((f"{queue_label}  ·  " if queue_label else "") + ("YouTube stream" if remote else "Local recording")
                                    + (f"  ·  output {output}" if output else ""))
        self.percent.setText("0%" if self.running else "—")
        self.progress.set_value(0)
        self.progress.set_running(self.running)
        for tile in (self.t_samples, self.t_hits, self.t_events, self.t_clips, self.t_rate, self.t_speed):
            tile.reset()
        self.chart.reset(settings["detection"]["threshold"] if settings else None)
        self.bars.set_rows([])
        self.timeline.reset()
        self.timeline.set_animating(self.running)
        self.table.setRowCount(0)
        self._clips_visibility()
        self.log.clear()
        self.follow.setChecked(True)
        self.open_button.setEnabled(False)
        self.stream_info: dict | None = None
        self.buffer_title.setVisible(bool(remote and source))
        self.buffer_view.setVisible(bool(remote and source))
        self.buffer_view.clear()
        self.stream_card.setVisible(bool(remote and source))
        if remote and source:
            self.stream_card.resolving(str(source))
        self.slot_title.setVisible(remote or source is None)
        self.slot_view.setVisible(remote or source is None)
        self.slot_view.head = None
        self._render_ring()
        for key in list(self.info.rows):
            self.info.set(key, "—")
        if settings:
            crop = settings["kill_feed_crop"]
            self.info.set("Source", str(source))
            self.info.set("Output", str(output) if output else "—")
            self.info.set("Sampling", f"{settings['sampling']['fps']} fps")
            self.info.set("Threshold", f"{settings['detection']['threshold']:.2f}")
            self.info.set("Crop", f"{crop['width']}×{crop['height']} @ {crop['x']},{crop['y']}")
            self.info.set("Decoder", settings["ffmpeg"]["hwaccel"])
            self.info.set("Templates", "loading…")
            self.info.set("Encoding", f"v:{settings['encoding']['video']}  a:{settings['encoding']['audio']}")
        self._render_strip()
        self._render_facts()
        self.conf_stats.setText("")
        self.stage_now.setText("")
        if self.running:
            self.timer.start()
        else:
            self.timer.stop()

    def finish(self, state: str, detail: str = "") -> None:
        """Mark the run as Completed, Failed or Stopped."""
        self.running = False
        self.finished_at = time.monotonic()
        if self.remote and self.stream_info is None and state == "Failed":
            self.stream_card.failed(detail)
        now = time.monotonic()
        for key, value in self.states.items():
            if value == "Running":
                self.states[key] = {"Completed": "Done", "Failed": "Failed", "Stopped": "Stopped"}[state]
                self.stage_spent[key] = self.stage_spent.get(key, 0) + now - self.stage_start.pop(key, now)
            elif state == "Completed" and value == "Waiting":
                self.states[key] = "Skipped"
        for segment in self.segments.values():
            if segment.get("state") in {"Downloading", "Ready", "Processing", "Held"}:
                segment["state"] = "Deleted"
        self.buffer_view.clear(finished=True)
        self._render_ring()
        self.pill.set_state(state)
        self.progress.set_running(False)
        if state == "Completed":
            self.progress.set_value(1)
            self.percent.setText("100%")
        self.progress.color = {"Completed": t.GREEN, "Failed": t.RED, "Stopped": t.AMBER}[state]
        self.progress.update()
        self.timeline.set_animating(False)
        self.stage_now.setText("")
        self._add_entry(time.time(), state, detail)
        self._render_strip()
        self._tick()
        self.timer.stop()

    # ------------------------------------------------------------------ incoming data

    def update_metrics(self, values: dict) -> None:
        hit = values.get("hit")
        if hit:
            stats = self.templates.setdefault(hit.get("template") or "?", [0, 0.0])
            stats[0] += 1
            stats[1] = max(stats[1], hit.get("confidence") or 0)
            self.timeline.hits.append(hit["timestamp"])
            self.bars.set_rows([(name, n, best) for name, (n, best) in self.templates.items()])
            self._add_entry(time.time(), "Detection", f"{hit.get('template')} · {hit['confidence']:.3f} at {fmt_time(hit['timestamp'])}")
        if "buffer" in values:
            buffer = values["buffer"]
            held = buffer["segments"]
            self.buffer_view.set_buffer(held, buffer["awaiting"], held[-1]["number"] + 1 if held else None)
            self._render_ring()
        if values.get("stream"):
            self.stream_info = values["stream"]
            self.stream_card.set_info(self.stream_info)
            if self.stream_info.get("title"):
                self.source_label.setText(self.source_label.fontMetrics().elidedText(
                    self.stream_info["title"], Qt.TextElideMode.ElideRight, 640))
        record = values.get("clip")
        if record:
            self._add_clip(record)
        if values.get("score") is not None:
            score = values["score"]
            self.chart.push(score)
            self.score_count += 1
            self.score_sum += score
            self.score_peak = max(self.score_peak, score)
            self.last_score = score
            self.conf_stats.setText(f"now {score:.3f}   avg {self.score_sum/self.score_count:.3f}   peak {self.score_peak:.3f}")
        for key in ("start", "end", "position", "samples", "hits", "segments", "events", "threshold"):
            if key in values:
                self.m[key] = values[key]
        if "threshold" in values:
            self.chart.threshold = values["threshold"]
            self.info.set("Threshold", f"{values['threshold']:.2f}")
        self.timeline.start = self.m["start"]
        self.timeline.end = self.m["end"]
        self.timeline.position = self.m["position"]
        self.timeline.update()
        self.t_samples.set_value(self.m["samples"])
        self.t_hits.set_value(self.m["hits"], f"{len(self.templates)} template{'s' if len(self.templates) != 1 else ''} matched" if self.templates else None)
        self.t_events.set_value(self.m["events"], "validated" if self.m["events"] else None)
        if self.remote and self.m["segments"]:
            self.t_samples.caption = f"{self.m['segments']} segments"
        self._render_progress()
        if self.remote and values.get("position") is not None:
            self._render_ring()

    def on_event(self, stamp: float, stage: str, detail: str) -> None:
        now = time.monotonic()
        match = re.fullmatch(r"Segment (\d+)", stage)
        if match:
            number = int(match.group(1))
            state, _, info = detail.partition(" | ")
            segment = self.segments.setdefault(number, {"number": number})
            previous = segment.get("state")
            segment["state"], segment["info"] = state, info
            span = re.match(r"([\d.]+)–([\d.]+)s", info)
            if span:
                segment["span"] = (float(span.group(1)), float(span.group(2)))
            self._render_ring()
            downloading = any(s.get("state") == "Downloading" for s in self.segments.values())
            self._set_stage("Download", "Running" if downloading else "Done", now)
            self._render_strip()
            if state == previous:
                return  # repeated progress reports only update the ring
        elif stage in EVENT_STAGE:
            target = EVENT_STAGE[stage]
            if self.current and self.current != target and self.current != "Download":
                self._set_stage(self.current, "Done", now)
            self.current = target
            self._set_stage(target, "Running", now)
            if target == "Match" and stage != "Scan complete":
                self._set_stage("Extract", "Running", now)
            if stage == "Scan complete":
                self._set_stage("Extract", "Done", now)
                self._set_stage("Match", "Done", now)
            self.pill.set_state("Running", stage)
            self.stage_now.setText(stage)
            if stage == "Templates loaded":
                count = len([name for name in detail.split(",") if name.strip()])
                self.template_count = str(count)
                self.info.set("Templates", f"{count} enabled", detail)
            elif stage == "Frame extraction":
                self.decoder = detail.rsplit("·", 1)[-1].replace("decoding", "").strip()
                self.info.set("Decoder", self.decoder)
            elif stage == "Decoder retry":
                self.decoder = "software (fallback)"
                self.info.set("Decoder", self.decoder)
            elif stage == "Input ready":
                self.info.set("Range", detail.split("·")[0].replace("Range", "").strip())
            elif stage == "Frame matching":
                self._render_strip()
                return  # sampled every 0.5s; the chart already shows it
        self._render_strip()
        self._add_entry(stamp, stage, detail)

    # ------------------------------------------------------------------ helpers

    def _set_stage(self, stage: str, state: str, now: float) -> None:
        previous = self.states.get(stage)
        if previous == state:
            return
        if state == "Running":
            self.stage_start.setdefault(stage, now)
        elif previous == "Running":
            self.stage_spent[stage] = self.stage_spent.get(stage, 0) + now - self.stage_start.pop(stage, now)
        self.states[stage] = state

    def _render_ring(self) -> None:
        if not self.remote:
            self.slot_view.set_ring([None, None, None], bypassed=self.source is not None)
            return
        live = sorted((s for s in self.segments.values() if s.get("state") in {"Downloading", "Ready", "Processing"}),
                      key=lambda s: s["number"])
        slots = []
        for segment in live[:3]:
            state, info, progress = segment["state"], segment.get("info", ""), None
            if state == "Downloading":
                got = re.match(r"([\d.]+)s of ([\d.]+)s", info)
                mib = re.search(r"([\d.]+) MiB", info)
                if got:
                    progress = float(got.group(1))/max(.001, float(got.group(2)))
                    info = f"{float(got.group(1)):.0f}/{float(got.group(2)):.0f}s" + (f" · {float(mib.group(1)):.1f} MB" if mib else "")
            elif state == "Ready":
                info, progress = "downloaded, queued", 1.0
            elif state == "Processing" and "span" in segment:
                a, b = segment["span"]
                progress = max(0.0, min(1.0, (self.m["position"] - a)/max(.001, b - a)))
                info = f"{progress*100:.0f}% scanned"
            slots.append({"number": segment["number"], "state": state, "detail": info, "progress": progress})
        held = sum(1 for s in self.segments.values() if s.get("state") == "Held")
        deleted = sum(1 for s in self.segments.values() if s.get("state") == "Deleted")
        parts = [f"{len(self.segments)} segments"] if self.segments else ["Waiting for the first segment"]
        if held:
            parts.append(f"{held} in temp buffer")
        if deleted:
            parts.append(f"{deleted} deleted")
        self.slot_view.set_ring(slots, "  ·  ".join(parts))

    def _render_strip(self) -> None:
        now = time.monotonic()
        timing = {}
        for stage in STAGES:
            spent = self.stage_spent.get(stage, 0) + (now - self.stage_start[stage] if stage in self.stage_start else 0)
            if spent >= .05:
                timing[stage] = f"{spent:.1f}s" if spent < 60 else fmt_time(spent)
        self.strip.set_states(self.states, timing)

    def _render_progress(self) -> None:
        start, end, pos = self.m["start"], self.m["end"], self.m["position"]
        finite = end is not None and math.isfinite(end) and end > start
        if finite:
            fraction = min(1.0, max(0.0, (pos - start)/(end - start)))
            self.progress.set_value(fraction)
            if self.running:
                self.percent.setText(f"{fraction*100:.0f}%")
        elif self.running and self.source is not None:
            self.progress.set_running(True, indeterminate=True)
            self.percent.setText("LIVE")
        self._render_facts()

    def _render_facts(self) -> None:
        start, end, pos = self.m["start"], self.m["end"], self.m["position"]
        finite = end is not None and math.isfinite(end)
        if self.source is None:
            for value in self.facts.values():
                value.setText("—")
            return
        self.facts["Scanned"].setText(f"{fmt_time(pos - start)} / {fmt_time(end - start) if finite else 'live'}")
        elapsed = (self.finished_at or time.monotonic()) - self.started
        self.facts["Elapsed"].setText(fmt_time(elapsed))
        speed = (pos - start)/elapsed if elapsed > .2 else 0
        self.facts["Speed"].setText(f"{speed:.1f}× realtime" if speed else "—")
        if self.running and finite and speed > 0:
            self.facts["ETA"].setText(fmt_time((end - pos)/speed))
        elif not self.running and self.finished_at:
            self.facts["ETA"].setText("done" if self.pill.state == "Completed" else "—")
        else:
            self.facts["ETA"].setText("—")

    def _tick(self) -> None:
        now = time.monotonic()
        mark_time, mark_samples, mark_pos = self.rate_mark
        span = now - mark_time
        if self.running and span >= .9:
            rate = (self.m["samples"] - mark_samples)/span
            speed = max(0.0, (self.m["position"] - mark_pos)/span)
            self.t_rate.set_value(rate, "samples / second")
            self.t_rate.push(rate)
            self.t_speed.set_value(speed, "media s per wall s")
            self.t_speed.push(speed)
            self.rate_mark = (now, self.m["samples"], self.m["position"])
        elif not self.running and self.source is not None:
            elapsed = (self.finished_at or now) - self.started
            if elapsed > 0:
                self.t_rate.set_value(self.m["samples"]/elapsed, "average samples / s")
                self.t_speed.set_value((self.m["position"] - self.m["start"])/elapsed, "average ×realtime")
        self._render_strip()
        self._render_facts()

    def _add_clip(self, record: dict) -> None:
        self.records.append(record)
        self.timeline.clips.append((record["start"], record["end"]))
        row = self.table.rowCount()
        self.table.insertRow(row)
        peak = max((e.get("confidence", 0) for e in record.get("events", [])), default=0)
        cells = [str(row + 1), record["file"], f"{fmt_time(record['start'])} – {fmt_time(record['end'])}",
                 f"{record['end'] - record['start']:.1f}s", str(len(record.get("events", []))), f"{peak:.3f}",
                 ("⇄ " if len(record.get("segments") or []) > 1 else "") + "+".join(map(str, record.get("segments") or [])) or "—"]
        for column, value in enumerate(cells):
            item = QTableWidgetItem(value)
            if column == 6 and len(record.get("segments") or []) > 1:
                item.setForeground(t.color(t.AMBER))
                item.setToolTip("Cross-cut: this clip spans a segment boundary and was joined from several segments")
            elif column in (0, 3, 4, 5, 6):
                item.setForeground(t.color(t.TEXT_2))
            if column == 1:
                item.setToolTip(record["file"])
            self.table.setItem(row, column, item)
        self.t_clips.set_value(len(self.records), f"{sum(r['end'] - r['start'] for r in self.records):.0f}s of footage")
        self.open_button.setEnabled(True)
        self._clips_visibility()
        self._add_entry(time.time(), "Clip", f"{record['file']} · {record['end'] - record['start']:.1f}s · {len(record.get('events', []))} events")

    def _clips_visibility(self) -> None:
        has = self.table.rowCount() > 0
        self.table.setVisible(has)
        self.clips_empty.setVisible(not has)

    def _open_clip(self, row: int, _column: int) -> None:
        if self.output and 0 <= row < len(self.records):
            self.open_file_cb(self.output / "clips" / self.records[row]["file"])

    @staticmethod
    def _category(stage: str) -> str:
        if stage in {"Detection"}:
            return "Detections"
        if stage in {"Clip", "Clip saved", "Clip encoding", "Clip log"}:
            return "Clips"
        if stage in {"Failed", "Decoder retry", "Stopped"} or stage.startswith("Error"):
            return "Issues"
        return "Other"

    def _format(self, stamp: float, stage: str, detail: str) -> str:
        color = LOG_COLORS.get(stage) or LOG_COLORS.get(EVENT_STAGE.get(stage, ""), t.TEXT_2)
        if stage == "Detection":
            color = t.VIOLET
        elif stage == "Clip":
            color = t.AMBER
        elif stage.startswith("Segment"):
            color = t.AMBER
        return (f"<span style='color:{t.TEXT_3}'>{datetime.fromtimestamp(stamp):%H:%M:%S}</span>&nbsp;&nbsp;"
                f"<span style='color:{color}'>{html.escape(stage).ljust(20).replace(' ', '&nbsp;')}</span>"
                f"<span style='color:{t.TEXT_2}'>{html.escape(detail)}</span>")

    def _visible(self, stage: str) -> bool:
        wanted = ("All", "Detections", "Clips", "Issues")[max(0, self.filters.checkedId())]
        return wanted == "All" or self._category(stage) == wanted

    def _add_entry(self, stamp: float, stage: str, detail: str) -> None:
        self.entries.append((stamp, stage, detail))
        if self._visible(stage):
            self.log.appendHtml(self._format(stamp, stage, detail))
            if self.follow.isChecked():
                bar = self.log.verticalScrollBar()
                bar.setValue(bar.maximum())

    def _render_log(self) -> None:
        self.log.clear()
        lines = [self._format(*entry) for entry in self.entries if self._visible(entry[1])]
        self.log.appendHtml("<br>".join(lines[-2500:]))
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())
