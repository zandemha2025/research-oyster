# Oyster Build Plan — how we close every gap

> The master plan. `RESEARCHER-REACH.md` = the method, `DATA-SOURCE-COVERAGE.md` = the sources +
> the 6 gates. This doc is the **sequence and the architecture**: what to build, in what order, and
> the one structural idea that makes all of it maintainable.

## Two principles that decide the order

1. **Gates before breadth.** Most of the pain in the gap list is *trust*, not *coverage* — fields
   silently zero, windows leaking, empty arrays scored as real. Adding 40 connectors on top of an
   untrustworthy base multiplies the bug surface. So the trustworthiness gates come first; then every
   connector we add is born trustworthy.
2. **One collection gateway.** Every source — free scrape, Apify actor, 1p API, RSS — routes through
   a single `collect()` gateway that applies all 6 gates uniformly. Connectors stay thin ("fetch X");
   trust logic lives in one place, tested once. This is the spine of the whole plan.

## The architecture (the spine)

```
                         ┌───────────────────────────────────────────┐
  a research node  ──►   │  collect(source, intent, query, window)     │
                         │   1 resolve source spec + field contract    │
                         │   2 walk the fallback chain until one works │
                         │   3 enforce window + min-sample (local)     │
                         │   4 normalize engagement into metadata      │
                         │   5 silent zero/null/empty gate  ← flag/repull
                         │   6 stamp access-tier + precision caveat     │
                         └───────────────────────────────────────────┘
                                     │ clean, labelled evidence
                                     ▼
                    metrics / consensus / synthesis  (RESEARCHER-REACH)
```

New modules: `research_engine/collect_gateway.py` (the gateway) + `research_engine/sources.py` (the
source registry: per-source spec, field contract, fallback chain, access tier). Existing connectors
become thin fetchers the gateway calls. `apify.py` already prototypes half of this (registry +
windows + numeric extraction) — the gateway generalizes it to *all* sources, free and paid.

---

## Phase 0 — shipped (the demo build)
Silent-zero gate v1 (`detect_uniform_zero`), consensus weighting + cross-channel engagement capture,
Apify crash-guard, export robustness (matplotlib optional), MCP-reap, report-pending honesty,
title/gaming-language, parallel-collect fix, Reddit-boilerplate filter. Free connectors (Reddit +
old.reddit route, web search, fingerprint crawl, Kick no-cred, Twitch/Discord public). Apify engine +
web reader wired (need keys). **This is the base the gateway wraps.**

## Phase 1 — The Trust Layer (the 6 gates) · highest leverage
Build the gateway and each gate as a pure, tested module, then route existing connectors through it.

| # | Gate | Build | Closes |
|---|------|-------|--------|
| 1 | Silent zero/null/empty | extend `detect_uniform_zero` → null + empty-array + suspicious-constant (view_count=1); run it inside the gateway on every batch | Reddit upvotes:0, TikTok view=1, Trends null→zero, X/IG/editorial empty arrays |
| 2 | Field-coverage contract | `sources.py` declares required fields per source; gateway asserts + fails loud | SGF includeScore, listing-not-posts, SteamSpy nulls, IG Explore re-config |
| 3 | Fallback chain | ordered routes per source (direct→RSS→alt-actor→residential→manual); gateway auto-walks | Teen Vogue→RSS, www→old.reddit, actor A→B, DDG→Tavily |
| 4 | Window & ID correctness | local window enforce + base-36 Reddit ID windowing + min-sample | 2020 posts in a 2026 pull; thin batches |
| 5 | Query-shape tuning | laddering + disambiguation + variant expansion (shared w/ reach move #2) | IT/Persona false positives; exact-string undercount 12→67 |
| 6 | Access tiers | each source stamps tier + "precision lost at free tier"; surfaced in the report | TikTok Creative Center authed demo filter; paywalled summary-only |

*Effort: medium. Each gate is small and unit-testable; the gateway wiring is the integration work.
Dependency: gateway first (1→3→2→4→5→6 is a fine order). Nothing after Phase 1 should bypass it.*

## Phase 2 — The Method Layer (RESEARCHER-REACH)
With clean data flowing, add the reasoning that makes the free path strong on obscure topics:
agent-reach (thread-following), question-modeling, proxy reasoning, read-the-silence, domain lenses.
Consensus weighting is already shipped. *Effort: medium; see RESEARCHER-REACH.md for the per-move build.*

## Phase 3 — Coverage (connectors, each born through the gateway)
Add breadth in business-value order; every new connector is just a fetcher + a `sources.py` entry
(contract + fallback + tier), so it inherits all 6 gates for free.
- **3a — v1 1p APIs (organic + ad):** Meta (Graph+Marketing), Google (GA4+Ads+Search Console), TikTok
  (Business API). OAuth per account. *Highest CPG value — the paid-performance half of the vertical.*
- **3b — 3p social scrapers:** curated Apify actors for TikTok / X / Reddit / IG / YouTube / Twitch /
  Steam / Goodreads, each behind a field contract.
- **3c — Retail + trend:** Sephora / Ulta / competitive PDPs; Google Trends (all four pull types).
- **3d — Trade press & culture:** RSS-first registry for the beauty / beverage / Gen-Z outlets.
- **3e — Paywalled tier:** Mintel / NIQ / Circana / Numerator / SPINS / WGSN / Trendalytics as honest
  manual/authed paste-in with tier labels.
- **3f — Handles + reference:** brand & fandom profiles; national-days / reference data.
- **v2:** Snapchat, Pinterest.

## Phase 4 — Recurring monitors (the "every day" ask)
A scheduler that re-runs a saved brief on a cadence and **diffs** against the last run — "what changed
since yesterday," not a fresh report each time. Reuses the whole pipeline; adds a schedule store +
delta synthesis. *This is what turns "research a topic once" into "watch e.l.f. daily."*

## Phase 5 — Vertical productization (CPG/beauty + entertainment — overtake Alloy)
Package the above into vertical briefs, a share-of-buzz composite that declares its inputs and refuses
to compute when one is missing (the SGF lesson), the BQ data-source integration (Starface ref), and
the deliverable polish. This is where method + coverage converge into the product.

---

## How to run it
Build **Phase 1 in full before Phase 3** — the gateway is the thing that makes 40 connectors
maintainable instead of 40 bug sources. Phases 2 and 3 can then proceed in parallel (method vs
breadth). Each connector added in Phase 3 ships with its `sources.py` contract + a captured-payload
unit test, so "did this actor silently drop a field" is caught by CI, not by a client's eyebrow.
