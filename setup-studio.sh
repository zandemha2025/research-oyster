#!/usr/bin/env bash
# Create the isolated virtualenv for Research Oyster Studio (the live chat UI).
# Kept separate from .venv because claude-agent-sdk needs mcp<2.0 and the MCP server
# needs mcp>=2.0; stdio subprocess isolation keeps them apart. See docs/STUDIO.md.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3.11}"
if ! command -v "$PY" >/dev/null 2>&1; then PY=python3; fi

echo "Creating .venv-studio with $PY ..."
"$PY" -m venv .venv-studio
.venv-studio/bin/pip install --quiet --upgrade pip
.venv-studio/bin/pip install --quiet -r requirements-studio.txt

echo
echo "Studio venv ready."
echo "Next:"
echo "  1) Ensure the main app venv exists (.venv) and the database is migrated."
echo "  2) Authenticate:  claude setup-token   (or rely on an already-logged-in claude CLI)"
echo "     then:          export CLAUDE_CODE_OAUTH_TOKEN=...    (optional; ambient login also works)"
echo "  3) Launch:        ./research-oyster-studio"
echo "     Open:          http://127.0.0.1:8770/"
