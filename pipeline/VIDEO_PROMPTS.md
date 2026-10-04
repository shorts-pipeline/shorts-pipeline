## Video prompt shape (Phase 2 → vendors)

Phase 2 (`pipeline/narration_phase2.py`) loads its system instructions from `prompt_packs/<prompt_pack>/phase2_system.txt` (see `pipeline/prompt_pack_text.py`; profile `prompt_pack` from `generate-narration-two-phase.py`). It produces a `video_prompt` per segment, which is then wrapped by `video_vendors/__init__.py` (`build_prompts`) before being sent to vendors (Google Veo, fal/Wan, Sora). Shared vendor fragments live under `prompt_packs/_shared/` (e.g. `video_safety_suffix.txt`, FAL i2i strings).

A final per-segment string from `build_prompts` is assembled in this order:

1. **Optional fal-only markers** (not sent to the Wan API; stripped in `video_vendors/fal.py`):
   - When `vendor == "fal"`, the segment has `reference_character_id`, and `character-portraits/{id}.png|.jpg|.jpeg` exists:
     - `FAL_IMAGE_CHAR={id} ` — portrait drives image-reference generation.
     - `FAL_SCENE_ANCHOR=1 ` — **always** added with the portrait path so fal runs **image-to-image** (scene opening frame from portrait + prompt) then **image-to-video**, instead of starting from a transparent cutout on black.
   - New narrations from `generate-narration-two-phase.py` also set `fal_scene_anchor_openings: true` on the JSON for clarity; video behavior is determined by the markers above when a portrait exists. To skip portrait anchoring for a segment, remove `reference_character_id` (or the portrait file) and regenerate or edit the narration.
   - **Per-segment `opening_frame`** (optional on `narration_script` items): When present, FAL’s **image-to-image** step uses that string plus the same **opening period-shot line** as `build_prompts` (year from `date_id`, location/mood from `scene_spine`). When absent, i2i uses the full segment prompt (minus tails). Google/Sora ignore this field.

2. **Opening period-shot line** (`_opening_period_shot_prefix` + per-segment `resolve_core_location_for_segment`):
   - Short shard only: **era** (`early 1800's`), **place** (block `place_label`, `core_location_override`, or spine default), optional **mood** from `scene_spine.visual_mood`, plus optional **lighting** / **color grade** shards (item 3/4 of the FAL video-quality follow-ups).
   - **Not injected here:** calendar **ROUTE ATTITUDE** (`pipeline/journey_river_context.py` → Phase 2 user message only), `persistent_anchors`, or `Continuity:` suffixes—those belong in each segment's Phase 2 `video_prompt` so forest/hunt clips are not forced to show rowers.
   - Example: `Photorealistic period scene set in early 1800's, forest margin behind the same camp, overall mood grounded. `

3. **No-on-screen-text prefix** (`NO_ON_SCREEN_TEXT_PREFIX` in `video_vendors/prompt_parts.py`, loaded from `prompt_packs/_shared/video_no_on_screen_text_prefix.txt`):
   - `No readable text in frame; pure imagery only. ` — the full sentence, placed right after the opening line (a **prefix** in the highest-attention position, not a compressed suffix near the end — a prior version of this doc had this backwards).

4. **Visual style** (from `visual_style`):
   - Styles come from `config/narration_config.json` (`visual_themes`). When present, **only** `visual_style.description` is used (the short Title Case `name` is omitted). Prefer conversational descriptions in config (avoid label-like `thing: detail` phrasing):
     - `{description}. `

5. **Optional character snippet** (fal only; from `reference_character_id` + `narration_characters`):
   - **Portrait exists:** for **humans**, no extra character snippet — identity comes from the portrait and i2i. For **animals** (e.g. Seaman), `Keep this breed and build: {physical_description}. ` is prepended.
   - **No portrait file:** animals get `Keep this breed and build: …`; humans get `{physical_description}. ` only.
   - **Google / Sora:** no character markers or this prefix.

