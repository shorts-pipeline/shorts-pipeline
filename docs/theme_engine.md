### Theme engine (`theme_engine/`) overview

The theme engine reduces repetitiveness by:

- Tracking recent episodes and their **summaries**.
- Computing **similarity** between the current journal entry and recent entries.
- Managing a pre-filtered **focus topic queue** for “deep dive” episodes.

#### State file: `theme_engine/state.json`

`state.json` has three main parts:

- `entries`: recent narration summaries, e.g.
  - `date_id`: `YYYYMMDD` string (e.g. `"18031206"`).
  - `summary`: one-line episode summary (usually the title or first narration sentence).
  - `journal_excerpt`: short excerpt from the journal entry.
  - `closest_similarity`: precomputed similarity to the most similar other entry.
- `used_focus_topics`: list of topics already used as focus themes (strings, lowercased for comparison).
- `focus_topic_queue`: pre-filtered, prioritized list of candidate focus topics:
  - Built from key topics derived from recent narrations.
  - Excludes:
    - Topics already used (`used_focus_topics`).
    - Adjective-like words from `FOCUS_TOPIC_EXCLUDED_WORDS` (e.g. `"thick"`, `"cold"`, `"similar"`).
  - Ordered by frequency (most common first) to make the next few topics easy to review and override.

#### Core API: `theme_engine/theme_selector.py`

Key functions (used by `generate-narration-two-phase.py` and CLI):

- `recommend(date_id, journal_dir, narrations_dir, state_path, embeddings_path, n=MAX_ENTRIES) -> dict`
  - Decides whether to recommend a `focus_topic` for the given `date_id`.
  - Returns `{"focus_topic": str|None, "reason": str}`.
  - Reasons:
    - `"blank_page"`: no journal text; picks from the queue to allow a theme-based short episode.
    - `"not_similar"`: current entry is not similar enough to recent ones.
    - `"short_entry"` / `"similar_redirect"`: similar enough and has a short or normal entry, respectively.
    - `"no_journal"`, `"no_unused_topic"` as other failure modes.
  - Uses embeddings (if enabled) or word overlap to decide similarity and derive `avoid_topics` from similar entries.
- `record_narration(date_id, narrations_dir, state_path, journal_dir, embeddings_path, max_entries=MAX_ENTRIES) -> None`
  - Called after narration is generated.
  - Updates `entries` with:
    - `summary`, `journal_excerpt`, `closest_similarity`.
  - Manages embeddings store (if enabled).
  - Rebuilds `focus_topic_queue` from the last `max_entries` narrations via `build_focus_topic_queue`.
- `entry_word_count(date_str, journal_dir=DEFAULT_JOURNAL_DIR) -> int`
  - Counts alphabetic words in the journal entry for `--date next` logic in `run-daily.py`.
- `rebuild_focus_topic_queue(state_path, narrations_dir, n=MAX_ENTRIES, strip_key_topics_from_entries=True) -> None`
  - CLI helper (`backfill-topics`) to:
    - Rebuild `focus_topic_queue` from existing narrations.
    - Optionally strip legacy `key_topics` fields from `entries` so state is driven solely by the queue.

#### Similarity and thresholds

- **Embeddings**:
  - When `OPENAI_API_KEY` is set and `THEME_USE_EMBEDDINGS` is not `"0"`, the engine:
    - Embeds `journal_excerpt` for each entry and stores vectors in `embeddings.json`.
    - Uses cosine similarity with `EMBEDDING_SIMILARITY_THRESHOLD` (default 0.65) to decide if the current entry is “similar” to any recent ones.
- **Word overlap fallback**:
  - When embeddings are disabled, uses word-overlap ratio:
    - `SIMILARITY_THRESHOLD` is the minimum ratio (default 0.15).
    - Similar entries contribute topics to `avoid_topics` so we don’t immediately pick the same themes again.

#### Where to change what

- **Change how similarity works or thresholds**:
  - Edit `SIMILARITY_THRESHOLD`, `EMBEDDING_SIMILARITY_THRESHOLD`, or `_word_set` / `similarity_ratio` in `theme_engine/theme_selector.py`.
- **Change which topics become focus candidates**:
  - Edit `extract_summary_and_topics_from_narration` (controls how key topics are derived).
  - Edit `build_focus_topic_queue`:
    - Filter rules (e.g. adjectives via `FOCUS_TOPIC_EXCLUDED_WORDS`).
    - How topics are prioritized (frequency vs. recency, etc.).
- **Change how/when a focus topic is recommended**:
  - Edit `recommend`:
    - Word-count cutoff for `short_entry`.
    - Whether blank pages always try to use a theme.
    - How `reason` is chosen and logged.

