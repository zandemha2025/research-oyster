#!/bin/zsh
set -e
cd "${0:A:h}"

launcher="${0:A:h}/research-oyster-mcp"
attached=()

if command -v codex >/dev/null 2>&1; then
  codex mcp remove research-oyster >/dev/null 2>&1 || true
  codex mcp add research-oyster -- "$launcher"
  attached+=("Codex")
fi

if command -v claude >/dev/null 2>&1; then
  claude mcp remove --scope user research-oyster >/dev/null 2>&1 || true
  claude mcp add --scope user research-oyster -- "$launcher"
  attached+=("Claude Code")
fi

if (( ${#attached[@]} == 0 )); then
  osascript -e 'display alert "No supported AI host found" message "Install Codex or Claude Code, then run this attachment tool again."'
  exit 1
fi

hosts="${(j:, :)attached}"
osascript -e "display alert \"Research Oyster attached\" message \"Added to ${hosts}. Restart any open AI sessions, then ask the agent to use Research Oyster for a research assignment.\""
