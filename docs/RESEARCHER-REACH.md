# Researcher Reach — making the free path severely strong (v1.1 flagship)

> The free path's ceiling isn't which sources it reaches. It's how it **thinks**.
> A thin/obscure topic isn't the tool's weakness — it's the reason someone opens the tool.
> So the behavior we want isn't "obscure → thinner report." It's **"obscure → researcher mode."**

Apify (and any paid key) then becomes what it should be: a funnel-widener, a cherry on top. The
intelligence lives in the method, so a user with **no keys at all** still gets a real researcher.

This is a methodology build. It gets its own testing pass and does **not** ship in the demo build.

---

## The thesis, in one line

A great researcher / data scientist doesn't stop at "not much out there." They **change technique**:
they model the question, ladder their queries, follow every thread, reason from proxies, read the
silence, and bring a lens. This spec turns each of those into a concrete move in the graph.

---

## Where it plugs into the current graph

Today (`research_engine/graph.py`):

```
plan → discover → collect(lanes) → quantify → synthesize → review_gate ──needs_more──┐
                                                              │ pass                  │
                                                              ▼                       │
                                                     verify → synthesize(final) → export
        ▲                                                                             │
        └──────────────── lanes_for_gaps() re-runs the SAME lanes ◄──────────────────┘
```

The weak link is the loop: `review_gate → needs_more → lanes_for_gaps → collect more of the same`.
It collects *more of the same* instead of reaching *outward*. That's the whole upgrade surface.

Target:

```
plan → MODEL ──► discover → collect(lanes) → quantify → synthesize → review_gate ──needs_more──┐
       (new)                                                            │ pass                  │
                                                                        ▼                       │
                                                               verify → synthesize(final) → export
        ▲                                                                                       │
        └────── REACH: expand from what we found (entities+links+proxies) → targeted collect ◄──┘
                (new — replaces "re-run same lanes")
```

Two new ideas: a **question-model** step up front, and a **reach expansion** step in the loop.

---

## The six moves → concrete changes

### 1. Model the question before mining it  *(the data-modeler move — lead build A)*
Don't gather-then-summarize. First define what would have to be **true** to answer the decision:
the variables, the sub-claims, the evidence each needs. Collection then targets the gaps in that
model, and the review gate measures fill against it (not just raw row counts).

- **New node `model`** after `plan`. Produces a structured "answer skeleton":
  `claims[]` (each with `needs`, `proxy_ok`, `status`), `decision_variables[]`, `disconfirmers[]`.
- **Store** it on the job (JSONB, additive — no migration), same as synthesis.
- **`review_gate`** upgrades from "enough chatter?" to "**which claims are still unfilled?**" — the
  gaps it returns become targeted reach queries, not just lane names.
- Files: `graph.py` (new `model_prompt`, node, wire after plan; `review_gate` reads the model),
  `mcp_server.py` (a `write_question_model` tool mirroring `write_research_synthesis`).

### 2. Ladder the queries  *(planning depth)*
One brief → many angles: synonyms, substitutes, the incumbent's name, the niche communities, time
windows. A researcher never runs one search and stops.

- **`plan_prompt` / `discover_prompt`** emit an explicit **query ladder** (10–20 shaped queries)
  instead of a handful. Cheap, prompt-only, high yield.
- Add a small `research_engine/ladder.py` helper: expand a term into synonyms / "vs" / "alternative
  to" / "problem with" / "<incumbent> complaints" / community-scoped (`site:reddit.com`) shapes.
- Files: `graph.py` prompts, `ladder.py` (new, pure + unit-testable).

### 3. Follow the threads — **agent reach** *(lead build B — the one that changes everything)*
Every finding names new leads: a competitor, a subreddit, a linked video, a named power-user, an
outbound URL. Chase them. Reach **compounds** — 4 weak hits become 40 targeted ones.

- **New `research_engine/reach.py`**: `extract_leads(evidence) -> Leads` pulling from stored rows:
  - **entities** — product/brand/competitor names (already normalized in Apify metadata; for free
    rows, a light NER/keyword pass over titles+excerpts).
  - **links** — outbound URLs in excerpts and the `crawl_web_page` bodies (we already fetch pages;
    just harvest their hrefs), ranked by frequency + relevance.
  - **communities** — subreddits / forums mentioned.
  - **people** — recurring authors worth reading more of.
