# Pipeline UI (local web front-end for `run-daily.py`)

Small **localhost-only** HTML + Python server so you can pick options without memorizing CLI flags.

## How to run

From the **repo root**:

```powershell
.\scripts\Start-LewisClarkUI.ps1
```

Or with the venv Python directly:

```powershell
.\.venv\Scripts\python.exe pipeline_ui\server.py --reload
```

Open **http://127.0.0.1:8765/** in your browser.

- **Last run log:** each **Run** / **Dry-run** writes **`logs/pipeline_ui_last.json`** at the repo root with form values, resolved argv, cwd, return code, stdout, and stderr (very long streams are truncated). Use it when debugging or when asking an assistant to inspect a failed UI run without pasting output. The `logs/` folder is typically gitignored.
- **Refresh the page** to reload `pipeline_ui/options.json` (form fields and help text).
- **Run** executes `run-daily.py` with the selected flags and shows stdout/stderr in the page.
- **Stop** sends `POST /api/cancel` to terminate the current run (the same subprocess as **Run**). On Windows the server uses `taskkill /T` so child processes (e.g. `narration-to-video.py`) are stopped too. **`GET /api/status`** returns `{"running": true|false}` so the page can show Stop when a run is active (e.g. after refresh).
- **Previews**: on the **Produce** and **Video** workflow tabs, the journal date picker drives narration segment review and `GET /api/preview/video` (HTTP-served MP4 with `Range` support). Video segments use `GET /api/preview/video-cues` (parses output filename → `date_id`, then `audio/<date_id>/durations.json` + `narrations/narration<date_id>.json`). The inline player and preview modal show **visual_mode**, **talking_head** fields, **dialogue[]**, and **video_prompt** per timeline slot (dialogue episodes include pack/focus in the cue header). The **modal** is still used for **Open diary XML** (`GET /api/journal/<YYYY-MM-DD>`). Paths must stay **under the repo root**.
- **Produce tab** (`workflow: narration` in the UI, `video` on Run): three sub-tabs — **Script & audio**, **Shots & clips**, and **Anchor preview**. Run flags are grouped by sub-tab (voice/TTS vs vendor/regen). **Generate TTS** / **Regenerate all TTS** on **Script & audio** and **Anchor preview**, plus per-segment **Regen TTS** in the segment table, run `narration-to-mp3.py` via `POST /api/generate-tts` (optional `segment_indices` for one segment; uses the form **TTS** field: openai / pyttsx3). Does not run video. **Edit opening frame:** **Edit OF** in the segment table → `POST /api/narration/update-opening-frame` (scene-anchor t=0 still; rebuild anchors/clips after). **Remove a segment:** **Edit voice** (or **Edit VP** on dialogue rows) → type `DELETEME` → close/save; `POST /api/narration/update-narration` or `update-video-prompt` drops that row and renumbers `segment_index` (cannot delete the last segment).
- **Anchor preview (before OmniHuman):** on **Produce → Anchor preview**, after TTS exists: **Build missing anchors** / **Build all anchors** → `POST /api/anchor-preview/build-anchors` (async; same FAL scene-anchor i2i as per-segment, writes `movie-images/<date_id>/anchors/`). **Build preview clips + assemble** → `POST /api/anchor-preview/assemble` (builds missing anchors first, then clips + mux) → `movie-images/<date_id>/anchor_preview/NN.mp4` + `output/lewis_clark_anchor_preview_<date>_video.mp4`. Player: `GET /api/preview/anchor-preview-video`; timeline cues: `GET /api/preview/video-cues?preview_mode=anchor`. Segments without anchors use existing `NN.mp4` when present, else a labeled slate (talking_head without anchor = warning slate). **Generate full video (reuse anchors)** → `POST /api/generate-video-reuse-anchors` (`narration-to-video.py --reuse-scene-anchor-stills` + assemble). CLI: `python scripts/build_anchor_preview.py <date_id>`. Status: `GET /api/anchor-preview/status?journal_date=…`.
- **Scene anchor (FAL i2i only)**: on **Produce → Shots & clips**, B-roll suggestions load for the picker date; each segment row with a reference character has **Scene anchor**, calling `POST /api/fal/scene-anchor-i2i` with `aspect_ratio` **9:16** (fixed in the UI). Saves under `movie-images/<date_id>/anchors/` without running image-to-video (the server normalizes paths so a mis-parsed date id cannot create `anchors/anchors`). If you already have a nested `anchors/anchors` folder, run `python scripts/fix_nested_movie_images_anchors.py` from the repo root. Existing stills load from `GET /api/preview/anchor-image?journal_date=…&segment=…` (same picker date); you can regenerate. Requires `FAL_KEY` and a portrait-backed scene-anchor segment.
- Check **Narration only** to pass `--narration-only` to `run-daily.py` (narration JSON only; no audio/video).
- **Regenerate video segments** (optional text field): passes `--regenerate-video-segments` with comma-separated 1-based indices (e.g. `3` or `2,5,7`). **google/fal only** — backs up listed clips and the final output, regenerates those segments, reassembles (and uploads if enabled). Does not update `state/run_daily_state.json` last_date.
- **No automatic focus topic**: on the **Full run** workflow tab, maps to `--no-focus-topic` on `run-daily.py` (narration step skips theme_engine similarity focus topic and recommend/record). The **Journal** tab has the same option as its own checkbox when generating narration.
- **Journal → Regenerate narration**: when checked, Run sends `--no-skip-existing` with the usual Journal run so two-phase narration runs again even if `narrations/narration<date>.json` already exists (still narration-only: no TTS or video).
- **Journal → Reset day (archive artifacts)**: moves `narrations/narration<date>*`, `audio/<date_id>/`, `movie-images/<date_id>/`, and `output/*<date_id>*` into `archive/reset_<date_id>_<timestamp>/` via `POST /api/reset-day` (does not delete journal XML). Reverts `state/run_daily_state.json` `last_date` when it matches this date. CLI: `run-daily.py --date … --reset-day` (archive then run) or `--reset-day-only`.
- **B-roll suggestions**: on **Produce → Shots & clips**, `GET /api/b-roll/suggest` runs for the journal date when you open that sub-tab (same picker as the rest of the pipeline); thumbnails use `GET /api/b-roll/thumbnail`.
- **Video tab → YouTube publish backlog**: at the top of the tab, shows the **last uploaded journal date** (latest `date_id` with `youtube_video_id` in `output/` and `archive/*/output` manifests) and how many assembled videos in `output/` are **ready to upload** (manifest present, MP4 on disk, no `youtube_video_id`). Refreshes when you open the Video tab and after **Upload to YouTube** succeeds. API: `GET /api/youtube/upload-status`.
- **Video tab → Add segment to B-roll library**: same as the video preview modal — pause the inline player on a narrative segment (not intro), then click; calls `POST /api/b-roll/add-from-preview` with `date_id` and `segment_index`.
- **Video tab → FAL run report**: **Load run report** calls **`GET /api/run-report?journal_date=YYYY-MM-DD`** (same journal picker). Returns **`movie-images/<date_id>/run_report.json`** if present (written after **`narration-to-video.py --vendor fal`**). See **docs/AGENTS-pipeline.md** — *Talking-head, FAL video, run reports, and UI*.
- **Mix ambient bed (assembly)**: checkbox maps to `--ambient` on `run-daily.py` → `videos-mp3-to-movie.py`. When narration `audio_design.ambience_level` is `light` or `moderate`, mixes loops from `ambient_library/` at repo root (see manifest). No effect if level is `none` or flag is off.
- **Journal date picker** (1803–1806): on load and after a successful **Run**, the client still calls `GET /api/latest-links` (no list UI) to read `suggested_journal_date` and pre-fill the picker when known (pipeline `state/run_daily_state.json` `last_date`, else `latest_narration.url` / `latest_video.url`). Opens diary XML via `GET /api/journal/<YYYY-MM-DD>`. A link below opens the matching entry on the [UNL Journals site](https://lewisandclarkjournals.unl.edu/) (`…/item/lc.jrn.YYYY-MM-DD`). **Pipeline state** (button beside the picker) calls **`GET /api/episode-state?journal_date=YYYY-MM-DD`** — refreshes and shows `narrations/narration<DATE>_state.json` (segment audio/clip/anchor readiness, blocking issues, full JSON).

## Internal structure

`server.py` was a 6,241-line single file through 2026-09-02; it's now a ~1,500-line
entrypoint (arg/env parsing, port probe, reload loop, `main()`) plus these modules
(see `ai-plans/pipeline-ui-server-split.md` for the full history — no
behavior/HTTP-contract change from the split, same routes and JSON):

- **`pipeline_ui/runtime.py`** — `srv()`, resolves the running `pipeline_ui.server`
  module whether it's running as `python pipeline_ui/server.py` (script mode,
  registered as `__main__`) or imported normally (`import pipeline_ui.server` /
  pytest). Every module below reads shared repo-root/path state off `server.py`
  through this at call time, so tests monkeypatching `pipeline_ui.server.<name>`
  keep working and `server.py` can import these modules without a circular-import
  error.
