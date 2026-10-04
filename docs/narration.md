### Narration pipeline overview

This project uses a **two-phase narration pipeline**:

- **Phase 1** (text-only narration + metadata)
- **Phase 2** (visual plan / `scene_plan` + `video_metadata` + `audio_design`)

The final pipeline JSON is used by:

- `narration-to-mp3.py` (audio)
- `narration-to-video.py` + `video_vendors/` (clips)

#### Key modules and scripts

- **Phase 1 library**: `pipeline/narration_phase1.py`
  - `run_phase1(entry_text, date_id, model="gpt-4o", focus_topic=None, max_retries=...) -> dict`
  - Prompt helpers and rules:
    - Tension / urgency: `_resolve_tension_context`, `_tension_weight_to_urgency`, `_urgency_instruction`.
    - System prompt: `build_phase1_system_prompt()`.
    - User prompt: `build_phase1_user_prompt(...)`.
    - Validation: `_validate_phase1(...)`.
- **Phase 2 library**: `pipeline/narration_phase2.py`
  - `run_phase2(phase1, date_id, model="gpt-4o", style=None, focus_topic=None, max_retries=...) -> dict`
  - `merge_phase1_phase2(phase1, phase2, style=None, character_config=None, date_id="") -> (merged, used_counts)`
  - Prompt helpers and rules:
    - System prompt: `build_phase2_system_prompt(focus_topic=None)`.
    - User prompt: `build_phase2_user_prompt(...)`.
    - Validation: `_validate_phase2(...)`.
    - Visual synthesis: `_synthesize_video_prompt(...)`, `_synthesize_stage_direction(...)`.
    - Map usage, gear/bodies, closure rules all live inside the Phase 2 system prompt.
- **Full pipeline / gates / FAL talking-head operator docs**: **docs/AGENTS-pipeline.md** — sections **Automation gates**, **Talking-head, FAL video, run reports, and UI**, **Prompt pack versioning** (covers `run-daily.py` flags, `run_report.json`, fallback model, shot library, and related tests).
- **Top-level CLI**: `generate-narration-two-phase.py`
  - Thin orchestrator:
    - Reads journal XML (`pipeline.narration_common.extract_entry_text`).
    - Optionally calls the theme engine (`theme_engine.theme_selector.recommend/record_narration`).
    - Calls `run_phase1(...)`, then `run_phase2(...)`.
    - Calls `merge_phase1_phase2(...)`.
    - Applies prompt replacements and conditional video cues (`pipeline.narration_common.apply_prompt_replacements`; cues in `config/video_prompt_cues.json`, logic in `pipeline/video_prompt_cues.py`).
    - Validates the final narration JSON (`pipeline.narration_common.validate_narration_schema`) and writes `narrations/narration<date_id>.json`.
  - **`--dialogue`**: uses `prompt_packs/lewis_clark_dialogue/`; Phase 1 emits per-segment `dialogue: [{ speaker_id, text }]`. Merge copies `dialogue` onto `narration_script`; `narration-to-mp3.py` uses `voice_by_speaker` in `config/narration_config.json` for multi-voice OpenAI TTS per segment.

#### Where to change what

- **Prompt pack text** (primary copy for Phase 1 / Phase 2 system prompts): `prompt_packs/<pack>/phase1_system.txt`, `phase2_system.txt`, optional `phase1_user.txt`, plus **`pack.json`** for version metadata. After regeneration, merged narration JSON includes **`prompt_pack_lineage`** (fingerprints + manifests). When you change prompts, bump `pack.json` `version` and see **docs/AGENTS-pipeline.md** — section **Prompt pack versioning** for the full checklist and `scripts/prompt_ab_harness.py` usage.
- **Change Phase 1 behavior** (structure of the narration JSON, tension, tone register, closure text):
  - Edit `pipeline/narration_phase1.py`:
    - System prompt text → `build_phase1_system_prompt`.
    - Tension weighting or urgency → `_resolve_tension_context`, `_tension_weight_to_urgency`, `_urgency_instruction`.
    - User prompt scaffolding → `build_phase1_user_prompt`.
- **Change Phase 2 behavior** (visual rules, “boots shouldn’t walk alone”, map usage, pacing):
  - Edit `pipeline/narration_phase2.py`:
    - System prompt text → `build_phase2_system_prompt` (gear & bodies, map usage, pacing rules).
    - User prompt scaffolding → `build_phase2_user_prompt`.
    - Merge / injection behavior → `merge_phase1_phase2` (character injection, scene spine, etc.).
- **Change config / styles / replacements**:
  - Edit `config/narration_config.json` and the loader in `pipeline.narration_common.load_narration_config`.

