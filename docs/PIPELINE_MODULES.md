# Pipeline package (`pipeline/`)

Import from the repo root: `from pipeline.narration_common import …`. Entry-point scripts at the root stay thin; behavior lives here.

## Narration generation

| Module | Role |
|--------|------|
| `narration_common.py` | Config load, XML extraction, JSON cleaning, schema validation, visual style |
| `narration_phase1.py` | Phase 1 system/user prompts, `run_phase1`, pack selection (`lewis_clark_dialogue`, `lewis_clark_long_conversation`) |
| `narration_phase1_dialogue.py` | phase1-dialogue pass: polish `dialogue[].text` only |
| `narration_phase2.py` | Phase 2 visual plan, merge into v2.0 `narration_script` |
| `narration_utils.py` | Load narration JSON, v2 helpers, `dialogue_mode` / `long_conversation_mode` inference |
| `narration_visual_mode.py` | Canonical `visual_mode` rules (`b_roll` vs `talking_head`; standard vs long-conversation limits) |
| `prompt_pack_text.py` / `prompt_pack_metadata.py` | Load pack files, fingerprints, `prompt_pack_lineage` |
| `phase1_prompt_prepare.py` | Phase 1 prompt assembly helpers |
| `tension_arcs.py` | Expedition tension cues (`config/narration_tension-arcs.json`) |
| `week_arc.py` | Variable-length narrative-bundle plans (`config/week_arc.json`, `state/week_arcs/`) |
| `recent_episode_diversity.py` | Soft diversity hints from prior episodes (**Lewis & Clark prompt packs only**; `pack.json` → `episode_diversity`) |
| `phase1_title_hook.py` | Title stem uniqueness + short post-Phase-1 title rewrite (generic/reused titles only; does not retry narration) |
| `episode_diversity_audit.py` | Offline LLM audit → cached dynamic hints; checker gating; **`scripts/refresh_episode_diversity_audit.py`** |
| `pipeline_profile.py` | Profile JSON (`config/profiles/*.json`) |

## Audio (TTS)

| Module | Role |
|--------|------|
| `tts_stage_directions.py` | Strip non-spoken parentheticals / stage lines; map to silence gaps (`narration-to-mp3.py`) |
| `tts_speaker_voice.py` | Per-speaker voice routing for dialogue segments |
| `tts_text_normalize.py` | Spoken-text normalization (e.g. St. → Saint) |
| `fal_minimax_tts.py` | Optional FAL MiniMax TTS paths |

Config: `config/narration_config.json` → **`tts_stage_direction_pause_seconds`** (default `0.75`).

## Video / assembly helpers

| Module | Role |
|--------|------|
| `talking_head_shots.py` | Resolve `talking_head_shot_id` / archetypes (`config/talking_head_shots.json`) |
| `map_display_policy.py` | At most one route-map graphic in final video |
| `ambient_audio.py` | Ambient bed mixing when `run-daily --ambient` |
| `run_report.py` | FAL run report schema (`movie-images/<date_id>/run_report.json`) |
| `automation_gates.py` | Preflight/postflight for `run-daily.py` |
| `episode_state.py` | Per-episode pipeline readiness sidecar (`narrations/narration<DATE>_state.json`) |
| `segment_plan.py` | Canonical `SegmentPlan` / `EpisodeSegmentPlans` from narration JSON |
| `day_reset.py` | Archive narrations + audio + movie-images + output for one `date_id` (`archive/reset_*`) |
| `scene_anchor_quality.py` | Post-i2i QC for FAL scene anchors (seam composites + uniform white studio fields); one retry with `core_location_override`; else `SceneAnchorQualityError` |
| `fal_content_policy_rewrite.py` | After FAL `content_policy_violation` (and local sanitizer retry), OpenAI rewrites `opening_frame` / `video_prompt`, writes them to the merged narration JSON, then FAL retries |
| `output_naming.py` | Assembled MP4 filename from profile `output_prefix` |
| `portrait_composite.py` | Composite portrait paths for talking-head / FAL i2i |
| `youtube_srt.py` | Build timed English SRT from narration + `audio/<date_id>/durations.json` for YouTube captions |

## When you change behavior

- **Prompt packs only:** bump `prompt_packs/<id>/pack.json` `version`; see `prompt_packs/README.md` and `docs/AGENTS-pipeline.md` (Prompt pack versioning).
- **Visual / talking-head rules:** edit `narration_visual_mode.py` and matching tests under `tests/test_narration_visual_mode.py`, `tests/test_phase1_long_conversation_requirements.py`.
- **TTS pauses / stage directions:** `tts_stage_directions.py` + `tests/test_tts_stage_directions.py`.
- **CLI flags:** update `generate-narration-two-phase.py`, `run-daily.py`, and `docs/AGENTS-pipeline.md` (Dialogue modes section).

See also **`tests/README.md`** for a quick test map.
