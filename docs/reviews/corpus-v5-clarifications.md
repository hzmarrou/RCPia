# Corpus v5 — answers to your four questions

21 August 2026. Answers to the clarifications raised against
`docs/reviews/corpus-v5-change-request.md`.

Three have definite answers. One is a clinical decision and is marked as such.

---

## 1. Logical reference, not physical file

**The unit of coverage is the logical reference.** Physical files are attributes of it.

The failure this manifest exists to prevent is *"a guideline never arrived and nobody noticed"*,
not *"a file format was missed"*. `EAU_2026_Prostate_Cancer.pdf` and its `.md` are one thing.

```json
{
  "reference_id": "eau-2026-prostate",
  "title": "EAU Guidelines on Prostate Cancer, 2026",
  "folders": ["ref-01"],
  "files": ["EAU_2026_Prostate_Cancer.pdf", "EAU_2026_Prostate_Cancer.md"],
  "processed_from": "EAU_2026_Prostate_Cancer.md",
  "dispositions": [
    {"kind": "corpus", "document_id": "eau.prostate.2026", "chunks": 443}
  ]
}
```

`processed_from` names the file you actually chunked, so provenance stays checkable when a
markdown parse turns out to be the thing that was wrong.

### Your question exposed something we had not seen

`folders` is a list rather than a string because the reference library contains cross-folder
duplicates:

```
ref-21/GGPO_Psycho_Oncology_Longversion_2.0.md      same document, two folders,
ref-22/GGPO_Psycho-oncology_Longversion_2.0.md      differing only in punctuation

ref-15/Mini_GDS_4_items.md                          same instrument, two folders,
ref-22/Mini-GDS-ou-GDS-4-items.md                   differing in name
```

A strictly per-folder manifest would report both as covered and we would ingest the same
document twice under two ids — which is the opposite failure to the one we set out to fix, and
just as invisible.

**So: one entry per logical reference, listing every folder it appears in.** Where you judge
two files to be the same reference, say so and name which you processed. Where you judge them
genuinely different — the GGPO pair may be different editions — say that instead, and give them
separate entries. Either answer is fine; silence is not.

`ref-22/Psycho_Oncology_preview_9780190097660.md` looks like a publisher's preview of a
textbook rather than a guideline. Your call, but our inclination is `excluded`.

---

## 2. Multiple dispositions, attached to the part they cover

**Allow a list.** RECIST really is both a calculator and a body of guidance; G8 really is both
an instrument and a subject of clinical recommendation. Forcing one disposition would make the
manifest inaccurate, and an inaccurate manifest is worse than none because it looks authoritative.

But a bare list loses the distinction that matters. **Each disposition names the part of the
reference it covers:**

```json
{
  "reference_id": "recist-1-1",
  "dispositions": [
    {"kind": "calculator",
     "covers": "response categories, target lesion selection, measurement rules"},
    {"kind": "corpus", "document_id": "recist.1.1",
     "covers": "narrative guidance on assessment timing and interpretation",
     "chunks": 41}
  ]
}
```

```json
{
  "reference_id": "g8",
  "dispositions": [
    {"kind": "tool", "tool_id": "g8",
     "covers": "the seven items and their scoring"},
    {"kind": "corpus",
     "covers": "what an abnormal G8 implies — but no source in ref-09 carries this;
                it comes from the oncogeriatric guideline, see question 4",
     "document_id": null}
  ]
}
```

**Only `corpus` dispositions carry `document_id` and `chunks`**, because those are the ones our
loader checks against the delivered release. Everything else is a statement of intent that we
read but do not verify against chunk files.

Note the second example. The interpretive half of an instrument usually lives in a *different*
reference — the guideline that discusses the score, not the questionnaire. Recording
`"document_id": null` with a note naming where it should come from is exactly the kind of
visible gap we want, and it is how the ESPEN, Pallia-10 and oncogeriatric asks in Part 4 connect
back to the instruments.

Splitting into separate artefacts would work too, but we prefer one entry per reference: the
manifest is a coverage proof, and coverage is a property of the reference.

---

## 3. AUA — excluded pending a clinician decision, but prepare it

**Do not include it in v5. Do the curation work anyway.**

Two reasons for holding it.

**It is a scope decision, not a technical one.** Adding a guideline family changes what the
system compares for every prostate question, and that goes to our clinician.

**And there is a specific hazard worth putting to them.** AUA and NCCN are both American and
draw on largely the same evidence base and the same interpretive tradition. Four authorities of
which two are correlated would show as `consensus` in our comparison step when it is really one
position counted twice — with AFU or EAU appearing as the lone dissenter against a three-to-one
majority that is actually two-to-one. That is a subtler failure than a missing family and
harder to see.

If the clinician wants AUA, that is answerable — the comparison can weight or group by
tradition — but it should be a decision made knowingly.

**Meanwhile: curate it.** Your judgement that the 15 files overlap (algorithms, summaries,
unabridged) is almost certainly right, and that assessment is work the clinician decision does
not depend on. Record in the manifest which files you would deliver and which you would drop,
with `"kind": "excluded"`, `"reason": "pending clinical scope decision — curation complete,
N of 15 files selected"`. Then a yes costs one release rather than two.

Same treatment for the psycho-oncology collection.

---

## 4. Proceed. v5 is complete when the manifest is complete

**v5 must not wait for sources we have not yet chosen.**

The question underneath yours is what "complete" means, so let us define it:

> **A release is complete when its manifest accounts for every logical reference in the library
> — including the ones deliberately excluded and the ones identified as missing.**

Complete does *not* mean every desirable source exists. It means nothing is silently absent.

By that definition, v5 is complete with the current SIOG material, provided the gaps are stated
rather than omitted. Three reasons this is the right line:

- **The blocking item in Part 1 is the manifest, not source coverage.** Consolidation and a
  coverage proof are what v5 is for.
- **The unresolved questions are clinical.** SIOG versus SoFOG is with our clinician and has
  been for some time. A renal source needs someone to say what is authoritative for platinum
  eligibility in impaired clearance. Blocking a consolidation release on those stalls it
  indefinitely.
- **A documented gap is a working outcome.** Once the manifest exists, "no renal source" is a
  visible line in a file rather than something nobody has noticed for five releases. That is the
  whole point.

### What we ask you to record

```json
{
  "reference_id": "siog-prostate-2010",
  "dispositions": [{"kind": "corpus", "document_id": "siog.prostate.2010", "chunks": 42}],
  "concerns": [
    "2010 edition. 17 chunks are blocked downstream as superseded treatment guidance.",
    "A SIOG prostate task force update is believed to exist (c. 2017) and is not in the library."
  ]
}
```

And as declared gaps, with no reference to point at:

```
oncogeriatric, current      pending — SIOG update or SoFOG, with our clinician
renal / platinum eligibility pending — source not yet identified, recommendation requested
nutrition                    ESPEN, ref-17 — available, please include (Part 4.2)
```

A `concerns` field on an included reference and a gaps list are both new. If you would rather
express them differently, propose it — the requirement is that a known problem with a delivered
document, and a known absence, are both machine-readable rather than prose in a covering note.

---

## One thing your questions changed in our thinking

All four are about the manifest rather than about which documents to load, and that is the right
emphasis. The documents are recoverable at any time. **The manifest is the thing that stops this
happening again**, and it is worth getting its shape right before you build to it.

If any of the above is impractical against how the library is actually organised, say so before
implementing. You have seen the sources and we have not.
