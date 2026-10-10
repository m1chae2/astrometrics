#!/bin/bash
# Purpose: Prepare a Claude Code cloud session so it can run this repo's
# checks. Builds the Python 3.14 virtualenv the project requires, adds the
# lint tools CI pins, and writes a marker file that agents can wait on.
#
# Description: Runs only in a remote (cloud) session. It is idempotent: a
# second run with the venv already present only re-installs the project in
# editable mode and the lint pins. The container image carries Python 3.13,
# which cannot import this code (PEP 758 syntax), so the hook installs 3.14
# with uv and points build/linux/setup_venv.sh at it. Front-end packages are
# installed when network allows; a failure there does not fail the hook.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT"

log() { echo "[session-start] $*"; }

# ---------------------------------------------------------------------------
# 1. Python 3.14 (the project floor; see pyproject.toml requires-python)
# ---------------------------------------------------------------------------
PY314=""
if command -v python3.14 >/dev/null 2>&1; then
  PY314="$(command -v python3.14)"
else
  UV="$(command -v uv || true)"
  if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
  fi
  if [ -z "$UV" ]; then
    log "Installing uv to fetch Python 3.14"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV="$HOME/.local/bin/uv"
  fi
  "$UV" python install 3.14 >/dev/null
  PY314="$("$UV" python find 3.14)"
fi
log "Using $PY314 ($("$PY314" --version))"

# ---------------------------------------------------------------------------
# 2. Project virtualenv through the repo's own script
# ---------------------------------------------------------------------------
rm -f .venv/.ready
PYTHON_BIN="$PY314" ./build/linux/setup_venv.sh

# The lint pins match .github/workflows/lint.yml. setup_venv.sh installs the
# mcp extra only, so ruff and import-linter are added here.
.venv/bin/pip install --quiet ruff==0.16.1 import-linter==2.15

# ---------------------------------------------------------------------------
# 3. Front-end packages (best effort; the Python suites do not need them)
# ---------------------------------------------------------------------------
if command -v npm >/dev/null 2>&1 && [ ! -d node_modules ]; then
  log "Installing npm packages (best effort)"
  npm install --no-audit --no-fund >/dev/null 2>&1 || log "npm install failed; UI checks are unavailable"
fi

# ---------------------------------------------------------------------------
# 4. Session environment and the ready marker
# ---------------------------------------------------------------------------
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo 'export PYTHONPATH="."'
    echo 'export ASTROMETRICS_TESTING="1"'
    echo 'export ASTROMETRICS_FAST_TEST="1"'
  } >> "$CLAUDE_ENV_FILE"
fi

touch .venv/.ready
log "Ready: .venv/bin/python, ruff, pytest, lint-imports"
