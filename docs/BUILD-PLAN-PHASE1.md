# Phase 1 deep-dive — the Trust Layer, spec'd to build

> The guarantee this phase delivers: **Oyster never says "fail," "couldn't get it," or reports a fake
> "0 shares."** Every field on every source resolves to one of a small, honest set of states, and the
> collector never dead-ends — it walks every door and reports exactly which it knocked on.
>
> Builds on `BUILD-PLAN.md`. New code: `research_engine/sources.py` (the registry) and
> `research_engine/collect_gateway.py` (the gateway). Everything else routes through the gateway.

## The one idea that kills "fail / couldn't get it / zero shares": the three-state field model

A metric is never just a number. Every field resolves to exactly one **state**, and the state — not a
raw 0 — is what flows to the report:

| State | Meaning | What the report shows | Example |
|---|---|---|---|
| `value` | got a real number | the number, weighted | Reddit upvotes = 4,300 |
| `not_applicable` | the platform has **no such metric** | omit it, or "n/a on Reddit" — **never 0** | Reddit "shares" |
| `needs_auth` | exists but requires login for precision | the free-tier value **plus** the precision caveat | TikTok Creative Center US/18–24 |
| `suspect` | platform HAS it but this pull is uniformly 0/null/constant | **re-pull first**; if exhausted, "unavailable this pull (tooling), weighted on X" — never 0-as-real | Reddit `upvotes:0`, TikTok `view=1` |
| `missing` | field simply absent this pull | try a route that returns it; else say so | actor didn't include it |
| `proxy` | couldn't get X directly, used Y | "no direct X; proxy Y, confidence …" | per-handle median views → category proxy |

**"Zero shares" is impossible under this model.** On Reddit, `shares` is declared `not_applicable`, so it
is omitted, never shown as 0. On TikTok, if `shares` comes back uniformly 0 while likes/comments are
present, it's `suspect` → re-pull via a route that returns shares → real value, or a labelled caveat.
A 0 only ever prints when it is a *verified real* zero (state `value`), which requires the field to be
`available` on that platform AND non-uniform across the batch.

## The registry — `research_engine/sources.py`

Declarative, one entry per source. This is where "Reddit has no shares" and "this actor needs
includeScore" live — as data, not scattered code.

```python
@dataclass(frozen=True)
class FieldSpec:
    name: str            # normalized: upvotes|likes|comments|shares|views|dislikes|ratio
    availability: str    # "available" | "not_applicable" | "needs_auth"
    aliases: tuple       # raw keys to read (score, ups, diggCount, playCount, …)
    sane_min: float = None   # e.g. views with a plausibility floor to catch the view_count=1 artifact

@dataclass(frozen=True)
class Route:
    kind: str            # "api"|"apify"|"fingerprint"|"reader"|"rss"|"reddit_json"|"manual"
    ref: str             # actor id / endpoint / rss-url-template / slug
    input: dict          # actor/API params — INCLUDING the ones a source needs (includeScore=True)
    requires: frozenset  # fields this route MUST yield, else it's a contract failure -> next route
    tier: str            # "public"|"authed"|"api"|"paywalled"

@dataclass(frozen=True)
class SourceSpec:
    id: str; platform: str
    intents: dict         # intent -> ORDERED list[Route]  (the fallback chain)
    fields: dict          # name -> FieldSpec
    window_mode: str      # "local_ts" | "reddit_base36" | "none"
    min_sample: int = 5
    precision_note: str = ""   # what free-tier loses vs authed/api
```

Example entries (abbreviated):

```python
REDDIT = SourceSpec(
  id="reddit", platform="reddit",
  intents={"search": [
      Route("apify","trudax/reddit-scraper",{"includeMediaLinks":True},frozenset({"upvotes"}),"public"),
      Route("reddit_json","/search.json",{},frozenset({"upvotes"}),"public"),
      Route("fingerprint","old.reddit.com",{},frozenset(),"public"),   # last resort: text, no metrics
  ]},
  fields={
    "upvotes": FieldSpec("upvotes","available",("score","ups","upvotes")),
    "comments":FieldSpec("comments","available",("num_comments","comments")),
    "shares":  FieldSpec("shares","not_applicable",()),      # <- Reddit has no public shares. NEVER 0.
    "dislikes":FieldSpec("dislikes","not_applicable",()),    # <- net-only; raw hidden.
  },
  window_mode="reddit_base36", min_sample=8)

TIKTOK = SourceSpec(id="tiktok", platform="tiktok",
  intents={"hashtag":[Route("apify","clockworks/tiktok-scraper",{},frozenset({"views","likes"}),"public")],
           "creative_center":[Route("api","tiktok/creative-center",{},frozenset({"reach"}),"authed")]},
  fields={"views":FieldSpec("views","available",("playCount","views"),sane_min=2),  # catch view_count=1
          "likes":FieldSpec("likes","available",("diggCount","likes")),
          "shares":FieldSpec("shares","available",("shareCount","shares")),
          "comments":FieldSpec("comments","available",("commentCount",))},
  window_mode="local_ts",
  precision_note="Free path is broad hashtag frequency; US/18–24 demo-filtered reach needs Creative Center (authed).")
```

## The gateway — `research_engine/collect_gateway.py`

Every collection call goes through this. It never raises "couldn't get it"; it returns a structured
result whose worst case is a fully-populated *attempts ledger*.

