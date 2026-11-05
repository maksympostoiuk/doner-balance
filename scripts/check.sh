#!/usr/bin/env bash
# Run every quality gate: lint, format, typecheck, GDScript lint, tests, production build.
# Usage: scripts/check.sh            (expects the dev venv in .venv, see README)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin
echo "== ruff (lint)";        $PY/ruff check .
echo "== ruff (format)";      $PY/ruff format --check .
echo "== mypy (types)";       $PY/mypy
echo "== gdlint (GDScript)";  $PY/gdlint godot/economy.gd && $PY/gdformat --check godot/economy.gd
echo "== pytest";             $PY/pytest -q
echo "== build (sdist+wheel)"
out=$(mktemp -d); $PY/python -m build --outdir "$out" >/dev/null && ls "$out"; rm -rf "$out" build
echo "all checks passed"
