# Ambient library (optional beds for `videos-mp3-to-movie.py --ambient`)

Place **loop-friendly** WAV or MP3 files here. Paths and tags are defined in [`manifest.json`](manifest.json).

## Loudness

Normalize loops to a consistent integrated loudness (e.g. **about −20 to −24 LUFS**) before mixing. Final level is further reduced in the pipeline via `ambience_level_gain_db` (`light` / `moderate`) so narration stays primary.

## Default placeholder

`prairie_wind.wav` may be a short **silence** placeholder generated for wiring tests. Replace it with a real wind loop for production.

## Per-segment tags

Phase 2 can set `ambient_tag` on each `scene_plan` entry (merged onto `narration_script[]`). Assembly builds one continuous ambient bed by concatenating per-segment clips (each looped to that segment’s duration). Episode-level `audio_design.ambient_tag` applies when a segment omits `ambient_tag`.

## Credits

Document sources and licenses in [`CREDITS-ambient.md`](CREDITS-ambient.md). YouTube description can cite required attribution.