```python
async def collect(store, job_id, source_id, intent, query, *, window_days, limit, settings) -> CollectResult:
    spec = sources.get(source_id)
    query = shape_query(spec, query)                 # GATE 5: disambiguate + variant-expand
    attempts = []
    for route in spec.intents[intent]:               # GATE 3: ordered fallback chain
        if route.tier in ("authed","api") and not has_creds(route, settings):
            attempts.append(("skip", route.ref, "needs auth")); continue      # note precision loss, keep going
        try:
            rows = await run_route(route, query, limit, window_days)
        except Exception as e:
            attempts.append(("error", route.ref, classify(e))); continue      # never fatal -> next door
        rows = enforce_window(rows, spec, window_days)                          # GATE 4
        missing_req = route.requires - present_fields(rows, spec)
        if missing_req:                                                         # GATE 2: contract
            attempts.append(("contract_fail", route.ref, f"missing {missing_req}")); continue
        fields = resolve_field_states(rows, spec)                              # GATE 1: three-state
        suspect = [f for f,s in fields.items() if s.state=="suspect"]
        if suspect and _has_route_covering(spec,intent,suspect,tried=attempts):
            attempts.append(("suspect_repull", route.ref, suspect)); continue  # try a route that returns it
        stamp_tier_and_precision(rows, route, spec)                            # GATE 6
        return CollectResult(rows=rows, fields=fields, route=route, attempts=attempts, exhausted=False)
    # every door knocked: NOT a failure — an honest, itemized ledger the synthesis turns into one line
    return CollectResult(rows=[], fields={}, route=None, attempts=attempts, exhausted=True)
```

`resolve_field_states` is the anti-"zero shares" core:

```python
def resolve_field_states(rows, spec):
    out = {}
    for name, fs in spec.fields.items():
        if fs.availability == "not_applicable":  out[name]=State("not_applicable"); continue
        if fs.availability == "needs_auth" and not authed(): out[name]=State("needs_auth"); continue
        vals = [read(r, fs.aliases) for r in rows if has(r, fs.aliases)]
        if not vals:                                   out[name]=State("missing")
        elif fs.sane_min and all(v<=fs.sane_min for v in vals) and _other_signal(rows):
                                                       out[name]=State("suspect")   # view_count=1 artifact
        elif all(v==0 for v in vals) and _other_signal(rows) and len(vals)>=3:
                                                       out[name]=State("suspect")   # uniform-zero tooling
        else:                                          out[name]=State("value", vals)
    return out
```

## How each ⚠️ gap resolves — worked, end to end

- **Reddit `upvotes:0`** → route `requires={"upvotes"}` + `includeMediaLinks` on the trudax route; if
  still uniform-zero → `suspect` → auto re-pull via the next upvote-returning route; if all exhausted →
  reported as "upvotes unavailable this pull, consensus weighted on comments" — never 0-as-real.
- **"Zero shares" on Reddit** → `shares.availability="not_applicable"` → omitted / "n/a on Reddit."
- **TikTok `view_count=1`** → `sane_min=2` → `suspect` → re-pull; if unresolved, labelled, not scored.
- **Google Trends null term** → Trends spec marks null as `missing`, **never** coerced to 0-interest;
  report says "no Trends signal for <term>," and gate 3 retries once (the "empty first pull" case).
- **X empty `top_tweets`** → empty array fails the route contract → gate 3 falls to the Apify tweet actor.
- **Teen Vogue / Mashable empty scrape** → `intents["headlines"]=[rss, direct]` → RSS route first.
- **2020 posts in a 2026 window** → `window_mode="reddit_base36"` drops rows below the window-start ID.
- **"IT" / "Persona" false positives** → `shape_query` disambiguates (entity/context scoping); the
  exact-string undercount (12→67) is fixed by variant expansion in the same function.
- **Mintel / Circana paywall** → `tier="paywalled"`, route `kind="manual"` → labelled "trade-press
  summary only," never presented as the full dataset.
- **SteamSpy null for new titles** → `requires={"owners"}` fails → falls back to Store review-count
  route; report notes "owner brackets unavailable for titles <N weeks old."

## The synthesis contract (so the report reads honestly)
`collect()` hands synthesis the `fields` states + the `attempts` ledger. The synth prompt already
leads results-first; it gains one rule: turn any `not_applicable`/`suspect`/`exhausted` into a single
plain sentence ("Reddit has no public share metric; weighted on upvotes+comments") — **never** a bare
"failed," "couldn't get it," or a 0. An `exhausted` source becomes: "we knocked on N doors for X
[list]; the proxy we used is Y." Specific, honest, and forward — the results-first voice, enforced by
structure instead of hoping the model does it.

## Files & tests
- New: `sources.py` (registry), `collect_gateway.py` (gateway), `State`/`CollectResult` dataclasses.
- Extend: `patterns.detect_uniform_zero` → also null/empty/suspicious-constant (feeds `resolve_field_states`).
- Migrate: existing connectors become thin `run_route` fetchers; `graph.collect_prompt` calls the gateway.
- Tests (captured payloads, no network): one per source asserting its field states + contract +
  window + fallback order — including a *fixture that reproduces each ⚠️ gap above* and asserts the
  gateway resolves it to the right state, not a fail/zero. That fixture set is the regression wall.
