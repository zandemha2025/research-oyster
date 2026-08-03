#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  osascript -e 'display alert "Gaming Pulse needs installation" message "Double-click Install Gaming Pulse.command first."'
  exit 1
fi
if [[ ! -f .env ]]; then
  osascript -e 'display alert "Gaming Pulse needs installation" message "Double-click Install Gaming Pulse.command first."'
  exit 1
fi
exec .venv/bin/python control_center.py
