# Lewis & Clark YouTube Pipeline — AI Agent Context

## What This Project Does

A **Lewis & Clark expedition video pipeline**: journal XML → OpenAI narration script → TTS audio → AI/manual video → final MP4. All scripts use a date identifier (e.g. `18030830` for Aug 30, 1803).

## Where to Read Next

| Doc | Contents |
|-----|----------|
| **[docs/AGENTS-pipeline.md](docs/AGENTS-pipeline.md)** | Pipeline steps, Shorts, key conventions, v2.0 narration JSON, **automation gates**, **talking-head / FAL / run reports**, prompt pack versioning, tech stack, coding notes, testing |
| **[docs/AGENTS-theme-engine.md](docs/AGENTS-theme-engine.md)** | `theme_engine/` focus topics, state, CLI, tuning |
| **[docs/DIRECTORY_LAYOUT.md](docs/DIRECTORY_LAYOUT.md)** | `pipeline/` vs `config/` vs generated output directories |
| **[docs/PIPELINE_MODULES.md](docs/PIPELINE_MODULES.md)** | `pipeline/` module map (narration, TTS, visual_mode, gates) — start here when editing Python |
| **[docs/AGENTS-jira.md](docs/AGENTS-jira.md)** | **Jira backlog:** comment **suggested handling**; **Done** when fixed; **push to GitHub** with **issue key** in the commit message; local scripts + MCP notes |

## Machine-local files (not in git)

These paths are **gitignored**; each has a committed **`.example`** you can copy after clone:

- `latest_narration.url` / `latest_video.url` — Internet shortcuts updated by the narration pipeline (point at your repo paths).
- `state/run_daily_state.json` — last successful `run-daily` journal date (`YYYY-MM-DD`).
- `state/episode_diversity_lewis_clark.json` — cached LLM episode-diversity audit (refresh via `scripts/refresh_episode_diversity_audit.py`).
- `state/week_arcs/week_*.json` — cached 7-journal-day week arc plans (create via `scripts/plan_week_arc.py` or `--use-week-arc` on run-daily).
- `pipeline_ui/options.json` — Pipeline UI form schema and defaults.
- `theme_engine/state.json` — theme_engine focus-topic history + queue; rewritten on every narration run. Starts empty; rebuilds from `narrations/`.
- `pipeline/character_usage.json` — cumulative cast-usage tallies for character balancing; rewritten by `generate-narration-two-phase.py`. Starts empty.
- `state/fal_usage_state.json` — incremental cursor for `scripts/fal_usage_to_csv.py` (last fetched FAL usage window). Recreated on next run if absent.

```text
copy pipeline_ui\options.json.example pipeline_ui\options.json
copy state\run_daily_state.json.example state\run_daily_state.json
copy state\episode_diversity_lewis_clark.json.example state\episode_diversity_lewis_clark.json
copy ambient_library\manifest.json.example ambient_library\manifest.json
copy theme_engine\state.json.example theme_engine\state.json
copy pipeline\character_usage.json.example pipeline\character_usage.json
copy state\fal_usage_state.json.example state\fal_usage_state.json
```

On Unix: `cp …`. Adjust `latest_*.url.example` URLs to match your machine.

## Key Conventions (short)

- Use the project **`.venv`** when running Python (`AGENTS-pipeline.md` has the full table).
- **Date ID** `18030830` = YYYYMMDD; journal XML files use `YYYY-MM-DD.xml`.
- **Composite portraits**: `scripts/build_portrait.py --preset lewis_clark` (and other presets, e.g. `lewis_drouillard`; see script docstring).
- **Prompt edits**: When you change `prompt_packs/*/`, bump that pack’s `pack.json` `version` and read **docs/AGENTS-pipeline.md** (section **Prompt pack versioning**); merged narration stores `prompt_pack_lineage` + fingerprints for traceability.

## Checks (lint + tests)

- **Run before pushing:** `.\scripts\check.ps1` (PowerShell) or `./scripts/check.sh`
  (Git Bash) — runs `ruff check .`, `ruff format --check .`, then the full
  `pytest` suite. Config for both is in `pyproject.toml`.
- **Pre-push hook** (opt-in, once per clone): `git config core.hooksPath .githooks`
  wires `.githooks/pre-push` to run the same checks; `git push --no-verify` bypasses.
- **Dev tools:** `pip install -r requirements-dev.txt` (pinned `ruff`, `pytest`).
- **Dependency pins:** `requirements*.txt` use `==`. Bump deliberately — change the
  pin, rebuild the venv, run the checks, commit. Don't `pip install -U` casually.
- **Formatting:** the repo was run through `ruff format .` once (2026-09-10); keep
  it formatted going forward — `scripts/check.*` / the pre-push hook fail on drift.
  If a diff shows unrelated reformatting, run `ruff format .` on just the files you
  touched before committing. `.git-blame-ignore-revs` hides that one-time reformat
  commit from `git blame` — enable with `git config blame.ignoreRevsFile
  .git-blame-ignore-revs` (once per clone; GitHub/most GUIs pick it up automatically).
- Ruff still ignores `E501` (594 pre-existing long lines `ruff format` won't safely
  wrap — long strings/URLs/comments) and two scoped `E402` per-file-ignores for the
  `sys.path`-preamble scripts; everything else is enforced.
