### Character injection overview

Character injection helps keep video prompts visually grounded in real members of the expedition (Lewis, Clark, York, Sacagawea, etc.) instead of generic “the men” / “the dog”.

Two files work together:

- `config/narration_characters.json` – **data**: character definitions and descriptions.
- `pipeline/narration_characters/` – **logic**: matching, aliases, usage tracking, and snippet rendering.

#### `config/narration_characters.json` schema

Top-level:

- `$schema`: documentation URL (optional).
- `version`: schema version string.
- `people`: array of character objects, each with:
  - `id`: short identifier (e.g. `"lewis"`, `"york"`, `"seaman"`, `"drouillard"`).
  - `name`: full display name (e.g. `"Meriwether Lewis"`).
  - `roles`: list of role tags (`"co-captain"`, `"hunter"`, `"interpreter"`, etc.).
  - `active_from`: `"YYYY-MM-DD"` – first date where injection is allowed.
  - `active_to`: `"YYYY-MM-DD"` – last date where injection is allowed.
  - `physical_description`: **single, period-less sentence** describing only visual traits:
    - Apparent age, build, notable facial features, hair/skin, clothing silhouette, and possibly a prop.
    - No internal traits (“long experience”, “draws notice”) or narrative context.
    - Example:  
      `A lean white man in his late twenties with sharp, observant eyes, dark hair tied back, wearing a Continental-style officer’s coat adapted for frontier travel, with worn boots, a weathered hat, and often a field journal`
  - `key_skills`: list of textual skills (`"hunting"`, `"navigation"`, `"carpentry"`, etc.).
  - `skill_keywords`: list of free-text keywords used by the matcher (lowercased in code).
  - `aliases` (optional): list of alternate spellings / journal spellings (e.g. `"drewer"`, `"drewyer"` for Drouillard).
    - These are lowercased in code and used as additional name matches.
  - `dialogue_profile` (optional): temperament / speech-habit cues for **Phase 1 dialogue polish only** (`--dialogue`); not TTS audio and not used for portrait matching. Object with any of:
    - `habitual_words` (alias `lexical_tics`): strings to sprinkle occasionally in spoken dialogue (phase1-dialogue pass).
    - `speech_rhythm` (alias `speech_style`): one line on sentence shape (e.g. rambling vs terse vs order-heavy).
    - `cues` (aliases `behaviors`, `prompt_cues`): bullet strings (weather, hunger, courtesy, command style, etc.).

**Journal mining (Lewis & Clark exploration):** To compare hand-curated `habitual_words` against corpus statistics, run [`scripts/mine_journal_dialogue_profiles.py`](../scripts/mine_journal_dialogue_profiles.py) on local `journal-entries/*.xml`. Reports land in [`config/dialogue_profile_mining/`](../config/dialogue_profile_mining/) (see that folder’s README). Curate only a short list into `dialogue_profile`—do not paste full frequency tables into the polish pass.

#### `pipeline.narration_characters` responsibilities

- **Loading and parsing**:
  - Repo root and paths are defined in `pipeline/narration_characters/paths.py`.
  - `CHAR_PATH` points to `config/narration_characters.json`.
  - `load_characters()`:
    - Parses the JSON, builds `Character` dataclass instances.
    - Enforces presence of `id`, `name`, and `physical_description`.
    - Converts `active_from` / `active_to` to `datetime.date`.
    - Normalizes `skill_keywords` and `aliases` to lowercase.
    - Optional `dialogue_profile` is parsed into `DialogueProfile` on each `Character`; Phase 1 dialogue polish appends it to the user prompt only when **dialogue mode** is enabled (`--dialogue`). Legacy key `narration_voice` is still accepted when reading config.
- **Matching**:
  - `_is_active(character, date)` – checks if a character is active on a given date.
  - `_explicit_name_match(character, narration_text)`:
    - Matches:
      - Full name (`"Meriwether Lewis"`).
      - Last name (`"Lewis"`, `"Clark"`).
      - Any `aliases` (e.g. `"drewer"`, `"drewyer"` → Drouillard).
  - `_build_keyword_sets(character)`:
    - Splits `skill_keywords` into:
      - `activity_keywords` → verb-like phrases (“hunt”, “scout”, “carry”, “build”…).
      - `context_keywords` → noun-like descriptors.
  - `find_character_match(date_id, narration_text, used_counts, max_per_character) -> Optional[CharacterMatch]`:
    - Filters characters to those **active** on the given date.
    - Prefers explicit name matches first.
    - Falls back to activity-based scoring using `skill_keywords`:
      - Seaman has special logic (requires dog + water-related action).
      - Colter has special logic (only when hunting/scouting implied).
    - Respects `used_counts` so no character exceeds `max_per_character`.
    - Returns a `CharacterMatch` or `None`.
- **Snippet rendering**:
  - `render_character_snippet(match, include_name=False) -> str`:
    - Returns `character.physical_description` only (no “This is X”).
    - Phase 2 merge no longer prepends `Subject: ...` when a portrait exists (FAL uses that portrait for i2i anchoring). Without a portrait, `video_vendors.build_prompts` prepends `physical_description` for humans as plain prose, and `Keep this breed and build: ...` for animals.

#### Observer vs portrait (Lewis / Clark)

- If narration **only** names Lewis or Clark as someone who **spots / notices / sees** a **landscape or earthwork** (mounds, formation, fortification, horizon, prairie, ice sheets, etc.)—including phrases like **“In the distance, Clark spots …”**—`find_character_match` **skips** explicit portrait injection for that segment so video can show the feature, not a face. Prompts in Phase 1 are also instructed to prefer neutral or expedition-wide wording for those beats.

#### Video prompt must ground the portrait

- Even when `find_character_match` returns a character from **narration**, `merge_phase1_phase2` only sets `reference_character_id` if **`video_prompt_grounds_character(video_prompt, character)`** is true: the synthesized `video_prompt` (primary + secondary from Phase 2) must contain at least one of that character’s **`id`**, **name** (word or full phrase), **`aliases`**, or **`skill_keywords`** (from `narration_characters.json`). If the visual plan is only landscape/environment with no such tokens, no portrait image is anchored (narration may still mention the name—voiceover vs picture stay aligned).

#### Usage in Phase 2 merge

- `pipeline.narration_phase2.merge_phase1_phase2(...)`:
  - For each segment, it:
    - Synthesizes a `video_prompt` from the `visual_strategy`.
    - Optionally assigns `reference_character_id` (FAL portrait path):
      - Calls `find_character_match(...)`.
      - If a match is found, `character-portraits/<id>.png|jpg|jpeg` exists, and `video_prompt_grounds_character(video_prompt, character)` is true (see above), sets `reference_character_id` and does **not** prepend a `Subject: Name—...` line (FAL uses image-to-image scene anchoring from the portrait before video).
      - If there is no portrait file, or the video prompt does not ground that character, the character is not used for image anchoring; the base `video_prompt` stands alone.
    - Tracks `used_counts` so the same character is not overused.
  - Returns:
    - `merged` narration JSON.
    - `used_counts` for persistent usage tracking.

#### Persistent usage tracking

- File: `pipeline/character_usage.json`
  - Managed by:
    - `load_character_usage()` → returns `{ "totals": {id: count}, "last_updated_date_id": str|None }`.
    - `update_character_usage(date_id, used_counts)` → merges this episode’s counts into `totals` and updates `last_updated_date_id`.
  - Useful for:
    - Monitoring which characters appear most often.
    - Potentially tuning `max_per_character` or refining when certain characters are chosen.

