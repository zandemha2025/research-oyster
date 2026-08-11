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

This is the only thing that needs the extra pieces. Do it in order.

**4. Start the Control Center** in a *second* terminal (leave Studio running):

```sh
cd ~/research-oyster && .venv/bin/python control_center.py
```

A new tab opens at **`localhost:8765`** with a **"Set up browser capture"** panel.

**5. Load the extension** (one time only): in Chrome, go to `chrome://extensions` → turn on **Developer mode** (top-right) → click **Load unpacked** → select the folder `~/research-oyster/browser_extension`.

**6. Pair them:** on the Control Center's capture panel, click **Create one-time pairing code**. Then click the Oyster extension icon → **Pairing & settings** → paste that code. Leave the address as `http://127.0.0.1:8765`.

**7. Make a job to capture into:** back in **Studio**, type a brief like *"Research what people are saying in [that Discord community]."* That creates the job the Discord messages will save into.

**8. Open Discord:** in that same Chrome, log into Discord and open a server + channel **you're a member of**.

**9. Capture:** click the **Oyster extension icon** → pick your job from the list → click **Find visible candidates** (it grabs the messages on screen) → review them → click **Approve & save**. Those messages now land in your Studio job.

That's it — the captured Discord messages show up in the same job's evidence back in Studio.

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
