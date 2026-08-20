# Change request — RCPia corpus v3.1 → v4

Corpus v3.1 is loaded: 10 documents, 1,513 passages, all embedded, 38 blocked. The checksum
matched, our validator refused nothing, all 1,227 existing `chunk_id` values were preserved,
and the release loaded on the first attempt. `retrieval_eligible` was exactly the mechanism
needed and we have implemented it.

A clinician has confirmed the SIOG 2010 split: keep the geriatric assessment content, block
the superseded treatment recommendations. That is now enforced in our filter, and widening
cannot reach a blocked passage.

Three findings below. Every number is a query against the loaded database, and the queries
are given at the end.

---

## Part 1 — Blocking: bibliography entries are retrievable passages

We went looking for mislabelled disease settings and found something larger.

**158 retrievable passages across the corpus have a bibliography citation as their section
heading**, 114 of them in the SIOG documents:

| Document | Passages | Citation-shaped |
|---|---|---|
| `siog.geriatric-assessment.2014` | 114 | **55** |
| `siog.geriatric-screening.2015` | 81 | **37** |
| `siog.prostate.2010` | 42 | **21** |
| `siog.bladder.2020` | 49 | 1 |

Ten passages drawn at random from `siog.geriatric-assessment.2014`; seven were reference-list
entries:

```
"2. Crome P, Lally F, Cherubini A, et al: Exclusion of older people from clinical trials..."
"29. Nabhan C, Smith SM, Helenowski I, et al: Analysis of very elderly non-Hodgkin lymphoma..."
"54. Pallis AG, Fortpied C, Wedding U, et al: EORTC elderly task force position paper..."
"72. Deschodt M: Multidisciplinary Geriatric Consultation Teams in Acute Hospitals..."
```

These carry `agent_roles = ['geriatric_frailty']`, a real citation label and a page number.
They are retrieved, reranked and offered to an agent as evidence. A bibliography entry that
names a relevant-sounding paper is exactly the kind of passage a cross-encoder scores highly
and an agent cites — and it contains no guidance at all.

**This changes what your headline figure means.** You reported geriatric coverage rising from
11 passages to 297. By `unit_type` the split is 202 assessment or treatment guidance and 95
background evidence, and a large part of that background is reference lists. The improvement
is real and substantial; it is not 297.

**What we ask:** exclude reference lists, bibliographies and appendices from chunking, or
mark them `retrieval_eligible: false`. You now have that mechanism and it works well —
this is exactly what it is for.

Please apply it across all documents, not only SIOG: 44 of the 158 are elsewhere in the
corpus.

---

## Part 2 — PDF extraction artefacts in `siog.geriatric-assessment.2014`

**27 passages contain uninterpreted PDF character codes**:

```
"...very elderly ( /H1135080 years) non-Hodgkin lymphoma..."
"...factors with P /H11021.20 in univariable analysis..."
"...of drugs /H20851monov polychemotherapy/H20852..."
```

`/H11021` is `<`, `/H20851` is `[`. A passage that reads *"P /H11021.20"* instead of
*"P < .20"* is corrupted in the part that carries the meaning, and it is embedded that way.

Confined to that one document, so it looks like a single extraction path rather than a
general problem.

---

## Part 3 — `general_principle` on setting-specific guidance

Smaller than the two above, and it is the finding we set out to report.

`disease_setting = not_specified` with `not_specified_reason = general_principle` means
*applies to every patient*, and our filter admits it for every disease setting. 230 prostate
passages carry it. Of those, **21 name castration, metastatic disease or systemic therapy**.

Reproduced end to end. Asking *"Quel traitement pour un cancer de prostate localisé à faible
risque, Gleason 6, PSA 5 ?"* returns, at relevance 0.774:

```
nccn.prostate.6.2026 — PRINCIPLES OF ANDROGEN DEPRIVATION THERAPY
"ADT for Patients with M0 CSPC BCR: ADT Options: Orchiectomy..."
```

Androgen deprivation for biochemical recurrence, offered for early low-risk disease, above
our relevance floor, with a real citation.

