# Repository layout

This repo mixes **entry-point scripts** (repo root), **importable pipeline code** (`pipeline/`), **committed configuration** (`config/`), **theme tooling** (`theme_engine/`), **video vendor adapters** (`video_vendors/`), and **large generated trees** (mostly gitignored).

## Top level

| Path | Role |
|------|------|
| `*.py` at root | CLI entry points (`run-daily.py`, `generate-narration-two-phase.py`, `narration-to-video.py`, …) and small shared helpers (`pipeline_logging.py`, `video_manifest.py`). |
| `pipeline/` | Python package: narration phases, character matching, ambient mixing, tension arcs, portrait compositing. Import as `from pipeline.narration_common import …`. Module map: **`docs/PIPELINE_MODULES.md`**. |
| `prompt_packs/` | Phase 1/2 system prompts + `pack.json` versioning; catalog in **`prompt_packs/README.md`**. |
| `config/` | Committed JSON: `narration_config.json`, `video_prompt_cues.json`, `narration_characters.json`, `narration_tension-arcs.json`, `profiles/`, `talking_head_shots.json`. |
| `tests/` | Pytest suite; quick map in **`tests/README.md`**. |
| `theme_engine/` | Focus-topic selector, embeddings, CLI. |
| `video_vendors/` | Sora / Google / fal adapters used by `narration-to-video.py`. |
| `scripts/` | Utilities (scraping, portraits, analysis). |
| `pipeline_ui/` | Local browser UI for `run-daily` (options JSON is machine-local; see `options.json.example`). |
| `character-portraits/` | Portrait PNGs/JPEGs keyed by `character_id` (tracked assets, not pipeline bulk output). |
| `location_data/` | Waypoints JSON; `cache/` is gitignored. |

## Generated / machine-local (typical gitignore)

These directories hold **outputs** or **cache** from running the pipeline. They stay at the repo root so paths stay short and scripts already assume these names.

| Path | Contents |
|------|----------|
| `journal-entries/` | Scraped TEI XML from CDRH. |
| `narrations/` | Generated narration JSON (+ sidecars). |
| `audio/<date_id>/` | TTS segments and `final.mp3`. |
| `movie-images/<date_id>/` | Per-segment video clips and map intro. |
| `output/` | Final muxed `lewis_clark_<date>_video.mp4` and manifests. |
| `logs/` | Pipeline UI and other logs. |
| `ambient_library/` | Optional local ambient loops + manifest (not in git). |

**Why not a single `workspace/` parent?** Moving these would touch every path in `run-daily`, assembly, map intro, and the UI. The current layout is documented here so you can add a wrapper or symlink layout later without surprise.

## Runtime data next to code

| Path | Role |
|------|------|
| `pipeline/character_usage.json` | Persistent counts for character injection (updated by narration generation). |
| `state/run_daily_state.json` | Last successful journal date for `run-daily --date next` (machine-local; see example file). |
| `state/week_arcs/` | Cached variable-length week arc plans (`week_<start_date_id>.json`). |

## Imports

Run scripts from the **repository root** (or ensure `sys.path` includes the root) so `import pipeline.…` resolves. The project venv should be active; see `AGENTS.md`.
