# Oyster as an Evidence Layer — not a "trust-me" agent

> The positioning that ties the other docs together. `BUILD-PLAN*` and `DATA-SOURCE-COVERAGE` are
> about *getting* the real numbers; `RESEARCHER-REACH` is about *reasoning*. This doc is about the
> *shape of the output* — what Oyster actually is to the thing consuming it.

## The reframe

Oyster is **not** an agent that produces a final answer you take on faith. It is an **evidence layer**
an agent (or a developer's app) queries. Every result it returns is structured:

```
{ value,                      // the number / quote / finding
  sources: [ {url, tool, pulled_at, excerpt} ],   // deep-link attribution, not "trust me"
  confidence: { level, reasons },                 // explainable, source/result-level — NOT magic
  freshness: { pulled_at, staleness },            // how recent the underlying data is
  field_state }               // value | not_applicable | needs_auth | suspect | proxy | missing
```

The consumer carries these references **forward** into its own justification. So at every step you can
see *which sources supported which intermediate claim or action* — a trail, not a verdict. Instead of
"here's the answer," you get "here's the answer, here's exactly what it rests on, here's how sure to be,
and here's how fresh it is."

## Confidence is a signal, not a certainty

Confidence is **source/result-level** and **derived from explainable inputs**, never a vibes score:
- source authority (who said it), corroboration count (how many independent sources agree),
- engagement weight behind it (the consensus signal), freshness/recency,
- acquisition method (real internal-endpoint number vs a proxy vs a suspect uniform-zero).

It is **composable with the developer's own policy**, not a decision Oyster makes for them:
- require ≥2 corroborating sources before a claim is "accepted,"
- **flag conflicts** when sources disagree (surface both, don't average them into a false middle),
- **drop to a human-review step** when confidence is below the caller's bar.

Oyster's job is to report the signal honestly and expose the conflict; the caller's job is the policy.

## This is already Oyster's DNA — promote it to the interface
Already built, today, as report provenance — this reframe makes it the **primary product surface**:
- `[n]` deterministic bibliography (deep link + quote + tool + pull date) → per-claim attribution.
- source-runs ledger + `raw-data/` black box → the full acquisition trail, auditable.
- three-state field model (value / not_applicable / suspect / proxy / …) → result-level confidence at
  the field grain (the anti-"fake 0" mechanism *is* a confidence signal).
- consensus weighting (endorsement-weighted, normalized) → a corroboration/authority signal.
- `analyze_chatter` sufficiency verdict + `detect_uniform_zero` → honest "how much do we trust this."

## What to add to make it a first-class evidence layer
1. **A result contract** every collector/metric emits: `{value, sources[], confidence{level,reasons},
   freshness, field_state}` — standardize it in the Phase-1 `collect_gateway` output so every source
   speaks the same evidence shape.
2. **Queryable, mid-reasoning** — expose evidence pulls over the MCP/API surface so an agent can ask
   for supporting evidence for a specific claim *during* its reasoning and thread the refs forward,
   not only read a final report. (Oyster is already an MCP server — this extends the tool surface.)
3. **Freshness stamps** on every row (`pulled_at`) and a staleness policy the caller can read.
4. **Conflict detection** — when sources disagree on a value, return both with their attribution and a
   `conflict: true` flag; never silently pick one or average.
5. **Confidence reasons, not just a number** — every confidence carries the explainable inputs above,
   so a developer's policy (or a human reviewer) can act on the *why*.
6. **Human-review hooks** — a result can be marked "needs review" (below-bar confidence, conflict,
   suspect field, opt-in source) so a workflow can route it to a person before it's trusted.

## How it fits the whole picture
- **Acquisition engine** (getting the real numbers) → produces the raw evidence.
- **Evidence layer** (this doc) → wraps each result in attribution + confidence + freshness + state.
- **Researcher-reach method** → reasons over that evidence, carrying references forward.
- Net: the consumer never gets "trust me." It gets real numbers, each with its receipts, its
  honestly-derived confidence, and its recency — composable with its own policies and human review.