**We are not asking you to reduce the category.** Most of the 230 are labelled correctly — a
comprehensive geriatric assessment genuinely does apply to every setting, and that is the
label working as intended. Our reviewer proposed excluding `general_principle` outright; we
declined, because it would remove 233 of 297 geriatric passages to fix 21.

The ones that need attention are concentrated in `nccn.prostate.6.2026`, and roughly half are
arguably fine (a `PRINCIPLES OF IMAGING` passage may mention metastatic disease while
remaining general). The clear cases are those whose *subject* is a specific setting:

```
nccn.prostate.6.2026.principles-of-androgen-deprivation-therapy.04.0081   ADT for M0 CSPC BCR
nccn.prostate.6.2026.principles-of-androgen-deprivation-therapy.06.0083   metastatic CSPC
nccn.prostate.6.2026.general.01.0113                                      metastasis-directed therapy
nccn.prostate.6.2026.radiotherapy-details-...01.0115                      metastasis-directed therapy
nccn.prostate.6.2026.parp-inhibitors-with-or-without-arpis.01.0120        mCRPC systemic therapy
```

A passage whose section is `PRINCIPLES OF METASTASIS-DIRECTED THERAPY` is not a general
principle. **Please review the 15 `general_principle` passages in `nccn.prostate.6.2026`
individually** rather than applying a rule; the population is small enough to read.

The same review of the `general_principle` set in the SIOG documents would be worth doing
once Part 1 removes the reference entries, since some of the 21 are bibliography rather than
guidance.

---

## What changed on our side since v3.1

So you know what we now enforce:

- `retrieval_eligible` is honoured, checked unconditionally, and widening cannot relax it
- **A release carrying any field we do not implement is refused.** Three times a field of
  yours has been silently discarded by our loader — `not_specified_reason`, the v3 metadata
  corrections, and `retrieval_eligible`. If you add a field, we will now stop and ask rather
  than ignore it. Tell us about new fields and we will implement them.
- A release must declare permitted values for every routing field; a schema that constrains
  nothing is refused
- `not_determinable` and `ancestry_unreliable` are no longer treated as universally
  applicable, nor is a missing reason

---

## What to hand back

1. Reference lists, bibliographies and appendices excluded or marked ineligible, corpus-wide.
2. The extraction artefacts in `siog.geriatric-assessment.2014` corrected.
3. The 15 `general_principle` passages in `nccn.prostate.6.2026` reviewed individually, with
   a real `disease_setting` where the subject is specific.
4. The revised geriatric count after Part 1, so we can report an honest figure.

Chunk ids stayed stable across v3 → v3.1 and it made the load trivial. Please keep that.

---

## Appendix — how to reproduce

```sql
-- Part 1: bibliography entries chunked as retrievable passages
SELECT document_id, count(*) AS total,
       count(*) FILTER (WHERE section_path[1] ~ '^[0-9]+\.? +[A-Z][a-zA-Z-]+ [A-Z]') AS citation_shaped
FROM evidence_chunks GROUP BY 1 ORDER BY 3 DESC;

-- Part 2: uninterpreted PDF character codes
SELECT document_id, count(*) FROM evidence_chunks
WHERE text ~ '/H[0-9]{5}' GROUP BY 1;

-- Part 3: general_principle passages naming systemic or metastatic therapy
SELECT chunk_id, array_to_string(section_path,' > '), left(text, 120)
FROM evidence_chunks
WHERE disease_setting='not_specified' AND not_specified_reason='general_principle'
  AND retrieval_eligible AND cancer_types @> ARRAY['prostate']
  AND text ~* 'orchiectomy|castration|mCRPC|abiraterone|enzalutamide|docetaxel|metastatic'
ORDER BY document_id, chunk_id;

-- What the geriatric figure is actually made of
SELECT document_id, unit_type, count(*) FROM evidence_chunks
WHERE document_id LIKE 'siog%' GROUP BY 1,2 ORDER BY 1,3 DESC;
```
