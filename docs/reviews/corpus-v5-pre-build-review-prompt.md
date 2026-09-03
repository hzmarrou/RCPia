# Review request — the v5 corpus, before it is built

**Nothing has been built yet, and that is deliberate.** 37 guideline documents have been
expressed as routing rules, but no release has been emitted and nothing has been loaded. The
database still holds corpus v4.0: 10 documents, 1,513 passages. We want your review *before*
`emit` runs, because after it runs, acting on a finding means re-emitting and re-embedding
everything.

The useful question is not "is this reasonable". It is **"does this corpus claim anything that
is not true, and what did the author miss while being careful about something else?"**

```
289cb10  docs: make the curation say what was already decided, before v5 is built
4bef351  feat: route NCCN, and close step 8
956e48e  feat: whether page furniture ends a section is declared, not inferred
2c5056f  feat: route the classification standards, and invert the default for a reference work
e2c549d  feat: route the ESMO tranche, entirely on leaves
7b251af  fix: a conversion whose headings are page numbers is not usable
fa5b9ec  fix: a failed conversion stops that document, not the batch
c3a64a5  feat: route the rest of the AUA prostate guidelines
44755ae  feat: give nmCRPC a setting, and route AUA advanced prostate cancer
f3a7969  feat: adopt the hand conversions, and stop the CLI demanding a key it does not need
fde739f  feat: PDF conversion becomes stage 0, and checks its own work
8fc4dff  feat: route the two AUA bladder guidelines, and let AUA name its own risk groups
37cfd7d  feat: three nutrition practice tools, and where a cut-point actually belongs
```

**1,138 tests pass; `ruff check .` and `mypy rcpia` are clean.** PostgreSQL 17 with pgvector on
port 5433 (a Docker Compose container, `rcpia-postgres-1` — if the DB tests error en masse with
long timeouts, that container has stopped).

---

## Why this corpus is being rebuilt at all

Phase 6 stalled for a long time. The diagnosis, recorded in
`docs/implementation/phase-6-corpus-build.md` §2, is that the previous builder decided which
patient a passage was about **by reading the passage body**. Measured on `eau.prostate.2026`,
84 of 443 passages depended on that body text, and the results included a passage about diabetes
and metformin routed to locally advanced disease, and section 6.7.15 — *non-metastatic
castrate-resistant* — routed to metastatic hormone-sensitive.

So the rebuild rests on one rule: **a passage is routed by the section it sits in, never by the
words in its body.** Everything below is downstream of that.

---

## How the corpus is built

Four stages, in `apps/api/rcpia/corpus/`. Stage 0 was added late and is not part of the build.

### Stage 0 — `convert.py` (not part of the build)

PDF → markdown via LlamaParse, reusing the dossier parser in `rcpia/parsing/llamaparse.py`
rather than a second client. It converts, then immediately **runs stage 1 against its own
output** and records the verdict in a `.conversion.json` sidecar beside the file.

That self-check exists because two tranches were lost to silent conversion failures: the ASPEN
practice tools and the AFU 2019 chapters are multi-column PDFs whose first conversion read
straight across the columns, producing interleaved sentences and no markdown headings at all.
Nothing reported it until stage 1 refused them weeks later, after curation records had been
written against them.

Conversions cache on the source SHA-256. Deliberately **not** wired into the build: the build
must stay runnable with no network, no API key and no spend.

### Stage 1 — `prepare.py`: the document's own structure