6. **Motion sentence + core `video_prompt`** (Phase 2 synthesis):
   - `_motion_cue_prose(stage_direction)` maps motion tokens to one short sentence (e.g. `Use tracking camera movement. `).
   - `_synthesize_video_prompt(visual_strategy)` runs `strip_unrenderable_language` (`pipeline/video_prompt_language_stripper.py`) over `primary_visual`, `secondary_elements`, and `closing_frame` — a deterministic, best-effort cleanup of sound/abstraction language rule 2b already forbids but the model sometimes emits anyway (see item 6 of the FAL video-quality follow-ups). It then joins the cleaned `primary_visual` + `secondary_elements`, and appends **`closing_frame`** (required for non-`talking_head` segments) as a trailing `By the end of the shot, {closing_frame}.` clause — gives the generative model an explicit begin→end trajectory instead of a static situation. `closing_frame` is Phase 2 input only; it is folded into `video_prompt` at merge time and does not appear as its own key on `narration_script` items (unlike `opening_frame`, which FAL's i2i step also consumes directly).

7. **Safety suffix** (vendor-agnostic):
   - ` Family-friendly.` (FAL’s `negative_prompt` still lists nudity explicitly.)

### Example: `narrations/narration18040413.json`, segment 1

From `build_prompts("18040413")` after any fal markers — note the no-on-screen-text sentence right after the opening line, not at the end:

```text
Photorealistic period scene set in early 1800's, Camp near St. Louis, Missouri River, overall mood grounded. No readable text in frame; pure imagery only. Slow cinema pacing with very little motion in the frame, long-held shots, and tiny shifts in light, smoke, water, or weather carrying the moment. Use a slow push-in. Medium shot of the boat gently cutting through the water, with William Clark visible near the bow, his figure steady against the soft movement of the river. The camp on the riverbank with tents barely visible through the trees, a few men watching the boat's approach. Family-friendly.
```

**fal** with portrait + `reference_character_id` `clark`: same text, with `FAL_IMAGE_CHAR=clark FAL_SCENE_ANCHOR=1 ` prefixed (stripped before the Wan API).

**FAL i2i** (scene anchor with `opening_frame`): portrait instructions, then the same opening period-shot line, then ` No readable text in frame; pure imagery only.`, then the opening-frame moment (no duplicate anti-text block elsewhere in the string).

### FAL scene-anchor i2i — human hardening (`video_vendors/fal.py`)

When a segment uses **portrait scene anchoring** (`FAL_SCENE_ANCHOR=1`) and `reference_character_id` is a **human** (not classified as an animal by `_is_animal_character` in `video_vendors/__init__.py`), the **image-to-image** call gets extra constraints:

- **Prompt suffix:** asks for exactly **one** person with **two arms and two legs**, correct joints, **no extra or duplicated limbs or hands**; hands **empty** or holding **at most one** clear object, and to **simplify** if the pose would overload the hands (multiple props, crowded hands, juggling tools).
- **Negative prompt:** the usual shared video negatives **plus** anatomy-oriented terms (extra limbs/hands, duplicated hands, third hand, too many / six fingers, malformed or fused fingers, crowded hands, mutation, conjoined duplicate figures, mirrored duplicate, body duplication). The fal schema caps **`negative_prompt` at 500 characters**; the client **truncates** to that limit.
- **Seed (optional):** Set **`FAL_SCENE_ANCHOR_I2I_SEED`** to an integer for reproducible anchor i2i. Wan’s **image-to-image** endpoint does **not** expose strength / denoise controls in the published schema—identity drift is controlled mainly by prompt, negative prompt, and seed.

**Animals** (e.g. Seaman): unchanged — no human anatomy suffix and no extra human-specific negatives, so breed and leg count are not contradicted.

**Phase 2 (`opening_frame` discipline):** When character anchors apply, Phase 2 should write **one** simple t=0 pose in `opening_frame`—prefer singular hand language, avoid stacking multiple props or actions in the still; busier beats belong in `primary_visual` / `video_prompt`. See `pipeline/narration_phase2.py` rule 2 **opening_frame discipline**.

**Phase 2 (boats + route):** Rule 2 **Boats** and **ROUTE ATTITUDE** (from `journey_river_context` via Phase 2 user message) apply **only** on segments that show active boating—never pasted into every vendor string. **Episode visual world** (`episode_visual_world`, merged `scene_spine`) is planning metadata; block-scoped anchors go into `primary_visual` for that block only.

### Design goals

- **Vendor-agnostic base:** Opening line, style, motion, and main action are shared; fal adds markers and optional animal breed line as above.
- **No-on-screen-text sentence right after the opening line** puts the anti-lettering instruction in the highest-attention position of fal’s 800-char cap, rather than burying it at the end.
- **Most important details early:** Critical scene content stays left in the vendor character budget.
- **Human specificity:** Phase 2 rules forbid vague “people/visitors” and require concrete roles and actions.
- **Opening frame (FAL i2i):** Optional per-segment `opening_frame` disambiguates the scene-anchor still from the motion in `video_prompt`. Keep it a **single simple pose**; avoid overloaded “hands” / multi-prop stills (Phase 2 rule: **opening_frame discipline**).
- **Closing frame (trajectory):** Required per-segment `closing_frame` (non-`talking_head`) states the visible end state of the clip so `video_prompt` describes a change over the ~5–10s take, not a still situation (Phase 2 rule: **closing_frame**).
- **Cinematic clarity:** Every segment is one coherent shot with explicit framing, motion, pacing, and period-accurate materials/lighting.
