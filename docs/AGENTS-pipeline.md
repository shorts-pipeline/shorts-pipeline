# Pipeline, narration JSON, and tooling

This doc is the long-form companion to the root **`AGENTS.md`** index. It covers the pipeline table, Shorts, conventions, v2.0 narration shape, tech stack, coding notes, and local testing.

## Pipeline at a Glance

| Step | Script | Input | Output |
|------|--------|-------|--------|
| 1 | `scripts/scrape-journal-entries.py` | `YYYY-MM-DD` | `journal-entries/*.xml` |
| 2 | `generate-narration.py` (redirect) → `generate-narration-two-phase.py` | `18030830` + XML + optional `config/narration_config.json`; uses **theme_engine** for focus-topic when similar to recent entries; optional **`--dialogue`** (`lewis_clark_dialogue`) or **`--long-conversation`** (`lewis_clark_long_conversation`, implies dialogue) — see [Dialogue and long-conversation modes](#dialogue-and-long-conversation-modes); phase1-dialogue polish via `phase1_dialogue_system.txt`; per-segment `dialogue[]` for multi-voice TTS | `narrations/narration18030830.json` (v2.0) plus sidecars `narration18030830_voice.json` (Phase 1 after polish) and `narration18030830_visual.json` (Phase 2), merged into the canonical file |
| 3 | `narration-to-mp3.py` | `18030830` | `audio/18030830/segments/`, durations.json, final.mp3 |
| 3b | `map_intro.py` | `18030830` + journal XML geo (or fallback `location_data/location-dates.json`) + `--tts` | `movie-images/18030830/00_intro.mp4`; uses journal TEI `geoDecl` when present, else last known geo from earlier entry, else location-dates + geocode; caches map images in `location_data/cache/`; narrator says the date (TTS); prepends intro to durations.json and date audio to final.mp3 |
| 4a | `narration-to-video.py` | `18030830` + `--vendor` | sora/google/fal video clips (`01.mp4`, …; google/fal expect `00_intro.mp4` from 3b). **fal** uses Wan for `visual_mode: b_roll` (default) and **FAL talking-head** (`fal-ai/sadtalker` by default, overridable via `fal_talking_head_model`; optional **`fal_talking_head_fallback_model`** after SadTalker preprocess exhaustion) in `config/narration_config.json` for `visual_mode: talking_head` + `talking_head_subject` + single-speaker `dialogue`. **fal** also writes **`movie-images/<date_id>/run_report.json`** (per-run segment summary). |
| 4b | `scripts/b_roll_materialize_movie_images.py` (optional) | `18030830` + `SEG=repo_relative.mp4` … | Copies/symlinks chosen sources into `movie-images/18030830/NN.mp4`; writes `b_roll_episode.json` (provenance). Use `--clear-sidecar-only` to remove that sidecar. |
| 5 | `videos-mp3-to-movie.py` | `18030830` optional `--ambient` | `output/lewis_clark_18030830_video.mp4`; with `--ambient`, mixes `ambient_library/` beds when narration `audio_design.ambience_level` is `light` or `moderate` (see `pipeline/ambient_audio.py`) |
| 6 | `run-daily.py` | `--date` `--upload` `--vendor` (default: fal) `--shorts` optional `--ambient`; optional **`--no-preflight`** / **`--strict-preflight`** | Full pipeline + optional YouTube upload; **automation gates** (preflight/postflight) unless disabled — see [Automation gates](#automation-gates-run-dailypy) |

## YouTube Shorts (optional)

- **`--shorts`**: Produce 9:16 vertical video for YouTube Shorts. Use with `run-daily.py` (or pass through to `map_intro.py --format shorts`, `narration-to-video.py --shorts`, `videos-mp3-to-movie.py --shorts`). Output remains `lewis_clark_{date_id}_video.mp4`; same resolution (720×1280) and full audio (no shortening).
- **Manifest**: Manifest path and JSON are managed by `video_manifest.py`. When the pipeline produces a video, run-daily calls `video_manifest.create_after_pipeline()` to write `output/lewis_clark_{date_id}_video.manifest.json` with `aspect_ratio` (`"16:9"` or `"9:16"`), `length_seconds` (probed from the file), plus `date_id`, `output`, vendors, and `cost_estimate`. On YouTube upload, youtube_upload calls `video_manifest.update_youtube()` to add `youtube_video_id` and `youtube_url`. Standalone uploads can pass `youtube_upload.py --manifest <path>` to record the link (only updates if the manifest file already exists). **youtube_playlist.py** creates a YouTube playlist from all uploaded videos (output manifests with `youtube_video_id`), sorted by date_id; uses same OAuth as youtube_upload (token must include youtube scope).
- **Upload metadata (`youtube_upload.py`)**: Defaults for the Shorts channel — `categoryId` **27** (Education), `defaultLanguage` / `defaultAudioLanguage` **en**, `status.containsSyntheticMedia` **true**, `#Shorts` in the description, and an English **SRT** caption track built from narration + `audio/<date_id>/durations.json` (`pipeline/youtube_srt.py`). Captions need the **`youtube.force-ssl`** OAuth scope (`python youtube_upload.py --login-only` if the saved token is old). Backfill existing uploads: `python youtube_upload.py --backfill-synthetic-media [--with-captions] [--dry-run] [--limit N] [--scan-archive]`.
- **Upload guards (`pipeline/youtube_upload_guard.py`)**: every upload route — `upload_video()`, `youtube_upload.py` CLI, `run-daily.py --upload`, Pipeline UI upload-from-preview — refuses before contacting YouTube when (1) the episode's manifest already has `youtube_video_id` (re-upload would create a duplicate; override with `--force` / `--force-upload`), or (2) an assembled episode between the last-uploaded one and the target has no `youtube_video_id` (would publish out of journal order; override with `--allow-gap` / `--allow-upload-gap`). `run-daily.py --upload` treats an already-uploaded episode as success (`[SKIP]`, state still advances); an out-of-order gap is a hard error. The sequence check is windowed by the highest already-uploaded episode below the target, so ragged old back-catalogue manifests don't trip it. Tests: `tests/test_youtube_upload_guard.py`.
- **Shorts map intro**: Same 16:9 US map as landscape; letterboxed (black above/below) into 9:16 so the map is unchanged and does not include Mexico/Canada.
- Shorts mode is supported for **google** and **fal** vendors (they request 9:16 clips). **Sora** is not adapted for Shorts in the script.

## Key Conventions

- **Project venv (`.venv`)**: The repo has a `.venv` in the project root. Always activate it before running Python so `python` has access to dependencies (openai, pyttsx3, etc.); otherwise you get `ModuleNotFoundError`. **PowerShell:** `.\.venv\Scripts\Activate.ps1` then `python ...`. **Agents/tools:** When running any Python or pipeline command from this repo, activate `.\.venv` first (or use `.\.venv\Scripts\python.exe`). `run-daily.py` uses `sys.executable` for subprocesses, so the same venv is used for all steps when you start from an activated shell.
- **Run with venv Python**: Run any pipeline script using the project’s venv Python explicitly (e.g. `.\.venv\Scripts\python.exe run-daily.py ...` on Windows, or activate the venv first so `python` is the venv). Otherwise the interpreter may be system Python and will miss dependencies (pyttsx3, openai, etc.); `run-daily.py` uses `sys.executable` for subprocesses, so the same Python is used for all steps.
- **Date ID**: `18030830` = YYYYMMDD (no dashes).
- **XML file naming**: `YYYY-MM-DD.xml` (e.g. `1803-08-30.xml`).
- **Paths**: `journal-entries/`, `narrations/`, `audio/<date>/`, `movie-images/<date>/`, `output/`.
- **Partial B-roll (manual):** After narration + audio, you can copy a **subset** of segment clips from `b_roll_library/` (or reuse `movie-images/<other_date>/NN.mp4`) with `scripts/b_roll_materialize_movie_images.py <date_id> 1=b_roll_library/clips/foo.mp4 3=…`. That writes **`movie-images/<date_id>/b_roll_episode.json`** (which segments were sourced from where; not used to skip the vendor). Then run **`narration-to-video.py`** as usual: **Google** and **FAL** both **skip** any `NN.mp4` that already exists, so only missing segments hit the API. Regenerate one segment by deleting its `NN.mp4` (and fixing **`fal_segment_covers.json`** for that index if present), then re-run or use `--segments`. Remove the sidecar only: `… --clear-sidecar-only`.
- **Map intro**: For google/fal, `map_intro.py` produces `movie-images/<date>/00_intro.mp4`; segment clips are `01.mp4`, `02.mp4`, … (sorted by name for assembly).
- **Narration**: Step 2 runs the two-phase generator (Phase 1: narration + metadata; Phase 2: visual plan); `generate-narration.py` is a thin redirect. Shared helpers live in `pipeline/narration_common.py` and `pipeline/narration_utils.py`.
- **Narration config**: `config/narration_config.json` (optional) provides `visual_themes`, `allow_stylization`, `stylization_probability`, **`fal_talking_head_model`**, **`fal_talking_head_fallback_model`**, **`tts_stage_direction_pause_seconds`**, and related keys for narration / FAL talking-head / TTS.
- **Pipeline modules**: See **[docs/PIPELINE_MODULES.md](PIPELINE_MODULES.md)** for which `pipeline/*.py` file owns narration, visual_mode, TTS stage directions, and gates.
- **Narration ending**: The system prompt requires the final segment to provide narrative closure (reflect on the day’s significance, uncertainty, or forward momentum; feel complete and intentional; do not introduce new major events in the final segment).
- **Composite character portraits**: `scripts/build_portrait.py --preset <name>` builds side-by-side PNGs under `character-portraits/` (e.g. `lewis_clark`, `lewis_drouillard`, `clark_colter`). See that script’s docstring.

## Prompt pack versioning (when you edit `prompt_packs/`)

Use this whenever you change **`phase1_system.txt`**, optional **`phase1_dialogue_system.txt`** (dialogue packs), **`phase2_system.txt`**, or optional **`phase1_user.txt`** under `prompt_packs/<pack_id>/`, or when you add a new pack.

For a precise runtime explanation of what is sent as system vs user prompt, what is concatenated in code, and how Phase 2 placeholder substitution works, see **`prompt_packs/README.md`**.

1. **Edit the text files** in `prompt_packs/<pack_id>/` as needed.
2. **Maintain `pack.json`** in that same directory (see `lewis_clark/pack.json` and `lewis_clark_dialogue/pack.json` for examples). At minimum keep human-oriented **`id`**, **`version`**, and **`description`**. **Bump `version`** when a change is meant to be identifiable in review or regression (semver like `1.0.1` or a date stamp—pick one convention per pack and stay consistent).
3. **What the pipeline records automatically**: After a successful merge, `generate-narration-two-phase.py` writes **`prompt_pack_lineage`** on the canonical narration JSON (`narrations/narration<date_id>.json`). It includes:
   - **`phase1`** / **`phase2`**: which `pack_id` was used, the **`manifest`** read from each pack’s `pack.json` (or `version: "unversioned"` if the file is missing), and a **`fingerprint_sha256`** over the concatenated pack text files listed above. Any edit to those files changes the fingerprint even if you forget to bump `version`.
4. **Tooling for diffs and A/B prep**: From repo root, `python scripts/prompt_ab_harness.py`:
   - `fingerprint` / `fingerprint --pack <id>` — print manifests + fingerprints.
   - `compare-packs <a> <b>` — show whether two packs’ prompt text fingerprints differ.
   - `export-segments --date-id <YYYYMMDD> --segments 3,7` — dump merged `video_prompt` / `talking_head_prompt` for chosen segments (handy before/after a pack change, using the same saved narration file).
5. **Code entry points**: `pipeline/prompt_pack_metadata.py` (read manifest, compute fingerprint, build lineage), `pipeline/narration_phase1.py` / `pipeline/prompt_pack_text.py` (load paths).

**New pack checklist:** add `prompt_packs/<id>/phase1_system.txt` (required for Phase 1); add `phase2_system.txt` or rely on fallback to `lewis_clark` per `pipeline/prompt_pack_text.py`; add **`pack.json`** so lineage is not `unversioned`; point the pipeline profile’s `prompt_pack` at `<id>` (`docs/PIPELINE_PROFILES.md`).

**Pack catalog** (when to use which): see **`prompt_packs/README.md`** (table at top).

## Dialogue and long-conversation modes

Character dialogue is **disabled when a focus topic is active** (single-narrator deep dive). Otherwise:

| Mode | CLI (`generate-narration-two-phase.py` / `run-daily.py`) | Prompt pack | Typical use |
|------|----------------------------------------------------------|-------------|-------------|
| Standard VO | (none) | Profile default (`lewis_clark`) | Narrator-only journal episode |
| Dialogue | `--dialogue` / infer from JSON | `lewis_clark_dialogue` | Narrator-led; cast dialogue in **at most 3** segments; standard talking-head spacing |
| Long conversation | `--long-conversation` (implies dialogue) / infer from JSON | `lewis_clark_long_conversation` | Denser ping-pong exchanges; more segments; relaxed consecutive `talking_head` rules |

- **phase1-dialogue:** Second API call after Phase 1 when dialogue is effective; polishes `dialogue[].text` only (`pipeline/narration_phase1_dialogue.py`, `phase1_dialogue_system.txt` in the active pack). Per-character `dialogue_profile` cues come from `config/narration_characters.json`. Optional offline journal mining for Lewis/Clark: `scripts/mine_journal_dialogue_profiles.py` → `config/dialogue_profile_mining/` (exploration only; not auto-injected).
- **Inference on re-run:** If `run-daily` is called without `--dialogue` / `--no-dialogue` (or long-conversation pair), it reads `narrations/narration<date_id>.json` via `pipeline/narration_utils.py` (`dialogue_mode`, segment `dialogue[]`, or `long_conversation_mode`).
- **Explicit opt-out:** `--no-dialogue`, `--no-long-conversation` force regeneration without those modes even if the saved JSON had them.
- **Merged JSON flags:** Top-level **`dialogue_mode`** and **`long_conversation_mode`** (booleans) are written on merge so preflight and inference stay consistent (`pipeline/automation_gates.py` warns when flags disagree with CLI).
- **visual_mode rules:** Enforced in **`pipeline/narration_visual_mode.py`** — standard dialogue: first `talking_head` at segment **3+**, at most **2** consecutive talking-head segments; long-conversation: up to **12** consecutive, **one** cast dialogue line per talking-head segment (ping-pong). Tests: `tests/test_narration_visual_mode.py`, `tests/test_phase1_long_conversation_requirements.py`.
- **Week-arc talking-head enforcement:** When `--use-week-arc` drives `dialogue`/`long_conversation` mode for a day (`pipeline.week_arc.requires_talking_head`), Phase 1 must include **at least one** `talking_head` segment—`validate_dialogue_visual_modes(..., require_talking_head=True)` raises (feeding the retry loop) on an all-`b_roll` result. Manual `--dialogue`/`--long-conversation` runs (no week arc) are unaffected—all-`b_roll` stays valid there.

## TTS stage directions (dialogue)

`narration-to-mp3.py` uses **`pipeline/tts_stage_directions.py`** to remove non-spoken parentheticals and standalone stage-direction sentences from dialogue text before TTS, and inserts a short silence gap after those beats. Pause length: **`tts_stage_direction_pause_seconds`** in `config/narration_config.json` (default `0.75`). Tests: `tests/test_tts_stage_directions.py`.

## Week arc (variable-length narrative bundles)

Optional editorial planning for **`lewis_clark*`** packs: from **`config/week_arc.json`** → **`first_anchor_date_id`** (default **`18040705`**), bundle consecutive journal days into one arc. The LLM picks the arc's actual length itself — a contiguous run of **`week_journal_days_min`**–**`week_journal_days_max`** (default **4–10**) days, wherever the material's payoff/release naturally lands — rather than a fixed 7 days. Each bundle gets one LLM plan with a through-line, per-day role, and **`recommended_mode`** (`narration` | `dialogue` | `long_conversation`).

- **Storage:** `state/week_arcs/week_<start_date_id>.json` (machine-local; example: `state/week_arcs/week_arc.example.json`).
- **CLI:** `python scripts/plan_week_arc.py 18040705` (`--refresh`, `--dry-run`). Daily: **`--use-week-arc`** on `run-daily.py` / `generate-narration-two-phase.py` (create if missing, reuse if cached). **`--refresh-week-arc`** forces replan. **`--no-week-arc`** opts out.
- **Mode precedence:** manual `--dialogue` / `--long-conversation` flags → week arc recommendation → inference from existing narration JSON.
- **Focus topic:** `--use-week-arc` disables the automatic focus-topic selector entirely (in `generate-narration-two-phase.py`, before the theme_engine/source-bundle load) — a week arc's through-line/role owns the episode, so it isn't overridden by a focus-topic deep dive. Pass `--no-week-arc` (or don't use week arc) to let focus topics resume.
- **Phase 1:** today's slice is appended to the user prompt as **`WEEK ARC`**; merged JSON includes **`week_arc_ref`** (`week_id`, `week_start_date_id`, `week_end_date_id`, `role`, `recommended_mode`, `day_focus`) whenever the day plan loaded — this is the marker that a narration was generated under a week arc. Pipeline UI Produce/Video tabs surface it as a "Week arc: week_&lt;id&gt; (role)" badge (`pipeline_ui/narration_view.py::_week_arc_marker`).
- **Pipeline UI:** Journal tab checkboxes (`journalUseWeekArc`, `journalRefreshWeekArc`); not on Produce/Video run form.
- **Tests:** `tests/test_week_arc.py`.

## Episode diversity (Lewis & Clark Phase 1 hints)

Soft variety nudges for **`lewis_clark*`** prompt packs only. Config: **`prompt_packs/<pack>/pack.json`** → **`episode_diversity`** (`recent_window`, optional **`llm_audit`**).

- **Deterministic hints:** **`pipeline/recent_episode_diversity.py`** scans the last N prior merged `narrations/narration<date_id>.json` files and emits bullets (lens/tone skew, Seaman star/ambient, segment-1 wake vs final-segment camp bookends, talking-head load, etc.). Appended to the Phase 1 user prompt as **`DIVERSITY HINTS`**; per-episode history lines are **UI-only** (Journal tab).
- **Title uniqueness:** After Phase 1 narration validates, **`refine_phase1_title`** (`pipeline/phase1_title_hook.py`) runs a **short second prompt** only when the title is generic (`Lewis & Clark Expedition…`) or reuses a recent stem. Narration is not regenerated. Tests: `tests/test_phase1_title_hook.py`.
- **Cached LLM audit (hybrid):** Periodically run **`scripts/refresh_episode_diversity_audit.py`** — one OpenAI call over ~15 compact episode summaries → **`state/episode_diversity_lewis_clark.json`**. The audit prompt asks for **concrete alternatives** and to skip beats already covered by static rules; at runtime, **`episode_diversity_audit.py`** drops dynamic hints that duplicate fired static rules or restate them in generic wording, then merges the rest when checkers still fire (no per-run LLM).
- **Refresh when stale:** **`llm_audit.refresh_every_new_episodes`** (default 5) — Journal UI shows stale/fresh status. Example: `python scripts/refresh_episode_diversity_audit.py --last 15 --prompt-pack lewis_clark` (`--dry-run` prints JSON without writing).
- **Tests:** `tests/test_recent_episode_diversity.py`, `tests/test_episode_diversity_audit.py`.

## Automation gates (`run-daily.py`)

Guardrails run before the real pipeline (and on successful video output) so bad inputs fail fast instead of halfway through paid steps.

- **Module:** `pipeline/automation_gates.py` — **`preflight_run_daily`**, **`postflight_output_video`**, and **`narration_audio_segment_mismatch`** (single canonical implementation imported by `run-daily.py`).
- **When:** Preflight runs after journal date and **`--regenerate-video-segments`** validation, **before** the dry-run block (so **`--dry-run`** still runs gates). Postflight runs after **`video_manifest.create_after_pipeline`** when an output MP4 was produced this run, unless disabled.
- **CLI:** **`--no-preflight`** — skip preflight and postflight (escape hatch). **`--strict-preflight`** — when `--skip-existing` and existing `audio/<date_id>/final.mp3` disagree with narration segment count vs `durations.json`, treat as **error** instead of **warning** (full pipeline only; not for `--narration-only`).
- **Preflight** (when `narrations/narration<date_id>.json` exists): JSON parseable; `narration_script` present; dialogue narration vs **`--no-dialogue`**; **`long_conversation_mode`** vs **`--long-conversation`** / **`--no-long-conversation`**; optional stale-audio warning (or strict error). For vendor **`fal`** on a full run: each **`talking_head`** segment must resolve **`portrait_path_for_talking_head`**; empty subject / unsupported animal subjects are errors. For **`google`** / **`sora`**, `talking_head` rows in JSON produce **warnings** only (those vendors use B-roll for those segments).
- **Postflight:** Output file size sanity, **`ffmpeg.probe`** duration ≥ 0.5s, at least one video stream.
- **Tests:** `tests/test_automation_gates.py`.

## Talking-head, FAL video, run reports, and UI

This section records behavior implemented for talking-head quality, FAL reliability, prompt traceability, and operator visibility (formerly tracked in a standalone plan doc; **canonical reference is this file + code**).

- **Silent beats (dialogue / talking_head):** Encode pre/post mouth idle and delivery in **`talking_head_prompt`** (dialogue pack); **`video_prompt`** stays visual-only (merge does not append meta/director strings). **`tests/test_merge_talking_head_silence.py`** asserts `talking_head` merge leaves **`video_prompt`** free of `[Director note:…]` / silence-beat boilerplate.
- **FAL HTTP / subscribe retries:** `video_vendors/fal_retry.py` (env-tunable); used by FAL client paths and talking-head upload/download.
- **FAL content-policy rewrite:** After local prompt sanitizer retry, `pipeline/fal_content_policy_rewrite.py` asks OpenAI to rewrite that segment’s **`opening_frame`** and **`video_prompt`** (spoken `narration` is unchanged), persists them on `narrations/narration<date_id>.json` (and `_visual.json` when present), then retries FAL. Disable with **`FAL_SKIP_CONTENT_POLICY_REWRITE=1`**. Tests: **`tests/test_fal_content_policy_rewrite.py`**.
- **SadTalker preprocess:** `video_vendors/fal_avatar.py` tries **`preprocess`** in order **`full` → `resize` → `crop`** when face-detection-style failures warrant it.
- **Talking-head fallback model:** `config/narration_config.json` — **`fal_talking_head_fallback_model`** (empty string = off). After the primary model fails (or SadTalker preprocess chain is exhausted), **`generate_talking_head_clip`** attempts the fallback once (argument shape via **`_subscribe_args_for_talking_head`**: OmniHuman / HeyGen Avatar4 / Kling Avatar use **`image_url` / `audio_url`**; SadTalker uses **`source_image_url` / `driven_audio_url`**).
- **Shot library:** `config/talking_head_shots.json` + **`pipeline/talking_head_shots.py`**. Phase 1 may emit **`talking_head_shot_id`** or **`talking_head_archetype`**; merge appends resolved text to **`talking_head_prompt`**. **`tests/test_plan_workstreams_6_9.py`** exercises archetype merge.
- **Prompt pack lineage + A/B helpers:** **`prompt_pack_lineage`** on merged narration JSON, **`scripts/prompt_ab_harness.py`** — see [Prompt pack versioning](#prompt-pack-versioning-when-you-edit-prompt_packs) above.
- **Run report (FAL only):** After **`narration-to-video.py --vendor fal`** (non–**`--dry-run`**), **`movie-images/<date_id>/run_report.json`** summarizes B-roll clips generated in that run and talking-head segments (**generated** / **reused**, **`reason_code`** when applicable). Schema: **`pipeline/run_report.py`**.
- **Pipeline UI:** **`pipeline_ui/index.html`** + **`server.py`** — Video tab **Load run report** → **`GET /api/run-report?journal_date=YYYY-MM-DD`**. Grouped fieldsets, presets, run-flag preview, per-workflow dry-run: **`pipeline_ui/options.json`** (see **`pipeline_ui/README.md`**).
- **Anchor + TTS preview (before OmniHuman):** **`pipeline/anchor_preview.py`**, **`scripts/build_anchor_preview.py`**, Produce tab **Anchor preview**. Batch scene-anchor i2i into shared **`movie-images/<date_id>/anchors/`** (UI **Build preview clips + assemble** builds missing anchors first), assemble cheap preview **`output/lewis_clark_anchor_preview_<date>_video.mp4`** from stills (or existing segment MP4 / slates) + **`audio/<date_id>/final.mp3`** via **`videos-mp3-to-movie.py --clips-subdir anchor_preview`**. Full run reuses anchors: **`narration-to-video.py --reuse-scene-anchor-stills`**. Tests: **`tests/test_anchor_preview.py`**. **TTS-only from UI:** **`POST /api/generate-tts`** → **`narration-to-mp3.py`** (Produce **Script & audio** or Anchor preview when audio missing).

### Optional follow-ups (not built yet)

- Stricter postflight: black-frame / no-face heuristics, A/V drift tolerance, auto-regenerate only failed segments with reason codes in **`run_report`**. Run report does not yet list **which** model succeeded when fallback differs from primary.
- **Vendor spike / pricing:** Shortlist and API notes for OmniHuman etc. remain in **`ai-plans/talking-head-vendor-evaluation.md`** (research sheet, not a task tracker).

## Narration JSON Shape (v2.0)

Pipeline step 2 produces **v2.0** narration only. Use `pipeline.narration_utils.load_narration(date_id)`, `is_narration_v2(...)`, and `get_mid_episode_map_insertion(...)` for a single place to read and interpret narration JSON.

```json
{
  "narration_version": "2.0",
  "title": "Short episode title (optional; map_intro uses it in the intro overlay)",
  "scene_spine": { "core_location": "...", "environmental_elements": "...", "lighting_progression": "...", "color_palette": "...", "visual_mood": "..." },
  "visual_style": { "name": "Theme name", "description": "Style description" },
  "narration_script": [
    {
      "segment_index": 1,
      "stage_direction": "[CUT TO – SCENE]",
      "narration": "voiceover text",
      "video_prompt": "detailed visual description",
      "opening_frame": "optional; t=0 still for FAL scene-anchor i2i when it must differ from clip motion (per segment)",
      "visual_mode": "b_roll | talking_head — optional; default b_roll. dialogue-pack only: b_roll = narrator/B-roll Wan; talking_head = FAL audio-driven portrait (requires talking_head_subject + portrait file + dialogue lines only from that subject). First talking_head must be segment 3+; at most two consecutive talking_head.",
      "talking_head_subject": "optional; expedition character id (e.g. lewis) when visual_mode is talking_head",
      "talking_head_prompt": "optional short hint for the avatar model"
    }
  ],
  "video_metadata": { ... },
  "map_insertions": [ { "placement": "after_segment_index", "segment_index": N, "visual_style": "parchment_overlay", "duration_seconds": N } ],
  "audio_design": { ... },
  "open_questions": [ "optional strings for human review; often []" ],
  "editor_notes": "optional human-facing notes from Phase 2 (may be empty string)",
  "tone_register": "light | warm | reflective | tense | somber",
  "dialogue_mode": "optional bool; true when generated with --dialogue or --long-conversation",
  "long_conversation_mode": "optional bool; true when generated with --long-conversation"
}
```

Dialogue segments may also include **`dialogue`**: `[{ "speaker_id": "lewis", "text": "…" }, …]` on `narration_script[]` rows (multi-voice TTS). See [Dialogue and long-conversation modes](#dialogue-and-long-conversation-modes).

- **Sidecars**: After a full (non–dry-run) generation, `narrations/narration{DATE}_voice.json` holds Phase 1 output (`sidecar_role`: `voice`) and `narrations/narration{DATE}_visual.json` holds Phase 2 visuals + telemetry (`sidecar_role`: `visual`). The pipeline still consumes the merged `narration{DATE}.json`; `pipeline.narration_phase2.merge_narration_sidecars_to_pipeline_json` can restitch from two dicts if you load the sidecars manually.

- **segment_index** (each `narration_script` item): 1-based segment number set by two-phase merge; matches `NN.mp4` / `--segments` / `--regenerate-video-segments`. Older narration files without it still work.

- **open_questions** / **editor_notes**: Phase 2 telemetry for human review (non-blocking for automation); new merged narrations always include them (often `[]` and `""`). Older narration files may omit these keys; schema validation treats them as optional.

- **tone_register**: Chosen by the model to match the emotional character of the day (light, warm, reflective, tense, somber). Tone reflects the entry; not every episode is grave or dramatic.

- **`prompt_pack_lineage`**: Written by `generate-narration-two-phase.py` on new merges. Documents which `prompt_packs/` were used for Phase 1 and Phase 2, each pack’s `pack.json` manifest, and SHA-256 **fingerprints** of the pack text files so you can correlate a narration file with exact prompt sources. See **Prompt pack versioning** above. Older narration files may omit this key.

- **scene_spine**: Optional. Object describing the stable visual world for the entire entry. Two-phase merge produces it from Phase 2 `episode_visual_world` (stored on `narration<DATE>_visual.json` sidecar only, not duplicated on canonical merged JSON).
- **visual_style**: Optional. From `config/narration_config.json` theme selection; `narration-to-video` prepends it to every segment’s `video_prompt` so all clips share the same stylization. Vendors truncate to their limit (e.g. 500/800 chars).
- **opening_frame**: Optional per segment. When set and FAL uses a portrait scene anchor, image-to-image can build the still from `opening_frame` plus the episode `scene_spine` world line; image-to-video uses the full segment prompt. Omit when t=0 already matches `video_prompt`.
- **visual_mode / talking_head** (dialogue / long-conversation packs): See keys on `narration_script[]` in the JSON example above. Rules differ for standard vs long-conversation packs — **`pipeline/narration_visual_mode.py`**. `videos-mp3-to-movie.py` concatenates **video only** from each clip (no per-clip audio), then muxes `audio/<date>/final.mp3`, so talking-head MP4s may contain embedded audio from FAL but it is dropped at assembly.

- **map_insertions**: Optional. When exactly one entry has `placement == "after_segment_index"` and `visual_style == "parchment_overlay"`, `videos-mp3-to-movie.py` overlays the map on the segment (via `map_intro.generate_mid_episode_map`). Only parchment_overlay is supported (no standalone map clip). For v2, `run-daily` skips the standalone map intro (step 3b) because two-phase does not use it. **Assembly** allows **at most one** route-map graphic in the final video: if **`fal_segment_covers.json`** also requests a map opening mask, only the **first** map (in playback order) is shown; later map requests become black, and if the narration mid-episode map would be **second**, that parchment overlay is skipped (segment plays without map on top).

- **audio_design / ambient**: Optional. `audio_design.ambience_level` (`none` | `light` | `moderate`) and optional `ambient_tag` (key in `ambient_library/manifest.json`). Default is one bed for all segments; optional **`ambient_regions`** lists `{segment_indices, ambient_tag}` blocks (use `"none"` for silence); at most two distinct non-`none` tags across regions. With **`--ambient`**, assembly mixes under the voice (`pipeline/ambient_audio.py`).

## Tech Stack

- **Narration LLM**: `generate-narration-two-phase.py --llm-provider {claude,openai}` (default `claude`, `ANTHROPIC_API_KEY`; `openai` falls back to `OPENAI_API_KEY`) — see `pipeline/llm_provider.py`. Optional `config/narration_config.json` for visual themes and stylization. Shared helpers in `pipeline/narration_common.py`.
- **pyttsx3** / **OpenAI TTS**: `narration-to-mp3.py` (prefers British male voice for pyttsx3; `--tts openai` optional). Dialogue uses per-speaker routing (`pipeline/tts_speaker_voice.py`); stage-direction stripping and pause gaps (`pipeline/tts_stage_directions.py`).
- **ffmpeg** / **ffmpeg-python**: audio concat, video muxing, duration probing.
- **map_intro.py**: folium (map), geopy (Nominatim geocoding), Playwright (screenshot), Pillow (title overlay), ffmpeg (image→video). **Location**: Prefers journal XML `geoDecl` (TEI): tries current date’s journal, then walks back through earlier journal dates for the last known geo (so entries without geo use the previous entry’s position). Fallback: `location_data/location-dates.json` (waypoints) + Nominatim geocoding. Caches map images in `location_data/cache/` keyed by lat/lon.
- **video_vendors/**: pluggable vendors for `narration-to-video.py`:
  - **sora**: Playwright → ChatGPT Sora web UI (no API key).
  - **google**: `google-genai` → Veo API (`GOOGLE_API_KEY`).
  - **fal**: `fal_client` → fal.ai Wan 2.5 (`FAL_KEY`). If a segment has `reference_character_id` and `character-portraits/<id>.png|jpg|jpeg` exists, runs **image-to-image** (scene opening frame) then **image-to-video** so the clip does not start from a transparent portrait on black. **`video_vendors/fal.py`** softens words that often trigger fal’s content checker (e.g. *carcass* → *animal remains*), disables **prompt expansion** by default (`FAL_ENABLE_PROMPT_EXPANSION=1` to enable), and retries once on content-policy errors with stronger substitutions. Set **`FAL_DISABLE_SAFETY_CHECKER=1`** only if you accept the risk of false negatives. If **i2v rejects the scene anchor** (but portrait-only i2v succeeds), fal writes **`movie-images/<date_id>/fal_segment_covers.json`** so **`videos-mp3-to-movie.py`** can **mask the first N seconds** of that segment with solid black or the route **map** (same parchment PNG as mid-episode) without changing audio or segment duration. Defaults: **`FAL_FALLBACK_COVER_SECONDS`** (default `1.2`), **`FAL_FALLBACK_COVER_MODE`** (`map` or `black`, default `map`). You can edit the JSON by hand or use `--no-opening-cover` / `--opening-cover-seconds` / `--opening-cover-mode` on `videos-mp3-to-movie.py`.
- **requests**: scraping, downloading video from APIs.

## Coding Notes

- All scripts use `pathlib.Path` and `encoding="utf-8"`.
- `generate-narration-two-phase.py` uses `pipeline.narration_common` for config, XML extraction, JSON cleaning, prompt replacements, and schema validation; strips Markdown code fences before JSON parsing; loads `config/narration_config.json` (default). Optionally calls `theme_engine.theme_selector` (recommend before, record after) unless `--no-focus-topic` (legacy: `--no-theme-selector`). `generate-narration.py` is a redirect that runs the two-phase script.
- Expedition tension cues for Phase 1 live in **`pipeline/tension_arcs.py`** (reads `config/narration_tension-arcs.json`).
- `map_intro.py` synthesizes the date (e.g. "September 5, 1803") to `audio/<date_id>/intro_date.mp3` with TTS (`--tts openai` or `pyttsx3`, should match narration-to-mp3), prepends an `{"file": "intro", "duration": N}` entry to `durations.json` and that date audio to `final.mp3` so the narrator states the date while the map is shown; run after `narration-to-mp3.py`. Intro duration follows the date clip length.
- `videos-mp3-to-movie.py` uses sorted `*.mp4` (so `00_intro.mp4`, `01.mp4`, …) and ffmpeg `setpts` to match clip lengths to narration durations.
- `narration-to-video.py --vendor sora` expects Sora UI selectors (`textarea#videoPrompt`, etc.) — may need updates if Sora changes its UI.
- `run-daily.py` default vendor is `fal`; for google/fal it runs `map_intro.py` (step 3b) before generating segment clips.

## Testing Locally

- **Always use `--dry-run`** when testing `narration-to-video.py` locally. This shows what would be done (prompt count, new vs existing clips) without making any API calls.
  ```bash
  python narration-to-video.py 18030902 --vendor google --dry-run
  ```
- For paid video vendors (google, fal), use `--dry-run` first to see how many clips would be generated and estimated cost; then run without `--dry-run` to generate.
- **Video prompt truncation**: Google Veo truncates prompts to 500 chars; fal.ai uses 800. Keep the most important visual details at the start of each `video_prompt`.
- **Regenerate failed clips**: Delete the specific `NN.mp4` and re-run, or use `--segments 3,7` to regenerate only those segments (vendors skip segments whose files still exist).
- **narration-to-video --dry-run** (google/fal): prints **existing vs missing** `NN.mp4` under the output dir for the current `--segments` scope (or all segments).
- **run-daily --dry-run**: Shows full pipeline plan without executing. Use before real runs. Preflight gates still run unless **`--no-preflight`**.
- **run-daily --no-preflight / --strict-preflight**: See [Automation gates](#automation-gates-run-dailypy).
- **run-daily --regenerate-video-segments N,N,...** (google/fal): Moves `NN.mp4` for listed 1-based indices to `movie-images/<date_id>/regen_backup_<timestamp>/`, copies the final `output/lewis_clark_<date_id>_video.mp4` there if present, runs `narration-to-video.py --segments …`, then **always** re-runs assembly. Continues to upload if `--upload`. Does **not** update `state/run_daily_state.json` last_date. **Pipeline UI** exposes the same as an optional text field.
- **run-daily --narration-only**: Scrape (if needed) + `generate-narration-two-phase.py` only; stops before audio/video/assembly/upload and does **not** update `state/run_daily_state.json` last-run date.
- **Parallel clip generation (google/fal):** `narration-to-video.py` requests **up to 10** concurrent clip API jobs; if that run raises an error, it **prints the error and retries with concurrency 1**. Sora is unchanged (single combined video). No CLI flag; `run-daily` does not pass concurrency.
- **run-daily --no-focus-topic**: Passed to `generate-narration-two-phase.py` — disables **theme_engine** (no automatic focus topic; no `recommend`/`record` for that run). Legacy alias: `--no-theme-selector`.
- **run-daily --dialogue / --no-dialogue / --long-conversation / --no-long-conversation**: Passed through to narration generation; see [Dialogue and long-conversation modes](#dialogue-and-long-conversation-modes). **Pipeline UI** checkboxes map to `--dialogue` and `--long-conversation` (`pipeline_ui/options.json.example`).
- **pytest**: `.\.venv\Scripts\python.exe -m pytest tests/ -q` — see **`tests/README.md`** for which tests match which modules.
- **Stale audio:** If `narrations/narration<date>.json` has a different number of segments than `audio/<date>/durations.json` (e.g. you regenerated narration but `final.mp3` still exists), `run-daily` **re-runs `narration-to-mp3`** automatically even when `--skip-existing` is on. If you hit a segment mismatch in `narration-to-video.py`, run TTS for that date or use `run-daily` again.
- **Pipeline UI**: optional local browser UI for `run-daily.py` — `python pipeline_ui/server.py` then open `http://127.0.0.1:8765/`; options load from `pipeline_ui/options.json` on refresh (copy from `pipeline_ui/options.json.example` if missing); **Preview** modals use `/api/preview/narration` and `/api/preview/video` (see `pipeline_ui/README.md`). Each run writes **`logs/pipeline_ui_last.json`** (form values + command + stdout/stderr) for debugging.
- **narration-to-mp3 --tts openai**: Optional higher-quality TTS via OpenAI (requires OPENAI_API_KEY).
- **Dependencies**: `pip install -r requirements.txt -r requirements-youtube.txt` then `playwright install chromium`. See README. When running pipeline steps (including from automation or tools), invoke the venv Python explicitly so all steps see the installed packages.
