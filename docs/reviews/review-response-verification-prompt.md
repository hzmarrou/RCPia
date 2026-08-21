# Review request — verify the twelve findings, and find what they have in common

All twelve accepted and implemented. Three commits:

```
27ec4db  fix: close twelve review findings against the corpus loader
c4718f8  docs: correct a false claim in 63d0d40, and add the review prompt
d8d6676  fix: make filter loss measurable, and stop widening from rescuing the wrong purpose
```

**688 tests pass; `ruff check .` and `mypy rcpia` are clean.** Migration `e5a91c73b2d4` is
applied — `source_family`, `source_role`, `position_domains text[]`, indexed on family.

**Every fix is mutation-tested: reverting it fails a named test.** That is the claim to attack
first, because three of your four blockers were guards that had been mutation-tested and still
did not work.

---

## Read this before the list

**Three of the four blockers were checks that existed, passed their tests, and did not do what
they claimed.**

The block-evaporation guard is the clearest. It was written specifically to stop a re-chunk
silently unblocking the passage that leaked at 0.774. It read the rows the blocking UPDATE had
touched — and on a re-chunk the old row is still present at that moment, so it was blocked,
satisfied the check, and was deleted as stale a few lines later while its replacement went
unblocked. It had a test. The test deleted an entry from a dict, which is a different thing
from a re-chunk.

**And `63d0d40`'s commit message claimed behaviour the code did not have.** The edit never
applied, nothing verified it, and the message went into the permanent record saying the
operator command printed declared gaps. `c4718f8` is an explicit correction rather than a quiet
fix, because the message is what someone reads when deciding whether to look at the code.

So the question that matters more than any individual item below: **what else in this
repository claims to work and does not?** You found three by reading. We would rather you spend
your time there than confirming the twelve.

---

## The twelve, and what to check on each

### Blockers

**1 — block evaporation.** `ingestion.py`. Now checks `seen_chunks` — the release's own chunk
ids — before the UPDATE, never the database. Blocks declare their owning document
(`local_blocks.Block`) rather than having it parsed from an id prefix, as you asked. The UPDATE
is scoped to `declared`.

- Does checking before the UPDATE introduce anything? The order is now: compute owned blocks →
  refuse if any is missing from the release → apply → reconcile stale rows.
- `test_locally_blocked_passages_survive_a_reingest` had been passing **only because the UPDATE
  was unscoped**. Is there another test in that file passing for a reason it does not state?

**2 — circular `source_role`.** `manifest.SOURCE_ROLES` is owned here and versioned
(`SOURCE_ROLES_VERSION = "2.0"`). A release declaring its own `source_roles` mapping is refused;
one built against another version is refused.

- Is refusing an unknown *version* the right granularity, or should a release be able to
  declare a subset it uses?

**3 — source metadata optional.** Required on any reference with a `corpus` disposition. Tools,
calculators and exclusions are exempt — they have nothing for retrieval to route.

- The database columns are still nullable. You said non-null after the v5 migration; v4's rows
  are all null so it cannot happen yet. Is deferring right, and what should force it?

**4 — the file invariant.** You were right and the claim is withdrawn rather than defended: a
loader cannot enumerate a library it never sees, and `_check_folder_coverage` catches only the
largest version — a folder declared `accounted` that nothing claims from, one declared `empty`
that files come from, a file from an undeclared folder.

- **Is the folder-level check worth having, or is it false comfort?** It cannot see an
  individually omitted file. We have asked the corpus agent for a file inventory with digests
  and it is not delivered. Argue if you think a partial check is worse than none.

### The rest

**5 — gaps and concerns print.** `load.py`, last in the output, as warnings not refusals.

**6 — unknown fields.** Six allow-lists plus `MANIFEST_SCHEMA_VERSION`, and the nested
`version` / `source` / `licence` document objects.

- **Are the allow-lists complete?** They were written from the contract document, not from a
  delivered manifest. A field the corpus agent ships that we forgot to list will now refuse the
  release rather than discard it silently — the right failure, but still a failure.

