# Corpus v5 — manifest contract, confirmed with amendments

21 August 2026. Response to the proposed `manifest.json` and coverage report.

**The contract is accepted.** Six amendments below, none of which should cost you much. Build to
this and our loader will validate against it exactly.

Two things you added that we had not asked for and that improve it:

**`artifacts`.** Correct call. Validation JSON, collection reports and webpage snapshots are
physical files that are not clinical references, and without this bucket the file-accounting
rule would have forced you to mislabel them as references.

**"Every physical file must appear exactly once under `references[].files` or
`artifacts[].files`."** Stronger than we asked for, and it is the invariant that makes the
manifest a *proof* rather than a claim. Keep it.

---

## Amendments

### 1. `source_role` is authoritative; `position_bearing` is derived and checked

Your table maps role to position-bearing one-to-one. Two fields that must agree are two fields
that can disagree, and the release that first disagrees will be the one nobody reads carefully.

Declare the mapping in the manifest itself so both sides read the same table:

```json
"source_roles": {
  "treatment_guideline":     {"position_bearing": true},
  "treatment_consensus":     {"position_bearing": true},
  "specialist_guidance":     {"position_bearing": false},
  "classification_standard": {"position_bearing": false},
  "measurement_standard":    {"position_bearing": false},
  "assessment_instrument":   {"position_bearing": false},
  "predictive_model":        {"position_bearing": false}
}
```

Keep `position_bearing` on each reference — it is worth being explicit for a human reader — and
our validator will refuse a release where any reference's flag contradicts the declared mapping,
or where a reference carries a `source_role` the mapping does not define.

### 2. A checksum, at release level and per corpus file

Absent from both schemas. v3 and v3.1 carried one; v4 did not. It has caught nothing so far,
which is what a checksum is for.

```json
"checksums": {
  "algorithm": "sha256",
  "release": "01B89B15…",
  "files": {
    "documents.jsonl": "…",
    "chunks.jsonl": "…"
  }
}
```

### 3. The coverage report must name the manifest it was generated from

Otherwise a report generated against one manifest and shipped beside another passes every
internal check while describing a different release.

```json
"manifest_sha256": "…"
```

### 4. `concerns` must surface in the coverage report

A delivered document with a known problem — `siog.prostate.2010` and its superseded treatment
guidance — is currently visible in `manifest.references[].concerns` and invisible in the
report. Add a `concerns` array at the top of the coverage report, aggregating every non-empty
one, so a reader of the report alone sees them.

### 5. `retrieval_excluded_chunks` needs its reason breakdown

`document_coverage` gives the count. We already carry the reasons in our schema, and the
breakdown is what lets us tell a real regression from a re-categorisation — v4's superseded
count fell 38 → 17 and it took a manual set comparison to prove nothing had been unblocked.

```json
"retrieval_excluded_chunks": 23,
"retrieval_excluded_by_reason": {
  "bibliography_entry": 18,
  "appendix": 3,
  "historical_treatment_guidance_superseded": 2
}
```

### 6. `validations.passed` is your self-check, not our verdict

Keep it — it is useful, and a release that fails its own check should never be sent. But **our
loader will recompute every one of those checks and will not read your `passed` field as
evidence.**

This is not distrust of your implementation. It is the pattern this project keeps relearning:
the corpus states something, we accept the statement instead of verifying the thing, and a
mismatch survives for several releases. `passed: true` beside a manifest that does not account
for every file must fail on our side.

---

## On the 85

Your correction is the right kind. The breakdown —

```
217   physical files
 97   provisional logical groups
 85   groups still carrying "requires curation"
```

— and your identification of what is wrongly inside the 97 (validation JSON, collection
reports, invalid downloads, a duplicated SFFPO markdown, webpage snapshots, the duplicated ESMO
bladder import) is more useful than a confirmed number would have been.

Two notes:

**Webpage snapshots.** If a snapshot is a capture of the same guideline as an accompanying PDF,
it belongs in that reference's `files`, not as a separate reference and not as an artifact.
Where you cannot tell, make it an artifact with `belongs_to` naming your best guess and a note
saying it is a guess. A wrong-but-visible attribution is recoverable; a silent one is not.

**The duplicated ESMO bladder import and the duplicated SFFPO markdown.** Keep both files listed
under one reference rather than deleting either. The file-accounting rule then still holds, and
the duplication is recorded rather than tidied away.

---

## On `source_role`

**Confirmed, all seven values, with the mapping in amendment 1.** The examples are right and the
distinction does exactly what the comparison step needs.

Two things to record rather than change:

**`treatment_consensus` may double-count.** If an EAU–ESMO joint consensus is position-bearing
and we also hold EAU alone, a comparison could show two positions where one organisation
contributed to both. That is our problem to solve in the comparison step, not yours in the
manifest — but flag such references in `concerns` so we can see them coming.

**`position_bearing: false` means "holds no position on treatment choice", not "holds no
position".** ESPEN holds positions on nutritional support; a renal guideline holds positions on
platinum eligibility. If we later compare sources within one of those domains, this flag will
need a scope rather than a boolean. Not for v5 — but please note the intended meaning in the
manifest so nobody reads it as the broader claim.

---

Confirm these six and align the implementation. Nothing here should block curation or migration
from continuing in parallel.
