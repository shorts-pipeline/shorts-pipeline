# Prompt Packs: What Is Sent to the AI

This directory stores prompt-pack text files used by the two-phase narration pipeline.

The key distinction:

- `phase1_system.txt` and `phase2_system.txt` are **system messages** (loaded from prompt packs).
- Phase 1 and Phase 2 **user messages** are built in Python from runtime data (journal text, date, Phase 1 JSON, profile options, etc.).
- The model does **not** read local files directly. Any mention of local config paths in prompt text is explanatory guidance; enforcement happens in pipeline validation/merge code.

## Pack catalog

| Pack ID | When to use | CLI / profile |
|---------|-------------|----------------|
| `lewis_clark` | Default expedition narrator VO | Default profile `config/profiles/lewis_clark.json` |
| `lewis_clark_dialogue` | Narrator-led + cast dialogue (≤3 dialogue segments) | `--dialogue` on `generate-narration-two-phase.py` / `run-daily.py` |
| `lewis_clark_long_conversation` | Dense ping-pong cast exchanges, more segments | `--long-conversation` (implies dialogue) |
| `generic_documentary` | Non-journal plain-text episodes | `--profile config/profiles/generic_plain.json` |

Shared snippets live under `prompt_packs/_shared/` (included in pack fingerprints when referenced from pack text).

Versioning and lineage: bump each pack’s `pack.json` `version` when prompt text changes; see **`docs/AGENTS-pipeline.md`** (Prompt pack versioning).

## File Roles in `prompt_packs/<pack_id>/`

- `phase1_system.txt` (required): System instructions for Phase 1 narrator JSON generation.
- `phase1_dialogue_system.txt` (optional): Used in **dialogue mode** (`--dialogue` or `--long-conversation`). Second API pass after Phase 1; polishes `segments[].dialogue[].text` while merge logic preserves structure from Phase 1. Shipped under `lewis_clark_dialogue/` and `lewis_clark_long_conversation/`.
- `phase2_system.txt` (recommended): System instructions for Phase 2 visual-plan JSON generation.
  - If missing for a selected pack, Phase 2 falls back to `prompt_packs/lewis_clark/phase2_system.txt`.
- `pack.json` (recommended): Human metadata (`id`, `version`, `description`) used for lineage and review.
- `phase1_user.txt` (optional, currently not loaded at runtime): Included in pack fingerprinting/lineage if present, but Phase 1 user text is assembled from code/profile settings today.

## End-to-End Message Assembly

## Phase 1 (Narration + Metadata)

### System message (single file, sent as-is)

- Source: `prompt_packs/<phase1_pack>/phase1_system.txt`
- Loader: `pipeline/narration_phase1.py` -> `build_phase1_system_prompt(...)`
- Behavior: Reads UTF-8 text and sends it as one system message.

### User message (concatenated in code)

Source builder: `pipeline/narration_phase1.py` -> `build_phase1_user_prompt(...)`

The user message is assembled from multiple runtime pieces, joined with blank lines:

- Opening template from pipeline profile (`phase1_user_prompt.user_opening_template`)
- Length instruction block
- Optional tension/milestone and urgency text
- Optional focus-topic block
- Optional director notes (dialogue vs narrator mode differences)
- Optional voice prompt section (dialogue mode only when **not** using the phase1-dialogue split; with `lewis_clark_dialogue`, cues move to the phase1-dialogue user prompt—see below)
- Optional journal-author line
- Source body heading + actual journal text
- Optional TEI editorial notes
- Optional macro-event editorial priority note

Then sent as the single user message to OpenAI with the Phase 1 system message.

## phase1-dialogue (dialogue mode only)

When `generate-narration-two-phase.py` runs with **`--dialogue`** (and focus-topic is off), after Phase 1 succeeds it runs one additional Chat Completions call:

- **System:** `prompt_packs/<phase1_pack>/phase1_dialogue_system.txt` (required for `lewis_clark_dialogue`; if missing, the pass is skipped with a warning).
- **User:** `date_id`, full Phase 1 JSON, plus **`dialogue_profile_prompt_section()`** from [`pipeline/narration_characters/voice_prompt.py`](pipeline/narration_characters/voice_prompt.py) (per-character dialogue polish cues from config).

The pipeline merges **only** `dialogue[].text` strings from the model output back onto the Phase 1 object (see [`pipeline/narration_phase1_dialogue.py`](pipeline/narration_phase1_dialogue.py)), then validates again before Phase 2.

### Retry behavior

If Phase 1 output is invalid JSON/schema, the next attempt keeps the same system message and replaces the user message with a "fix JSON" instruction plus the previous cleaned output.

## Phase 2 (Visual Plan)

### System message (file + placeholder substitution)

- Source template: `prompt_packs/<profile.prompt_pack>/phase2_system.txt`
- Loader: `pipeline/prompt_pack_text.py` -> `load_phase2_system_template(...)`
- Fallback: `lewis_clark/phase2_system.txt` when selected pack is missing this file

Before sending, Phase 2 system text is transformed in code (`build_phase2_system_prompt(...)`):

- Replaces required placeholders (ambient enum/csv, anchor block, focus-tone text)
- Optionally swaps the historical-tone section based on profile flags

So Phase 2 system is not strictly raw-file text; it is file text plus deterministic runtime substitutions.

### User message (concatenated in code)

Source builder: `pipeline/narration_phase2.py` -> `build_phase2_user_prompt(...)`

The Phase 2 user message is assembled from:

- Conversion/task instructions for visual planning
- Entire Phase 1 JSON payload (pretty-printed)
- Optional character-anchor hint lines
- Optional style directive
- Optional focus-topic visual guidance
- Optional location context from Phase 1 metadata
- Core-location-override guidance
- Optional seasonal ambient hint from `date_id`
- Optional `user_suffix` appended last

Then sent as one user message with the Phase 2 system message.

### Retry behavior

If Phase 2 output is invalid JSON/schema, the next attempt keeps the same system message and replaces the user message with a "fix JSON / preserve segment count" instruction plus the previous cleaned output.

## What Is Concatenated vs Sent Alone

- Sent mostly "alone" from file:
  - Phase 1 system: `phase1_system.txt` (verbatim file content)
  - Phase 2 system: `phase2_system.txt` template + runtime placeholder substitution
- Concatenated in code:
  - Phase 1 user prompt (runtime blocks + journal content)
  - Phase 2 user prompt (runtime instructions + embedded Phase 1 JSON + optional blocks)

## User vs System Split (Quick Reference)

- **Phase 1 system:** Output contract, narrative rules, chronology/entity constraints, dialogue schema rules (when dialogue pack).
- **Phase 1 user:** Episode-specific context (date, source journal, optional notes, profile-driven instructions).
- **Phase 2 system:** Output contract for visual plan JSON, visual safety/continuity rules, allowed fields and constraints.
- **Phase 2 user:** The concrete Phase 1 payload to transform, plus per-run style/focus/location/seasonal hints.

## Why prompts may mention `config/...`

Prompt text may refer to pipeline concepts backed by local config (allowed speaker IDs, shot templates, etc.). The external model cannot read those files directly. The pipeline enforces and enriches output locally during validation and merge.

## Versioning Reminder

When you edit prompt-pack text files, bump that pack's `pack.json` `version` so changes are identifiable in review and in `prompt_pack_lineage` metadata.