**7 — `release_id`.** Manifest wins; a disagreement is refused. Moved ahead of
`build_embedder`, since two file reads should not need Azure credentials — your finding, but
the reordering was found by the test.

**8 — the index** is declared in `__table_args__`.

**9 — `int()` coercion.** Non-integer indexes refused, `bool` tested first.

**10 — filter loss.** `UnfilteredCandidates` on the report, `trace=True` only, computed from
`Filter.invariants()` — cancer type and `retrieval_eligible` retained.

- **Is that the right denominator?** Including the 214 ineligible chunks would flatter the
  filter by comparing it against a configuration nobody would ship. Excluding them means
  `filter_retention` measures routing loss only, not eligibility loss. We think that is what the
  metric should mean. Say if not.

**11 — `position_domains`.** Replaced the boolean, aligned to `clinical_purpose`. `SOURCE_ROLES`
maps a role to the domains it may cover; a reference declares its subset; claiming beyond it is
refused. `classification_standard` and `measurement_standard` declare an empty list, which is a
statement rather than an absence.

- **Are the domain sets right?** `treatment_guideline` covers therapy indication, modality
  selection and treatment delivery. `specialist_guidance` covers supportive care, safety, risk
  stratification and follow-up. Those were chosen by us and no clinician has seen them.
- ESPEN is the case that proved the boolean wrong, and it is now a test.

**12 — widening.** `clinical_purpose` is never relaxed. Only the therapy line is.

- **Is never-relax right, or too strict?** Your finding was that dropping it let an unrelated
  purpose rescue the search. The alternative to never-relaxing is relaxing to some adjacent set,
  which needs a notion of adjacency nobody has defined. We took the strict option.

---

## One we got wrong while fixing these

`verify_source_metadata` compared the manifest's parsed **tuple** against the raw JSON **list**,
so `("therapy_indication",) != ["therapy_indication"]` and **every well-formed release would
have been refused as a disagreement.**

Too strict rather than too loose, which is the safe direction — but it would have rejected
corpus v5 on arrival and the corpus agent would have been blamed first.

It was caught by the full suite, not by the subset run after that edit. Worth knowing when
judging the mutation-testing claim: mutation tests prove a check *fires*, not that it fires on
the right thing.

---

## What we already know is still weak

- **No retrieval evaluation exists.** Designed only. `RELEVANCE_FLOOR = 0.5` still rests on
  three probe queries.
- **Nothing builds `Concepts` from a case ledger.** First measurements will be an upper bound.
- **Source-stratified retrieval is not built.** Measured: a prostate question on active
  surveillance has NCCN 48, EAU 23, AFU 18 eligible passages, and the top ten returns NCCN 8,
  AFU 2, EAU 0. `position_domains` is the metadata it needs; the retrieval change is not done.
- **Nothing measures citation faithfulness.**
- **Corpus v5 has not arrived.** It is being generated against a contract that changed twice
  during this round — `position_domains` and the owned role vocabulary both landed after they
  had started. They have not been told yet.

---

## How to run it

```bash
cd apps/api
uv run pytest tests/ -q                  # 688
uv run ruff check . && uv run mypy rcpia
```

Database on `localhost:5433`; `.env` names the Docker-internal host, so alembic from the host
needs `DATABASE_URL` overridden.

Freeze at `d8d6676`.

---

## What we most want

1. **Which of the twelve is still not doing what it claims.** Three of four blockers were
   exactly that, and the same author wrote the fixes.
2. **Where else a check exists, passes, and does not hold** — the pattern behind items 1, 4 and
   the false commit message.
3. **The three judgement calls**: the domain sets in item 11, never-relaxing in item 12, and
   whether the folder-level check in item 4 is false comfort.
4. Anything above that is overstated. Two rounds running, the sharpest finding has been a claim
   of ours that did not survive being tested.
