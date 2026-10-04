# Presentation notes: Lewis & Clark pipeline

Running doc of **big decisions** for a future talk. Add bullets as the project evolves; use git history to anchor dates and diffs when building the deck.

---

## Pipeline shape

- **Linear stages, date-scoped.** One journal entry (one date) → one narration JSON → one audio file → one set of clips → one final video. Simplifies scheduling (e.g. “today’s anniversary”) and keeps artifacts easy to find (`narrations/narration18030830.json`, `audio/18030830/`, etc.).
- **Date ID everywhere.** `18030830` (YYYYMMDD, no dashes) is the single key for that episode across all steps. XML files use `YYYY-MM-DD.xml`; scripts accept the 8-digit id for consistency.

---

## Two-phase narration (v2)

- **Phase 1: narration + metadata only.** One model call produces the story (segments, title, narrative spine, closing type) with no visual descriptions. Keeps the “what we’re saying” separate from “what we’re showing.”
- **Phase 2: visual plan.** Second call takes Phase 1 output plus a chosen visual style and produces scene plan, video_metadata, map_insertions, audio_design. Lets us change visual strategy without re-writing the script.
- **Merge step.** Combined output is a single v2 narration JSON (same shape downstream tools expect: title, scene_spine, narration_script, visual_style, plus map_insertions, etc.). One canonical file for the rest of the pipeline.
- **Why not one big prompt?** Separation gave better control over tone vs. visuals, easier iteration on “scene spine” and mid-episode map placement, and a clear place to plug in theme_engine (focus topic) and narration_config (visual themes).
- **Tone register:** Phase 1 chooses `tone_register` (light | warm | reflective | tense | somber) from the emotional character of the day and focuses most segments in that register. Not every day is grave or dramatic; the prompt allows curiosity, satisfaction, and quiet appreciation when appropriate.
- **Refactor: split into libraries.** As the script grew, we moved Phase 1 and Phase 2 into `pipeline/narration_phase1.py` and `pipeline/narration_phase2.py`, leaving `generate-narration-two-phase.py` as a thin CLI/orchestrator. This makes behavior changes (tension rules, closure wording, visual rules) safer and easier to test.

---

## Theme engine (reducing repetitiveness)

- **Problem:** Consecutive journal days often cover similar ground; a single “summarize the day” prompt yields repetitive episodes.
- **Decision:** Compare current journal to the **last 14 narrations** (summaries + key topics). If similarity is above a threshold, recommend a **focus topic** and inject “Focus this episode on &lt;topic&gt; in depth” into the prompt. Record the new narration in state and trim to 14.
- **State:** `theme_engine/state.json` holds `entries` (trimmed to 14 after each successful record), `used_focus_topics`, and `focus_topic_queue` so we don’t repeat the same focus too soon. Optional: disable automatic focus topic with `--no-focus-topic` (legacy: `--no-theme-selector`).
- **Embeddings vs. disk:** **Recommend** similarity is judged against the **last 14 narration JSON files on disk** (and `theme_engine/embeddings.json` when present)—not only what’s already in `state.entries`, so state can lag without changing the recommendation. **Record** / backfill paths use their own threshold when embeddings are off.
- **Closest peer:** Each recorded entry can include **`closest_peer_date_id`** (which other row in the sliding window had the highest embedding similarity). Useful for debugging “why did we get this focus topic?”
- **Narration artifact:** When a focus topic is applied, the narration JSON may include **`theme_selector_decision`** (closest prior `date_id`, similarity score, threshold).
- **Audit CLI:** `python theme_engine/theme_selector.py <date_id> verify` recomputes and prints the closest peer for a date without mutating state—handy after manual state edits or when `entries` lags `narrations/`.
- **Tuning (code as source of truth):** `theme_engine/theme_selector.py` defines window size (`MAX_ENTRIES = 14`), an embedding-based threshold for **recommend**, and a separate threshold for **record** / backfill. Full design: `theme_engine/README.md`; state field semantics: `docs/theme_engine.md`.

---

## Narration tension (expedition arcs)

