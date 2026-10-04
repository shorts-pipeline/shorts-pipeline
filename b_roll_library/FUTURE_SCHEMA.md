# Future: optional narration JSON / ffmpeg overlay (deferred)

The current pipeline assembles **one video file per narration segment** and concatenates them in `videos-mp3-to-movie.py`. True **second-track** B-roll (picture-in-picture, crossfades, or simultaneous stock under dialogue) is **not** implemented.

If you outgrow “replace `NN.mp4`” or NLE-only workflows, plausible extensions are:

## Optional fields in merged narration JSON (sketch)

- Per segment or global: `b_roll`: `{ "library_id": "...", "mode": "replace" | "overlay", "opacity": 0.0-1.0, "trim_start_seconds": 0 }` aligned with `b_roll_library/manifest.json` `id` values.
- Assembly would resolve `library_id` → file path, then either **replace** the segment clip (same as manual swap) or call **ffmpeg** `overlay` / `xfade` with explicit geometry and timing—**significantly** more complex than the current concat + map mask path.

## FFmpeg considerations

- Second input streams, `overlay`, `shortest=1`, and careful alignment to `durations.json` slices.
- Prefer prototyping in a one-off script before wiring into `videos-mp3-to-movie.py`.

Treat this document as a **design placeholder** until Phase 1–2 manual workflows prove the need.