- **`pipeline_ui/jobs.py`** — `JobRegistry`, the shared background-job bookkeeping
  (thread start, TTL prune, optional dedup-by-key, status polling) used by the
  scene-anchor i2i, anchor-preview, and conversation-master-regen async jobs
  (the job-specific run functions and key helpers stay in `server.py`).
- **`pipeline_ui/paths.py`** — journal-date/repo-path helpers, run-report and
  episode-state payloads.
- **`pipeline_ui/youtube_view.py`** — YouTube upload-status payload and
  upload-from-preview action.
- **`pipeline_ui/prompt_preview.py`** — Phase 1 prompt-preview payload (Options
  tab "preview prompt").
- **`pipeline_ui/broll_view.py`** — b-roll thumbnails, suggest, and materialize.
- **`pipeline_ui/narration_view.py`** — video-cue and narration-segments payloads
  (Produce tab).
- **`pipeline_ui/library_view.py`** — latest-links and Library tab payloads
  (portraits, prompt packs, episode-diversity catalog) plus static-asset path
  safety helpers.
- **`pipeline_ui/narration_edit.py`** — options loader and narration-JSON edit
  endpoints (Produce tab editors).
- **`pipeline_ui/run_actions.py`** — TTS-only, video-reuse-anchors, reset-day,
  per-segment video regen, and assembly-only pipeline runs.
- **`pipeline_ui/handler.py`** — `class Handler`, HTTP request dispatch for all
  ~54 API routes plus static/preview file serving.

## Configuration

- **`pipeline_ui/options.json`** — defines labels, defaults, and how each control maps to `run-daily.py` arguments. Edit this file to add/remove options or change defaults. The file is gitignored (machine-local); copy from **`pipeline_ui/options.json.example`** if missing.
- **Environment (optional)**  
  - `PIPELINE_UI_HOST` (default `127.0.0.1`)  
  - `PIPELINE_UI_PORT` (default `8765`)  
  - `PIPELINE_UI_PYTHON` — override path to Python if needed (otherwise tries `.venv/Scripts/python.exe` / `.venv/bin/python`, then `sys.executable`).

## Security

The server binds to **127.0.0.1** only. Do not expose it to a network or run it on a shared machine without additional controls.
