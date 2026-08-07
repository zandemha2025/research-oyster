# Data-Source Coverage — the connector & data-quality roadmap

> Companion to `RESEARCHER-REACH.md`. That doc is the *method* (how the tool thinks). This one is
> the *coverage* (what it can reach, and how to keep every source trustworthy). Built from a real
> gap catalog (Starface-class CPG/beauty + entertainment research), including the ⚠️ failures
> observed in live runs.

## The key insight: the gaps aren't one-offs — they cluster into 6 systemic capabilities

Every ⚠️ below is a symptom of one of six failure modes. Build these six **once** and every current
and future connector inherits the fix. This is the high-leverage work; the source list after it is
breadth that plugs into these gates.

### 1. Silent zero / null / empty gate  *(partly shipped)*
A field that comes back uniformly zero, null, or empty across a whole batch — while other signal is
present — is almost always a tooling limitation, not a real value. It must be **flagged and re-pulled,
never scored as real.** Covers these observed gaps:
- Reddit `upvotes: 0` for every post (missing `includeScore`) — **shipped**: `patterns.detect_uniform_zero`.
- TikTok `view_count = 1` artifact on high-like posts (rate-limit/protection).
- Google Trends null terms **silently scored as zero** before being caught (Hello Kitty, Kuromi…).
- X `top_tweets` arrays empty across all topics in a cycle.
- Instagram Explore themes returned empty arrays.
- Teen Vogue / Mashable / Input / BuzzFeed direct scrape → empty array.
- **Build:** generalize `detect_uniform_zero` to zero **and** null **and** empty-array, across any
  numeric or list field feeding a score, on every platform batch. One gate, all sources.

