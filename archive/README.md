# Archived pipeline artifacts (by expedition quarter)

Heavy outputs from completed work live here so the repo-root `output/`, `movie-images/`, and `audio/` trees stay smaller. **Narrations and journal XML are not archived**; the pipeline expects `narrations/narration{date_id}.json` and `journal-entries/` at the project root.

## Layout

Each quarter folder mirrors the repo structure:

```text
archive/{YYYY}-Q{n}/
  output/              # lewis_clark_{date_id}_video.mp4, *.manifest.json, extras (e.g. *_old.mp4)
  movie-images/{date_id}/
  audio/{date_id}/
```

**Per-day reset** (full fresh start for one date, including narration JSON):

```text
archive/reset_{date_id}_{YYYYMMDD-HHMMSS}/
  narrations/          # narration{date_id}.json, sidecars, timestamped backups
  audio/{date_id}/
  movie-images/{date_id}/
  output/              # any output/* file whose name contains date_id
  reset_manifest.json  # list of moved paths
```

Created by Pipeline UI **Reset day (archive artifacts)** on the Journal tab, or CLI `run-daily.py --date YYYY-MM-DD --reset-day` (archive then run) / `--reset-day-only` (archive and exit).

Calendar quarters use **expedition** dates (`date_id` = `YYYYMMDD`):

| Quarter | Months   |
|---------|----------|
| Q1      | Jan–Mar  |
| Q2      | Apr–Jun  |
| Q3      | Jul–Sep  |
| Q4      | Oct–Dec  |

Example: August–September 1803 → `1803-Q3`; November–December 1803 → `1803-Q4`.

## When to archive

Archive a quarter **only after the following quarter is nearly finished** in your production run (so you are unlikely to need those paths under the repo root for day-to-day work). For example, with March 1804 in progress (end of 1804 Q1), it is reasonable to archive **1803 Q3 and Q4** (and separately, when 1804 Q2 is almost done, archive **1804 Q1**).

## Archiving a future quarter

From the project root (venv optional):

```powershell
.\.venv\Scripts\python.exe scripts\archive_quarter.py 1804 1 --dry-run
.\.venv\Scripts\python.exe scripts\archive_quarter.py 1804 1
```

Replace `1804` with the expedition year and `1` with quarter `1`–`4`. Inspect `--dry-run` output, then run without it. The script moves any `output` files whose names contain an 8-digit `date_id` in that quarter, plus `movie-images/{date_id}/` and `audio/{date_id}/` directories.

## Restoring one episode (one `date_id`)

To re-run assembly, video, or TTS for a single date, put the three locations back at the repo root (names must match what the scripts expect):

1. `archive/{YYYY}-Q{n}/output/lewis_clark_{date_id}_video.mp4` (and its `lewis_clark_{date_id}_video.manifest.json` if present) → `output/`
2. `archive/{YYYY}-Q{n}/movie-images/{date_id}/` → `movie-images/{date_id}/`
3. `archive/{YYYY}-Q{n}/audio/{date_id}/` → `audio/{date_id}/`

Move any other sibling files for that `date_id` in `archive/.../output/` (e.g. `*_old.mp4`, `*_original.mp4`) if you need them. You do **not** need to move `narrations/` files unless you deleted them separately.

## YouTube stats and manifests

`youtube_stats.py` and `youtube_playlist.py` discover uploads via `output/*.manifest.json`. After archiving, manifests for those dates no longer sit in `output/`, so those tools will not list them until you copy the relevant `.manifest.json` files back into `output/` (the MP4 can stay in `archive/` if you only need stats metadata, but paths inside the JSON may still reference the original filename).

## Initial archive (this repo)

- **2026-03-28:** Archived **1803-Q3** and **1803-Q4** (all August–September and November–December 1803 artifacts that were present under `output/`, `movie-images/`, and `audio/`).
