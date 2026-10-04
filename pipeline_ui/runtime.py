"""Resolve the running ``pipeline_ui.server`` module from any of its submodules.

``server.py`` is meant to work both as ``python pipeline_ui/server.py`` (script mode --
Python registers it as ``__main__``, not ``pipeline_ui.server``) and as an ordinary
import (``import pipeline_ui.server`` / pytest). Every other pipeline_ui module holds
shared repo-root/paths/state on ``server.py`` and reads it lazily via ``srv()`` so
tests that monkeypatch ``pipeline_ui.server.<name>`` keep working, and so ``server.py``
can import these modules at load time without a circular-import error.

Blindly doing ``import pipeline_ui.server`` in each module would, in script mode,
trigger a *second*, independent execution of server.py's module body (a different
module object under a different name) the moment any module-level code here called
it -- ``srv()`` avoids that by returning whichever of the two module identities is
already loaded and running, importing fresh only as a last resort.
"""

from __future__ import annotations

import sys


def srv():
    mod = sys.modules.get("pipeline_ui.server")
    if mod is not None:
        return mod
    mod = sys.modules.get("__main__")
    if mod is not None and hasattr(mod, "_UI_DIR"):
        return mod
    import pipeline_ui.server as server

    return server