- **Goal:** Episodes leading up to a major event should hint at what’s coming instead of treating every day in isolation. Build anticipation; on the big day, pay it off; in the days after, shift tone appropriately.
- **Mechanism:** `config/narration_tension-arcs.json` defines **expedition milestones** (each with `start_date`, `end_date` and three prompt strings). For each journal date, the two-phase generator looks up which arc (if any) applies and injects the matching hint into the Phase 1 prompt:
  - **approach_prompt** — when the date falls *before* the milestone end (e.g. “Subtle signs of illness are spreading” before Floyd’s death; “Winter is advancing… stress urgency in finding shelter” before Mandan). These are the “hints of what was to come.”
  - **arrival_release_prompt** — on the milestone date itself (e.g. “Sergeant Charles Floyd has died — the expedition’s only fatality”).
  - **post_event_shift_prompt** — in the few days after the milestone (e.g. “Infuse the following days with quiet sobriety”).
- **Tension weight:** For each date inside a milestone arc, we compute a numeric weight (0–1) based on distance to the event: tension ramps up from a low floor as they approach the milestone, peaks on the event date, then decays over the following days. The Phase 1 prompt passes this as “TENSION WEIGHT (0–1 scale)” so the model knows how strongly to lean into the arc.
- **Result:** A viewer binging in order gets a through-line: earlier episodes foreshadow major events; the event episode lands with weight; the next episodes feel like aftermath. We don’t name the future explicitly in approach episodes—we nudge tone and emphasis so the payoff feels earned, with the weight controlling how strong that nudge is.

---

## Pluggable video vendors

