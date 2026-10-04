# Dialogue profile mining (exploration)

Offline reports for Lewis vs Clark **journal voice** statistics. Output informs hand-curation of `dialogue_profile` in [`config/narration_characters.json`](../narration_characters.json); it is **not** wired into the Phase 1 dialogue polish pass automatically.

## Generate a full report

From repo root (requires `journal-entries/*.xml` from [`scripts/scrape-journal-entries.py`](../../scripts/scrape-journal-entries.py)):

```bash
python scripts/mine_journal_dialogue_profiles.py --journal-dir journal-entries --out-dir config/dialogue_profile_mining
```

Optional flags: `--authors lewis,clark`, `--top-n 50`, `--min-term-count 5`.

## Outputs (gitignored when generated locally)

| File | Purpose |
|------|---------|
| `lewis_clark_journal_voice_report.json` | Machine-readable stats, contrastive terms, overlap with curated `habitual_words` |
| `lewis_clark_journal_voice_report.md` | Human-readable summary and spoken-dialogue hints |

Committed **`.example`** files were produced from `tests/fixtures/journal_voice_mining/` (tiny sample corpus) to show shape only—not expedition-scale statistics.

## Tests

```bash
python -m pytest tests/test_mine_journal_dialogue_profiles.py -q
```
