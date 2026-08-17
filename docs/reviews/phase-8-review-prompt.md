# Review request — RCPia Phase 8: the provisional PPS

This is the phase where RCPia produces a document a clinician reads. Everything before it
was internal: a ledger with provenance, an agent opinion with checked citations. This turns
those into a *fiche RCP* — the six-section French form a urology MDT fills in every week.

That is what makes it the highest-risk phase so far. **The output is visually
indistinguishable from the validated record.** Same title, same six numbered sections, same
checkbox conventions. A reader who skims it and acts on it has been misled by RCPia, not by
a model. Weight your findings accordingly.

**What RCPia is.** A web application preparing *provisional* RCP/MDT outputs for urological
oncology (prostate and bladder) at CHU Mohammed VI Marrakech. Every output is reviewed by
clinicians before use. It does not diagnose and does not decide. Clinical text is French;
code and keys are English.

## What to read

`git log 416a1d4..HEAD` plus the two commits before it — five in total, `5fbe125..HEAD`.

| File | What it is |
|---|---|
| `apps/api/rcpia/pps/schemas.py` | The document as typed data, and `assert_provisional` |
| `apps/api/rcpia/pps/generator.py` | Assembles it from a ledger version and agent runs |
| `apps/api/rcpia/pps/render.py` | Renders the six-section French Markdown |
| `apps/api/rcpia/pps/routes.py` | JSON and Markdown endpoints |
| `apps/api/rcpia/rules/referrals.py` | Oncogeriatric / genetic / fertility proposals |
| `apps/api/rcpia/rules/support.py` | Support-care items triggered by recorded values |
| `apps/web/app/cases/[caseId]/pps/page.tsx`, `apps/web/lib/pps.ts` | The page and its client |
| `apps/api/tests/test_pps_generator.py` | 36 tests |
| `docs/reference/pps-format.md`, `docs/reference/examples/` | The target format and the two reference documents |

Run the API suite from `apps/api` with
`TEST_DATABASE_URL=postgresql+psycopg://rcpia:rcpia@localhost:5433/rcpia_test uv run --frozen pytest`
(needs real PostgreSQL). Web: `pnpm test`, `pnpm lint` from `apps/web`. Currently 419 API
tests, 29 web tests, ruff/mypy/eslint clean.

To see real output rather than fixtures, the dev database has an analysed bladder case
(`13d026c3-1e48-46dd-8458-d1b83badfeb0`) — generate its PPS and read it as a clinician
would.

---

## The claims this phase makes

1. **No language model composes the document.** The agent reasoned and had its citations
   checked; the generator is plain Python over typed structures. There is no second place
   a fact can change between the ledger and the page.
2. **It never presents itself as a decision.** Section 2's box is unticked and section 6
   says unvalidated, both hardcoded, with `assert_provisional` refusing to serve a document
   that claims otherwise.
3. **A gap is left visibly empty**, never written around.
4. **Anything derived says what it was derived from** — a referral proposal names the
   recorded value, a treatment step its citation ids, a support item the measurement that
   triggered it.
5. **A withheld recommendation stays withheld** rather than being rendered as a plan.
6. **The document reports the ledger version the agent actually read**, and says so when
   the case has moved on since.
7. **Agent text cannot forge document structure.**

Attack these. As in the previous two rounds, the useful findings came from probing with
real code — direct inserts, concurrent requests, crafted agent outputs, a filesystem
junction. Please do that again rather than reading for style.

---

## Where I am least confident

### a. The clinical thresholds are mine, and one of them could cause harm

`rules/referrals.py` hardcodes `FERTILITY_AGE_MALE = 60` and `FERTILITY_AGE_FEMALE = 45`.
**I invented both.** They are not from any guideline. RCPia proposing *Préservation
fertilité : Non* for a 46-year-old woman with a curable tumour is a real harm, and the form
gives that proposal the same visual weight as the ones derived from a validated score.

`GERIATRIC_AGE = 75` is conventional and `G8_THRESHOLD = 14` is the standard cutoff, so
those I am comfortable with. The support thresholds in `rules/support.py`
(Hb < 10, albumin < 30, MNA < 17, eGFR < 60) are conventional but also unreviewed by a
clinician.

Tell me what should happen here. Options I can see: refuse to propose at all and always
emit *À déterminer*; keep the proposal but mark thresholds as unvalidated on the page;
move them to configuration a clinician signs off. I lean towards the second plus an
explicit "seuil non validé cliniquement" marker, but this is a clinical-safety judgment and
I would rather have an outside view.

### b. Support triggers read contested values without saying so

