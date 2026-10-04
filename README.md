> **About this repository.** This is a public snapshot of a private project that
> turns historical journal entries into narrated short videos (YouTube Shorts). It was built for a Lewis & Clark channel, so parts of it are still
> Lewis & Clark-specific (journal scraping, location data, `config/profiles/`).
>
> What is **not** included: the channel's tuned `lewis_clark*` prompt packs, its
> character portraits and voices, and its working notes. `config/narration_characters.json`
> contains two placeholder characters, and `prompt_packs/generic_documentary` is the
> working example pack. Set `PROMPT_PACKS_DIR` to load your own packs from elsewhere.
> Some tests that need the private assets are skipped automatically.
>
> Provided as-is, with no support. You pay for your own API usage (OpenAI / Anthropic,
> FAL, etc.). Journal text comes from the University of Nebraska CDRH edition
> ([Lewis and Clark Journals](https://lewisandclarkjournals.unl.edu/)); check their
> terms before redistributing it.

# Lewis & Clark YouTube Video Pipeline

A Python pipeline that turns Lewis & Clark expedition journal entries into narrated YouTube-style videos. Journal text flows through AI-generated scripts, text-to-speech, and video generation to produce historical documentary content. Supports **YouTube Shorts** (9:16 vertical output).

## Overview

```
Journal XML → OpenAI (two-phase narration script) → TTS (audio) → fal (default) / Google Veo / Sora / manual videos → ffmpeg (final video)
```

| Script | Purpose |
|--------|---------|
| `scripts/scrape-journal-entries.py` | Fetch Lewis & Clark TEI XML journal entries from CDRH (batch: one year) |
| `generate-narration-two-phase.py` | Use the OpenAI API to turn journal XML into a narration script + video prompts (Phase 1: narration/metadata, Phase 2: visual plan, then merge). `generate-narration.py` is a compatibility shim that forwards here. |
| `narration-to-mp3.py` | Synthesize narration JSON to MP3 via pyttsx3, then concatenate with ffmpeg |
| `narration-to-video.py` | Generate AI video via configurable vendor: sora, google (Veo), or fal.ai |
| `map_intro.py` | Generate map intro clip (location pin + title) via folium + Playwright |
| `videos-mp3-to-movie.py` | Combine pre-made MP4 clips with narration audio into final video |
| `run-daily.py` | Orchestrate full pipeline for a date, optionally upload to YouTube. `--shorts` produces 9:16 vertical output for YouTube Shorts |
| `youtube_upload.py` | Upload a video to YouTube (OAuth 2.0) |
| `youtube_playlist.py` | Create a YouTube playlist from uploaded journal videos (output manifests) |
| `instagram_upload.py` | Cross-post to Instagram Reels (Graph API) — built, not yet verified against a real account |

## Prerequisites

- **Python 3** with virtual environment (`.venv`)
- **ffmpeg** installed and on PATH
- **OpenAI API key** in `OPENAI_API_KEY` (for `generate-narration-two-phase.py`)

Per-vendor requirements:

- **sora**: Playwright + `playwright install chromium`
- **google**: `GOOGLE_API_KEY`, `pip install google-genai`
- **fal**: `FAL_KEY`, `pip install fal-client`

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Unix

# Core pipeline + YouTube upload (one command)
pip install -r requirements.txt -r requirements-youtube.txt
playwright install chromium

# Optional: Google Veo vendor only
# pip install google-genai
```

If you already have a venv and see `ModuleNotFoundError` (e.g. openai, ffmpeg, pyttsx3), re-run:

```bash
pip install -r requirements.txt -r requirements-youtube.txt
```

Optional (Windows): to add only YouTube deps to an existing venv, run `.\install-youtube-deps.ps1` from the project root.

## Usage

### 1. Scrape journal entries

Fetch journal XML from CDRH (one year starting at the given date):

```bash
python scripts/scrape-journal-entries.py 1803-08-30
```

XML files are saved to `journal-entries/` as `YYYY-MM-DD.xml`.

### 2. Generate narration script

Create a `narration<date>.json` from a journal entry using OpenAI (two phases: Phase 1 narration + metadata, Phase 2 visual plan, then merged to pipeline JSON). Optional `config/narration_config.json` supplies visual themes and stylization (see [Data formats](#data-formats) below). Prompt-pack and config detail lives in **`docs/AGENTS-pipeline.md`** and **`docs/PIPELINE_MODULES.md`**.

```bash
python generate-narration-two-phase.py 18030830 --xml-dir journal-entries
# Optional: custom model / config
python generate-narration-two-phase.py 18030830 --model gpt-4o-mini --config config/narration_config.json
```

`python generate-narration.py 18030830` still works — it is a shim that forwards to the two-phase generator.

Output: `narrations/narration18030830.json` with `narration_script` array of `stage_direction`, `narration`, and `video_prompt`.

### 3. Generate narration audio

Synthesize the narration to MP3 (British male voice, pyttsx3):

```bash
python narration-to-mp3.py 18030830
```

Output: `audio/18030830/segments/` (per-segment MP3s), `audio/18030830/durations.json`, and `audio/18030830/final.mp3`.

### 3b. Map intro (optional, for google/fal)

Generate a short intro clip (map pin + date/location title) used when assembling with per-segment clips. Requires `location_data/location-dates.json` (waypoints with `date`, `location`, and optional `comments`). Map images (no title) are cached in `location_data/cache/` by lat/lon so the same location reuses one image across dates (avoids repeated network/tile calls). Run after narration-to-mp3; it prepends the intro to `durations.json` and adds leading silence to `final.mp3`.

```bash
python map_intro.py 18030830
```

Output: `movie-images/18030830/00_intro.mp4`. When using google or fal, `run-daily.py` runs this step automatically.

### 4. Generate video (choose vendor)

```bash
# Sora (ChatGPT web UI, Playwright) — one combined video
python narration-to-video.py 18030830 --vendor sora

# Google Veo (Gemini API) — per-segment clips → movie-images/<date>/
python narration-to-video.py 18030830 --vendor google

# fal.ai Wan 2.5 (API) — per-segment clips → movie-images/<date>/
python narration-to-video.py 18030830 --vendor fal
```

| Vendor | Env var | Output |
|--------|---------|--------|
| `sora` | — | `output/video_<date>.mp4` (combined, via browser login) |
| `google` | `GOOGLE_API_KEY` | `movie-images/<date>/00_intro.mp4` (from map_intro), `01.mp4`, `02.mp4`, … |
| `fal` | `FAL_KEY` | `movie-images/<date>/00_intro.mp4` (from map_intro), `01.mp4`, `02.mp4`, … |

For google/fal, the pipeline generates a map intro as `00_intro.mp4`; segment clips are `01.mp4`, `02.mp4`, etc. **Optional – Manual:** Place pre-made MP4 clips in `movie-images/<date_id>/` (e.g. `00_intro.mp4`, `01.mp4`, …).

### 5. Combine videos and audio

Build the final video from clips + narration:

```bash
python videos-mp3-to-movie.py 18030830
```

Expects:

- `movie-images/18030830/*.mp4` – clips in sorted order (e.g. `00_intro.mp4`, `01.mp4`, `02.mp4`, …) matching `durations.json`
- `audio/18030830/durations.json` – segment durations (intro first if map intro was used)
- `audio/18030830/final.mp3` – concatenated narration (with leading silence if map intro was used)

Output: `output/lewis_clark_18030830_video.mp4`

### 6. Run full pipeline and upload to YouTube

To run the entire pipeline for a date and optionally upload to YouTube:

```bash
# Process today's anniversary (e.g., Aug 30 → 1803-08-30) and upload
python run-daily.py --upload

# Process a specific journal date
python run-daily.py --date 1804-08-30 --upload

# Use Google Veo for video generation (default: fal)
python run-daily.py --vendor google --upload

# Dry-run: show plan without executing
python run-daily.py --date 1804-08-30 --dry-run

# Unlisted or private upload
python run-daily.py --upload --privacy unlisted
```

**YouTube setup:**

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **YouTube Data API v3**.
3. Create **OAuth 2.0 credentials** (Desktop app).
4. Download the JSON and save as `client_secrets.json` in the project root (see `client_secrets.example.json` for structure).
5. First run opens a browser for authorization; `token.json` is saved for future runs.

```bash
pip install google-api-python-client google-auth-oauthlib
```

**Create a playlist from uploaded videos:**

After uploading several journal videos, create a playlist in chronological order (by date_id) from `output/*.manifest.json` entries that have a YouTube link:

```bash
python youtube_playlist.py
# Optional: custom title, description, privacy
python youtube_playlist.py --title "Lewis & Clark 1803" --privacy unlisted
python youtube_playlist.py --dry-run   # List videos that would be added
```

Uses the same OAuth token as `youtube_upload.py` (you may need to run `youtube_upload.py --login-only` once so the token includes the playlist scope).

**Scheduling (e.g., daily at 6am):**

- **Windows Task Scheduler:** Create a task that runs `python run-daily.py --upload` daily.
- **Linux/macOS cron:** `0 6 * * * cd /path/to/project && .venv/bin/python run-daily.py --upload`

## Directory structure

The authoritative map is **`docs/DIRECTORY_LAYOUT.md`** (pipeline package, config, generated trees). Quick orientation:

| Path | Role |
|------|------|
| `*.py` at repo root | CLI entry points: `run-daily.py` (full pipeline + optional YouTube upload, default vendor `fal`), `generate-narration-two-phase.py`, `narration-to-mp3.py`, `map_intro.py`, `narration-to-video.py`, `videos-mp3-to-movie.py`, `youtube_upload.py` |
| `scripts/` | Utilities incl. `scrape-journal-entries.py`, portrait builders, analysis/mining |
| `pipeline/` | Importable package: narration phases, characters, ambient, tension (`import pipeline.…`) — module map in `docs/PIPELINE_MODULES.md` |
| `pipeline_ui/` | Local browser UI for `run-daily` |
| `theme_engine/` | Focus-topic selector, embeddings, CLI (`docs/AGENTS-theme-engine.md`) |
| `video_vendors/` | Pluggable video vendors: sora, google, fal |
| `config/` | Committed JSON: narration_config, narration_characters, tension arcs, profiles |
| `prompt_packs/` | Phase 1/2 system prompts + `pack.json` versioning |
| `state/` | Machine-local run state: `run_daily_state.json`, `week_arcs/`, `fal_usage_state.json` — copy from the `*.example` files (see `AGENTS.md`) |
| `journal-entries/` `narrations/` `audio/` `movie-images/` `output/` | Generated per-date trees (mostly gitignored); `output/` also holds each video's `.manifest.json` |
| `client_secrets.json` | YouTube OAuth credentials (create from Google Cloud Console) |

## Data formats

### Narration JSON (`narrations/narration<date>.json`)

```json
{
  "title": "Short evocative episode title (optional, used in map intro)",
  "scene_spine": { "core_location": "...", "environmental_elements": "...", "lighting_progression": "...", "color_palette": "...", "visual_mood": "..." },
  "narration_script": [
    {
      "stage_direction": "[CUT TO – SCENE]",
      "narration": "Spoken voiceover text...",
      "video_prompt": "Detailed visual description for AI video..."
    }
  ]
}
```

Optional `scene_spine` describes the stable visual world for the entry so all segment `video_prompt`s stay consistent unless time or location changes.

### Durations JSON (`audio/<date>/durations.json`)

When using the map intro, the first entry is the intro; segment files follow:

```json
[
  {"file": "intro", "duration": 4.0},
  {"file": "segments/01.mp3", "duration": 12.5},
  {"file": "segments/02.mp3", "duration": 8.3}
]
```

### Narration config (`config/narration_config.json`)

Optional JSON used by `generate-narration-two-phase.py` to influence visual style of video prompts:

- `allow_stylization`: allow random visual theme (default: true)
- `stylization_probability`: 0–1 chance to apply a theme (default: 0.35 in code; config can override)
- `visual_themes`: array of `{ "name", "description" }` objects

## Dependencies

| Package | Purpose |
|---------|---------|
| openai | ChatGPT API for narration generation |
| requests | HTTP for scraping and video download |
| pyttsx3 | Text-to-speech (British male voice) |
| ffmpeg-python | Audio concatenation, video muxing |
| playwright | Browser automation for Sora vendor |
| fal-client | fal.ai video API (optional, for `--vendor fal`) |
| google-genai | Google Veo API (optional, for `--vendor google`) |
| google-api-python-client | YouTube upload (optional, for `--upload`) |
| google-auth-oauthlib | YouTube OAuth (optional, for `--upload`) |

## Tracking vendors per video

When `run-daily.py` produces a final video it:

- Logs a **VIDEO_COMPLETE** event to `logs/pipeline.log` with `date_id`, `output`, `narration_vendor`, `tts_vendor`, and `video_vendor`.
- Writes **`output/<name>.manifest.json`** next to the video (e.g. `lewis_clark_18030903_video.manifest.json`) with the same fields so you can see which narration, TTS, and video vendors were used for each output. Manifest path, schema, and updates are handled by **`video_manifest.py`** (run-daily creates it; youtube_upload updates it with the YouTube link when provided).

You can grep the log for `"event": "VIDEO_COMPLETE"` or read any video’s `.manifest.json` to see how it was built.

Manifests also include a **`cost_estimate`** (approximate USD) when the pipeline produces the video: `narration_usd`, `tts_usd`, `video_usd`, and `total_usd`. Narration cost uses token usage from `narrations/narration<date>.meta.json` (written when you run `generate-narration-two-phase.py`). TTS cost uses character count from the narration script (OpenAI TTS only). Video cost uses total duration × vendor rate (e.g. FAL 480p). Pricing constants are in `run-daily.py`; update them if APIs change.

## Repository (AWS CodeCommit)

This project uses AWS CodeCommit for Git. To set up or restore access from a new machine (SSH key, IAM user, config), see **[AWS-CodeCommit-Setup.md](AWS-CodeCommit-Setup.md)**.
