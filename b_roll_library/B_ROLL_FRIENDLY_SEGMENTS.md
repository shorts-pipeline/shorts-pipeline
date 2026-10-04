# Example episode: B-roll–friendly segments

This file satisfies the “define segments” step for **one** merged narration: pick segments where the voice-over describes **place, light, weather, or texture** so the picture can be **environment-first** (wide river, camp, prairie) rather than a named leader in close-up.

## Chosen episode

| Field | Value |
|--------|--------|
| **date_id** | `18040507` |
| **Title** | Final Preparations (from merged narration `title`) |
| **Journal date** | May 7, 1804 |

## Recommended segment indices (environment-first)

| segment_index | Rationale |
|---------------|-----------|
| **1** | VO: expedition at the edge of the known world; riverbank, sky, water. `video_prompt` is river/group wide; **no** `reference_character_id`. Strong B-roll candidate. |
| **7** | VO: sunset, readiness, civilization fading; `video_prompt` is **wide** silhouettes on the river. **no** `reference_character_id`. Strong closing B-roll. |

### Optional (mixed)

| segment_index | Note |
|---------------|------|
| **4** | River and banks read as landscape, but VO names Ordway and prompt uses `reference_character_id` **ordway**. Use for **river texture** B-roll if you accept a figure in frame or replace with stock. |

## How this ties to the pipeline

- Phase 1 steers copy toward **visual subject vs named observer** when the journal is about seeing the landscape; segments **1** and **7** align with that for this file.
- The pipeline still outputs **one** MP4 per segment (`movie-images/18040507/01.mp4` … `07.mp4`). B-roll means **replacing** a segment file, **editing in an NLE**, or a future automated overlay—see `README.md` and `FUTURE_SCHEMA.md`.
