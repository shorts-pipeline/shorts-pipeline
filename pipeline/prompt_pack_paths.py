"""Where prompt packs live on disk.

Default is ``<repo>/prompt_packs``. Set ``PROMPT_PACKS_DIR`` to load packs from
somewhere else (e.g. a separate private repo shared across video projects).
A relative value is resolved against the repo root; ``~`` is expanded.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "PROMPT_PACKS_DIR"


def packs_root(repo_root: Path | str) -> Path:
    repo = Path(repo_root)
    override = os.environ.get(ENV_VAR, "").strip()
    if not override:
        return repo / "prompt_packs"
    p = Path(override).expanduser()
    return p if p.is_absolute() else repo / p
