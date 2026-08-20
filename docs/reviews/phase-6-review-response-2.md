# Response to the second Phase 6 review

You were right that "all fixed" was not supported. Three of the four are fixed now. The
fourth I am declining as proposed, with measurements, and I would rather you push back again
than accept a bad trade quietly.

589 tests pass; ruff and mypy are clean across the package.

Note on what you were reading: the migration that appeared mid-review was ours, not another
agent's — `6988a5f`, adding `retrieval_eligible` for corpus v3.1. You froze at `a5d0c95`, so
it was not in scope for you, but it is why the files moved. Corpus v3.1 is now loaded: 10
documents, 1,513 passages.

---

## Fixed

### Under-specified `chunk.schema.json` accepted — blocker

Confirmed exactly as you described. An empty schema produced `enums = {}`, every enum check
was skipped, and `validate_chunk` returned `None` for a record carrying `TYPO_SETTING`,
`fr-FR`, `TYPO_LINE` and invented roles and purposes.

A release is now refused unless it declares permitted values for every field the filter
routes on: `disease_setting`, `therapy_line`, `language`, `cancer_types`, `clinical_purpose`,
`agent_roles`. A schema that constrains nothing is worse than no schema, because it looks
like validation.

Seven tests, one per routing field plus the empty case.

I did not reach for a JSON Schema library. The check is narrow — enums, array shape, digest
shape, required keys — and adding a dependency through our proxy has cost we would rather
spend elsewhere. Say if you think that is the wrong call.

### `asciiword` ignored — major, and worse than you stated

You are right, and the consequence is larger than "English-token matching changes".

Ordinary English words are not `word`. `ts_debug` classifies `cystectomy`, `metastatic` and
`pembrolizumab` all as `asciiword`. The old check verified `word`, `hword` and `hword_part` —
none of which ordinary English text produces. So the check was not weak on the English path;
it was blind to it, across 81% of the corpus.

Replaced with a fingerprint of the complete token mapping, both configurations, all 19 token
types. Proved live rather than argued:

```
clean configuration            passes
ALTER asciiword → simple       detected
after rollback                 passes
```

Fingerprinting avoids having to know which token types matter, which is the assumption that
produced the gap.

### Verified only at ingestion — major

Now also verified once per process, before the first search, and it fails closed.

Once per process rather than per query: the condition changes at most once in a process's
lifetime, and a catalogue round trip per search is a real cost for a check that would be
identical every time. If you think that window is too wide, say so — per-query is a one-line
change.

We did not restrict configuration ownership. The MVP runs as a single role and the production
system will be rebuilt on an internal server. You flagged this as "before clinical use",
which we agree with; it is recorded as a deployment prerequisite rather than done now.

---

## Declined as proposed — `general_principle`

**Your finding is right. Your remedy costs more than it saves.**

I reproduced the leak end to end rather than reasoning about it. Asking *"Quel traitement pour
un cancer de prostate localisé à faible risque, Gleason 6, PSA 5 ?"* returns, at relevance
0.774, above our floor, with a real citation:

```
nccn.prostate.6.2026 — PRINCIPLES OF ANDROGEN DEPRIVATION THERAPY
"ADT for Patients with M0 CSPC BCR: ADT Options: Orchiectomy..."
```

Androgen deprivation for biochemical recurrence, offered for early low-risk disease. Not
theoretical, and not caught by the floor.

**But the measurement you cite is mine and it is stale.** 15 of 33 was corpus v3. Corpus v3.1
added SIOG, and the category is now:

```
general_principle admitted      230
  naming systemic therapy        21   (9%, not 45%)
```

**And the cost of excluding it:**

```
general_principle passages                    230
  siog.geriatric-assessment.2014              114
  siog.geriatric-screening.2015                81
  nccn.prostate.6.2026                         33
geriatric passages it would remove      233 of 297
```

The SIOG assessment and screening documents are almost entirely `general_principle`, and
correctly so — how to perform a comprehensive geriatric assessment genuinely does apply to
every disease setting. That is the label working, not failing.

A clinician approved adding that material hours before your review. Removing 78% of it to
catch 21 mislabelled passages is a trade I am not willing to make silently, so I put it to
the project owner explicitly and he chose to keep the geriatric material and fix the labels.

**I looked for a precise rule and there is none.** The leak does not separate by therapy
line (11 under `not_applicable`, 10 under `primary`), by clinical purpose (spread across
nine), or by agent role (4 of the leaking passages carry `geriatric_frailty` themselves).
It is a labelling defect in individual passages, not a property the filter can see.

`admit_unspecified` remains a `Filter` field, so excluding `general_principle` is one argument
away and the retrieval evaluation will measure it.

---

## What chasing your finding turned up

Investigating the 21 found two problems larger than the one you reported. Both are now
blocking items in `docs/reviews/corpus-v4-change-request.md`.

**Bibliography entries are retrievable passages.** 158 corpus-wide, 114 in SIOG, whose
section heading is a citation:

```
"2. Crome P, Lally F, Cherubini A, et al: Exclusion of older people from clinical trials..."
"54. Pallis AG, Fortpied C, Wedding U, et al: EORTC elderly task force position paper..."
```

Seven of ten random samples from `siog.geriatric-assessment.2014`. They carry the geriatric
role, a citation label and a page number, and they contain no guidance. A reference naming a
relevant-sounding paper is precisely what a cross-encoder ranks highly.

**27 passages carry uninterpreted PDF character codes** — `P /H11021.20` where the source
reads `P < .20`. The meaning-bearing part is corrupted, and it is embedded that way.

This also revises a figure we quoted you: geriatric coverage is 297 passages, of which 202
are guidance and 95 background, much of that background being reference lists. Real
improvement over 11, but not 297.

Roughly six of the 21 leaking passages are SIOG bibliography, so Part 1 of the corpus request
removes them regardless.

---

## Still open, and unchanged

- **No retrieval evaluation exists.** Nothing has measured recall, ranking, filter loss or
  refusal rate. The `0.5` relevance floor still rests on three probe queries. Largest gap.
- Nothing builds `Concepts` from a case ledger; every end-to-end run, including the two
  reproductions above, used concepts written by hand.
- The bilingual terminology map shipped with corpus v3 and is not wired in.

---

## What I would like re-reviewed

1. **The `general_principle` decision.** If you think 21 live leaks outweigh 233 geriatric
   passages, say so plainly and I will take it back to the project owner. The reasoning
   above is the whole of my case.
2. **Once-per-process configuration verification** rather than per-query.
3. **Hand-rolled schema validation** rather than a JSON Schema library.

Commits: `0712e54` (this round), `6988a5f` (corpus v3.1 support).
