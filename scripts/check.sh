#!/usr/bin/env bash
# Pre-push / pre-commit gate: ruff lint + ruff format check + full pytest suite.
# Run from anywhere:  ./scripts/check.sh
# Wired as a git pre-push hook via .githooks/pre-push (see AGENTS.md "Checks").
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

py="$repo_root/.venv/Scripts/python.exe"   # Windows layout
[ -x "$py" ] || py="$repo_root/.venv/bin/python"   # POSIX layout
if [ ! -x "$py" ]; then
  echo "venv python not found (.venv/Scripts/python.exe or .venv/bin/python) -- create the project venv first." >&2
  exit 1
fi

echo "== ruff check =="
"$py" -m ruff check .

echo "== ruff format --check =="
if ! "$py" -m ruff format --check .; then
  echo "Run '$py -m ruff format .' to fix, then re-commit." >&2
  exit 1
fi

echo "== pytest =="
"$py" -m pytest