Recovers a heading hierarchy that PDF conversion flattened. Two strategies, chosen from the
document and reported: **numbered** (the heading's own `6.4.5.b` number keys the stack) and
**level** (the `#` depth keys it). The numbered strategy exists because EAU's `#` level
saturates — 243 of 449 headings are `###` — while its numbering runs seven deep.

Also here: page-furniture detection (a heading repeating more than 15 times cannot identify
anything), citation-heading detection (SIOG's screening paper turns 64 bibliography entries
into numbered headings), glyph-artefact refusal (`/H11021` where `<` was meant), and
`furniture_ends_section`, described below.

### Stage 2 — `rules.py` + `routing/*.json`: which patient the section is about

One JSON file per document. A `default`, then ordered rules; the first match wins. A rule names
a substring of the section path (`match_leaf` restricts it to the final element), and assigns a
`disease_setting` from a closed vocabulary, or excludes the passage with a stated reason.

`audit()` refuses a file whose rules are dead (match nothing), shadowed (can never fire), or
ambiguous. `baselines/*.routing.jsonl` records what each file currently does, and
`sections/*.sections.jsonl` records the headings it was checked against, so CI re-proves the
routing with no database, no OneDrive and no PDFs.

### Stage 3 — `split.py`, `locate.py`, `emit.py`

Splits sections into passages (`MAX_CHARS = 3600`, measured from v4.0; tables are atoms and are
never cut), recovers page numbers by token overlap against `pdftotext` output, and writes
`documents.jsonl`, `chunks.jsonl` and `manifest.json`, self-checked against the validators in
`rcpia/evidence/manifest.py`.

**Note for your review:** what gets embedded is `contextual_text` =
`" > ".join(section_path) + "\n\n" + text`. **The section path is inside the embedding.** So a
routing mistake is not only a filter mistake; it changes the vector.

---

## What was built

37 rule files. **4,475 passages, 1,798 excluded (40%), 2,677 retained.**

| document | passages | excluded | rules | furniture_ends_section |
|---|---:|---:|---:|:--:|
| who-iarc.urinary-male-genital.2022 | 1100 | 927 | 6 | |
| eau.prostate.2026 | 577 | 132 | 24 | |
| nccn.prostate.6.2026 | 394 | 127 | 12 | **yes** |
| nccn.bladder.3.2026 | 282 | 72 | 17 | **yes** |
| eau.bladder-mibc.2026 | 227 | 49 | 8 | |
| eau.bladder-nmibc.2026 | 201 | 50 | 9 | |
| afu.prostate.2024-2026 | 144 | 0 | 9 | |
| aua.prostate-advanced.2026 | 119 | 47 | 14 | |
| siog.geriatric-screening.2015 | 109 | 0 | 0 | |
| aua.bladder-nmibc.2024 | 107 | 45 | 9 | |
| aua.prostate-localized.2026 | 103 | 49 | 15 | |
| aua.bladder-mibc.2024 | 100 | 44 | 8 | |
| aua.prostate-early-detection.2026 | 94 | 45 | 7 | |
| eau-esmo.bladder-advanced-variant.2020 | 83 | 10 | 10 | |
| aua.prostate-salvage.2024 | 79 | 34 | 14 | |
| *(21 more, each under 65 passages)* | | | | |

Settings across the retained 2,677:

```
1314  not_specified            118  prostate_biochemical_recurrence
 290  bladder_nmibc_unspecified_risk   109  prostate_metastatic_castration_resistant
 287  bladder_mibc_non_metastatic       83  no_known_cancer
 258  prostate_nonmetastatic_unspecified_risk   76  prostate_metastatic_hormone_sensitive
                                        63  bladder_metastatic
   … then a long tail: 14, 13, 11, 11, 10, 6, 5, 3, 3, 1, 1, 1
```

---

## The decisions we most want challenged

### 1. `not_specified` is 49% of the retained corpus

1,314 of 2,677. Every one is meant to be a passage that genuinely spans settings or states a
general principle, and `ADMISSIBLE_UNSPECIFIED_REASONS` admits `general_principle` and
`spans_settings` for **any** query of the right cancer type.

**So half the corpus is admissible for every question.** Is that a corpus that discriminates, or
a corpus that has given up discriminating and will drown the reranker? We think each individual
decision is defensible. We are much less sure the aggregate is.

### 2. A 40% exclusion rate

1,798 passages are written, hashed and traceable but never answer. Most are bibliographies,
panel rosters, conflict-of-interest disclosures and methodology chapters. **927 of them are one
document**: WHO/IARC, below.

Each rule file states its exclusions. What we cannot check ourselves is whether we have
*over*-excluded somewhere — the author verified per document that no clinically-shaped path was
excluded, using a regex of junk words, which is exactly the kind of check that passes because it
was written by the person who wrote the rules.

### 3. WHO/IARC: the only file whose default excludes

`who-iarc.urinary-male-genital.2022` is a reference work covering the whole urinary tract and
male genital system — 308 top-level entities, of which kidney, testis, penis, scrotum and
adrenal are the majority and none is in `CANCER_TYPES` (prostate, bladder).

With an admitting default, every entity no rule named would enter as a general principle, and
`Birt-Hogg-Dubé syndrome`, `Seminoma` and `Nephroblastoma` would be retrievable for a prostate
question. So the default excludes and six rules admit. **173 of 1,100 passages are admitted.**

Two things to challenge. First, whether inverting the default is right at all, or whether it
throws away real content (`Grading`, `Tumours of the urinary tract: Introduction`, `Clear cell
adenocarcinoma of the urinary tract` are all currently dropped). Second, the admitted passages
carry `not_specified` rather than an organ-prefixed setting — see the file's own note. The first
version used `bladder_nmibc_unspecified_risk` for urothelial entities and that was wrong
(`Invasive urothelial carcinoma` is precisely not non-muscle-invasive), but the honest version
means **a prostate question can retrieve a urothelial definition**, because `cancer_types` is a
document-level property and this document is both.

### 4. `furniture_ends_section` — a declaration, because it could not be measured

NCCN prints a version banner at `#` on every page while every real chapter is `##`. Dropping the
banner as furniture left each chapter attached to the last `#` that survived: `Table of Contents`
held 234 of 394 prostate passages, and `Primary Carcinoma of the Urethra` held 156 of 282 bladder
passages — a heading that is *out of scope*, so a rule excluding it correctly would have deleted
more than half the guideline.

Making a dropped heading pop the stack fixes NCCN and **breaks AUA**, whose banners are page
turns inside a chapter: applied globally it took `aua.prostate-advanced` from 18/17/9/8 passages
across its four settings to 8/5/7/6. The two layouts are identical in markdown. Measuring was
tried — the share of a level that is furniture is 63–82% across AUA and 88–94% across NCCN, six
points apart — and abandoned.

So it is a per-document boolean, declared in the routing file. **Is that the right place, and is
"declare it" the right answer, or is there a discriminator we failed to find?**

### 5. Documents excluded wholesale

- **AUA: 6 of 16 documents routed.** Excluded: 3 duplicates of a routed unabridged guideline
  (verified by probing the unabridged for their distinctive trial names — RTOG 9601, SPPORT,
  GETUG-AFU 16, bicalutamide 150 mg, the 0.7 ng/mL threshold), 4 mermaid-only algorithms, 2
  edition change logs, 1 renal (out of scope).
- **TNM excluded entirely.** `ref-19/.../document.md` is a README *describing* a dataset, and
  contains zero T/N/M definitions. The real rules are `raw_source.json` beside it. The position
  taken is that stage grouping is arithmetic and belongs in `rcpia/rules/`, but that the
  `notes_exceptions` ("Direct extension into prostatic stroma is pT4, but mucosal involvement of
  the prostatic urethra alone is not pT4") are citable clinical statements that *should* be in
  the corpus and currently are not, because they are JSON and stage 1 reads markdown.
- **Psycho-oncology: 29 documents deferred** by the treating clinician. ~2,156 passages, all
  parsing cleanly. Would have grown the corpus by ~60%, mostly as `not_specified`.

### 6. Settings with one or two passages

`bladder_nmibc_very_high_risk` = 1. `prostate_localized_low_risk` = 1.
`bladder_locally_advanced_non_metastatic` = 1. `prostate_locally_advanced_non_metastatic` = 5.

Are these real distinctions the corpus can support, or accidents of one heading somewhere that
will never be retrieved and give false confidence that the setting is covered?

---

## What we already know is weak

**The author's own claims are not self-verifying, and one has already been caught.** Three commit
messages assert that the substring-ordering trap (`mCRPC` is a substring of `nmCRPC`;
`Muscle-Invasive` of `Non–Muscle-Invasive`) is something "no test would catch" and that "both
values are valid, so the audit passes either way". **That is false.** Reversing the two rules
produces `rule 'NON-METASTATIC CASTRATION-RESISTANT' can never fire`. The audit checks
reachability, and total shadowing is unreachable.

The residual risk is real but narrower: **total shadowing is caught, partial shadowing is not.**
If only some of the longer-needle headings contained the shorter needle, the rule would still
fire for the rest, the audit would pass, and a few passages would be misrouted silently.

**That check has since been run, and it found two real defects.** Every pair of rules whose match
sets overlap with different outcomes: 22 pairs across the 37 files, 20 of them the deliberate
orderings working correctly. The two that were not:

- `eau.prostate.2026` section 6.7.15, *recommendation for non-metastatic castrate-resistant
  disease*, was routed to `prostate_nonmetastatic_unspecified_risk`. Its own note said a
  `prostate_nonmetastatic_castration_resistant` value would fix it; that value was added later
  the same day for AUA and this rule was never revisited.
- `nccn.prostate.6.2026` had no rule for `M0 CRPC`, so *apalutamide in M0 CRPC* and
  *darolutamide in M0 CRPC* — the two drugs indicated in non-metastatic castration-resistant
  disease — fell to the document default.

Both fixed. nmCRPC now carries 15 passages across four guidelines rather than 11 across three.
**The check was one-off and is not a test**, so the class is not closed; and one case was
deliberately left alone rather than changed on inference — EAU 6.7.13 and 6.7.14, chapter-wide
summaries titled *recommendations for systemic treatments of castrate-resistant disease*, are
routed to the metastatic setting and plausibly span M0 and M1. **We would value your opinion on
those two.**

**Consistency across 37 files written in one long session.** Standards drifted. `match_leaf` is
used liberally in the ESMO files and sparingly in the AUA ones; `not_specified` versus an
organ-prefixed setting was decided differently in WHO/IARC than elsewhere. Some of that is
justified per document and some is probably just drift. We cannot tell which from the inside.

**Nothing maps a patient to a setting.** `Filter.disease_setting` exists and the corpus is
labelled, but no code anywhere derives a setting from a case — the only `Filter(` construction in
`rcpia/` is inside `retrieval.py` itself. So the routing precision this whole exercise bought is
**not currently reachable from a query.** That is known debt, recorded in `decisions.md`, and it
means the corpus's value cannot yet be measured end to end.

**Clinical correctness is unreviewed.** BCG failure → NMIBC. Organ-preservation therapy → MIBC.
AUA salvage's "molecular imaging metastatic recurrence" → biochemical recurrence rather than
metastatic, on the grounds that its statements read *"in patients with BCR who have non-regional
disease seen on PET/CT but no visible disease on conventional imaging"*. These are judgements a
reviewer without clinical training cannot settle, and they are recorded in prose in each rule
file's `note` partly so a clinician can read them back.

---

## What to review, in priority order

1. **Partial shadowing across all 37 rule files.** The known gap. A rule whose needle is a
   substring of a later rule's needle, where the shadowing is incomplete so the audit stays
   quiet.
2. **`rcpia/corpus/prepare.py`** — the `furniture_ends_section` change (956e48e) is the newest
   and most invasive. Note that the baseline tests compare rules against *committed fixtures*,
   not a fresh parse, so a reader change can invalidate them without failing anything. The author
   checked for fixture drift by hand; there is no test for it.
3. **`rcpia/corpus/rules.py`** — `furniture_ends_section` was added to the file schema. Is a
   layout declaration the right thing to put in a routing file?
4. **The aggregate**, not the individual decisions: the 49% `not_specified`, the 40% exclusion
   rate, the one-passage settings.
5. **`who-iarc.urinary-male-genital.2022.json`** — the inverted default, the largest single
   exclusion in the corpus.
6. **`rcpia/corpus/convert.py`** — newest module, and the only one that talks to the network.
7. **The rule-file `note` fields.** They are the reasoning, and they are load-bearing: if a note
   claims something the rules do not do, that is a finding.

## How to run it

```bash
cd apps/api
uv run pytest -q                       # 1138 pass
uv run ruff check . && uv run mypy rcpia
```

Rule files, fixtures and baselines are in `rcpia/corpus/{routing,sections,baselines}/`. The
audit runs with no database and no source documents. `docs/implementation/phase-6-corpus-build.md`
is the plan; `docs/decisions.md` has a dated entry for every decision above.

## What we most want

Not a list of style nits. **The place where this corpus says something that is not true** — a
passage routed to a setting the guideline does not put it in, an exclusion that removes clinical
content, a note that describes behaviour the rules do not have, or an aggregate property that
makes retrieval worse than v4.0 despite every individual decision being defensible.

If you find nothing in the individual files, say so, and spend the remaining effort on the
aggregate questions in §1, §2 and §6 — that is where we are least able to check ourselves.
