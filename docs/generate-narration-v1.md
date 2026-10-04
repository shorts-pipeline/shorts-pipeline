# Narration v1 (Legacy) — Archived Code

This document preserves the original single-phase narration generator (`generate-narration.py`) for reference. The pipeline now uses **two-phase narration** only (`generate-narration-two-phase.py`), which produces `narration_version: "2.0"` JSON with optional mid-episode map insertions.

The v1 script produced `narration_version: "1.0"` with a single OpenAI call, one schema (title, scene_spine, narration_script with segment_type, stage_direction, narration, video_prompt), and no map_insertions. The prompts below are useful if you want to compare or reuse v1 phrasing in future prompt design.

---

## System prompt (build_system_prompt)

- Documentary scriptwriter + cinematic visual designer for Lewis and Clark journals.
- **Historical entity mapping**: Expand "the dog", "the boat", "the men" using internal knowledge (Seaman, York, Keelboat, etc.).
- **Chronological asset integrity**: Use date_id; pirogue in 1803, dugouts/horses later; Sacagawea/Charbonneau only after Nov 1804; Seaman throughout; period trade items (blue beads, vermillion, lead, Peace Medals).
- **JSON structure**: `title`, `scene_spine` (core_location, environmental_elements, lighting_progression, color_palette, visual_mood), `narration_script` array of `{ segment_type, stage_direction, narration, video_prompt }`.
- **General**: Valid JSON only, no markdown, never truncate; under token pressure shorten earlier segments to complete the final one.
- **Cinematic specificity**: "Tactile Minimalism"; texture (pitted iron, salt-crusted leather, etc.); lighting behavior; 70/30 rule (70% macro/medium, 30% wide).
- **Scene spine**: Defines stable visual world; geography, terrain, persistent elements, lighting progression, color palette, mood; all video_prompts consistent with it.
- **Segment structure**: Distinct journal moments; early = setting/motion, middle = observation/challenge/discovery, **final = reflection**.
- **Narration style**: Calm, reflective, TTS-friendly; short–medium sentences, commas and breaks for rhythm.
- **Ending (mandatory)**: Final segment_type = 'reflection'; thematic meaning, no new action, elevation + forward motion/uncertainty/destiny; deliberate final two sentences; slightly slower pacing.
- **Stage direction**: Bracketed cue only, e.g. [drift], [tracking], [slow_push], [static].
- **Video prompt**: Longer than narration; camera movement, lighting, texture, weather, depth, motion; final segment signals closure (lower light, horizon, embers, pullback, etc.).
- **Token management**: Reserve space for full final reflective segment; never end abruptly.
- Optional blocks: **Visual treatment** (stylization from config), **Thematic emphasis** (focus on a detail, narration clarifies, video_prompt isolates it).

---

## User prompt (build_user_prompt)

- "Generate an 8–12 segment narration JSON based on the journal entry for {date_id}."
- **Length**: narration 15–200 words/segment (aim 60–120), video_prompt 100–300 (aim 140–220).
- **Token-pressure**: Shorten video_prompt first; keep narration in range and never omit required fields.
- **Director's note**: Use full historical knowledge; expand journal mentions of the dog, boat, men, camp into vivid, accurate cinematic descriptions per system prompt.
- Optional: apply **visual style** (name + description) to all segments.
- Optional: **Focus topic** — first 2 segments third-person, middle 6–10 first-person deep dive (1803 vs 2026), video_prompt stays in 1800s, final segment third-person and legacy.
- Then the raw **entry text**.

---

## Full Python source (as of archival)

The complete v1 script is preserved in git history. Key pieces for prompt review:

- **build_system_prompt(config)** — builds the system prompt from the rules above (see repo history for exact string).
- **build_user_prompt(entry_text, date_id, style, config, focus_topic)** — journal date, length rules, director's note, optional style/focus, then entry text.
- **generate_narration_json(...)** — single OpenAI call, retries with self-repair, sets `narration_version: "1.0"`, applies prompt_replacements and video_prompt_replacements.
- **Output**: `narration{date_id}.json` and optionally `narration{date_id}.meta.json` (usage).

To view or restore the full script, use git to show the file from the commit before it was replaced by the two-phase stub.
