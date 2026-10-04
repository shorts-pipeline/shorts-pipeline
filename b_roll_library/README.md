# B-roll library (manual-first)

This folder holds **tagged clip metadata** (`manifest.json`) and optional **copied MP4s** under `clips/`. The main **video assembly** step does not read this folder yet; use it to organize reusable shots and provenance before NLE edits or segment swaps. The **Pipeline UI** can append entries directly into `manifest.json` (see below).

## Manual cut (NLE)

1. Build the episode as usual through audio (`audio/<date_id>/final.mp3`) and segment durations (`audio/<date_id>/durations.json`).
2. Import the final mix (or stems) as the master audio in DaVinci Resolve, Premiere, etc.
3. Align B-roll video to **segment boundaries** using `durations.json` order: segment 1 = first block after any intro row, then 2, 3, …
4. Export a single master when satisfied. Aspect ratio should match your pipeline target (e.g. 9:16 Shorts vs 16:9).

## Hybrid: swap `NN.mp4` and re-run assembly only

1. Place your replacement video for segment **N** at `movie-images/<date_id>/NN.mp4` (same resolution and codec expectations as other segment clips: H.264, pipeline frame size).
2. Re-run assembly only, for example:

   ```text
   python videos-mp3-to-movie.py <date_id>
   ```

   Use the same flags you normally use (e.g. `--shorts`, `--ambient`) so mux matches the prior run.

Assembly stretches each clip to the narration slice for that segment; if your replacement is much shorter or longer in **content**, trim or loop in an editor **before** swapping the file, or accept the ffmpeg `setpts` stretch behavior.

## Manifest

- **`manifest.json`**: `clips[]` entries with `id`, `file` (relative to `b_roll_library/`), `tags`, optional `duration_seconds`, and `source` (pipeline `date_id` / `segment_index` / `vendor`, or stock license fields you add).
- **Pipeline UI**: With `python pipeline_ui/server.py` running, open **Latest video → Preview**. When the player is **paused** on a narrative segment (not the intro), **Add segment to B-roll library** is enabled; it calls the UI server, which appends a new `clips[]` row to this manifest (deduped by `source.date_id` + `source.segment_index`), derives `tags` from that segment’s `video_prompt`, and requires `movie-images/<date_id>/<NN>.mp4` to exist. It does **not** copy the file into `clips/`; add a copy or symlink there yourself if you want the `file` path to resolve.
- **Pipeline UI thumbnails**: Optional `thumbnail_movie_rel` (or `preview_movie_rel`) — repo-relative path to any MP4 used only for preview when the canonical paths are wrong or missing. If the library file and `movie-images/<source.date_id>/<NN>.mp4` are both absent, the UI falls back to `movie-images/<episode_date_you_opened>/<NN>.mp4` **for the thumbnail only** (same `NN` as `source.segment_index`); the summary still treats the canonical source as missing until you add files, symlinks, or fix `source` in the manifest.
- **`clips/`**: Drop MP4s here (or symlink). Paths in the manifest are relative to `b_roll_library/`.

## Example B-roll–friendly segments

See `B_ROLL_FRIENDLY_SEGMENTS.md` for episode `18040507` and segment indices **1** and **7**.

## Prompt theme mining

Run from the repo root (venv recommended):

```text
python scripts/b_roll_mine_prompt_themes.py --out-dir b_roll_library
```

This writes `prompt_theme_report.csv` and `prompt_mine_report.json` under `--out-dir`. Edit `bucket_keywords.json` to tune labels.
