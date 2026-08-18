# Phase 6 design inputs

Collected 17–18 August 2026, before the design session. Not a design — the material the
design should be argued from.

## Why Phase 6 moved ahead of Phase 9

The blinded expert evaluation (`docs/reference/blinded-evaluation-results.md`) measures an
earlier RCPia build against real RCP fiches: **36% strictly better, 24% tied, 40% worse.**
Losses by dimension:

| Dimension | Machine worse |
|---|---|
| **Conformité AFU 2026 / NCCN** | **33%** |
| Sécurité (aucune erreur critique) | 29% |
| Multidisciplinarité | 24% |
| Personnalisation | 16% |

Conformity is the largest deficit and the one no prompting fixes: the agent has nothing to
conform to and no way to check itself. Personalisation is effectively saturated (84% ≥, but
only 9% strictly better — almost all ties), so there is little to win there.

The panel (Phase 9) multiplies whatever grounding one agent has. Five ungrounded
specialists are still ungrounded, at five times the cost.

## The approach under consideration: an LLM-wiki, not vector RAG

Instead of chunking guidelines and retrieving by embedding similarity, hold them as a
structured, hyperlinked, human-readable corpus the agent navigates. Related work goes by
several names — GraphRAG, filesystem-as-memory, agentic search, curated knowledge folders.

Reasons it may suit this project specifically:

- **Guidelines are already a wiki.** AFU and NCCN are hierarchies — disease → stage → risk
  group → recommendation — with stable section numbers. Chunking into fixed windows
  destroys the structure that makes them usable.
- **Citable identity comes free.** RCPia requires every citation to resolve to something a
  reviewer can find. "AFU 2026 §6.3" is checkable by a human; "chunk 4718" is not.
- **We already know what the case is.** Most retrieval systems search because they only
  have free text. RCPia has a validated ledger — `muscle_invasion=Oui`, `egfr=30`,
  `gleason=7 (3+4)`. That is a *key*, not a query. Similarity search would be a worse way
  to answer a question we can answer by lookup, and it fails badly here: text about
  cisplatin eligibility appears in many contexts where it does not apply, and a
  similar-sounding passage is exactly the wrong citation nobody catches.

Counterweights, to be argued rather than assumed: someone must *build* the wiki, which is
clinical work; navigation costs turns and tokens where today one call carries everything;
and similarity search genuinely wins when nobody knows where to look.

Current instinct, not a decision: deterministic routing from the ledger into a structured
index, with the agent free to read further pages when the route is uncertain.

## What to take from *Building Deterministic AI Agents*

Source: `deepcontextai.substack.com/p/building-deterministic-ai-agents`. It proposes two
standards — Open Knowledge Format (OKF v0.2) and Agent Plugins 1.0.0. **Take the ideas, not
the standards.**

Worth stealing:

- **Knowledge as version-controlled Markdown with YAML frontmatter**, carrying identity,
  edition, source and trust metadata (creator, auditor, expiry). This is the LLM-wiki idea
  made concrete, and it answers three open questions at once: stable citable identity,
  edition versioning (AFU 2026 → 2027), and who signed a page off. Review happens through
  a pull request, which is a better audit story than rows in a table.
- **Trust metadata as a first-class field.** `rules/approval.py` already hand-rolls this
  (`approved`, `source`, `approved_by`, `approved_on`) and is missing an expiry. If a
  converging shape exists, adopt it rather than keep the invention.
- **The agent never computes in-context; it calls a deterministic script and validates the
  result.** Already RCPia's principle — but it points at something unplanned: **clinical
  calculators the agent must call rather than reason about.** D'Amico risk group from
  PSA/Gleason/stage, ISUP from Gleason, Cockcroft-Gault clearance. Today a model does these
  silently with no receipt. Making them verified scripts is a real gain on *sécurité*,
  where we lose 29%.

Not worth adopting:

- **OKF as a specification** — v0.2 is early, and a clinical corpus should not ride an
  unstable format. The ideas are stable; the schema is not.
- **Agent Plugins / MCP orchestration** — RCPia runs Pi with `--tools read`, one call,
  everything supplied. That constraint is why agent behaviour is predictable.
- **"Zero-hallucination guardrails" as framing.** Deterministic validators stop hallucinated
  *arithmetic*. They do not stop a wrong recommendation built on correct numbers, which is
  the actual failure mode behind the 40%.

## Open questions for the session

1. **Corpus sourcing and licensing.** Can AFU 2026 and NCCN be stored and restructured
   legally? This constrains everything and is not an engineering question.
2. **What a citable passage *is*** — the unit that becomes `ev-001`, and how it keeps
   section, edition and source.
3. **Versioning.** An opinion cited against an edition must stay reproducible, the way an
   agent run is pinned to a ledger version.
4. **Behaviour when retrieval finds nothing relevant.** Today the agent says so and §6
   declines to name a référentiel. A weak match must not become a citation that looks
   strong.
5. **One format or two?** Do guideline pages and clinical rules share a single signed
   knowledge object, or stay separate? Same provenance problem, same review path — but
   they are used differently.

## Measurement, decided before building

The improvement must be measurable, because it will not be visible by reading: a grounded
answer and an ungrounded one both display convincing citations, and whether the passage was
the *right* one for this patient cannot be seen on the page.

We hold an unusual instrument: 15 dossiers already scored by three specialists on four
dimensions, the first being conformity. So — re-run the same 15 dossiers after Phase 6 and
put the same four questions to the same experts. If conformity does not move, the corpus
did not help, and we would know rather than assume.

This only works if it is built for: same dossiers, same rubric, and the ability to re-run a
case and get a comparable document. Decide the measurement before the retrieval.
