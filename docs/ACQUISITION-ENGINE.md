# The Acquisition Engine — how Oyster actually GETS the real numbers

> The doc the whole roadmap points at. `DATA-SOURCE-COVERAGE` says *what* to reach and *which gates*
> keep it trustworthy; `BUILD-PLAN*` says *in what order*; `EVIDENCE-LAYER` says *what shape* the
> result takes. **This doc answers the one question the user kept asking:** "it can't be that Oyster
> still says fail, or couldn't get it, or zero shares — you need to know *exactly* how to solve it."
> Here is exactly how, per platform, grounded in the code that already does it.

## The thesis (and why official APIs are the wrong frame)

A user comes to Oyster **because they couldn't get the number themselves.** So "we couldn't get it"
is never an answer — it's the restatement of their problem. The whole job is to actually get it.

The good news that makes this tractable: **the engagement numbers are already public.** When you load
a Reddit thread, a TikTok video, a Kick channel, a Discord server — the likes, upvotes, viewers,
followers, member counts are *rendered on the page*. They arrive from the platform's own **internal
JSON / GraphQL endpoints** that the site's front-end calls. Nothing is hidden; it's just not handed to
you in a tidy API.

Official APIs are **not** the path (this was the user's correction, and it's right):
- Reddit's API is gated/expensive and hard to get approved — not worth it.
- X's API is priced out of research use.
- TikTok has no real public engagement API; YouTube/Twitch/Meta/IG are all gated or partial.
- If an official API made this easy, everyone would already be doing it. The moat *is* the acquisition.

So the problem is not "which API." **The problem is reliable acquisition of numbers that are already
public.** That's an engineering problem with three known techniques — and Oyster already runs all three.

## The three acquisition techniques (this is the "how")

| # | Technique | What it defeats | In Oyster today |
|---|-----------|-----------------|-----------------|
| **T1** | **Internal endpoint + real-browser TLS fingerprint** — hit the platform's own JSON/GraphQL endpoint directly, but with a genuine browser's TLS/JA3 fingerprint so it's served, not 403'd. | Cloudflare / bot walls that block "a script" but pass "a browser." | ✅ **`curl_cffi` `impersonate="safari17_0"`** — `connectors.py:578-593` (`_kick_get_blocking`). Verified live. |
| **T2** | **Managed scraper fleet** — residential-proxy rotation + headless-browser fingerprinting at scale, run by a provider (Apify) so you're not maintaining the anti-bot arms race. | IP-based rate limits, per-session challenges, JS-gated content, volume. | 🔌 **Apify engine** — `apify.py` (curated actor registry) + `connectors.apify_collect` / `run_apify_actor`. Wired; needs `APIFY_TOKEN`. |
| **T3** | **Server-side JS render → text** — a reader service loads the page (JS and all) on *its* servers and hands back clean text, no browser tab open on your side. | JS-heavy public pages that return boilerplate to a plain fetch. | ✅/🔌 **Jina Reader `r.jina.ai`** + **Scrapling** fallback — `connectors.crawl_web_page` (`:770`). Works keyless; key lifts the rate limit. |

**T1 is the free, precise path** (the exact number straight from the source, one channel at a time).
**T2 is the scale path** (hundreds of rows, the hard walls, residential IPs). **T3 is for editorial /
long-form pages.** Every source below is one of these three plus a fallback chain and a tier label.

## Per-platform: where the number lives, and exactly how Oyster gets it

