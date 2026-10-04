"""Persistent character usage totals (pipeline/character_usage.json)."""

from __future__ import annotations

import json
from typing import Any

from pipeline.narration_characters.paths import CHAR_USAGE_PATH


def load_character_usage() -> dict[str, Any]:
    if not CHAR_USAGE_PATH.exists():
        return {"totals": {}, "last_updated_date_id": None}
    try:
        data = json.loads(CHAR_USAGE_PATH.read_text(encoding="utf-8"))
        totals = data.get("totals") or {}
        return {"totals": dict(totals), "last_updated_date_id": data.get("last_updated_date_id")}
    except (json.JSONDecodeError, OSError):
        return {"totals": {}, "last_updated_date_id": None}


def update_character_usage(date_id: str, used_counts: dict[str, int]) -> None:
    if not used_counts:
        return
    data = load_character_usage()
    totals = data["totals"]
    for cid, count in used_counts.items():
        totals[cid] = totals.get(cid, 0) + count
    out = {"totals": totals, "last_updated_date_id": date_id}
    CHAR_USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHAR_USAGE_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
