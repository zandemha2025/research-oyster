#!/bin/zsh
set -e

cd "${0:A:h}"

fail() {
  osascript -e "display alert \"Gaming Pulse installation stopped\" message \"$1\""
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "This one-click installer currently supports macOS. See README.md for manual installation."
fi

if ! command -v brew >/dev/null 2>&1; then
  open "https://brew.sh/"
  fail "Homebrew is required to install the local database. Its installation page has been opened; install it, then double-click this installer again."
fi

if ! command -v python3 >/dev/null 2>&1; then
  brew install python
fi

if ! command -v psql >/dev/null 2>&1 || ! command -v createdb >/dev/null 2>&1; then
  brew install postgresql@17
  brew link --overwrite postgresql@17
fi

if ! pg_isready -q >/dev/null 2>&1; then
  postgres_formula="$(brew list --formula | grep -E '^postgresql(@[0-9]+)?$' | tail -1 || true)"
  [[ -n "$postgres_formula" ]] || fail "PostgreSQL was installed but its service could not be identified."
  brew services start "$postgres_formula"
  for attempt in {1..30}; do
    pg_isready -q >/dev/null 2>&1 && break
    sleep 1
  done
fi

pg_isready -q >/dev/null 2>&1 || fail "The local database did not start. Restart your Mac and run this installer again."

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

if ! psql -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='gaming_pulse'" | grep -q 1; then
  createdb gaming_pulse
fi

if [[ ! -f .env ]]; then
  umask 077
  {
    print -r -- "DATABASE_URL=postgresql:///gaming_pulse"
    print -r -- "TWITCH_CLIENT_ID="
    print -r -- "TWITCH_CLIENT_SECRET="
    print -r -- "KICK_CLIENT_ID="
    print -r -- "KICK_CLIENT_SECRET="
    print -r -- "X_BEARER_TOKEN="
    print -r -- "APIFY_TOKEN="
    print -r -- "DISCORD_BOT_TOKEN="
    print -r -- "COLLECTION_HOUR_UTC=16"
  } > .env
fi

.venv/bin/python main.py migrate
osascript -e 'display notification "Installation complete. Opening the control center." with title "Gaming Culture Pulse"'
exec "${0:A:h}/Open Gaming Pulse.command"
