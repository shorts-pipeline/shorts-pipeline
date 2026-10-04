"""Repository paths for character data, config, and portraits."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_DIR = _REPO_ROOT / "pipeline"
CONFIG_DIR = _REPO_ROOT / "config"
CHAR_PATH = CONFIG_DIR / "narration_characters.json"
PORTRAITS_DIR = _REPO_ROOT / "character-portraits"
CHAR_USAGE_PATH = PIPELINE_DIR / "character_usage.json"

# Backwards compatibility (older docs referred to "lib")
LIB_DIR = PIPELINE_DIR
