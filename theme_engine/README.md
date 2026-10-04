# Theme Selection Engine

This directory holds the **theme selection engine**: logic to reduce repetitiveness across Lewis & Clark video episodes by comparing the current journal entry to recent ones and, when content is similar, redirecting narration to focus on a single **key topic** in depth instead of covering the same ground again.

## Goal

Journal entries often repeat similar activities (travel, weather, camp, river conditions). The engine:

1. **Tracks recent episodes** — Keeps summaries and key topics from the last 14 narrations in a state file.
2. **Summarizes the current journal** — So we can compare it to recent episodes.
3. **Detects similarity** — If the current entry is similar to recent ones, we avoid another generic pass and instead pick one key topic to explore in depth.
4. **Tracks used focus topics** — Topics we’ve already chosen for “in depth” are moved to a **used** list so we don’t repeat the same focus.

## State file: `state.json`

The engine reads and updates a JSON state file (default: `theme_engine/state.json`). Schema:

```json
{
  "entries": [
    {
      "date_id": "18030904",
      "summary": "Episode title or first line.",
      "journal_excerpt": "Leading slice of journal XML entry text (same length cap as the entry portion of theme embeddings; see ``THEME_JOURNAL_EMBED_MAX_ENTRY_CHARS`` in ``theme_selector.py``).",
      "closest_similarity": 0.64,
      "closest_peer_date_id": "18030903"
    }
  ],
  "used_focus_topics": ["weather", "river conditions"],
  "focus_topic_queue": ["river", "camp", "..."]
}
```

- **entries** — Last **14** episodes **after `record_narration` runs** (oldest → newest). Each has `date_id`, `summary`, `journal_excerpt`, and `closest_similarity`. **`closest_peer_date_id`** (when embeddings are on) is the other `date_id` in this sliding window whose journal embedding has the **highest cosine similarity** to this row (ties broken by last win in the loop). **`closest_similarity`** is that max score (0–1 for cosine). Consecutive expedition days often score high (e.g. ~0.74) because ice, river, and camp life repeat — that is expected, not a bug. Key topics are **not** stored here; they are derived from narration JSON when needed.
- **used_focus_topics** — Focus strings already chosen; avoided on future similar days.
- **focus_topic_queue** — Candidate topics rebuilt from the last 14 **narration files on disk**.

### Important: `state.json` can lag `narrations/`

`entries` only updates when **`record_narration`** completes successfully after a run. If state was reverted, `record` failed, or you moved machines, **`entries` may not list recent `date_id`s** even though `narrations/narration*.json` (and possibly `embeddings.json`) exist.

**`recommend()`** (focus-topic decision) compares the current journal to the **last N narration dates under `narrations/`** (same window as the focus queue), using **`embeddings.json` when a prior date has a stored vector**, not to `entries` alone — so similarity stays aligned with files on disk. After a desync, run **`python theme_engine/theme_selector.py record <date_id>`** for missing dates (in chronological order) or regenerate narrations so `record` runs again.

## Flow

1. **Before generating narration** (e.g. from `run-daily` or `generate-narration`):
   - Call the engine with the **current `date_id`** and path to the **journal XML** (or narration root).
   - Engine loads `state.json`, loads last 14 narrations to get their summaries and `key_topics`, and gets a **current journal summary** (from XML or a future LLM).
   - If **current summary is similar** to recent ones:
     - Choose a **focus topic** from the pool of recent `key_topics` that is **not** in `used_focus_topics`.
     - Return e.g. `{ "focus_topic": "river conditions" }` and append that topic to `used_focus_topics`.
   - If not similar:
     - Return `{ "focus_topic": null }` (normal narration).
   - Pipeline (or narration generator) then uses `focus_topic` when present to instruct the model to focus the current narration on that topic in depth.

2. **After generating narration** for the current date:
   - Call the engine to **record** this episode: add an entry with `date_id`, `summary`, and `key_topics` (extracted from the new narration), and trim `entries` to the last 14.

## Integration (future)

- **generate-narration** (or **run-daily**) can call the theme engine **before** generating to get `focus_topic` and pass it into the user prompt (e.g. “Focus this episode on: river conditions” when similarity is high).
- After narration is written, the pipeline calls the engine to **record** the new episode so the next run has an up-to-date `state.json`.

## Files