### 2. Per-source field-coverage contract  *(design exists — SGF case)*
Each actor/source declares the fields it MUST return (`includeScore`, `includeMediaLinks`, view
counts). A run that drops a contracted field **fails loudly**, not silently. Covers: Reddit
`includeScore`/actor-returns-listing-not-posts, TikTok Creative Center authed fields, SteamSpy
null-for-new-titles, IG Explore re-configuration. **Build:** a per-actor contract in the registry;
assert it consumer-side on every run (a third-party actor can't run your CI).

### 3. Fallback chains  *(partly shipped)*
Every source has an ordered route list; when one fails, walk to the next automatically instead of
returning empty. Observed: Teen Vogue direct → **RSS** (fixed); Reddit actor A (listing pages) →
actor B (post-level); www.reddit (JS/403) → **old.reddit** (server-rendered); X API → Apify;
DuckDuckGo → Tavily/Brave/Serper. **Shipped in part:** `CONNECTOR_GUIDES` fallbacks, Kick public
path, the old.reddit route-around. **Build:** promote to a first-class ordered fallback registry per
source, with the RSS tier explicit for editorial sites.

### 4. Window & ID correctness  *(design exists)*
Enforce the date window **locally** (scrapers ignore their own), using stable IDs where the source
lies about time. Observed: 2020–2025 Reddit posts leaked into a June-2026 window — fixed via **base-36
submission-ID windowing**. **Build:** local window enforcement + ID-based windowing for Reddit;
require a minimum sample; flag/re-pull thin or out-of-window batches.

### 5. Query-shape tuning  *(ties to reach laddering)*
Term specificity swings results both ways. Observed: low-specificity terms ("IT", "Persona",
"Dispatch") → high false positives from common-word matches; overly-exact strings → undercount (a
title indexed at 12 corrected to **67** once natural query variants were added). **Build:** query
laddering + disambiguation (entity-scoped, community-scoped) + variant expansion — the same ladder
as `RESEARCHER-REACH.md` move #2, applied to every scrape.

### 6. Access tiers  *(the honest capability ceiling)*
Each source declares its tier and what precision is lost below it:
- **Public scrape (free):** hashtag/keyword/profile/PDP/RSS — the default.
- **Authed-cookie precision:** e.g. TikTok Creative Center US/18–24 demo filtering — the free
  fallback gives broad hashtag frequency, **not** demo-filtered reach; label it as such.
- **1p platform API (OAuth):** Meta/Google/TikTok ad + organic performance — the v1 build below.
- **Paywalled / manual:** Mintel, Circana, NIQ, WGSN, Trendalytics, Numerator, SPINS — trade-press
  summary or gated paste-in only; never pretend to have the full dataset.

---

## Source coverage — tiered catalog (every source + its known gap)

### 1p — Social platform APIs (organic **and** ad performance)
**v1:** Meta (Graph + Marketing API) · Google (GA4 + Google Ads + Search Console) · TikTok (Business/
Marketing API). OAuth per ad account; organic + paid performance.
**v2:** Snapchat · Pinterest.
_(ref: Starface BQ Data Sources; these feed the CPG/entertainment vertical — the Alloy-overtake path.)_

### 3p — Social scrapers (via Apify actors + free fingerprint path)
- **TikTok:** hashtag · keyword · profile/handle · reel · comment-level · Creative-Center trending
  (⚠️ authed-cookie demo filtering; free fallback = broad hashtag freq) · (⚠️ `view_count=1` artifact).
- **X/Twitter:** trending-topics (⚠️ empty `top_tweets`/volume in a cycle) · follower-list · bio-keyword · sample-tweet-set.
- **Reddit:** subreddit-targeted (⚠️ listing-not-post actor failure; ⚠️ timestamp leak → base-36 ID
  windowing) · keyword search (⚠️ low-specificity false positives) · engagement/upvote pull (⚠️ all
  `upvotes:0`, `includeScore`) · user-history/overlap (⚠️ empty `all_comment_subs` → `sub_authors.json`).
- **Instagram:** hashtag · reel · explore/trending-audio (⚠️ empty arrays, re-config) · profile/handle.
- **YouTube:** search-and-video · trending-tab · comment-level · blog/editorial.
- **Twitch:** live-streamer-count · VOD/channel-history.
- **Steam:** review-count/Store API · SteamSpy owner-bracket (⚠️ null for very new titles).
- **Goodreads:** search-result scrape.

### Retail PDPs
Sephora · Ulta · competitive brand PDPs — product search / PDP scrape.

### Trend & search indices
**Google Trends:** keyword/term index (⚠️ null terms silently scored zero; ⚠️ one term empty first
pull, fixed on retry) · geo-filtered · historical multi-year · related/rising queries (⚠️ exact-string
undercount: 12 → 67 with variants).

### Syndicated data (paywalled — access tier: summary / manual)
Mintel (⚠️ paywall, trade-press summary only) · NIQ/Nielsen (⚠️ secondary summary only) · Spate
(public-facing) · Circana (⚠️ paywalled summary) · Numerator (⚠️ gated paste-in) · SPINS (⚠️ gated
paste-in) · IWSR (annual report) · Drizly/Gopuff (annual report) · WGSN (⚠️ login-gated paste-in) ·
Trendalytics (⚠️ login-gated paste-in).

### Trade press & culture (article/headline — RSS-first)
**Beauty:** Glossy · Business of Fashion (Beauty) · WWD Beauty · Cosmetics Business · Happi · Beauty
Independent · Beautymatter · Allure · Byrdie · Refinery29 · Cosmopolitan · Who What Wear.
**Beverage:** Beverage Industry · Brewbound · Shanken News Daily.
**Gen-Z / culture:** YPulse · The Plug · Culture Study · Garbage Day · Teen Vogue (⚠️ direct empty →
RSS fixed) · Mashable / Input / BuzzFeed (⚠️ empty-array; RSS fallback unconfirmed for these).

### Brand & fandom handles (profile scrape)
ColourPop · Glamlite · Hero Cosmetics · Mighty Patch · Peace Out · Crunchyroll (followers) ·
MyAnimeList (followers) · VIZ Media (followers).

### Reference data
September national-days calendar · ad-hoc browser/manual verification pulls.

---

## What today's build already covers
- **Silent-zero gate (#1):** shipped — `patterns.detect_uniform_zero` + the consensus warning;
  extend to null/empty for Trends and empty-array sources.
- **Engagement capture across channels:** shipped — collect-lane directive + `engagement_of`.
- **Consensus weighting:** shipped — rank by endorsement, normalized.
- **Fallback chains (#3):** partial — `CONNECTOR_GUIDES` fallbacks, Kick free path, old.reddit route.
- **Apify (residential/headless at scale):** wired (needs token) — the engine for most 3p scrapers.
- **Web reader (JS render):** wired (needs key) — for JS-heavy public pages.

## Sequencing
1. **The 6 gates first** — they make every existing and future connector trustworthy (highest leverage).
2. **v1 1p ad APIs** (Meta/Google/TikTok) — highest business value for the CPG vertical.
3. **3p social scrapers** via curated Apify actors, each behind a field-coverage contract (#2).
4. **Trade-press & culture** via an RSS-first fallback registry (#3).
5. **Paywalled tier** as honest manual/authed paste-in with tier labels (#6).
6. **v2** — Snapchat, Pinterest; retail PDPs; syndicated summaries.