Legend: ✅ verified live in this session · 🔌 wired in code, needs a key · 📋 planned (spec'd in Phase 1/3).

### Reddit — upvotes, comments (shares/dislikes are `not_applicable`, never 0)
- **Where the number lives:** every listing and thread has a `.json` twin (`/r/<sub>/search.json`,
  `<permalink>.json`) carrying `score`, `ups`, `num_comments`.
- **How Oyster gets it:**
  1. ✅ **T1 free** — `search_reddit` hits `www.reddit.com/…/search.json` (`connectors.py:1048-1067`).
     Datacenter IPs sometimes 403 → route to **old.reddit** (server-rendered) → still blocked → T2.
  2. 🔌 **T2 scale** — Apify `trudax/reddit-scraper-lite` (`apify.py` REGISTRY) with `includeScore` in the
     input contract, so `upvotes:0` can't come back silently (the SGF failure).
- **Field truth:** `shares` and raw `dislikes` **don't exist publicly on Reddit** → declared
  `not_applicable` in the registry → omitted or "n/a on Reddit," **never printed as 0.**
- **Window:** Reddit lies about time in listings → **base-36 submission-ID windowing** (Phase-1 gate 4)
  drops 2020 posts from a 2026 pull.

### TikTok — views, likes, comments, shares
- **Where:** each video row from the internal feed carries `playCount`, `diggCount`, `commentCount`,
  `shareCount`.
- **How:** 🔌 **T2** — Apify `clockworks/tiktok-scraper` (`apify.py`), numeric extraction via
  `_METRIC_ALIASES` (`diggCount→likes`, `playCount→views`, `shareCount→shares`).
- **Gotchas handled:** the `view_count=1` protection artifact → `sane_min` plausibility floor → `suspect`
  → re-pull (Phase-1). Demo-filtered reach (US/18–24) needs **Creative Center (authed cookie)** →
  📋 tier-labelled; free path gives broad hashtag frequency and *says so*.

### X / Twitter — text + public_metrics (likes, reposts, replies, quotes)
- **Where:** tweet objects carry `public_metrics`; the API is priced out, but scraper actors read the
  same rendered numbers.
- **How:** 🔌 **T2** — `apidojo/tweet-scraper` (`apify.py`) returns tweet **text + metrics** (this is the
  fix for the old "profile-scraper returned follower counts instead of tweets" failure).
- **Empty `top_tweets` in a cycle** → route contract fails → fallback to the next tweet actor (gate 3).

### Instagram — likes, comments, followers, views
- **Where:** post/profile JSON carries `likeCount`, `commentCount`, `followersCount`, video `viewCount`.
- **How:** 🔌 **T2** — `apify/instagram-scraper` with two intents: `hashtag` (posts) and `profile`
  (details). Empty Explore arrays → silent-empty gate → re-config/re-pull.

### YouTube — views, likes, comments, subscribers
- **Where:** watch/search payloads carry `viewCount`, `likeCount`, `commentCount`, `subscriberCount`.
- **How:** 🔌 **T2** — `streamers/youtube-scraper` (`apify.py`). (Data API v3 exists free-tier but is
  quota-capped and gated — kept only as an optional authed accelerator, not the primary path.)

### Twitch — live viewers, followers, clips-by-views, chat
- **Where:** Helix endpoints + the live IRC.
- **How:**
  1. ✅ **T1 free, no login** — `read_twitch_chat` reads **anonymous WSS IRC** (`connectors.py:1370`).
     Verified: 11 live #xqc messages in ~5s.
  2. 🔌 **authed accelerator** — **Helix API** (free *app* token via `twitch_client_id/secret`,
     `search_twitch` `:520-533`): clips ordered by view count, live streams + viewer counts, VODs, followers.
  3. 🔌 **T2** — `automation-lab/twitch-scraper` for channel stats at scale.

### Kick — followers, live viewers, category, chat  ✅ the proof case
- **Where:** `kick.com/api/v2/channels/{slug}` returns `followers_count`, `livestream.viewer_count`,
  `livestream.categories[]`, `is_live`. The public endpoint **403s a plain script** under Cloudflare.
- **How:** ✅ **T1** — `search_kick_public` (`connectors.py:632`) hits it via `_kick_get_blocking` with
  **`curl_cffi impersonate="safari17_0"`** (Safari fingerprint passed where Chrome got reset). Verified
  live: xqc **1,098,986** followers / LIVE / 7,845 viewers; adinross 2.1M; westcol 4.0M. Discovery via
  `/api/search?searched_word=…` (same fingerprint) resolves slugs from a keyword. **No OAuth needed.**
- This is the whole thesis in one working connector: a "you can't get it" number, gotten, free, today.

### Discord — server member / online counts (always), messages (opt-in)
- **Where:** the public invite API + `guilds/{id}/widget.json` expose member and online counts with no token.
- **How:**
  1. ✅ **T1 free, always-on** — `inspect_discord_invite` (`connectors.py:251`) reads invite +
     `widget.json`. Verified: Python ~429k/~35k online, OpenAI ~854k/~80k, Minecraft ~4.0M/~454k.
  2. 📋/🔌 **opt-in messages** — `harvestedge/discord-message-scraper` (`apify.py`, `opt_in=True`) needs a
     burner research token; ToS-gray, **off by default, per-run toggle + warning.** This is the realistic
     path to third-party server messages (the Oyster bot won't be in arbitrary servers).

### Editorial / trade press (Glossy, Teen Vogue, WWD, YPulse, …)
- **How:** ✅/🔌 **T3** — `crawl_web_page` server-side reader for the article body; **RSS-first** fallback
  registry (gate 3) for sites whose direct scrape returns empty (Teen Vogue → RSS, fixed).

### Google Trends / retail PDPs / Steam / Goodreads
- **How:** 🔌 **T2/T3** — Apify actors + reader. Trends null-term → `missing`, **never** coerced to
  0-interest (gate 1). SteamSpy null for new titles → fall back to Store review-count route (gate 3).

### Syndicated / paywalled (Mintel, Circana, NIQ, WGSN, Trendalytics)
- **How:** 📋 honest **`tier="paywalled"`, `kind="manual"`** — trade-press summary or gated paste-in,
  **labelled as such, never presented as the full dataset** (gate 6). The one place "we don't have the
  raw feed" is the *honest* answer — and it's stated as a tier, with a proxy offered, not as a failure.

## What ties acquisition to "never a fake 0 / never a bare fail"

Getting the number is T1/T2/T3. **Not lying when a specific field is genuinely unavailable** is the
three-state field model from `BUILD-PLAN-PHASE1.md`, applied to whatever acquisition returns:

- got it → `value` (real, weighted).
- platform has no such metric (Reddit shares) → `not_applicable` (omitted, never 0).
- uniformly 0/null across the batch while other signal is present → `suspect` → **auto re-pull via the
  next route in the chain** before anything prints.
- every route exhausted → an **itemized "we knocked on N doors for X; the proxy we used is Y"** line —
  specific and forward, never "failed."

So acquisition (this doc) makes the number *obtainable*; the gateway + three-state model
(`BUILD-PLAN-PHASE1`) makes the *reporting of it* honest; the evidence layer (`EVIDENCE-LAYER`) wraps
each obtained number in source + confidence + freshness. Three layers, one promise: **real numbers,
with receipts, and no fake zeros.**

## Where this already runs vs. what to build

**Runs today (free, verified live):** Kick (T1 fingerprint), Discord counts (T1 invite), Twitch chat
(T1 WSS), Reddit `.json` (T1), web reader (T3). These need **no keys** — Oyster gets real numbers on a
cold install.

**Wired, needs one key (`APIFY_TOKEN`):** the whole T2 fleet — Reddit/X/TikTok/IG/YouTube/Twitch/Kick at
scale with residential proxies. `apify.py` REGISTRY is the single place actor ids live.

**To build (in priority order):**
1. **Phase-1 gateway** (`collect_gateway.py` + `sources.py`) — routes every acquisition through the
   fallback chain + three-state model, so *no* connector can dead-end or fake-0. Highest leverage.
2. **Residential-proxy rotation on the T1 direct paths** — so the free internal-endpoint hits (Reddit,
   Kick) survive datacenter-IP blocks at volume, not just one-off.
3. **Authed accelerators** where they add precision — Twitch Helix, TikTok Creative Center — behind tier
   labels, never as the required path.
4. **Breadth** (Phase 3) — each new source is a `sources.py` entry (actor/endpoint + contract +
   fallback + tier) and inherits all of the above for free.

## Bottom line
The numbers are public; the skill is acquisition, and Oyster already does the three things that make
acquisition reliable — real-browser fingerprinting, a managed proxy/scraper fleet, and server-side
rendering. Kick, Discord, Twitch, and Reddit prove it *free and live today*; one key turns on the rest
at scale. What's left is routing it all through one gateway so the honesty (never a fake 0, never a bare
"couldn't get it") is structural, not hoped-for.
