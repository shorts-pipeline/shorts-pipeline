# Spring ambient: tags and prompt hints

Manifest keys live in `manifest.json` and are enumerated automatically for Phase 2. This note maps **spring** settings to tags (see also `_spring_ambient_user_hint` in `lib/narration_phase2.py` for March–May).

## Tag cheat sheet (expedition region, spring)

| Setting | Tags to consider |
|--------|-------------------|
| Open march, steady breeze | `prairie_wind` |
| Hard wind (no dedicated short gust loop) | `prairie_wind` or `distant_thunder` if a storm front / rain mood fits |
| Main river, boats, flood stage | `river` |
| Small branch, ford, riffle | `creek` |
| Timber, bottomland, mixed woods | `forest` |
| Daytime birdsong, songbirds on the bank | `chirping_birds` |
| Marsh, slough, standing water | `wetland` |
| Frog-heavy shore / night pond | `spring_frogs` |
| Distant storm / rain under clouds | `distant_thunder` (rain-based bed; not isolated lightning) |
| Hail or ice pellets (journal-stated) | `hailstorm` (impact bed; not rain-only) |
| Night camp, fire | `campfire` |
| Dusk / night insects | `evening_insects` |

## Removed

- **`stormy_wind`** was removed (loop too short for clean use). Use **`distant_thunder`** or **`prairie_wind`** per scene.

## Adding more tags

Add WAV + `manifest.json` entry + row here and in `CREDITS-ambient.md`.
