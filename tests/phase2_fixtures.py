"""Shared Phase 2 JSON fragments for tests."""


def episode_visual_world_for_segments(segment_count: int) -> dict:
    n = max(1, int(segment_count))
    if n == 1:
        blocks = [
            {
                "block_id": "main",
                "segment_indices": [1],
                "place_label": "Expedition camp on the Missouri River",
                "continuity_note": "Same camp and riverbank throughout.",
                "lighting": "Flat overcast midday, cool grey light.",
            }
        ]
    else:
        blocks = [
            {
                "block_id": "main",
                "segment_indices": list(range(1, n)),
                "place_label": "Expedition camp on the Missouri River bank",
                "continuity_note": "Same camp, boat, and bank.",
                "lighting": "Flat overcast midday, cool grey light.",
            },
            {
                "block_id": "close",
                "segment_indices": [n],
                "place_label": "same camp at dusk",
                "continuity_note": "Same tent line; evening light only.",
                "lighting": "Warm low firelight, deep shadows.",
            },
        ]
    return {
        "primary_set": "Shared expedition camp with keelboat at the Missouri bank.",
        "lighting_arc": "Natural daylight through the episode.",
        "color_palette": "Desaturated cool-grey grade, soft overcast key from camera-left, low contrast.",
        "persistent_anchors": ["same keelboat at bank", "same tent row"],
        "visual_blocks": blocks,
    }
