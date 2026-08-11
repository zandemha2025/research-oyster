#!/usr/bin/env bash
# Research Oyster one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/zandemha2025/research-oyster/main/install.sh | bash
#
# The default branch (main) ships the live Studio UI and opens it when the install finishes.
# To install a different branch, set OYSTER_BRANCH:
#   curl -fsSL https://raw.githubusercontent.com/zandemha2025/research-oyster/main/install.sh | OYSTER_BRANCH=some-branch bash
#
# Downloads Oyster, installs Python + PostgreSQL if they are missing, creates and migrates a
# local database, and attaches Oyster to Claude Code / Codex. Safe to re-run: it updates in
# place and never overwrites an existing .env or database.
#
# Overridable with environment variables:
#   OYSTER_HOME    install location (default: $HOME/research-oyster)
#   OYSTER_BRANCH  git branch to install (default: main)
#   OYSTER_REPO    git URL (default: the public GitHub repo)
set -euo pipefail

REPO_URL="${OYSTER_REPO:-https://github.com/zandemha2025/research-oyster.git}"
BRANCH="${OYSTER_BRANCH:-main}"
DEST="${OYSTER_HOME:-$HOME/research-oyster}"
DB_NAME="gaming_pulse"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  \033[36m→\033[0m %s\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '\n  \033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM="macos" ;;
  Linux)  PLATFORM="linux" ;;
  *) die "This installer supports macOS and Linux. See docs/SETUP.md for manual setup on $OS." ;;
esac

