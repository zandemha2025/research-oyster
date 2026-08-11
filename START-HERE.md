# Start Here — Research Oyster in plain steps

Two things to keep straight, and then you're oriented:

- **Studio** = the app where you actually do research. Opens at `localhost:8770`. **This is the main thing.**
- **Control Center** = a *separate* helper you only need for Discord / logged-in capture. `localhost:8765`. Ignore it until Part 2.

**Prerequisite:** macOS or Linux, and you're signed into Claude (Studio runs on your Claude account). If Studio ever says "sign in," run `claude auth login`.

---

## Part 1 — Install and do research (this is 95% of it)

**1. Install.** Open a terminal, paste this one line, press enter:

```sh
curl -fsSL https://raw.githubusercontent.com/zandemha2025/research-oyster/main/install.sh | bash
```

It installs everything and **opens Studio** when it finishes. Safe to re-run anytime — it never wipes your data.

**2. If Studio didn't open** (or opened the wrong tab), start it yourself:

```sh
cd ~/research-oyster && ./studio
```

Studio is the page at **`localhost:8770`**. That's your home base.

**3. Do research.** In the Studio chat box, type a normal request and press enter. Example:

> Research what people are saying about the Deadpool & Wolverine movie over the last 90 days. Give me the raw chatter and the themes.

Watch it work live, then read the report. **That's the whole product for normal use.** You do *not* need the Control Center or the extension for this — Reddit, YouTube, web, and news all work here with no setup.

**Stop here** unless you specifically need Discord messages from a server you're logged into.

---

## Part 2 — Discord capture (optional, only for logged-in servers)

**`./studio` already starts the capture helper for you** — in the background, silently. You do **not** run a second command or open a second window anymore. There's a one-time pairing, then it just works.

**First time only — pair the extension (once, ever):**

1. **Load the extension:** in Chrome, go to `chrome://extensions` → turn on **Developer mode** (top-right) → click **Load unpacked** → select `~/research-oyster/browser_extension`.
2. **Pair it:** open `http://127.0.0.1:8765` in a tab (that's the capture helper Studio started) → click **Create one-time pairing code** → click the Oyster extension icon → **Pairing & settings** → paste the code. Leave the address as `http://127.0.0.1:8765`. You'll see **"Browser paired."** You never do this again.

**Every time you want Discord chatter:**

3. **Make a job.** In **Studio** (`localhost:8770`), type a brief like *"What are people saying in [that Discord community]?"* and send it.
4. **Open Discord** in the same Chrome — a server + channel **you're a member of**.
5. **Capture.** Click the **Oyster extension icon**. It **auto-picks your job** (if you have just one). Click **Find visible candidates** → review → **Approve & save**. The messages land in your Studio job.

If the extension badge ever says red **"Unavailable,"** it just means Studio isn't running — start it with `./studio` (or the Desktop icon) and click **↻** in the extension. If the job dropdown is empty, you haven't made a job yet — do step 3.

### The honest limits (good to know, and to say out loud in a demo)
- It only sees servers **you're in** and channels **you can view** — your own logged-in session, no burner account, no evasion.
- **You** drive every capture — it never joins servers or runs on its own.
- It reads **message text only** — never your cookies, password, or Discord token.
- Capturing from Discord can bump against their terms of service, so it's an opt-in, your-call action.

---

## Quick reference

| I want to… | Do this |
|---|---|
| Install / update | `curl -fsSL https://raw.githubusercontent.com/zandemha2025/research-oyster/main/install.sh | bash` |
| Start Studio (main app) | `cd ~/research-oyster && ./studio` → `localhost:8770` |
| Start the Control Center (for capture) | `cd ~/research-oyster && .venv/bin/python control_center.py` → `localhost:8765` |
| Do research | Type a brief in Studio |
| Capture Discord messages | Control Center + extension (Part 2) |
| Sign into Claude (if prompted) | `claude auth login` |

Install folder is always `~/research-oyster`.
