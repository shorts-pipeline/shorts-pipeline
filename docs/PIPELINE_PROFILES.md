# Pipeline profiles and non–Lewis & Clark sources

This repo can drive the same narration → TTS → video → mux path from **TEI journal XML** (Lewis & Clark) or from **arbitrary UTF-8 text**, using a **profile** JSON file that selects prompts, source type, feature flags, and default output naming.

For the full step table and v2.0 narration shape, see [AGENTS-pipeline.md](AGENTS-pipeline.md).

---

## Episode ID (eight digits)

The pipeline still uses an **eight-digit string** everywhere paths expect a “date id”: `audio/<id>/`, `movie-images/<id>/`, `narrations/narration<id>.json`.

- For journals it is calendar `YYYYMMDD` (e.g. `18040510`).
- For other shows you can use any eight digits that are unique for your episode (e.g. a hash prefix, or a synthetic id like `20260101`).

---

## Profile files

| File | Purpose |
|------|---------|
| [config/profiles/lewis_clark.json](../config/profiles/lewis_clark.json) | Default: `journal-entries/` TEI XML, Lewis & Clark prompt pack, theme engine, character hints, tension arcs, `output_prefix` `lewis_clark`. |
| [config/profiles/generic_plain.json](../config/profiles/generic_plain.json) | Plain text file input, generic documentary prompts, no theme/tension/seasonal ambient tricks, `output_prefix` `episode`. |

Fields worth knowing:

- **`source.type`**: `tei_journal` (XML by date) or `plain_file` (text from `--source-text-file`).
- **`prompt_pack`**: directory name under `prompt_packs/<name>/` (e.g. `lewis_clark`, `generic_documentary`); Phase 1 loads `phase1_system.txt` from there. Phase 2 loads `phase2_system.txt` from the same pack; if that file is missing, it falls back to `prompt_packs/lewis_clark/phase2_system.txt`. **`--dialogue`** and **`--long-conversation`** override the profile pack at runtime — see **`docs/AGENTS-pipeline.md`** (Dialogue and long-conversation modes) and **`prompt_packs/README.md`** (pack catalog).
- **`output_prefix`**: first part of the assembled MP4 name: `<prefix>_<8digit>_video.mp4`.
- **`phase1_user_prompt` / `phase2` / `features`**: tune expedition-specific user prompts, Phase 2 “historical only” visuals, theme engine, character hints.
- **`long_document`**: reserved (`parent_document_id`, `chunk_index`); for future multi-chunk runs. Narration output may include `source_parent_document_id` / `source_chunk_index` when set in the profile.

---

## `generate-narration-two-phase.py`

Always loads a profile. If you omit `--profile`, it uses `config/profiles/lewis_clark.json` (same behavior as before for journal-only workflows).

```text
python generate-narration-two-phase.py <8_digit_id> [--profile PATH] [--source-text-file PATH] [--model MODEL] [--no-focus-topic] [--dry-run] [--xml-dir DIR] [--config PATH] ...
```

- **`--profile PATH`**: profile JSON (absolute or repo-relative).
- **`--source-text-file PATH`**: required when the profile’s `source.type` is `plain_file`; UTF-8 text file.
- **`--xml-dir DIR`**: journal XML directory for `tei_journal` (default `journal-entries`); forwarded to the journal source path.
- **`--no-focus-topic`**: still disables theme_engine recommend/record regardless of profile. Legacy alias: `--no-theme-selector`.

Merged narration JSON includes **`pipeline_profile_id`** (the profile’s `id` field) so you can see which profile produced the file.

---

## Generic text: minimal end-to-end

1. **Narration** (pick an eight-digit `EPISODE_ID` and a `.txt` source):

   ```text
   python generate-narration-two-phase.py EPISODE_ID --profile config/profiles/generic_plain.json --source-text-file path/to/source.txt
   ```

   Or use the wrapper:

   ```text
   python run-episode.py EPISODE_ID path/to/source.txt [--model gpt-4o] [--dry-run]
   ```

2. **Audio** (unchanged):

   ```text
   python narration-to-mp3.py EPISODE_ID --tts openai
   ```

3. **Video clips** (unchanged vendor flags):

   ```text
   python narration-to-video.py EPISODE_ID --vendor fal
   ```

4. **Mux** — use the **same prefix** as the profile’s `output_prefix` (`episode` for `generic_plain.json`):

   ```text
   python videos-mp3-to-movie.py EPISODE_ID --output-prefix episode
   ```

   Default prefix is `lewis_clark`, which matches the Lewis & Clark profile.

---

## `run-daily.py`

Optional flags are forwarded where relevant:

- **`--profile PATH`** → `generate-narration-two-phase.py`
- **`--source-text-file PATH`** → same (for plain-file profiles)
- **`--output-prefix PREFIX`** → `videos-mp3-to-movie.py` and the expected final MP4 path for this run (default `lewis_clark`)

Journal scrape / date logic is unchanged when you run the usual journal workflow without `--profile` / `--source-text-file`.

---

## `videos-mp3-to-movie.py`

```text
python videos-mp3-to-movie.py <8_digit_id> [--output-prefix PREFIX] [--shorts | --wide-screen] [--ambient] ...
```

`--output-prefix` defaults to `lewis_clark`. Manifests and upload helpers accept any stem of the form `<letters/digits/underscores>_<8digits>_video`.

---

## What the generic profile turns off

Compared to the default expedition profile, `generic_plain` typically has:

- No **theme_engine** focus topic or state updates.
- No **tension arc** inserts in the Phase 1 user prompt.
- No **character anchor** portrait hints from `character_injection`.
- Phase 2 **seasonal ambient** hints from `date_id` (not meaningful for arbitrary ids).
- Relaxed Phase 2 rules so **modern-period** visuals are allowed when the narration implies them.

**Map intro** (`map_intro.py`) is still skipped automatically for **v2** narration, as documented in AGENTS-pipeline; it remains journal/geo oriented when you do run it for older flows.

---

## Adding a new show

1. Copy `config/profiles/lewis_clark.json` or `generic_plain.json` to a new name.
2. Adjust `source`, `prompt_pack`, `output_prefix`, and feature flags.
3. For a new prompt voice, add `prompt_packs/<your_pack>/phase1_system.txt` (required for Phase 1); you can start by copying `generic_documentary` or `lewis_clark` and editing.
4. Run `generate-narration-two-phase.py` with `--profile` pointing at your JSON; mux with `--output-prefix` matching `output_prefix`.

---

## Quick reference

| Goal | Command / note |
|------|----------------|
| Default journal episode | `python generate-narration-two-phase.py 18040510` (implicit `lewis_clark` profile) |
| Explicit L&C profile | `--profile config/profiles/lewis_clark.json` |
| Text file episode | `--profile config/profiles/generic_plain.json --source-text-file file.txt` |
| Wrapper for generic file | `python run-episode.py <id> file.txt` |
| Mux generic output | `videos-mp3-to-movie.py <id> --output-prefix episode` |
| Full daily run + custom profile | `run-daily.py ... --profile ...` and optional `--source-text-file`, `--output-prefix` |

Related implementation modules: `pipeline/pipeline_profile.py`, `pipeline/source_episode.py`, `pipeline/output_naming.py`.