- **README.md** — This file.
- **theme_selector.py** — Main module: load/save state, read last 14 narrations, extract summary/topics, similarity check, focus-topic choice, recommend, and record.
- **state.json** — Created/updated by the engine; holds `entries` and `used_focus_topics`.
- **embeddings.json** — Optional embedding store (see below); keyed by `date_id`, used for semantic similarity when enabled.

## Embedding-based similarity

When enabled, the engine uses **OpenAI `text-embedding-3-small`** for **recommend** (focus-topic similarity). Without embeddings, **recommend** does not use word overlap for that decision; **`record_narration`** may still use word overlap for `closest_similarity` when embeddings are off.

- **Store:** Vectors are stored in `theme_engine/embeddings.json` (or a path you pass via `--embeddings`) as JSON text. The file may include **`_embedding_store_note`** (string) explaining that vectors not rebuilt after raising **`THEME_JOURNAL_EMBED_MAX_*`** may still reflect the older ~500-character entry slice. Embedding input is built by **`journal_text_for_theme_embedding`**: leading **entry** text up to **`THEME_JOURNAL_EMBED_MAX_ENTRY_CHARS`** (default 24_000), plus linked TEI notes up to **`THEME_JOURNAL_EMBED_MAX_NOTES_CHARS`** (default 12_000), capped by **`THEME_JOURNAL_EMBED_MAX_TOTAL_CHARS`** (default 28_000). After changing those constants, run **`python theme_engine/theme_selector.py backfill`** (with `--embeddings` if non-default) so stored vectors and `state.json` similarity fields stay consistent, or **`python theme_engine/theme_selector.py refresh-embeddings 18040513 18040515`** to re-embed only listed `date_id`s (prints pairwise cosine among them). Each embedding is ~25–30 KB in JSON (1536 dimensions); if storage becomes a concern, vectors can be switched to binary (~6 KB each) via e.g. `struct` pack/unpack.
- **Recommend:** The current journal excerpt is embedded; similarity is computed vs **prior episodes in the last N narration files** that have vectors in `embeddings.json`. If cosine similarity to **any** of those ≥ **0.65** (`EMBEDDING_SIMILARITY_THRESHOLD`), the day is “similar” and a focus topic may be chosen. The decision is **audited** in the narration JSON as `theme_selector_decision` (closest prior date, score, threshold) when a focus topic is applied. **There is no word-overlap fallback:** if embeddings are off (`THEME_USE_EMBEDDINGS=0` or no `OPENAI_API_KEY`), or the current text cannot be embedded, or no prior in the window has a vector, the engine returns **not_similar** for journal-based focus (blank-page focus handling is unchanged). `SIMILARITY_THRESHOLD` remains used only for **`record_narration`** / backfill when embeddings are disabled.
- **Record:** After recording, the engine embeds the journal excerpt, saves it to the store (trimmed to the last N `date_id`s), and sets `closest_similarity` on the new `entries` row from max cosine vs other **recorded** entries (or word overlap if embeddings are disabled), plus **`closest_peer_date_id`** pointing at which peer achieved that max.
- **Backfill:** `backfill` adds embeddings for entries that don’t have one, trims the store to current state entries, then recomputes `closest_similarity` and **`closest_peer_date_id`** from cosine (same-date excluded; duplicate `date_id` → 0).
- **Quick audit:** `python theme_engine/theme_selector.py verify 18040109` prints stored values, recomputed max similarity vs **full** `embeddings.json`, and which `date_id` is closest (useful to compare to `closest_peer_date_id`, which is restricted to **other rows in `state.entries`**).

**Enabling / disabling:**

- Set **`OPENAI_API_KEY`** for embedding API calls. If unset, **`recommend`** will not assign a similarity-based focus topic (only blank-page handling may still pick a focus). **`record_narration`** still computes `closest_similarity` via word overlap when embeddings are off.
- Set **`THEME_USE_EMBEDDINGS=0`** to disable embedding API use; same behavior as no key for **recommend** (no similarity-based focus).

## Similarity and topics (extensibility)

- **Summarization** — Uses an LLM to summarize journal XML and/or narration content.
- **Similarity (recommend)** — Embeddings (cosine) only when enabled; no word-overlap fallback. **Record/backfill** may still use word overlap when embeddings are disabled.
- **Key topics** — Uses an LLM to extract a short list of key topics from each narration’s title and segments.

The module is structured so these steps can be replaced or extended without changing the overall flow.
