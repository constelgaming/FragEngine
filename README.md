# FragEngine

FragEngine finds **your kills** in Valorant footage and cuts them into clips automatically.

You show it once where the kill feed is and what your name looks like. It then scans
recordings or YouTube videos and live streams, spots every frame where your name appears in
the kill feed, groups kills that happen close together, and saves a padded clip of each moment.

Everything runs on your own computer. Recordings, screenshots, templates and settings never
leave your machine (YouTube sources are downloaded the same way a video player would).

---

## How it works (short)

```
source (file / YouTube) → FFmpeg samples + crops the kill feed → OpenCV template match
  → hits merged into kill events → nearby kills grouped → padded clips cut with FFmpeg
```

You supply the templates (small screenshots of your name) through the built-in calibration
window. Details are in [How detection works](#how-detection-works).

## Quick start (from GitHub)

```bash
git clone https://github.com/constelgaming/FragEngine.git
cd FragEngine
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1    Linux/macOS: source .venv/bin/activate
python -m pip install -e .
fragengine
```

Extra downloads: **FFmpeg + ffprobe** on your `PATH` (see [Installation](#installation)).
No templates ship with the repo: make your own in **Calibrate** before the first run
(see [Quick start: your first clips](#quick-start-your-first-clips)).

---

## Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Installation](#installation)
   - [Windows 10 / 11](#windows-10--11)
   - [Linux](#linux)
   - [macOS](#macos)
4. [Starting the app](#starting-the-app)
5. [Quick start: your first clips](#quick-start-your-first-clips)
6. [Usage guide](#usage-guide)
   - [Calibrate](#1-calibrate)
   - [Templates](#2-templates)
   - [Process (the queue)](#3-process-the-queue)
   - [Monitor](#4-monitor)
   - [Settings](#5-settings)
7. [YouTube and live streams](#youtube-and-live-streams)
8. [Output files](#output-files)
9. [How detection works](#how-detection-works)
10. [Tuning tips](#tuning-tips)
11. [Troubleshooting](#troubleshooting)
12. [For developers](#for-developers)

---

## Features

- **Queue many sources:** local recordings and YouTube videos or live streams, each with its
  own start and end time. They run one after another.
- **Guided calibration:** drag a box around the kill feed and your name, or type exact pixel
  values.
- **Live dashboard:** progress, ETA, speed, per-stage timing, a match-confidence chart, hits per
  template, a hoverable media timeline, saved clips and an activity log.
- **Segmented YouTube ingest:** a three-slot X / Y / Z download ring keeps disk use small while
  analysis runs.
- **Boundary-safe clips:** a kill that starts at the end of one segment and continues into the
  next is still cut as one correct clip.
- **Fast output:** clips are stream-copied by default (no quality loss, no re-encode), with
  optional encoders such as `libx264` or NVENC.
- **Hardware decoding** (CUDA, VAAPI, VideoToolbox), with automatic fallback to software.
- **Stop at any time.** Clips already written are kept.
- Minimal, dark-only interface.

---

## Requirements

| What | Version | Notes |
|---|---|---|
| Python | 3.10 or newer (64-bit) | 3.12 is tested |
| FFmpeg **and** ffprobe | any recent build | must be on your `PATH` |
| OS | Windows 10/11, Linux, macOS | Windows is the main target |
| GPU | optional | NVIDIA + CUDA speeds up decoding |

The Python packages (PySide6, OpenCV, NumPy, PyAV, yt-dlp, …) are installed for you in the
steps below.

---

## Installation

### Windows 10 / 11

**1. Install Python.** Get the 64-bit installer from [python.org](https://www.python.org/downloads/).
During setup, tick **"Add python.exe to PATH"**.

**2. Install FFmpeg.** The easiest way is winget, in PowerShell:

```powershell
winget install Gyan.FFmpeg
```

Close and reopen PowerShell, then check both tools work:

```powershell
ffmpeg -version
```

```powershell
ffprobe -version
```

> Prefer a manual install? Download a build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/),
> unzip it, and add its `bin` folder to your `PATH`.

**3. Get the code.** With [Git](https://git-scm.com/) installed:

```powershell
git clone https://github.com/constelgaming/FragEngine.git
```

(or use **Code → Download ZIP** on GitHub and unzip it).

**4. Set up FragEngine.** Open PowerShell **in the FragEngine folder** (Shift + right-click the
folder → *Open in Terminal*), then run these one at a time:

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\Activate.ps1
```

```powershell
python -m pip install --upgrade pip
```

```powershell
python -m pip install -e .
```

> If PowerShell says running scripts is disabled, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

### Linux

Debian, Ubuntu or Kali:

```bash
sudo apt update
sudo apt install ffmpeg python3-venv zenity
cd FragEngine
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

`zenity` is optional. With it, FragEngine uses the GNOME/GTK file picker.

### macOS

```bash
brew install python ffmpeg
cd FragEngine
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

---

## Starting the app

| How | Command |
|---|---|
| **Windows, easiest** | double-click **`Launch FragEngine.bat`** in the project folder |
| Any OS, venv active | `fragengine` |
| Calibration window only | `fragengine-calibrate` |
| From source without installing | `python -m fragengine` with `PYTHONPATH=src` |

The `.bat` launcher starts the app without a console window. It needs the `.venv` folder
from the installation steps.

---

## Quick start: your first clips

1. **Take a screenshot** of a Valorant match at the **same resolution as your recordings**
   (e.g. 1920 × 1080), with your name visible in the kill feed (top-right).
2. Open FragEngine → **Calibrate** → **Open calibration workspace**.
3. **Load** the screenshot, drag a box around the **whole kill feed**, then **Continue**.
4. Drag a tight box around **your name**, **Continue**, then **Save Template**.
5. **Continue** to the last step and press **Save Configuration**. The main window picks it up
   automatically.
6. Go to **Process**. Paste a recording path or YouTube URL and press **Add to queue**, then
   choose an **Output** folder.
7. Press **Start run**. The **Monitor** tab opens and shows progress. Clips appear in
   `<output>/clips/` as they are found.

---

## Usage guide

The sidebar has five pages. The environment panel at the bottom shows whether FFmpeg was
found, whether a configuration is loaded, and how many templates are enabled.

### 1. Calibrate

Calibration tells FragEngine **where** the kill feed is and **what your name looks like**.

The **Calibrate** page shows the current calibration: source resolution, the crop drawn to
scale, the FFmpeg crop string, and the latest saved preview image. Press
**Open calibration workspace** to change it.

In the workspace:

| Step | What to do |
|---|---|
| **01 Kill Feed** | **Load** a full-size screenshot. Drag over the entire kill-feed area, the full height where kill rows can appear. Use **X / Y / W / H** for pixel-exact edits. |
| **02 Template** | The view zooms to the crop. Drag tightly around **your name** only. |
| **03 Review** | Check the preview on the right. |
| **04 Save** | **Save Template** writes your name image to `assets/templates/`. **Save Configuration** writes `config/config.json` and backs up the old one to `config.json.bak`. |

Workspace tools: **＋ / −** zoom, **Fit**, **Reset** or **Esc** (clear the selection), **Copy crop** (copy the FFmpeg `crop=` string), **Save Kill Feed Sample** and
**Save Selection Preview** (both save images to `output/calibration/`).

**Tips**
- Calibrate at the resolution you record at. Other resolutions are rescaled to it, but an exact
  match detects best.
- Save **several templates** of your name: on different backgrounds, as killer and as victim,
  and in different team colours. Each one is tried on every frame.
- Keep the `.PNG` extension in capitals when you name a template. Only `*.PNG` files are
  loaded, which matters on Linux and macOS.

### 2. Templates

A card for every template image, showing its size and, after a run, how many hits it got and
its best score.

- **Toggle** a template on or off without deleting it.
- **Enable all**, **Open folder**, **Add from calibration**.
- **Delete** removes the PNG permanently and asks first.

Disable templates that cause false hits rather than deleting them.

### 3. Process (the queue)

**Add source**
- Paste a **file path** or a **YouTube URL** (video, live stream or VOD) and press **Enter** or
  **Add to queue**. The box clears, ready for the next one.
- **Browse…** can pick **several files at once**. All of them are added.
- **Start / End** set the range for the item you are about to add. Leave End empty for the
  whole video or the whole stream. Accepted formats: `90`, `1:30`, `01:30:00`.
- While you type, a line under the box checks the input. Local files show resolution, length
  and size, and you get a warning if the resolution differs from your calibration.

**Queue**
- Items run **top to bottom, one at a time**.
- Each row shows its type (FILE or YT), name, media info, range (if not the full video), and
  status: Queued, Running %, Done · N clips, Failed, or Stopped. Hover over **Failed** to see
  the error.
- Row buttons: **↑ ↓** reorder · **↻** queue again · **✕** remove.
- **Clear finished** keeps only waiting items. **Clear all** empties the queue.

**Output**
- With **one** item, files go straight into the chosen folder.
- With **several** items, each gets its own subfolder: `01_<file name>`, `02_<YouTube id>`, …

**Run**
- **Start run** / **Start queue · N** begins. Text still in the Add box is queued first.
- The hint next to the button explains why it is disabled, if it is.
- **Stop** ends the current item. The rest stay **Queued**; press Start again to continue.
- If an item fails, the queue moves on to the next one and gives a summary at the end.

The right-hand side shows the **run configuration** (the settings in use, total local footage
and an estimated sample count) and the **FFmpeg command** that will sample the crop. **Copy**
copies the command.

### 4. Monitor

Shows **only the item running now** (with "Item 2 of 5" when a queue is running). Everything
resets for the next item.

| Section | What it tells you |
|---|---|
| **Header** | Source (stream title for YouTube), status, progress %, scanned / total time, ETA, speed (× realtime), elapsed time, **Open clips folder** |
| **YouTube source** | Thumbnail, title, channel, LIVE / past live / video, length, resolution, views. Shows the error if the URL can't be opened. |
| **Tiles** | Samples scanned, raw hits, kill events, clips saved, throughput (samples/s), speed |
| **Pipeline** | Input → Download → Extract → Match → Validate → Group → Encode → Save, with time spent in each |
| **Match confidence** | Best template score per sampled frame. Above the amber threshold line is a hit. Hover to read values. |
| **Templates** | Which templates matched, how often, and their best score |
| **Media timeline** | Scanned range (blue), each hit (purple ticks) and saved clips (amber). Hover to read the time and what is there. |
| **Saved clips** | File, range, length, kill events, peak score, source segments (`⇄ 1+2` = cut across a boundary). **Double-click a row to play the clip.** |
| **Run details** | Source, output, sampling, threshold, crop, decoder actually used, templates, encoding, range |
| **Download ring** | YouTube only. See [below](#youtube-and-live-streams). |
| **Temp buffer** | YouTube only. Processed segments kept on disk for boundary clips. |
| **Activity** | Timestamped log. Filter by **Detections**, **Clips** or **Issues**. Turn **Follow** off to scroll back. |

### 5. Settings

Changes apply to the **next run** even before you save. **Save settings** writes them to
`config/config.json`. **Revert** undoes unsaved edits.

| Setting | Default | What it does |
|---|---|---|
| Hardware acceleration | CUDA | `None`, `CUDA`, `VAAPI`, `VideoToolbox`. Falls back to software automatically if it fails. |
| Sampling FPS | `2/3` | Frames checked per second, **written as a fraction**: `2/3` = one every 1.5 s, `1/1` = every second, `2/1` = twice a second. |
| Maximum height | 1080 px | Highest YouTube quality to download |
| Segment duration | 60 s | Length of each YouTube download segment |
| Match threshold | 0.75 | Minimum template score that counts as a hit (0–1). Higher means stricter. |
| Temporal window | 3.0 s | Hits closer together than this are treated as **one** kill (the name stays in the feed for several frames). |
| Min. observations | 1 | How many hits a kill needs before it counts. Raise it to reject one-frame flukes. |
| Group kills within | 10 s | Kills closer than this go into **one** clip (multi-kills). |
| Before first kill / After last kill | 10 s / 10 s | Padding added around each clip |
| Output encoding | stream copy | Copy is fastest and lossless. Turn copy off to re-encode, e.g. `libx264` or `h264_nvenc`. |

> **Keep the temporal window larger than one sampling gap.** At `2/3` FPS frames are 1.5 s
> apart, so a 3 s window works. With **Min. observations = 3**, your name has to be visible in
> three samples (about 3 s at `2/3` FPS). Raise the sampling FPS if kills go missing.

---

## YouTube and live streams

YouTube sources are downloaded in **segments** (60 s by default) through a three-slot ring:

```
 X  processing   ← being scanned now
 Y  next         ← downloaded / downloading
 Z  after        ← downloaded / downloading
```

- While **X** is scanned, **Y** and then **Z** download.
- When X finishes, the ring **shifts**: Y → X, Z → Y, and a new download starts in Z.
- At most three segments are in flight, so disk use stays small even for long streams.

**Temp buffer (boundary clips).** A finished segment is deleted right away, unless a clip still
needs it:

- **edge:** a kill near the end of the segment whose clip continues into the next one. The
  segment is kept until the next one is processed, then the clip is **cut across both**
  (shown as `⇄ 1+2` in Saved clips).
- **lead-in:** the last few seconds may be needed as the "before" padding for a kill early in
  the next segment.

The Monitor's **Temp buffer** section shows what is held, why, and how much disk it uses.

**Live streams**
- Keep **Start at 0**. Times count from when capture begins. Leave **End** empty to run until
  the broadcast ends, or press **Stop**.
- For finished streams and normal videos, a Start above 0 works, but FFmpeg then **re-encodes**
  the download to seek precisely, which is CPU-heavy and slower. Start at 0 when you can.

---

## Output files

```text
<output>/
├── clips/
│   ├── recording_clip1.mp4      ← the clip
│   ├── recording_clip1.json     ← what is in it
│   └── …
└── detection_log.json           ← summary of the whole run
```

With several queue items: `<output>/01_<name>/clips/…`, `<output>/02_<name>/clips/…`, and so on.

- **Clip `.json`:** source, start/end seconds, each kill event (time, template, confidence,
  supporting observations) and, for YouTube, the segments it was cut from.
- **`detection_log.json`:** raw hits, validated kills, groups, clip list, elapsed time.
- Existing clips are never overwritten; new ones get a numeric suffix. `detection_log.json` is
  replaced by the next run into the same folder.
- YouTube clips are named `youtube_clipN.mp4`. Local clips use the recording's file name.

---

## How detection works

1. **Extract:** FFmpeg samples frames at the sampling FPS, scales them to your calibrated
   resolution, crops the kill feed, and streams the crops to OpenCV.
2. **Match:** every enabled template is compared to the crop (`TM_CCOEFF_NORMED`). A best
   score at or above the threshold is a **hit**.
3. **Validate:** hits within the temporal window merge into one **kill event**. Events with
   fewer than *Min. observations* hits are dropped.
4. **Group:** kills within *Group kills within* seconds become one clip.
5. **Cut:** each group is padded and written with FFmpeg, alongside its `.json` log.

---

## Tuning tips

| Symptom | Try |
|---|---|
| Missing kills | Lower the threshold slightly (e.g. 0.75 → 0.70), add more templates, raise sampling FPS, set Min. observations to 1 |
| False clips | Raise the threshold, raise Min. observations to 2, disable the template that misfires (see the Monitor's Templates card) |
| Many short clips for one fight | Increase *Group kills within* |
| Clips start too late / end too early | Increase *Before first kill* / *After last kill* |
| Clip starts slightly off | Stream copy cuts on keyframes. Pick an encoder in Settings for exact cuts. |
| Slow processing | Enable CUDA, lower sampling FPS, keep the crop tight, use fewer templates, start YouTube items at 0 |

Use the **Match confidence** chart: if real kills peak just under the amber line, lower the
threshold a little. If random frames cross it, raise it.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "FFmpeg / ffprobe not found" | Install FFmpeg, reopen the terminal, check `ffmpeg -version` and `ffprobe -version`. The launcher must see the same `PATH`. |
| `Launch FragEngine.bat` says no Python environment | Run the [installation](#windows-10--11) steps; the `.venv` folder must exist. |
| "Calibrate first" / Start disabled | No valid `config/config.json`. Run calibration and press **Save Configuration**. |
| "Enable at least one template" | Turn a template on in **Templates**, or save one in calibration. |
| No detections at all | Make sure the screenshot and recording resolutions match, check the crop on the Calibrate page, and watch the confidence chart. |
| Hardware decoding fails | Set acceleration to **None**. Automatic fallback is also attempted. |
| YouTube "Video unavailable" / download errors | The video may be private, age- or region-locked. YouTube also changes often, so update the downloader: `python -m pip install -U yt-dlp` (with the venv active). |
| Clips missing audio | The source has no audio, or the audio encoder failed. Try stream copy. |
| A black console window pops up | Update to the latest version. FFmpeg now starts hidden. |

---

## For developers

**Project files**

| Path | What it is |
|---|---|
| `src/fragengine/core/pipeline.py` | Sampling, matching, validation, grouping, clip cutting |
| `src/fragengine/core/stream.py` | YouTube / live-stream segmented download ring |
| `src/fragengine/core/config.py`, `paths.py`, `picker.py` | Config load/validate/save, project paths, native file pickers |
| `src/fragengine/gui/app.py` | Main window (Calibrate, Templates, Process, Monitor, Settings) |
| `src/fragengine/gui/calibration.py` | Calibration workspace |
| `src/fragengine/gui/monitor.py`, `queue_view.py`, `widgets.py`, `theme.py` | Monitor dashboard, queue, shared widgets, styling |
| `config/config.json` | Starter calibration for 1920 × 1080 plus default settings |
| `assets/templates/` | Your name templates (`*.PNG`), created in calibration |
| `tests/test_stream_pipeline.py` | End-to-end pipeline tests (need FFmpeg) |
| `tools/legacy/` | Original stand-alone scripts, not used by the app |
| `Launch FragEngine.bat` | Windows launcher (uses `.venv`) |
| `pyproject.toml`, `requirements.txt` | Package metadata and dependencies |

**Not in the repository (git-ignored)**

| Ignored | Why |
|---|---|
| `.venv/`, `__pycache__/`, `*.egg-info/`, `build/`, `dist/` | Created locally by install / Python |
| `assets/templates/*.PNG` | Personal: images of your own in-game name |
| `output/` | Run output and calibration previews |
| `config/*.bak` | Backups written on every save |
| `*.mp4`, `*.mkv`, `*.log`, temp files | Media, clips and logs |
| `.env` | Secrets, if you ever add any |

**Run the tests** (FFmpeg required; YouTube is mocked, so no network is needed):

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

PowerShell:

```powershell
$env:PYTHONPATH = "src"; python -m unittest discover -s tests -v
```

**Updating:** after pulling new code, run `python -m pip install -e .` again in the venv.

**Configuration file:** `config/config.json` stores calibration and settings only. Input
paths, output folders and ranges are never saved. Editing it by hand is fine; the app reloads
it within a second and checks every value.

**Legacy tools:** `tools/legacy/scan.py`, `group_timestamps.py` and `create_clips.py` are the
original manual workflow and are not used by the app.