`generator._values` takes `entry.value`, which for a **conflicting** field is the *first*
reading. The real bladder dossier records creatinine as both `17.33` and `173.3`. If a
triggering field were contradicted that way, a support item would fire on one arbitrary
reading and present its trigger as fact — `DFG : 30 mL/min` reads as settled even when the
ledger has it in amber. The contradiction does appear in the missing-information report,
but nothing connects the two. Is that acceptable, and if not, should the trigger be
suppressed or marked?

### c. The evidence register covers only the lead run

`build_evidence` resolves citations from `runs[0]` alone, while `appendices` lists every
agent's rationale. With one agent this is invisible; with Phase 9's panel, a second
specialist's rationale could reference ids that have no register entry, and a reader
following one would find nothing. Should the register merge across runs, and what happens
when two agents' bundles assign the same `snip-001` to different text?

### d. Which specialist leads section 3

`latest_runs` returns newest-first and `runs[0]` drives the treatment section. With several
specialists that is arbitrary — the most recently completed run wins, which is a function of
job scheduling rather than clinical relevance. Phase 9 introduces a synthesis agent that
should own this. Is leaving it arbitrary until then acceptable, or should the document
refuse to lead with one opinion when several exist?

### e. `assert_provisional` may be too narrow

I replaced a whole-document string scan with a check on the fields RCPia authors, because
scanning the rendered text broke a legitimate case: an agent quoting a previous fiche
("la fiche précédente portait la mention Fiche validée : OUI") is not RCPia making a claim.
The reasoning is that the claim is *structural* and escaping prevents it. Check that
reasoning. Is there a rendering path where agent text still lands somewhere structural — a
table cell, a heading, the download filename, the JSON consumed by the web page?

### f. Markdown escaping

`render.STRUCTURAL_LINE` escapes `#`, `>`, `|`, `---`, `===` and `[x]` at line start;
`_inline` flattens single-line fields. What does that miss? Numbered-list starts, HTML
tags, link and image syntax, and the fact that the web page renders the JSON as React text
rather than Markdown are all things I have not thought hard enough about. Note that the
web page does *not* render Markdown, so the two surfaces have different exposure.

### g. Reproducibility and storage

Nothing is persisted; the PPS is regenerated from the ledger version and the run ids, both
printed in the document. `generated_on` is today's date, so two renders differ by that
field alone across a UTC midnight — the equality test in
`test_the_document_names_what_it_was_built_from` would flake then. Is regeneration the right
model at all, given a clinician may download a document and refer to it later while the
underlying runs remain but the code changes?

### h. Section 4 and section 6 content

Section 4 emits triggered items plus three standing ones (pain, psychological, social) and
says the RCP completes it. Section 6 refuses to name a *référentiel*, because no evidence
corpus exists yet and claiming one would be false. Both are judgment calls about how much
an unvalidated tool should say. Are they right?

Note deliberately: RCPia's nutrition advice **contradicts** `pps-example-01.md`, which
prescribes intravenous albumin as repletion. Albumin is not nutrition. See
`docs/reference/pps-format.md` for why neither reference document is a content benchmark —
and check my reasoning there too, including the inference about which example is human.

---

## Ground rules

- **Verify before reporting.** Write the failing test or run the probe. Two earlier reviews
  reported things that were not true — one about placeholder directories git simply does not
  track, one right about a lockfile's consequences and wrong about its cause.
- **Rank by clinical consequence.** A path that lets the document assert something nobody
  decided outranks anything about structure or style. Say which findings you would block
  the phase on.
- **Say when a claim holds.** It is as useful as a finding and stops me re-litigating it.
- **Read the rendered output as a clinician**, not only the code. If a section is
  misleading, ambiguous, or reads as more settled than it is, that is a finding even with
  no bug behind it.
- **Out of scope:** evidence retrieval and pgvector (deferred Phase 6), the multi-agent
  panel and synthesis (Phase 9), PDF export and the signed package (Phase 12), deployment.
  MVP security is deliberately light — the production system will be rebuilt on an internal
  server — but do report anything letting one organisation read another's data.

## Already verified

- 419 API tests and 29 web tests pass; ruff, mypy and eslint clean.
- Generated against the real bladder case: recommendation withheld and rendered as withheld,
  17 citations resolved to their source sentences, two amber contradictions surfaced,
  oncogeriatric assessment proposed on the recorded G8 of 5 with that value shown.
- Three defects found by auditing before this review and fixed in `a0569f8`: the document
  could show one version's data beside another version's opinion; an agent could forge
  document structure through its rationale; the uploader's own clinical question was being
  discarded in favour of a generic sentence.