SUDO=""
if [ "$PLATFORM" = "linux" ] && [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 && SUDO="sudo" || die "Please install as root or install 'sudo'."
fi

# Run psql as the postgres superuser, reading SQL from stdin. Works both as root (su) and as a
# normal user (sudo). Used only on Linux to provision a login role for the current OS user.
pg_super() {
  if [ "$(id -u)" -eq 0 ]; then
    su -s /bin/sh postgres -c 'psql -v ON_ERROR_STOP=0 -f -'
  else
    sudo -u postgres psql -v ON_ERROR_STOP=0 -f -
  fi
}

bold "Research Oyster installer"
info "Platform: $PLATFORM   Location: $DEST"

# --- Homebrew (macOS only) --------------------------------------------------------------
if [ "$PLATFORM" = "macos" ] && ! command -v brew >/dev/null 2>&1; then
  die "Homebrew is required. Install it from https://brew.sh, then re-run this command."
fi

# --- git --------------------------------------------------------------------------------
if ! command -v git >/dev/null 2>&1; then
  info "Installing git…"
  [ "$PLATFORM" = "macos" ] && brew install git || { $SUDO apt-get update -y && $SUDO apt-get install -y git; }
fi

# --- get the code -----------------------------------------------------------------------
if [ -d "$DEST/.git" ]; then
  info "Updating existing install…"
  # Fetch the branch and reset to FETCH_HEAD: a clone that was single-branch on a
  # different branch won't have an origin/<branch> ref to reset to, but FETCH_HEAD
  # always points at what we just fetched.
  git -C "$DEST" fetch --depth 1 origin "$BRANCH"
  git -C "$DEST" checkout -B "$BRANCH" FETCH_HEAD >/dev/null 2>&1
else
  info "Downloading Oyster…"
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$DEST"
fi
cd "$DEST"
ok "Code ready at $DEST"

# --- Python -----------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  info "Installing Python…"
  [ "$PLATFORM" = "macos" ] && brew install python || $SUDO apt-get install -y python3 python3-venv
fi

# --- PostgreSQL -------------------------------------------------------------------------
if ! command -v psql >/dev/null 2>&1 || ! command -v createdb >/dev/null 2>&1; then
  info "Installing PostgreSQL…"
  if [ "$PLATFORM" = "macos" ]; then
    brew install postgresql@17 && brew link --overwrite postgresql@17
  else
    $SUDO apt-get install -y postgresql
  fi
fi

info "Starting the local database…"
if [ "$PLATFORM" = "macos" ]; then
  if ! pg_isready -q >/dev/null 2>&1; then
    formula="$(brew list --formula | grep -E '^postgresql(@[0-9]+)?$' | tail -1 || true)"
    [ -n "$formula" ] && brew services start "$formula" >/dev/null 2>&1 || true
  fi
else
  $SUDO service postgresql start >/dev/null 2>&1 || $SUDO pg_ctlcluster "$(ls /etc/postgresql 2>/dev/null | tail -1)" main start >/dev/null 2>&1 || true
fi
for _ in $(seq 1 30); do pg_isready -q >/dev/null 2>&1 && break; sleep 1; done
pg_isready -q >/dev/null 2>&1 || die "The database did not start. On macOS, restart and re-run; on Linux, start PostgreSQL and re-run."
ok "Database is running"

# --- database + role --------------------------------------------------------------------
# Peer auth maps the OS user to a same-named DB role, matching the default
# DATABASE_URL=postgresql:///gaming_pulse. On Linux we create that role via the postgres user.
if [ "$PLATFORM" = "linux" ]; then
  ME="$(id -un)"
  printf "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='%s') THEN CREATE ROLE \"%s\" LOGIN CREATEDB SUPERUSER; END IF; END \$\$;\n" "$ME" "$ME" \
    | pg_super >/dev/null 2>&1 || warn "Could not auto-create a database role; if the next step fails, create one for '$ME'."
fi
if ! psql -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>/dev/null | grep -q 1; then
  createdb "$DB_NAME"
  ok "Created database '$DB_NAME'"
else
  ok "Database '$DB_NAME' already exists (left untouched)"
fi

# --- python env + deps ------------------------------------------------------------------
[ -d .venv ] || python3 -m venv .venv
info "Installing Python packages…"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
ok "Packages installed"

# --- studio env (the live chat UI; separate venv, see docs/STUDIO.md) --------------------
if [ -f requirements-studio.txt ]; then
  [ -d .venv-studio ] || python3 -m venv .venv-studio
  info "Installing Studio packages…"
  .venv-studio/bin/python -m pip install --quiet --upgrade pip
  .venv-studio/bin/python -m pip install --quiet -r requirements-studio.txt
  ok "Studio ready"
fi

# --- .env (never overwrite an existing one) ---------------------------------------------
if [ ! -f .env ]; then
  umask 077
  cat > .env <<'ENV'
DATABASE_URL=postgresql:///gaming_pulse
TWITCH_CLIENT_ID=
TWITCH_CLIENT_SECRET=
KICK_CLIENT_ID=
KICK_CLIENT_SECRET=
X_BEARER_TOKEN=
APIFY_TOKEN=
DISCORD_BOT_TOKEN=
COLLECTION_HOUR_UTC=16
ENV
  ok "Wrote .env (credentials optional; add later in Setup)"
else
  ok "Existing .env kept"
fi

# --- migrate ----------------------------------------------------------------------------
info "Preparing the database…"
.venv/bin/python main.py migrate >/dev/null
ok "Database migrated"

# --- attach to AI hosts -----------------------------------------------------------------
LAUNCHER="$DEST/research-oyster-mcp"
ATTACHED=""
if command -v claude >/dev/null 2>&1; then
  claude mcp remove --scope user research-oyster >/dev/null 2>&1 || true
  claude mcp add --scope user research-oyster -- "$LAUNCHER" >/dev/null 2>&1 && ATTACHED="Claude Code"
fi
if command -v codex >/dev/null 2>&1; then
  codex mcp remove research-oyster >/dev/null 2>&1 || true
  codex mcp add research-oyster -- "$LAUNCHER" >/dev/null 2>&1 && ATTACHED="${ATTACHED:+$ATTACHED, }Codex"
fi
[ -n "$ATTACHED" ] && ok "Attached to $ATTACHED" || warn "No Claude Code or Codex CLI found — install one, then re-run to attach."

# --- desktop launcher (double-click, no terminal typing) --------------------------------
chmod +x "$DEST/studio" "$DEST/Research Oyster Studio.command" 2>/dev/null || true
if [ "$PLATFORM" = "macos" ]; then
  ln -sf "$DEST/Research Oyster Studio.command" "$HOME/Desktop/Research Oyster Studio.command" 2>/dev/null \
    && ok "Added 'Research Oyster Studio' to your Desktop — double-click it any time" || true
elif [ -n "${DISPLAY:-}" ] && [ -d "$HOME/Desktop" ]; then
  cat > "$HOME/Desktop/research-oyster-studio.desktop" <<EOF 2>/dev/null || true
[Desktop Entry]
Type=Application
Name=Research Oyster Studio
Exec=$DEST/studio
Terminal=true
EOF
  chmod +x "$HOME/Desktop/research-oyster-studio.desktop" 2>/dev/null || true
  ok "Added a Studio launcher to your Desktop"
fi

# --- done -------------------------------------------------------------------------------
echo
bold "Research Oyster is installed."
echo
echo "  Start the Studio — just double-click on your Desktop:"
echo "      \"Research Oyster Studio\""
echo "  …or from a terminal:"
echo "      cd \"$DEST\" && ./studio"
echo
echo "  Or use it in your AI host (restart it first):"
echo "      \"Use Research Oyster to research …\""
echo
echo "  Optional browser capture — load the extension once:"
echo "      Chrome/Edge → Extensions → Developer mode → Load unpacked → $DEST/browser_extension"
echo

# Open the Studio now so the browser lands on the right place — NOT the old dashboard.
# Only auto-open when already signed in (a background launch can't do the interactive
# sign-in); otherwise tell the user to double-click the Desktop icon, which can.
if [ "$PLATFORM" = "macos" ] || [ -n "${DISPLAY:-}" ]; then
  if command -v claude >/dev/null 2>&1 && claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
    info "Opening Research Oyster Studio…"
    OYSTER_NO_BROWSER=0 nohup "$DEST/studio" >/tmp/research-oyster-studio.log 2>&1 &
  else
    info "Double-click 'Research Oyster Studio' on your Desktop to start."
  fi
fi