- **Three backends:** Sora (Playwright → ChatGPT Sora UI, no API key), Google (Veo API), fal (fal.ai Wan 2.5). Same narration JSON and segment list; each vendor gets the same `video_prompt` (with optional visual_style prefix).
- **Why multiple?** Cost, availability, and quality differ. Sora is free but UI-bound; Google and fal are API-based with different rate limits and pricing. Default in run-daily is fal; users can pass `--vendor google` or `--vendor sora`.
- **Constraint:** Video prompts are truncated per vendor (e.g. 500 chars Google, 800 fal). We put the most important visual detail at the start of each prompt.
- **Parallel clip jobs (Google / fal):** `narration-to-video.py` requests **up to 10** concurrent segment API jobs; on failure it **retries with concurrency 1**. Sora remains single-job / UI-driven.
- **Prompt assembly:** Each segment prompt is assembled as `Photorealistic period scene set in early 1800's, {scene_spine location}` (with optional `overall mood {visual_mood}` and lighting/color-grade shards) + `No readable text in frame; pure imagery only.` (full sentence, placed here in the highest-attention position, not compressed to a suffix) + `{visual_style.description}` + optional fal character snippet + a one-sentence motion cue from `stage_direction` + Phase 2 `video_prompt` (cleaned by a deterministic sound/abstraction-language stripper, then a trailing `closing_frame` clause stating the clip's end state) + a family-friendly safety suffix. Fal may prepend internal markers for portrait i2i/i2v. Phase 2 rules forbid generic “people/visitors” in favor of specific expedition roles and communities.

---

## Map strategy: intro vs. mid-episode

- **Standalone map intro (step 3b).** For non–v2 flows, `map_intro.py` produces `00_intro.mp4` (date + location pin). Narrator says the date via TTS; intro is concatenated with segment clips. Used when we’re not using two-phase output.
- **V2: no standalone intro; mid-episode map instead.** Two-phase can emit `map_insertions` (e.g. one “after_segment_index” with `visual_style: "parchment_overlay"`). `videos-mp3-to-movie.py` overlays the map on the segment for the first N seconds (no standalone clip, so narration timing is unchanged). So for v2, run-daily **skips** step 3b (map intro) and relies on the overlay defined in the narration JSON.
- **Location source.** Prefer journal XML `geoDecl` (TEI). If missing, walk back through earlier journal dates for last known geo. Fallback: `location_data/location-dates.json` + geocoding. Map tiles cached in `location_data/cache/` by lat/lon.
- **Fal opening-cover vs. mid-episode map (at most one route graphic).** If fal writes **`movie-images/<date_id>/fal_segment_covers.json`** (e.g. i2v rejected the scene anchor), assembly can **mask the first N seconds** of a segment with black or the same **route map** parchment as the mid-episode overlay. **Assembly allows at most one** such route-map graphic in playback order: if both the opening mask and narration **`parchment_overlay`** want a map, **only the first** in order shows the map; later map requests become black, and if the mid-episode overlay would be **second**, that parchment overlay is **skipped** (segment plays full-frame). Good talk point: competing “map moments” need explicit priority so the video doesn’t feel repetitive.

---

## Narration as single source of truth

- **v2 JSON** carries everything downstream needs: title, scene_spine, narration_script, visual_style, and optionally video_metadata, map_insertions, **audio_design** (ambience level + optional single `ambient_tag` for the whole episode when using `--ambient`). One file per date.
- **Shared reading.** `pipeline/narration_utils.py` provides `load_narration(date_id)`, `is_narration_v2(...)`, `get_mid_episode_map_insertion(...)` so run-daily and videos-mp3-to-movie don’t duplicate parsing or v2/map logic. Single place to change if the schema evolves.
- **Shared writing helpers.** `pipeline/narration_common.py` holds config loading, visual-style selection, XML entry extraction, JSON cleaning, prompt replacements, and schema validation. Used by generate-narration-two-phase (and kept out of the stub so the two-phase script doesn’t depend on generate-narration.py implementing those functions).
- **Config vs. state directories.** To keep the repo root readable, long-lived configuration lives in `config/` (e.g. `config/narration_config.json`, `config/narration_characters.json`) and runtime state in `state/`, `theme_engine/`, and `pipeline/` (e.g. `state/run_daily_state.json`, `pipeline/character_usage.json`, `theme_engine/state.json`, `theme_engine/embeddings.json`). Top-level scripts use helpers/loaders instead of hardcoded root paths.

---

## CLI stability: redirect pattern

- **generate-narration.py** is a thin redirect that runs `generate-narration-two-phase.py`. External usage and docs can keep calling `generate-narration.py 18030830`; the real implementation lives in the two-phase script and in `pipeline/`.
- **Benefit:** No breaking change for scripts or docs that invoke the original name; we could swap the backend again without touching callers.

---

## Orchestration and YouTube

- **run-daily.py** is the single entry point for “run the full pipeline for a date.” Handles scrape → narration → audio → (optional map intro) → video clips → assembly. Optional `--upload` to push to YouTube after assembly.
- **Skip-existing.** By default we skip a step if its output already exists (e.g. narration JSON, final.mp3, clips). Lets you re-run after a failure or add a new step without redoing everything. `--no-skip-existing` forces a full re-run.
- **Manifest.** When a video is produced, we write a manifest JSON (`output/lewis_clark_{date_id}_video.manifest.json`: `date_id`, `output`, `aspect_ratio`, `length_seconds`, vendors, `cost_estimate`). On upload, we add `youtube_video_id` and `youtube_url`. Enables playlists (`youtube_playlist.py`) and cost tracking. **Important:** `aspect_ratio` is set from **`run-daily`’s Shorts vs wide-screen flag** (`9:16` if `--shorts`, else `16:9`), **not** by re-probing the MP4—so a mistaken wide-screen assembly still gets a “16:9” manifest even if source clips are portrait. YouTube only sees pixels; for a corrected file, use `video_manifest.refresh_from_video()` (or re-run assembly) so metadata matches the file. Standalone uploads: `youtube_upload.py --manifest <path>` updates the manifest when the file already exists.
- **Prompt QA harness.** We added a lightweight `scripts/check_prompts.py` that builds full vendor prompts and scans for known failure modes (anthropomorphized gear like “pair of boots trudging”, double periods after the `Subject:` prefix, over-repeated “Newfoundland dog”). This lets us catch regressions quickly when we tweak prompts or replacements.

---

## Subsystems as mini-modules

- **Theme engine as a library.** `theme_engine/theme_selector.py` exposes `recommend`, `record_narration`, `entry_word_count`, and `rebuild_focus_topic_queue`. Design and tuning are summarized under **Theme engine** above; field-level state docs live in `docs/theme_engine.md`.
- **Characters as data + logic.** Character definitions live in `config/narration_characters.json` (single, visual-only descriptions, aliases like `"drewer"`/`"drewyer"` for Drouillard), while all matching and usage tracking lives in the `pipeline/narration_characters/` package. This separation makes it easy to add or tweak characters without touching matching code.

---

## Character identity pipeline (description -> portrait)

- **Step order matters.** We first define character identity in `config/narration_characters.json` (name, active dates, aliases, physical description, role/skill keywords), then generate/update portrait assets in `character-portraits/`. This keeps visual references downstream consistent with narration-time matching.
- **Portrait generation workflow.** Use `scripts/fal_portrait_text_to_image.py` for first-pass stills from `character-portraits/portrait_prompts.json`, then optionally refine with `scripts/fal_portrait_refine.py` (i2i) and convert to transparent PNG via `rembg` for cleaner anchoring/compositing.
- **Prompt config as source of truth.** `character-portraits/portrait_prompts.json` records per-character prompt, negative prompt, image size, endpoints, and notes/history (reference photos, backup paths, refinement prompts). This prevents "why does this character look different now?" drift.
- **Canonical file convention.** Keep final files as `character-portraits/<character_id>.png` (or jpg/jpeg fallback). Pipeline lookup prefers `.png`; this gives deterministic identity references when `reference_character_id` is set.
- **Per-segment identity initialization (i2i -> i2v).** For portrait-backed segments, fal flow initializes each segment by generating an anchor frame from the character portrait + segment prompt via image-to-image, then uses that frame for image-to-video. This extra i2i step is what keeps face/clothing continuity segment-to-segment.
- **Ordering dependency.** Character dates and aliases affect whether a portrait can be applied at all (e.g., `active_from` gating in matching). Good talk point: identity data and portrait assets are coupled by design, not independent.

---

## TTS and timing

- **Choice:** pyttsx3 (local, free) or OpenAI TTS (higher quality, uses OPENAI_API_KEY). Default in run-daily is OpenAI; narration-to-mp3 can be told `--tts pyttsx3`.
- **Durations drive assembly.** narration-to-mp3 writes per-segment MP3s and a `durations.json`. videos-mp3-to-movie uses those durations and ffmpeg `setpts` to time-stretch or trim clips to match the narration length. Audio is the timing source of truth.

---

## AI disclosure and open-ended run

- **Transparency:** The project states clearly, in all the places that matter for the audience, that output is **AI-assisted** (narration, imagery, tooling as applicable). That is a deliberate trust and ethics choice, not a footnote.
- **Horizon:** There is **no fixed end date** for how long the daily (or periodic) journal run continues—decisions favor a **sustainable pipeline** and quality bar over committing to a predetermined episode count.

---

## Voice strategy: narrator (onyx) vs characters (bespoke)

- **Narrator track — keep OpenAI defaults.** The main documentary voice stays **OpenAI `tts-1-hd`**: **`onyx`** for typical episodes, **`nova`** when the narration JSON carries a **`focus_topic`** from theme_engine (same rule in `narration-to-mp3.py` and `map_intro.py` so date line and body match). We are **not** swapping to a bespoke FAL (or other) narrator voice mid-series for now—continuity for the “guide” voice, less integration work, and map intro / assembly paths unchanged.
- **Stock voice tradeoff:** **`onyx`** is common across other AI-voiced channels; we accept that for the **narrator** because differentiation and craft effort are focused elsewhere (see below), and because the series is explicitly labeled AI.
- **Characters — where bespoke effort goes.** Distinctive voice work targets **expedition characters** (dialogue lines, future FAL or other per-speaker assets), not replacing the default narrator. Aligns with “best final product” for cast while keeping the through-line voice stable.
- **Design archive (not pipeline TTS yet):** Long-form voice **descriptions** for FAL (or similar) generation, plus post-run notes (model id, sample paths), live in **`character-portraits/voice_prompts.json`**, keyed like **`speaker_id`** / `config/narration_characters.json` **`people[].id`** plus **`narrator`**. Episode TTS today still uses **`voice_by_speaker`** in **`config/narration_config.json`** (OpenAI voice **names** per id) when **`dialogue`** is present—see **Character dialogue mode (MVP)** below.

---

## Character dialogue mode (MVP)

- **Purpose:** Optional **multi-speaker** lines per segment while preserving **one merged MP3 per segment** and the same downstream invariant (one clip index per narration row, `durations.json` row per segment).
- **CLI / orchestration:** **`generate-narration-two-phase.py --dialogue`** and **`run-daily.py --dialogue`** force the **`lewis_clark_dialogue`** prompt pack (with a stderr warning if the profile’s `prompt_pack` was something else). Dry-run plan text mentions dialogue when the flag is set.
- **Phase 1 output:** Each segment still has **`segment_type`**, non-empty **`narration`** (for Phase 2 / `find_character_match` / previews), and a non-empty **`dialogue`** array: **`{ "speaker_id", "text" }`**. **`speaker_id`** is validated against **`config/narration_characters.json`** plus reserved **`narrator`**. User prompt uses a **dialogue-mode** branch so third-person-only “AUTHORITY” lines do not fight in-character lines.
- **Merge:** **`merge_phase1_phase2`** copies **`dialogue`** onto **`narration_script`** (shallow copy of line dicts). Phase 2 system copy acknowledges optional **`dialogue`** for TTS while the director still reads Phase 1 story text.
- **Merged schema:** **`narration_version`** stays **`2.0`**; **`validate_narration_schema`** allows optional **`dialogue`**; **`apply_prompt_replacements`** can rewrite **`dialogue[].text`** when configured.
- **TTS:** **`narration-to-mp3.py`** — if a segment has non-empty **`dialogue`**, each line is synthesized with OpenAI TTS (voice per **`voice_by_speaker`**), then **ffmpeg-concatenated** into **`NN.mp3`**; otherwise unchanged single-voice path. **`--config`** defaults to **`config/narration_config.json`** for the map.
- **MVP scope:** **Single Phase 1** call only (no second LLM “Phase 1.1” pass to split narrator vs dialogue). A follow-up pass remains optional if director text and spoken lines ever need decoupling. Further detail: **`docs/narration.md`** and this file’s **Voice strategy** / **Character dialogue mode** sections above.

---

## Optional ambient bed (assembly)

- **Flag.** `run-daily.py` and `videos-mp3-to-movie.py` accept **`--ambient`**. When set, assembly may mix a quiet environmental loop under the full narration track (still subordinate to voice). The **pipeline UI** exposes the same behavior via a checkbox that appends `--ambient` to the run-daily command.
- **Source of truth in v2 JSON.** `audio_design.ambience_level` is `none` | `light` | `moderate`; `none` means no bed even if `--ambient` is passed. Optional **`audio_design.ambient_tag`** names a key in **`ambient_library/manifest.json`** (repo root); if omitted, the manifest **`default_tag`** is used.
- **Default: one tag for the whole timeline.** Optional **`audio_design.ambient_regions`** assigns `ambient_tag` or **`none`** (silence) to 1-based segment indices (and intro = index 0); unlisted segments use **`ambient_tag`**. At most two distinct non-`none` manifest tags across regions. Phase 2 describes this under **`audio_design`** only.
- **Implementation.** `pipeline/ambient_audio.py` builds the bed from `durations.json` lengths and ffmpeg (`aloop`, mix under voice). Good talk point: separating “what bed fits this day” (narration JSON) from “whether to mix it” (CLI/UI) keeps scheduled runs and one-off assemblies flexible.

---

## Shorts (9:16) support

- **Default is Shorts.** `run-daily.py` and `videos-mp3-to-movie.py` both default **`--shorts` to on** (9:16, **720×1280**). Opt **out** with **`--wide-screen`** on `run-daily` (16:9 **1280×720**), which passes the wide-screen choice through to `map_intro --format`, `narration-to-video`, and assembly—so **portrait fal/google clips inside a wide-screen final encode** look wrong on YouTube even though the manifest says 16:9. Demos and docs should say “Shorts unless you explicitly choose wide.”
- **Same pipeline, one format switch.** Shorts mode threads through map intro, clip generation, and mux; map intro uses the same 16:9 US map **letterboxed** into 9:16 (black above/below) so the map asset stays unchanged.
- **Vendor support:** Shorts implemented for **google** and **fal** (they request 9:16 clips). **Sora** path not adapted for Shorts in the script.
- **Manifest coupling:** See **Orchestration and YouTube**—manifest `aspect_ratio` follows the run’s format flag, not ffprobe.

---

## UI kickoff and operator flow (GUI driver)

- **Why a UI at all.** We added a lightweight **`pipeline_ui/`** so operators can kick off runs without memorizing CLI flags, reducing friction for daily production and demos.
- **GUI driver = local web server + form → argv.** Run from repo root (venv Python): `python pipeline_ui/server.py` → open **http://127.0.0.1:8765/** (bind **127.0.0.1** only; configurable via `PIPELINE_UI_HOST` / `PIPELINE_UI_PORT`). **`pipeline_ui/options.json`** is the **control surface**: labels, defaults, and how each checkbox/field maps to **`run-daily.py` arguments**—refresh the page after editing JSON to pick up changes.
- **Subprocess, not a reimplementation.** **Run** / **Dry-run** spawn **`run-daily.py`** with the resolved argv (same code path as CLI). **Stop** calls `POST /api/cancel` and tears down the process tree (on Windows includes child processes such as `narration-to-video.py`). **`GET /api/status`** exposes running vs idle for the page after refresh.
- **Observability.** Each run writes **`logs/pipeline_ui_last.json`** (form values, command, stdout/stderr; long streams may truncate)—primary artifact for debugging “what did the UI actually run?”
- **Operator affordances.** **Preview** modals: narration via `GET /api/preview/narration`, video via `GET /api/preview/video` (Range-capable MP4), segment cues via `GET /api/preview/video-cues`. **Journal date picker** and `GET /api/journal/<YYYY-MM-DD>` for inline XML; links to UNL journals site. Optional fields mirror CLI: **Narration only**, **Regenerate video segments** (google/fal), **No automatic focus topic**, upload/privacy/vendor/TTS, etc.—see `pipeline_ui/README.md`.
- **Operational lesson: always set explicit date.** Date-scoped artifacts make recovery easy, but accidental runs can still happen if “next” is inferred. In demos, emphasize selecting explicit journal dates / date IDs to avoid unintended runs.
- **Upload sequencing guardrail.** Treat upload as a separate, deliberate action after QA. Manifests track `youtube_video_id`/`youtube_url`; if a video is removed from YouTube manually, clear those fields locally so stats/playlist tools don’t treat it as live.

---

## B-roll library & Pipeline UI

- **`b_roll_library/manifest.json`.** Tagged clip metadata (`id`, `file` under `b_roll_library/`, `tags`, optional `source` with pipeline `date_id` / `segment_index` / `vendor`). Optional copies or symlinks in `b_roll_library/clips/`; assembly still reads `movie-images/<date_id>/NN.mp4`—the library is for reuse, provenance, and operator tooling first.
- **Suggest tab.** `GET /api/b-roll/suggest` runs `scripts/b_roll_suggest_for_narration.py` (`build_b_roll_suggest_payload`) logic: for each narration segment, rank manifest clips by **token Jaccard** overlap between the segment’s `video_prompt` and each clip’s resolved text (clip’s source segment `video_prompt` when `source` has `date_id`+`segment_index`, else tags), plus small bonuses from **`bucket_keywords.json`** (shared bucket name) and optional **portrait vs. environment** heuristics. Clips whose **`base_similarity`** (the Jaccard-heavy part of the score) is below a floor are dropped before taking the top N: default **`min_base=0.08`** on the API query string (same default as CLI **`--min-base-similarity`**); use **`0`** to show the old “top N no matter how weak” behavior. The UI shows up to **5** candidates per row (was 10); **`prefer_environment`** down-ranks clips sourced from portrait-heavy segments when the current segment has no `reference_character_id`. Same-date same-segment is excluded so you don’t “suggest” the target file itself.
- **Preview → add to manifest.** In **Latest video → Preview**, when playback is **paused** on a **narrative** segment (not intro), **Add segment to B-roll library** arms; **`POST /api/b-roll/add-from-preview`** appends a `clips[]` row (deduped by `source.date_id` + `source.segment_index`), derives `tags` from that segment’s `video_prompt`, and requires `movie-images/<date_id>/NN.mp4` to exist. It does **not** copy the MP4 into `clips/`—operator copies or symlinks if they want the manifest `file` path to resolve. Documented in `b_roll_library/README.md`.

---

## Add more as you go

- When you make a decision that would be good for the talk (e.g. “we tried X, then switched to Y because…”, or “we added Z to handle…”), add a short bullet or section here. When you build the deck, pair these with git tags or key commits for a clear timeline.
