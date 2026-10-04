# Location data for map intro

- **`location-dates.json`** – Waypoints for the expedition (date, location, optional comments). Used by `map_intro.py` to pick the location and title for each journal date. Move or copy your waypoint file here from `journal-entries/` if you used the legacy path.

- **`cache/`** – Cached map images (no title), keyed by lat/lon so the same location reuses one image across dates. Created automatically; network is used only when a new location is first seen.