- **New reach step in the loop**: on `needs_more`, `reach.next_targets(model, evidence, seen)` turns
  those leads into the next collection batch (dedup against a `seen` set so it converges, like the
  loop-until-dry pattern). This **replaces** `lanes_for_gaps`' "same lanes again."
- **`crawl_web_page` already exists** as the traversal primitive; reach just feeds it the harvested
  links. This is why it's mostly free and mostly additive.
- Files: `reach.py` (new), `graph.py` (loop change), small `connectors.py` hook to return page hrefs.

### 4. Reason from proxies — and state the confidence
No data on X? There's data on its substitutes, its category, the incumbent it'd replace, the job it
does. Use it, label it a proxy, and tag the confidence.

- The **question-model** marks each claim `proxy_ok: true/false` and what a valid proxy is.
- **`synth_prompt`** already leans results-first; extend it to render proxy-backed claims explicitly
  ("no direct owner reviews of <niche>; its two closest substitutes show <pattern>; confidence: …").
- Files: `graph.py` (`synth_prompt`), `reporting/html_report.py` (a subtle "proxy" tag on a claim).

### 5. Read the silence
Absence is a finding — nascent, unmet, or dead. A human never returns "nothing there"; they return
what the nothing **means**.

- `patterns.assess_sufficiency` already abstains on thin evidence; add a companion
  `patterns.interpret_absence(model, evidence)` that classifies thin signal → nascent | unmet | dead
  | niche-but-active, with the tell for each.
- **`synth_prompt`** gets a "when signal is genuinely thin, name what the silence means" clause.
- Files: `patterns.py` (new fn + tests), `graph.py` (`synth_prompt`).

### 6. Bring a lens
Jobs-to-be-done, five forces, diffusion of innovation, the vertical's economics. The scaffolding is
the value, not the quotes.

- Small `reporting/lenses.py` registry of named frameworks; the `model`/`synth` prompts may invoke
  the one that fits the decision type.
- Lowest priority of the six — do it after 1–5 land.

---

## Build order (each independently testable)

1. **Query ladder** (`ladder.py` + prompt) — smallest, immediate depth. *Verify: a run issues 10+
   shaped queries; unit tests on the expansion.*
2. **Agent reach** (`reach.py` + loop change) — the multiplier. *Verify: on a deliberately obscure
   brief, round 2 collects entities/links harvested from round 1; the run snowballs, converges via
   `seen`, and doesn't loop forever.*
3. **Question-model** (`model` node + `write_question_model` + gate upgrade). *Verify: the report's
   claims map to the model; the gate chases unfilled claims, not row counts.*
4. **Proxy + silence** (`patterns.interpret_absence`, synth prompt). *Verify: a thin-topic run
   returns a labelled proxy answer + a named "silence" verdict, not a shrug.*
5. **Lenses** — optional polish.

## What's new code vs. touch-only
- **New**: `research_engine/ladder.py`, `research_engine/reach.py`, `reporting/lenses.py`,
  `patterns.interpret_absence`, a `model` node + `write_question_model` tool.
- **Touch**: `graph.py` (model node, loop→reach, prompt upgrades), `mcp_server.py` (one tool),
  `connectors.py` (return page hrefs), `html_report.py` (proxy tag). No DB migration (JSONB).

## Testing
- Pure units for `ladder`, `reach.extract_leads`, `patterns.interpret_absence` (captured sample
  evidence, no network) — mirrors `tests/test_apify.py` / `tests/test_patterns.py`.
- One **obscure-topic acceptance run** (free path only) that must: snowball via reach, converge,
  and produce a labelled, proxy-aware, confidence-tagged answer — the whole point of the build.
- Keep the suite green; the loop must still terminate (round cap + `seen` dedup).

## Success criteria
On a deliberately obscure brief, no paid keys: the tool doesn't return "not much out there." It
returns a modeled answer — direct where it can, proxy where it must, silence read where it should —
and the run visibly **reached outward** (round 2+ targets came from round 1's findings). That's the
moat: the method, not the sources.
