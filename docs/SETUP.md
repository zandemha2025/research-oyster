# Setup guide

This guide starts from a new computer and ends with a working Research Oyster MCP connection.

## Option A: one-click macOS installation

### 1. Get the project

Install Git if necessary, open Terminal, and run:

```sh
git clone https://github.com/zandemha2025/research-oyster.git
cd research-oyster
```

You may alternatively download the repository ZIP from GitHub and extract it.

### 2. Run the installer

Open the project folder in Finder and double-click `Install Gaming Pulse.command`.

If macOS blocks it, Control-click the file, choose **Open**, then confirm. The installer:

- verifies macOS and Homebrew;
- installs Python and PostgreSQL when missing;
- creates `.venv` and installs Python packages;
- creates the local `gaming_pulse` database;
- creates `.env` only when one does not already exist;
- applies every database migration; and
- opens the browser control center.

If it opens the Homebrew website, install Homebrew using its official instructions and run the Oyster installer again. Re-running the installer preserves existing data and settings.

### 3. Configure optional sources

In the browser control center, click **Setup**. The database should already be configured. Add only the services you plan to use:

- **Apify API Token:** create a token in your Apify account. This is the broadest optional integration and can run user-selected Actors for Reddit, X, discovery, and other sites. Actors may have separate costs and terms.
- **X API Bearer Token:** create an X developer project with recent-search access. X may charge for API usage. If you do not have it, an appropriate Apify Actor is the optional fallback.
- **Twitch Client ID and Secret:** register an application in the Twitch developer console.
- **Kick Client ID and Secret:** create an application through Kick's developer platform.
- **Discord Bot Token:** create a bot in Discord's developer portal, install it only in servers whose owners authorize it, grant View Channel and Read Message History, and enable Message Content intent when required. Never use a personal user token.

Blank fields preserve already-saved values. Credentials remain in the local `.env` file and are not returned to the browser.

### 4. Attach the MCP server

Double-click `Attach Research Oyster.command`. It registers the absolute launcher path with every supported host installed on the computer. Restart open Claude Code or Codex sessions afterward.

Manual attachment commands:

```sh
codex mcp add research-oyster -- "$(pwd)/research-oyster-mcp"
claude mcp add --scope user research-oyster -- "$(pwd)/research-oyster-mcp"
```

The current working directory must be the cloned repository when using `$(pwd)`.

### 5. Verify from your AI host

Ask:

> Use Research Oyster. First show me connector status, then create a research job about consumer attitudes toward refurbished laptops in the US.

You should receive connector readiness plus a job ID and research plan. A disconnected optional source should explain how to enable it instead of preventing other sources from working.

## Option B: manual macOS or Linux installation

Install Python 3.11 or newer and PostgreSQL 15 or newer using your operating system's package manager. Then:

```sh
git clone https://github.com/zandemha2025/research-oyster.git
cd research-oyster
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
createdb gaming_pulse
python main.py migrate
```

If your PostgreSQL server requires a username, password, hostname, or different database name, edit `DATABASE_URL` in `.env`. Example:

```dotenv
DATABASE_URL=postgresql://research_user:replace_me@localhost:5432/gaming_pulse
```

Protect this file:

```sh
chmod 600 .env
```

Attach the MCP launcher using the commands in step 4. On Windows, use WSL for the documented path; the `.command` launchers are macOS-specific.

## Run without MCP

Start the local browser control center:

```sh
.venv/bin/python control_center.py
```

Or use the specialized collection CLI:

```sh
.venv/bin/python main.py collect press
.venv/bin/python main.py collect discord
.venv/bin/python main.py collect twitch
.venv/bin/python main.py collect kick
.venv/bin/python main.py collect all
.venv/bin/python main.py pulse
```

## Validate the installation

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q
.venv/bin/python tests/postgres_acceptance.py
.venv/bin/python tests/research_postgres_acceptance.py
```

The PostgreSQL acceptance checks create isolated temporary databases and require permission to create/drop databases.

## Troubleshooting

### The MCP host cannot start Oyster

- Confirm `.venv/bin/python` exists.
- Confirm `.env` exists and contains a working `DATABASE_URL`.
- Run `./research-oyster-mcp` in Terminal. Diagnostic errors belong on stderr; a normally running stdio MCP waits silently for its host.
- Re-run the attachment tool after moving the repository because the host stores an absolute launcher path.

### PostgreSQL is not ready

On macOS:

```sh
brew services list
brew services start postgresql@17
pg_isready
```

Then run the installer again.

### A connector says it is not configured

That is not a core installation failure. Add its credentials in Setup or omit that source. Call `connector_status` for exact guidance and available fallbacks.

### Discord returns 403

The configured bot is not authorized for the channel or lacks the necessary intents. Ask the server owner to grant access. Do not substitute a user token or attempt to bypass the server's permissions.

### A site blocks crawling

Do not bypass the site's protections. Use an official API, an authorized provider/Actor, an RSS feed, or exclude the source. Respect the site's terms and rate limits.
